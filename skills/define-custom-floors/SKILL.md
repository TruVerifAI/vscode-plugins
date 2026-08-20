---
name: define-custom-floors
description: >
  When the user wants their own domain-critical code protected by TruVerifAI's
  review gates — "mark our tax rules as critical", "changes to X should always
  get a real review", "set up custom floors", "protect this module like auth is
  protected" — or asks what custom floor classes are, or wants to create, edit,
  extend, or fix a repo's .truverifai/risk.json: run the guided define-custom-
  floors workflow (interview → draft → validate with preview → user approval →
  save). Applies to any codebase and any domain (finance, healthcare, pricing,
  compliance — whatever the user names as critical).
---

# Define custom floor classes

TruVerifAI's built-in floor classes (auth, secrets, money, migrations, removed
guards, …) are universal. **Custom floor classes** let this repo declare its own
domain-critical code — "tax rules", "dosing logic", "pricing engine" — in a
committed file, `.truverifai/risk.json` at the repo toplevel. A change matching a
custom floor then blocks exactly like a built-in floor: the free judgment skip is
denied and only a real review (an `audit_coding` PASS, `confirm_floor`,
`synthesize_coding` low-risk verdict, or a logged `accept_risk_no_review`)
releases it. The file needs no server setup and no deployment — committing it is
the whole install.

## The workflow (five steps — the user approves before anything is saved)

**Step 1 — Interview, then go wider than the answers.** Ask the user briefly:
*which code in this repo, if changed incorrectly, would be catastrophic for the
business?* Get concrete modules, directories, config/data files, and (if they
know them) the key identifiers. Two or three questions is enough for the
interview itself — but treat their answers as a **starting point, not the full
scope**. Users name the few areas top of mind and routinely miss others; finding
the rest is your job in Step 2, not theirs.

**Step 2 — Scan the WHOLE codebase thoroughly, then draft broadly.** Take the
time to do this properly. A comprehensive first draft the user trims is far
better than a thin one they have to keep extending (a user should be *removing*
floors, not repeatedly asking you to add them). Thoroughness beats speed here; it
is fine for this step to take a while. **Do not stop at the areas the user
named.**

1. **Map the repo first.** List the top-level directories and modules, the entry
   points, the config/data files, and the dependency/infra files. Understand what
   the codebase actually does before proposing floors.
2. **Enumerate every plausibly-critical domain on your own initiative** — walk
   this checklist against the real code and include a floor for each domain that
   actually exists in this repo, whether or not the interview surfaced it:
   money / billing / credits / pricing / subscriptions; auth / access control /
   permissions / sessions; secrets / credentials / key management / encryption
   (including customer-supplied keys); data integrity (schema, migrations, core
   data models); the core business logic (whatever the product fundamentally
   *is*); abuse / fraud / rate-limiting defenses; the external API / webhook
   surface and anything that spends money or issues access; file-upload,
   deserialization, or other untrusted-input pipelines; compliance / PII / audit
   trails; infrastructure-as-code, deploy, and production config.
3. **Err toward INCLUDING a domain.** If it plausibly belongs, add a floor for it
   with generous but precise path coverage — it is cheaper for the user to delete
   one than to discover a gap after an incident. Your first draft should be
   *broader* than what the user named.
4. Then draft `.truverifai/risk.json`:

```json
{
  "version": 1,
  "custom_floors": [
    {
      "name": "tax_rules",
      "description": "Tax calculation logic and rate tables — must be reviewed before release",
      "paths": ["(^|/)src/tax/"],
      "keywords": ["tax_rate", "withholding_calc"],
      "patterns": ["(?i)\\bTAX_YEAR_\\d{4}\\b"],
      "exclude_paths": ["(^|/)src/tax/examples/"]
    }
  ]
}
```

Field rules (the validator enforces all of this — you don't have to be perfect):

- `name`: `[a-z][a-z0-9_]{1,40}`, unique, no collision with built-in classes,
  must not start with `strict_`.
- `description`: **required** — the user's own words; it is shown verbatim when
  the gate blocks, so make it say *why* this code is critical.
- At least one of: `paths` (regexes vs repo-relative paths — any change to a
  matching file floors; **paths are the 90% answer**), `keywords` (whole-word
  identifiers, matched case-insensitively in added AND removed code lines, never
  in comments/strings), `patterns` (raw regexes, advanced).
- `exclude_paths` (optional): per-floor deny-list — carve out examples/,
  sandboxes, generated copies. Only narrows THIS floor, never built-ins.
  **Treat each entry as a floor removal**: confirm the user understands exactly
  what coverage they're giving up before committing it (the preview in step 3
  shows the excluded count).
- `test_exempt` (optional, default true): whether keyword/pattern matches in
  test/docs paths are exempt, like the built-in content floors. Path floors are
  never test-exempt — the user named those paths explicitly.
- Caps: ≤16 floors, ≤32 keywords+patterns and ≤20 paths per floor.
- `patterns` are real regexes but **catastrophic-backtracking shapes are rejected**
  (a nested unbounded quantifier like `(x+)+` or `(x*)*`) — the validator flags them;
  rewrite without the nesting. And a custom pattern does not scan a single line longer
  than ~2000 chars (minified/data blobs), so anchor domain identifiers to real code,
  not giant one-liners. Keywords are always safe (matched literally).

**Step 3 — Validate with preview.** Run:

```
npx @truverifai/init floors check --preview
```

It validates the schema (collisions, regexes, caps) and shows, per floor, which
actual tracked files the paths cover, what the exclusions removed, and warns if
a floor covers nothing. Fix anything it flags.

**Step 4 — Review with the user.** Show the file AND the preview output. The
user approves, edits, or asks you to revise — loop back to step 3 until they
approve. Never save a floor set the user hasn't seen.

**Step 5 — Save and commit.** Write the file and commit it like any normal file
(no special review is forced on the config itself). The floors are live for
every contributor and every agent platform on their next pull.

## Editing an existing config

Same workflow from step 2 — read the current file first, keep the user's
existing floors unless they ask otherwise, and always re-run step 3 after a
change. Note: removing floors or adding `exclude_paths` narrows protection; the
gate surfaces a low, non-blocking advisory when that happens, which you can
acknowledge and proceed.

## What NOT to do

- Don't invent floors the user didn't ask for; the interview decides scope.
- Don't put secrets or credentials in the config file (it's committed).
- Don't try to tune built-in floors, weights, or thresholds here — the schema
  rejects any such key by design (the file can only ADD protection).
