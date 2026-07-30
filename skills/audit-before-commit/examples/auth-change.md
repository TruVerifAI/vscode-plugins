# Example — auditing an auth code change

A worked example showing how to populate inputs and act on the response for a change to authentication logic.

## Scenario

You're about to commit a change that adds refresh-token rotation to the OAuth flow. On each `/api/auth/refresh` call, the server invalidates the old refresh token and issues a new one. Goal: limit replay-attack window if a refresh token is exfiltrated.

## The diff (illustrative)

```python
# auth.py — before
def refresh_access_token(refresh_token: str) -> dict:
    record = RefreshToken.query.filter_by(token=refresh_token).first()
    if not record or record.expired():
        raise UnauthorizedError("Invalid refresh token")
    return {
        "access_token": issue_access_token(record.user_id),
        "refresh_token": refresh_token,  # same token returned
    }

# auth.py — after
def refresh_access_token(refresh_token: str) -> dict:
    record = RefreshToken.query.filter_by(token=refresh_token).first()
    if not record or record.expired():
        raise UnauthorizedError("Invalid refresh token")
    # Rotate: invalidate the old token and mint a new one
    record.revoked_at = datetime.utcnow()
    new_token_value = secrets.token_urlsafe(64)
    new_record = RefreshToken(
        user_id=record.user_id,
        token=new_token_value,
        expires_at=datetime.utcnow() + REFRESH_TTL,
    )
    db.session.add(new_record)
    db.session.commit()
    return {
        "access_token": issue_access_token(record.user_id),
        "refresh_token": new_token_value,
    }
```

## How to populate the inputs

```python
mcp__truverifai__audit_coding(
    proposed_action=(
        "Adding refresh-token rotation to the OAuth flow. On each /api/auth/refresh "
        "call, the server invalidates the old refresh token and issues a new one. "
        "Goal: limit replay-attack window if a refresh token is exfiltrated."
    ),
    relevant_code=(
        "# auth.py — before\n"
        "def refresh_access_token(refresh_token):\n"
        "    record = RefreshToken.query.filter_by(token=refresh_token).first()\n"
        "    ...\n"
        "    return {access_token: ..., refresh_token: refresh_token}\n\n"
        "# auth.py — after\n"
        "def refresh_access_token(refresh_token):\n"
        "    record = RefreshToken.query.filter_by(token=refresh_token).first()\n"
        "    ...\n"
        "    record.revoked_at = datetime.utcnow()\n"
        "    new_token_value = secrets.token_urlsafe(64)\n"
        "    new_record = RefreshToken(...)\n"
        "    db.session.add(new_record)\n"
        "    db.session.commit()\n"
        "    return {access_token: ..., refresh_token: new_token_value}\n\n"
        "# DB schema (unchanged):\n"
        "# RefreshToken(id, user_id, token, expires_at, revoked_at, created_at)"
    ),
    tests=(
        "Existing: test_refresh_with_valid_token, test_refresh_with_expired_token, "
        "test_refresh_with_revoked_token. No new tests added in this change."
    ),
    architectural_context=(
        "Auth layer. Tokens issued during login (/api/auth/login) and refreshed via "
        "/api/auth/refresh. Refresh tokens are stored in the refresh_tokens table; "
        "access tokens are JWTs with short TTL. Per ADR-12 we use short-lived access "
        "tokens with long-lived refresh tokens. The /api/auth/refresh endpoint is "
        "called by web + mobile + API clients."
    ),
    constraints=(
        "All token storage is encrypted at rest (SOC 2 requirement). p99 latency on "
        "/api/auth/refresh must stay under 200ms. Refresh-token rotation must be "
        "backward-compatible: existing tokens issued before this deploy must continue "
        "to work until they expire naturally."
    ),
)
```

## What a good audit response looks like

The response would surface findings like these (illustrative):

**Critical findings:**

1. *TOCTOU race condition.* Between the `record` lookup and the `db.session.commit()`, a concurrent refresh request with the same token could both authorize and both issue new tokens. The window is small (~10ms) but it's a real auth bypass under concurrent usage (mobile clients with retry logic).
   - **Fix suggestion:** wrap the read + revoke + create in a `SELECT ... FOR UPDATE` transaction, or use a unique-when-not-revoked partial index that fails the second concurrent insert.

2. *No new tests for the rotation path.* Existing tests cover the old behavior; the new revoke + mint path is untested. A test that exercises concurrent calls would catch the TOCTOU. (Rated *critical* here — not a quality nit — because this is auth-layer code under a SOC 2 constraint: shipping an untested token-rotation path is a security-grade gap, not a style one.)

**Minor findings:**

3. *Error path leaks the failed-rotation state.* If `db.session.commit()` fails after `record.revoked_at` is set, the old token is now revoked AND no new token is issued. The user is logged out unexpectedly.
   - **Fix suggestion:** explicit try/except with rollback that resets `revoked_at`, or use a single atomic statement.

4. *Backward-compatibility unclear.* The constraint says existing tokens must continue to work, but the new code doesn't check whether the token was issued before the rotation rollout. If old tokens lack the new behavior contract, edge cases may surface.

**Preference findings:**

5. The token entropy is fine (`secrets.token_urlsafe(64)` ≈ 512 bits) but the column type would benefit from explicit length validation at the DB layer.

**Response shape:**

```json
{
  "verdict": "request_changes",
  "findings": [
    { "severity": "critical", "summary": "TOCTOU race between the token lookup and commit allows a concurrent refresh to double-issue tokens — an auth bypass under retry load." },
    { "severity": "critical", "summary": "No tests cover the new revoke + mint rotation path." },
    { "severity": "minor", "summary": "Failed commit after revoke leaves the user logged out with no new token." },
    { "severity": "minor", "summary": "Backward-compatibility for pre-rollout tokens is unverified." },
    { "severity": "preference", "summary": "Add explicit column-length validation at the DB layer." }
  ],
  "action": "escalate_to_human",
  "action_basis": "derived",
  "action_reason": "Verdict was request_changes, but a critical finding (the TOCTOU auth-bypass race) raised the action to escalate_to_human.",
  "agreement_score": 0.84,
  "dimensions_of_disagreement": [
    {
      "model": "gpt-5.4",
      "severity": "low",
      "disagreement": "Treated the TOCTOU as 'edge case' rather than critical; other three models flagged it as critical."
    }
  ]
}
```

## How to act on this

Follow `action`, not the verdict. The verdict here is `request_changes`, but `action` is `escalate_to_human` — read `action_reason`: a `critical` finding (the TOCTOU auth-bypass race) tightened the action above the verdict's base mapping. `agreement_score` (0.84) is auxiliary convergence context; it didn't drive this. `escalate_to_human` means don't proceed without the user's input.

1. Surface the audit findings + the disagreement to the user; don't unilaterally dismiss the TOCTOU. Once you have the user's go-ahead to address-and-proceed:
2. Address findings 1 + 2 (the criticals): add `SELECT ... FOR UPDATE` + concurrent-call test.
3. Address finding 3 (the minor): add explicit rollback handling.
4. Re-audit the revised change. The TOCTOU fix is itself a non-trivial change; verify it.
5. Capture finding 4 as a follow-up task — the backward-compat question deserves explicit verification but may not block this commit if you've already confirmed externally.
6. Ignore finding 5 (preference) unless you have a separate reason to add column-length validation.

Commit only after the revised audit returns `proceed` or `proceed_with_caveats`.
