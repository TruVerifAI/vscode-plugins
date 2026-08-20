---
name: define-custom-floors
description: >
  When the user wants their own domain-critical code protected by TruVerifAI's
  review gates — "mark our tax rules as critical", "changes to X should always
  get a real review", "set up custom floors", "protect this module like auth is
  protected" — or asks what custom floor classes are, or wants to create, edit,
  extend, or fix a repo's .truverifai/risk.json: run the guided define-custom-
  floors workflow (scan → propose broadly → refine with the user → validate with
  preview → save). Applies to any codebase and any domain (finance, healthcare,
  pricing, compliance — whatever is business-critical).
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

**Lead with the scan, not the interview.** The single most important thing this
skill does well is open with a *complete* candidate list the user trims, rather
than a thin one they have to keep extending. Do the whole-codebase sweep FIRST
(Step 1) and present every candidate floor up front; the interview (Step 2) then
refines that list. A user should be *removing* floors, not repeatedly asking you
to add them.

**Step 1 — Scan the WHOLE codebase and present the full candidate list.** Take
the time to do this properly; thoroughness beats speed and it is fine for this
step to take a while. Do NOT wait for an interview to build the list — you may
ask one quick framing question, but the deliverable of Step 1 is the complete set
of candidate floors, drafted from the real code.

1. **Map the repo first.** List the top-level directories and modules, the entry
   points, the config/data files, and the dependency/infra files. Understand what
   the codebase actually does before proposing floors.
2. **Enumerate every plausibly-critical domain on your own initiative** — walk
   this checklist against the real code and include a candidate floor for each
   domain that actually exists in this repo:
   money / billing / credits / pricing / subscriptions; auth / access control /
   permissions / sessions; secrets / credentials / key management / encryption
   (including customer-supplied keys); data integrity (schema, migrations, core
   data models); the core business logic (whatever the product fundamentally
   *is*); abuse / fraud / rate-limiting defenses; the external API / webhook
   surface and anything that spends money or issues access; file-upload,
   deserialization, or other untrusted-input pipelines; compliance / PII / audit
   trails; infrastructure-as-code, deploy, and production config.
3. **Err toward INCLUDING a domain.** If it plausibly belongs, add a floor for it
   — it is cheaper for the user to delete one than to discover a gap after an
   incident. Your first draft should be *broader* than anything the user has
   named so far.
4. **Fill every candidate floor completely — paths, keywords, AND excludes — the
   first time** (don't leave keyword/exclude work for a later revision):
   - **Paths** are the backbone. Cover the module's files.
   - **Keywords** are what catch the domain when it is touched from *outside* the
     floored directory, so they must be as thorough as the paths. For each floor,
     grep the covered code for its real, load-bearing identifiers — function
     names, constants, table/column names — and list them (typically 5–15). A
     floor carrying a path but only 1–2 keywords is under-specified; fill it. Do
     this for **every** floor, including any you add later in the conversation —
     don't let mid-interview additions ship thinner than the initial set.
   - **exclude_paths** — for each PATH floor, scan the covered subtree for
     test / example / fixture / generated / vendored directories and add
     `exclude_paths` for them **by default** (you already know the repo layout by
     now). Propose them proactively; surface every one in the Step 4 review so the
     user can veto (the preview shows the excluded count) — never drop coverage
     silently. (Keyword/pattern floors already skip test/docs via `test_exempt`;
     excludes matter mainly for path floors, which are never test-exempt.)
5. **Present the sweep as a coverage table** so a skipped domain is visible: each
   checklist domain marked *present → floored* or *absent in this repo*. Then draft
   `.truverifai/risk.json`:

```json
{
  "version": 1,
  "custom_floors": [
    {
      "name": "tax_rules",
      "description": "Tax calculation logic and rate tables — must be reviewed before release. Wrong values misreport every customer's tax.",
      "paths": ["(^|/)src/tax/"],
      "keywords": [
        "tax_rate", "withholding_calc", "apply_bracket", "TAX_YEAR_TABLE",
        "compute_liability", "exemption_amount", "round_half_even", "jurisdiction_code"
      ],
      "patterns": ["(?i)\\bTAX_YEAR_\\d{4}\\b"],
      "exclude_paths": ["(^|/)src/tax/tests/", "(^|/)src/tax/examples/"]
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
  matching file floors; **paths are the backbone**), `keywords` (whole-word
  identifiers, matched case-insensitively in added AND removed code lines, never
  in comments/strings — **make these thorough, not token-sparse**), `patterns`
  (raw regexes, advanced).
- `exclude_paths` (optional but **proposed by default for path floors**): per-floor
  deny-list — carve out tests/, examples/, sandboxes, generated copies. Only
  narrows THIS floor, never built-ins. Surface each one in the step-4 review so the
  user sees exactly what coverage a given exclusion drops (the preview shows the
  excluded count).
- `test_exempt` (optional, default true): whether keyword/pattern matches in
  test/docs paths are exempt, like the built-in content floors. Path floors are
  never test-exempt — the user named those paths explicitly (this is why path
  floors get `exclude_paths` for their test subtrees).
- Caps: ≤16 floors, ≤32 keywords+patterns and ≤20 paths per floor.
- `patterns` are real regexes but **catastrophic-backtracking shapes are rejected**
  (a nested unbounded quantifier like `(x+)+` or `(x*)*`) — the validator flags them;
  rewrite without the nesting. And a custom pattern does not scan a single line longer
  than ~2000 chars (minified/data blobs), so anchor domain identifiers to real code,
  not giant one-liners. Keywords are always safe (matched literally).

**Step 2 — Refine with the interview.** Now that the user can see the full
candidate list, ask what the code alone can't tell you: *of these, which matter
most? Anything business-critical I missed that isn't obvious from the code? Any of
these you consider out of scope?* Trim, add, and adjust from their answers. The
sweep sets scope; the interview supplies business context and trims.

**Step 3 — Validate with preview.** Run:

```
npx @truverifai/init floors check --preview
```

It validates the schema (collisions, regexes, caps) and shows, per floor, which
actual tracked files the paths cover, what the exclusions removed, and warns if
a floor covers nothing. Fix anything it flags.

**Step 4 — Review with the user.** Show the file, the coverage table, AND the
preview output — including every `exclude_paths` entry and what it drops. The
user approves, edits, or asks you to revise — loop back to step 3 until they
approve. Never save a floor set the user hasn't seen.

**Step 5 — Save and commit.** Write the file and commit it like any normal file
(no special review is forced on the config itself). The floors are live for
every contributor and every agent platform on their next pull.

## Editing an existing config

Read the current file first and keep the user's existing floors unless they ask
otherwise. Then apply the **same discipline as first authoring**: re-run the Step
1 sweep to catch domains added since the file was written, fill any thin floors
with thorough keywords, add `exclude_paths` for test subtrees under path floors,
and always re-run step 3 after a change. Note: removing floors or adding
`exclude_paths` narrows protection; the gate surfaces a low, non-blocking advisory
when that happens, which you can acknowledge and proceed.

## What NOT to do

- **Do** go beyond what the interview named — the sweep decides scope, not the
  interview. The only thing not to invent is a domain that does not exist in this
  repo (don't add a "tax" floor to a repo with no tax code).
- Don't put secrets or credentials in the config file (it's committed).
- Don't try to tune built-in floors, weights, or thresholds here — the schema
  rejects any such key by design (the file can only ADD protection).
