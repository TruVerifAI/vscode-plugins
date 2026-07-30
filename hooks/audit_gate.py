#!/usr/bin/env python3
"""PreToolUse gate — TruVerifAI audit-before-commit (proactive-invocation Layer 1).

Fires before a Bash `git commit` / `git merge`. Classifies the staged diff
LOCALLY; if it has risky hunks not covered by a prior PASS audit, BLOCKS with a
message routing the agent to call `audit_coding` (which records coverage). The
agent keeps full context and acts on it, then retries.

Fails OPEN on anything (not configured, no git, network down, our server down) —
the gate never traps the agent. The `recent_pass` escape valve prevents a
hash-misalignment deadlock.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_lib as g
from risk_classifier import classify_diff


def main():
    cfg = g.config()
    if not cfg["enabled"] or not cfg["token"]:
        g.emit_allow()  # not configured → fail open

    inp = g.read_hook_input()
    if inp.get("tool_name") != "Bash":
        g.emit_allow()

    command = (inp.get("tool_input") or {}).get("command", "") or ""
    # Robust to git global options (`git -C repo commit`, `git --no-pager commit`,
    # `git -c k=v commit`) — a naive `git\s+commit` regex would miss those and let the
    # commit bypass the gate entirely (audit F-001, 2026-06-17).
    if not g.command_invokes_git(command, ("commit", "merge")):
        g.emit_allow()  # not a commit/merge

    cwd = inp.get("cwd") or os.getcwd()
    session_id = inp.get("session_id")
    diff = g.staged_diff(cwd, command)
    if not diff.strip():
        g.emit_allow()  # nothing staged

    classification = classify_diff(diff, trigger_threshold=g.effective_threshold(cfg),
                                   file_content_fetcher=g.file_content_fetcher(cwd))
    gate_self = g.diff_touches_gate_self(diff)
    if not classification["risky"] and not gate_self:
        g.emit_allow()  # trivial, non-gate-self change

    repo = g.repo_fingerprint(cwd)

    # Gate self-mutation (§6.1, audit F-005): a commit that modifies the gate's own
    # config/hooks can disable it from inside — privilege escalation. It releases ONLY
    # on a real audit PASS of THIS exact change (no recent_pass, no skip; Option 4,
    # 2026-06-17). ALL gate-self changes bind to the synthesized self-coverage hash —
    # empty-hunk AND risky-hunk (re-audit F-001): the `gself:` namespace is what the
    # SKIP / recent_pass exclusions key off, so a risky gate-self change must NOT fall back
    # to bare-hex hunk hashes (those would be SKIP-releasable). audit_decision_gate_self
    # ignores recent_pass; the server writes the matching gself hash on a PASS.
    if gate_self:
        # Phase 9 (inc 5): a purely INERT gate-self edit (comment / whitespace only — its code
        # skeleton is empty) to a NON-gate-core file doesn't change enforcement behavior, so
        # release it without forcing a full review. gate-CORE files (the classifier, the decision
        # logic, the hook entrypoints + config, the plugin manifest, the real git hooks) ALWAYS
        # require a review, even for an inert edit (a comment there can be load-bearing). A
        # string-value / code change is NOT inert (diff_is_inert keeps string delimiters).
        if g.diff_is_inert(diff) and not g.diff_touches_gate_core(diff):
            g.emit_allow("trivial gate-self edit (comment/whitespace only, non-core) — released")
        resp = g.check_audit_coverage(cfg, repo, [g.gate_self_coverage_hash(diff)])
        action, detail = g.audit_decision_gate_self(resp)
        if action == "deny":
            g.emit_deny(
                "TruVerifAI flagged a high-risk change for a quick review before it ships — "
                "this commit edits the review gate's own settings (risk_signals.json / "
                "risk_classifier.py / gate_lib.py / hooks.json / .claude-plugin), the "
                "highest-stakes area, so the review can't be skipped.\n"
                "Run `audit_coding` with your proposed_action + relevant_code, AND pass:\n"
                f'  gate_repo = "{repo}"\n'
                + ("  gate_diff = the change being committed (run: git diff HEAD)\n"
                   if (g.commit_targets_worktree(command)
                       or not g._git(["diff", "--staged"], cwd).strip())
                   else "  gate_diff = the staged diff (run: git diff --staged)\n") +
                "A PASS lets the commit proceed on retry. (`deliberate_coding` is accepted if the "
                "design is still open. Gate-self changes need a real audit/deliberate PASS of THIS "
                "change — they can't be skipped, and an unrelated recent review won't release them.)"
            )
        g.emit_allow(detail if action == "allow_warn" else None)

    # Non-gate-self risky change: covered / recent_pass escape valve / fail-open. Send the
    # fire-time classifier metadata (+ session_id) so the server can mint a COMPLETE
    # gate-fire context (Step 0, design §2.2) and return its gate_context_id — the
    # preferred skip handle surfaced by skip_and_signal below.
    hashes = [h["content_hash"] for h in classification["hunks"]]
    is_merge = g.is_merge_in_progress(cwd)
    resp = g.check_audit_coverage(cfg, repo, hashes,
                                  classification=classification, session_id=session_id,
                                  is_merge=is_merge)
    action, detail = g.audit_decision(classification, resp, force_risky=False,
                                      tightness=cfg["gate_tightness"])
    if action == "deny":
        cats = ", ".join(sorted({h["category"] for h in classification["hunks"]})) or "high-stakes code"
        # §4.E human override (Phase 4 Increment 1): a floor-class hunk uncovered AND the
        # review tool in a SUSTAINED outage (both server-asserted on `resp`) → no agent
        # self-release; route to a FAST, agent-inaccessible HUMAN via permissionDecision
        # "ask". maybe_human_override EXITS the process on the ask; otherwise it RETURNS and
        # the normal deny below runs. Fails open by construction (None `resp` → our
        # gate-server down → returns → normal deny) and robust (any internal failure → returns
        # → normal deny, never crashes / never auto-allows). Debounced per repo+hunkset.
        g.maybe_human_override(cfg, classification, resp, session_id, repo,
                               permission_mode=inp.get("permission_mode"))
        gcid = (resp or {}).get("gate_context_id")
        # Gate-usability §3.6 ("unstages on block" confusion, 2026-07-22 L9781): this
        # hook fires BEFORE the Bash command runs, so a fused `git add X && git commit`
        # that gets denied never executed its `git add` — nothing was ever staged, and
        # `git diff --staged` would capture an EMPTY diff (the "no binding" episodes).
        # Name the capture command that matches how the gate itself read the change.
        _staged_now = g._git(["diff", "--staged"], cwd)
        if g.commit_targets_worktree(command) or not _staged_now.strip():
            diff_cmd = "git diff HEAD"
            staging_note = (
                "  NOTE: this command was blocked BEFORE it ran — any `git add` in it "
                "never executed, so nothing is staged (nothing was 'reset'). Capture the "
                "change with `git diff HEAD`; after releasing, re-run your full command.\n")
        else:
            diff_cmd = "git diff --staged"
            staging_note = ""
        # Phase 9: pass the gate_context_id to audit_coding so coverage binds to the gate's OWN
        # recorded hunks — a cosmetically-drifted gate_diff (a smart-quote, an em-dash an LLM
        # courier mangled) then still releases the change instead of silently missing coverage.
        gcid_line = f'  gate_context_id = "{gcid}"  (binds coverage to THIS change)\n' if gcid else ""
        # Wave 3 (§3.3): a MERGE commit re-presents branch content whose per-commit
        # receipts don't hash-match the merge diff's re-hunked boundaries. Offer the
        # first-class merge release instead of teaching the accept-risk + external-review
        # pile the 2026-07-22 episode needed. Server-enforced merge-only; floor-denied.
        merge_line = ""
        if is_merge and gcid:
            merge_line = (
                "This is a MERGE commit. If the uncovered content was already reviewed on the "
                "branch being merged (per-commit receipts don't hash-match a merge's re-hunked "
                "diff), release the NON-floor hunks in ONE call: "
                f"`record_gate_skip(branch_already_reviewed, gate_context_id=\"{gcid}\", "
                "reason_text=<which PRs/commits reviewed it>)`. Conflict-resolution or otherwise "
                "NEW floor content still needs a real PASS.\n")
        g.emit_deny(
            f"TruVerifAI flagged a high-risk change worth a review before it ships — this commit "
            f"touches {cats}.\n"
            + g.transparency_block(classification, resp) +
            "Run `audit_coding` with your proposed_action + relevant_code, AND pass:\n"
            f'  gate_repo = "{repo}"\n'
            f"  gate_diff = the change being committed (run: {diff_cmd})\n"
            + staging_note + gcid_line + merge_line +
            "A PASS — a review whose final action is proceed/proceed_with_caveats; a major "
            "finding raises the action past that — releases the commit on retry. It covers FLOOR "
            "and non-floor hunks alike, so "
            "when both are present it is the ONE call that clears everything. Re-committing after "
            # §4.I diff-delta: a prior audit PASS still covers the hunks you didn't touch.
            "fixing earlier findings? Scope `audit_coding` to the changed hunks — your prior PASS "
            "still covers the rest.\n"
            "Each bucket in 'Still uncovered' above has its OWN release, and one does not do the "
            "other's job:\n"
            "  - FLOOR (auth / secrets / money / migrations / removed-guard) you believe mis-fired: "
            "`confirm_floor` (FREE, one model), or `synthesize_coding` (~15-30s) — a low-risk "
            "verdict releases the FLOOR hunks (forward gate_repo + gate_diff + the gate_context_id "
            "above). Neither releases a NON-floor hunk.\n"
            "  - NON-floor risky hunks: an `audit_coding` PASS, or `record_gate_skip` with a "
            "judgment reason (free, one line). Floor tools release NOTHING here.\n"
            "After ONE review, apply its findings and call "
            "`record_gate_skip(recommendations_applied, gate_context_id)` instead of re-auditing "
            "— with your recent review on record it releases FLOOR hunks too (compliance is "
            "never penalized: the release is lineage-verified, minutes-TTL, and logged as "
            "'findings applied', distinct from an audited PASS).\n"
            "LAST RESORT on a FLOOR change — only after the paths above genuinely don't fit (the "
            "gate mis-fired, you're deadlocked, or you're consciously shipping un-reviewed): "
            "`record_gate_skip(accept_risk_no_review, gate_context_id, reason_text=<pre-mortem>)` "
            "ships it un-reviewed as a logged OVERRIDE to the human, not a review; needs a "
            "substantive pre-mortem and expires in minutes.\n"
            "Do NOT disable the gates, and do NOT ask the user to. Every bucket above has a "
            "reachable exit. `accept_risk_no_review` is the FLOOR escape only — it releases a floor "
            "block's floor hunks and nothing else. If a release tool reported success and the count "
            "did NOT move, you cleared a bucket that wasn't the blocking one: re-read 'Still "
            "uncovered' and use that bucket's tool.\n"
            + g.skip_and_signal(classification, audit=True,
                                gate_context_id=(resp or {}).get("gate_context_id"))
            + g.release_options_block(resp)
        )

    # gate_tightness 'focused' downgrade (GATE-TIGHTNESS-DESIGN.md §3/§6b): the uncovered risky
    # hunks are ALL non-floor low-confidence (or soft-floor), so this is not a "major decision" —
    # surface a MODEL-VISIBLE, NON-blocking advisory and allow. CRITICAL (feedback c/d): this path
    # emits emit_allow_advisory ONLY — it never calls record_gate_skip or writes any receipt, so an
    # advisory is neither a block nor a skip and cannot touch the Phase-5 skip counters/suspensions.
    # emit_allow_advisory EXITS the process, so control never falls through to emit_allow below
    # (deny above also exits) — the trailing emit_allow is the allow / allow_warn path only.
    if action == "advise":
        # audit F-004: name only the UNCOVERED (downgraded) hunk categories, not every hunk — a
        # covered floor hunk must not appear here and imply the floor isn't blocking. (action is
        # only 'advise' when coverage was KNOWN, so the tuple's known flag is True here.)
        uncovered, _ = g._uncovered_risky_hunks(classification, resp)
        cats = ", ".join(sorted({h.get("category") for h in uncovered if h.get("category")})) \
            or "low-confidence changes"
        g.emit_allow_advisory(
            "TruVerifAI (focused tightness): this commit touches " + cats + " — lower-confidence, "
            "non-floor changes, so the gate is NOT blocking (floor classes — auth / secrets / money / "
            "migrations / removed-guard — and high-confidence security changes still block). If any of "
            "this is genuinely consequential, consider running `audit_coding` before you rely on it. "
            "To block on every risky change instead, set the plugin's gate_tightness=thorough."
        )

    # Fail-open must never be SILENT (2026-07-23: the gates ran dark against a wrong
    # backend URL for a whole session — every commit sailed through with zero output).
    # Still fail-OPEN — the commit proceeds — but with a model-visible advisory so a
    # broken coverage path is noticed on the FIRST commit, not never.
    if action == "allow" and detail == g.FAIL_OPEN_AUDIT_DETAIL:
        g.emit_allow_advisory(
            "TruVerifAI commit gate: the coverage check was UNREACHABLE, so the gate "
            "FAILED OPEN — this commit was NOT gated. If this repeats, the gates are "
            "not enforcing at all: verify connectivity/API key (the plugin's /setup "
            "command includes a gate-endpoint self-check). Tell the user about this."
        )
    # Reached only for action in {'allow', 'allow_warn'} — 'deny' and 'advise' exited above.
    g.emit_allow(detail if action == "allow_warn" else None)


if __name__ == "__main__":
    g.host_run(main)
