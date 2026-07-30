# Action classes

The audit response carries an `action` enum and an `action_basis` indicator. The combination tells you what to do next.

## The four action values

### `proceed`

The audit found no significant concerns. The change is safe to commit as written.

**How to respond:** commit. No further action needed.

### `proceed_with_caveats`

The audit found minor or preference-level issues but no critical concerns. The change can ship after addressing the caveats.

**How to respond:**

1. Read the `minor` findings (see `severity-tags.md`).
2. Address what you can address quickly. Capture the rest as follow-ups.
3. Commit.

### `request_changes`

The audit found issues that should be addressed before the change ships. Not a hard block, but the audit thinks the change is incomplete as written. `request_changes` is typically driven by a `major` finding — a real defect that must be fixed. There should be no `critical` finding at this action level: a critical would have raised `action` to `escalate_to_human`.

**How to respond:**

1. Read all `major` and `minor` findings. The `major` findings are what drove this action — address them; they are not optional follow-ups.
2. Address them. Re-audit if the revision is substantial.
3. If you disagree with a finding, document why in the commit message or PR description.
4. Commit only after the `major` findings are addressed.

### `escalate_to_human`

The audit reached this because the verdict mapped to it (a `reject`-class verdict) OR a `critical` finding fired and raised the action here. It is NOT reached from a low `agreement_score`. Typical triggers:

- A `reject` verdict — the audit judges the change unshippable as written.
- A `critical` finding the agent should not unilaterally dismiss (raises `action` here even when the verdict itself was milder; `action_reason` records the cause).
- A situation where the trade-offs are policy decisions, not technical decisions.

**How to respond:**

1. **Do not commit the change without the user's explicit input.** This is the strongest signal in the contract.
2. Surface the audit's findings + the disagreement dimensions to the user.
3. Ask the user how to proceed. Common forms:
   - "The audit raised X concern. Should I address it as suggested, or do you want me to proceed as-is?"
   - "The four models disagreed on Y. Here's the breakdown. Which approach do you want?"
4. Wait for the user's call before proceeding.

## The `action_basis` field

`action_basis` distinguishes how `action` was determined. Three values:

### `derived`

`action` was derived from the **verdict + findings (severity)** — the verdict sets the base mapping, and a finding can tighten the action beyond it (in which case `action_reason` explains the divergence). `agreement_score` does NOT contribute to this computation; it's auxiliary convergence telemetry only. This is the normal path — the action is a real signal, treat it accordingly.

### `parse_failure`

The aggregator's structured-output block didn't parse cleanly (missing fields, malformed JSON, score not numeric, etc.). `action` defaulted to `escalate_to_human` as a defensive fallback, but **this is not a real critical-dissent signal** — it just means the audit's structured output was malformed.

**How to respond:** check `assessment_lookup.error_code` for diagnostic detail. Surface the response text to the user (it's still a multi-model critique, just missing the structured assessment block on top). Don't treat the `escalate_to_human` action as a real critical-dissent signal; the user can read the text and decide.

### `config_error`

The profile's `action_class` config is malformed (defense-in-depth — schema validation at server startup should prevent this from happening in practice). `action` falls back to `escalate_to_human`.

**How to respond:** same as `parse_failure` — the response text is still valid; the structured-action layer is broken; defer to the user.

## Summary table

| `action` | `action_basis` | Meaning | Default response |
|---|---|---|---|
| `proceed` | `derived` | All clear | Commit |
| `proceed_with_caveats` | `derived` | Minor issues | Address quickly, commit |
| `request_changes` | `derived` | Real concerns | Address, then commit |
| `escalate_to_human` | `derived` | `reject` verdict or a `critical` finding | Ask user before committing |
| any | `parse_failure` | Output malformed | Read text, ask user |
| any | `config_error` | Server config broken | Read text, ask user |

When `action_basis` is `derived`, branch on `action`. When `action_basis` is anything else, defer to the user regardless of the surfaced `action`.
