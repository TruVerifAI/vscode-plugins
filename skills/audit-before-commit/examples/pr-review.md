# Example — first review of someone else's PR

A worked example for the "first review of a non-trivial PR" trigger. The mental model is different from auditing your own change: you didn't write the code, so you have less intuition about what was intended.

## Scenario

A teammate submitted a PR adding a Stripe webhook handler for the `customer.subscription.deleted` event. The handler downgrades the user's tier to `free` and revokes premium features. You're reviewing it before approving.

## The diff (illustrative)

```python
# stripe_routes.py — added handler
@stripe_bp.route("/webhook", methods=["POST"])
def webhook():
    event = stripe.Webhook.construct_event(
        request.data, request.headers.get("Stripe-Signature"), WEBHOOK_SECRET
    )

    if event.type == "customer.subscription.deleted":
        sub = event.data.object
        user = User.query.filter_by(stripe_customer_id=sub.customer).first()
        if user:
            tier = UserTier.query.filter_by(user_id=user.id).first()
            tier.tier = "free"
            tier.subscription_status = None
            db.session.commit()
            # Revoke features
            user.has_premium_features = False
            db.session.commit()
            send_subscription_cancelled_email(user.email)

    return jsonify({"status": "ok"})
```

## How to populate the inputs

Reviewing someone else's PR is a slightly different exercise — you don't have full context on intent, so be explicit about that in `proposed_action` and lean harder on `architectural_context`. Use the PR description as your starting point for `proposed_action`.

```python
mcp__truverifai__audit_coding(
    proposed_action=(
        "First review of teammate's PR adding a Stripe webhook handler for the "
        "customer.subscription.deleted event. Per PR description: 'When a "
        "subscription is cancelled in Stripe (via customer self-serve or "
        "support action), the user should be downgraded to the free tier and "
        "premium features should be revoked. They also get a notification "
        "email.' I haven't reviewed prior context; flagging that this is a "
        "fresh review."
    ),
    relevant_code=(
        "# stripe_routes.py — added handler\n"
        "@stripe_bp.route('/webhook', methods=['POST'])\n"
        "def webhook():\n"
        "    event = stripe.Webhook.construct_event(...)\n"
        "    if event.type == 'customer.subscription.deleted':\n"
        "        sub = event.data.object\n"
        "        user = User.query.filter_by(stripe_customer_id=sub.customer).first()\n"
        "        if user:\n"
        "            tier = UserTier.query.filter_by(user_id=user.id).first()\n"
        "            tier.tier = 'free'\n"
        "            tier.subscription_status = None\n"
        "            db.session.commit()\n"
        "            user.has_premium_features = False\n"
        "            db.session.commit()\n"
        "            send_subscription_cancelled_email(user.email)\n"
        "    return jsonify({'status': 'ok'})"
    ),
    tests=(
        "PR adds tests/test_stripe_webhook.py::test_subscription_deleted_downgrades_user "
        "which mocks the Stripe event and asserts tier.tier == 'free' after the call. "
        "No test for the multiple-commit behavior or the email path."
    ),
    architectural_context=(
        "Stripe webhook handler. Other event types (customer.subscription.created, "
        "invoice.payment_succeeded, etc.) are handled in the same function. The "
        "webhook URL is configured in Stripe dashboard. Two relevant cross-system "
        "concerns: (1) Stripe retries failed deliveries — handler must be "
        "idempotent. (2) The webhook fires AFTER Stripe has already cancelled "
        "the subscription on their side, so this handler must complete the "
        "downgrade or the user will be in a Stripe-cancelled but app-still-premium "
        "state until manual intervention."
    ),
    constraints=(
        "Stripe webhook signature must validate (already enforced via "
        "stripe.Webhook.construct_event). Idempotency: handler may receive the "
        "same event multiple times due to Stripe's retry behavior — must handle "
        "this gracefully. Handler must respond within Stripe's ~10s timeout."
    ),
)
```

## What a good audit response looks like

**Critical findings:**

