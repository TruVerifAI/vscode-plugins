# Example — "is there an established pattern for this?" question

A worked example for the "I have a problem; is there a known pattern?" use case. This is the question shape where synthesize shines most — you want to know what the community has converged on.

## Scenario

You're implementing a webhook receiver. The challenge: webhooks from your provider (let's say Stripe) include a signature header that needs validating against the raw request body, but your web framework (Flask) parses the body into JSON before your handler sees it. You vaguely remember this is a common pattern with a standard solution. You want to know what it is.

## How to populate the inputs

```python
mcp__truverifai__synthesize_coding(
    question=(
        "What's the established pattern for validating signed webhook "
        "signatures (like Stripe's Stripe-Signature header) in Flask when "
        "the framework's default JSON parsing happens before my handler? "
        "Specifically: how do I get the raw request body bytes for signature "
        "verification?"
    ),
    context=(
        "Flask 3.0. Stripe webhook receiver. Need to validate the signature "
        "against the raw body before any parsing. Want a clean pattern, not "
        "a hack."
    ),
)
```

## What a good response looks like

```json
{
  "answer": "The standard Flask pattern is to access `request.get_data(as_text=False, cache=True)` BEFORE any JSON access. The `cache=True` ensures the body bytes remain available if downstream code wants to parse them again. The full pattern: in your webhook handler, first call `payload = request.get_data(as_text=False, cache=True)` to get the raw bytes, then pass that to `stripe.Webhook.construct_event(payload, request.headers.get('Stripe-Signature'), STRIPE_WEBHOOK_SECRET)`. The construct_event method handles the HMAC verification + the timing-safe comparison + the timestamp tolerance check. Do NOT access request.json or request.get_json() before this — Flask will buffer the parsed JSON and `get_data` won't return the original bytes consistently. Reference: Stripe's official Python docs use this exact pattern.",
  "answer_status": "settled",
  "findings": [],
  "action": "proceed",
  "agreement_score": 0.94,
  "models_participating": ["gpt-5.4", "claude-sonnet-4-6", "gemini-3-flash", "grok-4-1-fast"]
}
```

## How to act on this

`answer_status = settled` with an empty `findings` list → the verdict is clean; use the pattern directly. The `agreement_score = 0.94` is auxiliary corroboration (essentially unanimous), and `action = proceed` is the advisory read.

1. In your Flask webhook handler, call `request.get_data(as_text=False, cache=True)` as the FIRST line.
2. Pass the raw bytes to `stripe.Webhook.construct_event`.
3. Avoid touching `request.json` / `request.get_json()` before the verification call.

Implementation:

```python
@stripe_bp.route("/webhook", methods=["POST"])
def webhook():
    payload = request.get_data(as_text=False, cache=True)
    sig_header = request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400
    # ... handle event.type ...
```

## What this example demonstrates

- The question is bounded: there IS a canonical answer; you just want to know it.
- A `settled` `answer_status` (with a high agreement_score of 0.94 as auxiliary corroboration) is expected for established patterns.
- The answer is directly implementable — synthesize is for "give me the pattern" not "evaluate my design."
- The response includes the WHY ("Flask buffers the parsed JSON, get_data won't return original bytes consistently"), which lets the agent reason about edge cases without re-asking.

## When this would NOT be synthesize

If the question were "should I use Flask's request.get_data() or rewrite the webhook handler with a custom middleware that captures bytes before any framework parsing?" — that's deliberate. The question is asking about an architectural choice with trade-offs, not "what's the pattern."

The rule of thumb: if you'd be satisfied with the response "do X, here's how," it's synthesize. If you need "X has these trade-offs, Y has those trade-offs, here's how to think about it," it's deliberate.
