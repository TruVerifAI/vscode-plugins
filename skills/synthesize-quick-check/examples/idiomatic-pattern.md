# Example — idiomatic-pattern question

A worked example for "what's the standard way to do X in Y?" — the most common synthesize use case.

## Scenario

You're writing a Python service that needs to handle HTTP requests with exponential backoff on transient failures. You vaguely remember `tenacity` and `backoff` as relevant libraries but you're not sure which is the current standard. You want a quick sanity check before committing to one.

## How to populate the inputs

```python
mcp__truverifai__synthesize_coding(
    question=(
        "What's the standard way to handle HTTP retries with exponential "
        "backoff in Python 3.11+ in 2026? Comparing tenacity vs backoff vs "
        "rolling-my-own."
    ),
    context=(
        "Python 3.11, httpx as the HTTP client. Service makes 50-200 outbound "
        "requests per request to external APIs (some flaky). Need retry on "
        "5xx + network errors but not on 4xx. Decision is for an internal "
        "service; reversible later if the choice ages badly."
    ),
)
```

## What a good response looks like

```json
{
  "answer": "tenacity is the de facto standard for retry logic in modern Python (3.11+ in 2026). It's actively maintained, integrates cleanly with both sync and async code, and supports the precise retry-on-exception-type semantics you need. backoff is a viable alternative but has had less active development in the past year. Rolling your own is reasonable for very simple cases (one retry, fixed delay) but not worth it for the multi-condition retry semantics described. Recommended: tenacity with `@retry(retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.NetworkError)), stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=10))`, plus a check on response.status_code >= 500 inside the function body to retry on transient HTTP errors.",
  "answer_status": "settled",
  "findings": [],
  "action": "proceed",
  "agreement_score": 0.92,
  "models_participating": ["gpt-5.4", "claude-sonnet-4-6", "gemini-3-flash", "grok-4-1-fast"]
}
```

## How to act on this

`answer_status = settled` with no `findings` → the verdict is clean; use the answer. The `agreement_score = 0.92` is auxiliary corroboration (strong consensus), and `action = proceed` is the advisory read.

1. Adopt tenacity. Add `tenacity` to your dependencies.
2. Implement the suggested retry decorator + the status-code check. Copy the snippet from the answer or adapt to your code's style.
3. Move on. You spent 15-30 seconds to validate the choice; don't over-engineer the decision.

## Variant — when synthesize is NOT the right primitive

Compare this scenario to a similar-sounding but different question: "Should our service use tenacity-based retries or move the retry logic to an upstream API gateway?" That's NOT a synthesize question — it has multiple defensible architectures, real reversibility cost (changing where retry happens later requires touching multiple files), and the answer depends heavily on your architecture. That's deliberate territory.

The boundary: synthesize is for "which library / pattern is standard," deliberate is for "where in the system should this concern live."

## What this example demonstrates

- Concise question + targeted context = focused answer.
- A `settled` `answer_status` (with a high agreement_score as auxiliary corroboration) on a canonical pattern is the expected case.
- The answer is actionable — includes a code snippet so the agent can implement directly.
- Quick check is for moments where validation is what you need; not for moments where you actually have a design decision.