1. *Two separate `db.session.commit()` calls without transaction wrapping.* If the second commit (revoking features) fails after the first commit (tier downgrade) succeeded, the user is in an inconsistent state: their tier is `free` but their `has_premium_features` flag is still `True`. The next request from this user could trigger unexpected behavior. **Fix:** wrap the tier downgrade + feature revocation in a single transaction.

2. *No idempotency handling.* If Stripe retries this webhook (which it does on any non-2xx response, including transient DB issues), the handler will re-fire and email the user again. **Fix:** check `subscription.deleted` event uniqueness, e.g., via an `idempotency_key` table or by checking if `tier.tier` is already `free` before processing.

**Minor findings:**

3. *Missing user.* `User.query.filter_by(stripe_customer_id=sub.customer).first()` could return `None` (deleted account, race condition, test customer). The handler silently no-ops in that case. For a payment-flow webhook, missing-user should probably log a warning so support can investigate.

4. *Email is sent before the return statement.* If `send_subscription_cancelled_email` raises, the handler returns 500 to Stripe → Stripe retries → potential double email. **Fix:** send the email after the commit succeeds (already true) but wrap in try/except so email failure doesn't fail the webhook response.

5. *Test coverage gap.* The test mocks the happy path. There are no tests for the multi-commit failure mode, the missing-user case, the idempotency case, or the email-failure case. Stripe webhook handlers are exactly the kind of code that needs failure-path tests.

**Preference findings:**

6. The `if event.type == ...` check pattern works but a dispatch dictionary or a single `match` statement scales better when more event types are added.

**Response shape:**

```json
{
  "verdict": "request_changes",
  "findings": [
    { "severity": "critical", "summary": "Two unwrapped commits can leave tier=free while has_premium_features stays true — inconsistent state." },
    { "severity": "critical", "summary": "No idempotency handling; Stripe retries re-fire the handler and re-email the user." },
    { "severity": "minor", "summary": "Missing-user case silently no-ops; a payment-flow webhook should log a warning." },
    { "severity": "minor", "summary": "Email raised before return can 500 to Stripe and trigger a retry + double email." },
    { "severity": "minor", "summary": "Test coverage only exercises the happy path." },
    { "severity": "preference", "summary": "An event-type dispatch dict scales better than the if-chain." }
  ],
  "action": "escalate_to_human",
  "action_basis": "derived",
  "action_reason": "Verdict was request_changes, but a critical finding (the unwrapped multi-commit inconsistent-state risk on a billing flow) raised the action to escalate_to_human.",
  "agreement_score": 0.79,
  "dimensions_of_disagreement": [
    {
      "model": "claude-sonnet-4-6",
      "severity": "medium",
      "disagreement": "Suggested the idempotency check belongs at the event-id level (using Stripe's event.id) rather than the state level (checking tier == 'free')."
    }
  ]
}
```

## How to act on this

Follow `action`, not the verdict. The verdict is `request_changes`, but `action` is `escalate_to_human` — `action_reason` says a `critical` finding (the unwrapped multi-commit inconsistent-state risk on a billing flow) tightened it. `agreement_score` (0.79) is auxiliary; it didn't drive this. Don't approve the PR, and loop the user in before deciding how to push back.

1. Surface the audit findings to the user and to the PR author as a review comment. The audit is doing the work you'd do in a manual review, with multiple models' perspectives.
2. The critical findings (1 + 2) genuinely block — wrap in a transaction, add idempotency. Comment on the PR asking for these.
3. The minor findings (3 + 4 + 5) are quality improvements you can ask for or merge as follow-ups depending on team norms.
4. The dimensions_of_disagreement entry is useful — one model thought idempotency should be event-id-level. That's a real design choice the author should weigh in on.
5. After the author revises, re-audit the new version. Re-running the audit catches regressions in the fix.

The audit reviewing someone else's PR is one of the highest-leverage uses of this skill. The author's blind spots are often the things you have fresh eyes on; the audit gives you four extra sets of fresh eyes.
