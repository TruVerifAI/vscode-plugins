# `notes` — privacy rules with examples

The `notes` field is a small free-text channel for explaining *why* a call was useful/not-useful or *what* the decision was about. The aggregate is visible to the user on their TruVerifAI dashboard; individual rows are NOT visible to TruVerifAI staff or other users.

Still, agents must not include confidential or code-specific details. The rules:

## Do NOT include

- **Proprietary file paths** — `src/internal/payment/processor.py`, `lib/tenant_isolation.go`, `/home/user/project/...`
- **Function or class names from the user's codebase** — `_validate_payment_signature`, `TenantContext.assume`, `process_refund_with_retry`
- **Secret values** — API keys, tokens, passwords, even partial ones
- **Internal system identifiers** — internal service names, deployment IDs, cluster names, customer IDs
- **Copy-pasted source code** — even small snippets

## Do include

- The decision in general terms: "Chose SQS over Kinesis because ordering wasn't required."
- The reason the call mattered: "Audit caught a missing input-validation check on the new endpoint."
- The reason a call didn't matter: "Duplicated what I already knew about idiomatic React form patterns."

## Examples of acceptable vs unacceptable phrasings

| Unacceptable | Acceptable |
|---|---|
| "Audit caught that `verify_signature()` in `src/webhooks/stripe.ts` was using HMAC-SHA1 instead of SHA256." | "Audit caught a signature-verification algorithm mismatch on a webhook handler." |
| "Changed `TenantContext.assume_role()` to throw on cross-tenant access; previously it logged and continued." | "Changed a tenant-isolation method to throw on cross-tenant access instead of logging and continuing." |
| "Deliberation between Postgres `JSONB` and a normalized `customer_metadata` table; chose `JSONB`." | "Deliberation between JSON column and a normalized metadata table; chose JSON column." |
| "Synthesize confirmed using `tanstack-query` over `swr` for our auth-state hooks." | "Synthesize confirmed library choice for a state-management hook." |

The pattern: keep the SHAPE of the decision and the REASON it mattered; strip the proprietary names. Readers should be able to understand whether the deliberation was useful WITHOUT being able to reconstruct the user's codebase.

## Length

Max 500 characters. Most useful notes fit in 1-2 sentences. Brief is better — long notes invite copy-paste of detail you should have stripped.
