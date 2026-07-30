"""Two-channel deterministic risk classifier for the proactive-invocation gates.

v2 of the classifier (design: docs/MCP/Classifier/risk-trigger-classifier-design.md).
Replaces the v1 keyword/path matcher. Same public contract — `classify_diff()` and
`hunk_content_hash()` are byte-stable so existing receipts keep coverage — with a
superset output (adds `borderline_tier` + per-hunk `signals`).

Key ideas (see the design doc):
- **Declarative config.** All signals/weights/thresholds live in `risk_signals.json`
  (co-located). The engine just compiles + scores. This is the single source of truth;
  `plugin/hooks/risk_classifier.py` + `risk_signals.json` are byte-identical vendored
  copies (scripts/sync_risk_classifier.py; enforced by tests/test_classifier_sync.py).
- **Two channels.** Each signal is `trigger`-class (high-confidence, specific risk) or a
  borderline class (`primitive` / `significance` / `domain`). The *trigger* decision reads
  ONLY the trigger-class sub-score, so borderline-class weight can never promote to a hard
  trigger (the §4.2 cap, by construction). Suppressors apply to the trigger sub-score
  (demote trigger->borderline) but never silence a flagged trigger-class risk (F-007 floor).
- **Removed lines too.** A removed safety control (auth/validation/bounds) scores even when
  the added side is clean — closes the added-lines-only recall blind spot.
- **Borderline sub-tier.** `borderline_tier` ∈ {heavy, lite, None} drives the §6.5
  synthesize soft-gate: Heavy = a genuine *spike* (a borderline signal near a trigger-class
  signal, or significance + domain co-occurrence), never primitive density.

Pure stdlib, no model, no network. Runs server-side (import) AND vendored in the client
hook AND as a CLI:  `git diff --staged | python -m mcp_server.risk_classifier`
"""

import hashlib
import json
import os
import re
import sys
import unicodedata
import warnings


HIGH = "high"
LOW = "low"

_TRIGGER_CLASS = "trigger"
_BORDERLINE_CLASSES = ("primitive", "significance", "domain")

# ---------------------------------------------------------------------------
# Floor categories + gate-tightness tier (single source of truth, shared byte-identical
# by the client gate hook and the server). The gate's HARD FLOOR (auth / secrets / money /
# migration / removed-guard) always blocks a commit; `gate_tightness` tunes only the
# NON-floor surface. `mcp_server.gate_fire_models.FLOOR_CATEGORIES` imports these so the
# server-side floor derivation and the client-side tightness partition never drift.
# See docs/MCP/gate skip solve/GATE-TIGHTNESS-DESIGN.md.
# ---------------------------------------------------------------------------

# The highest-risk classes the gate's hard floor protects, mapped to the classifier's actual
# category names (risk_signals.json). Design wording "auth / secrets / money / migration /
# removed-guard" → these tags.
FLOOR_CATEGORIES = frozenset({
    "auth_security",       # auth
    "hardcoded_secret",    # secrets
    "secret_material",     # secrets
    "billing",             # money — NAMED-SDK ONLY post-Phase-4 (B3): the ambiguous money nouns
                           #   (charge/invoice/…) moved to the non-floor `financial_custom` advisory;
                           #   webhook-sig / idempotency-key REMOVAL is added to this floor via the
                           #   `billing_control_removed` signal. (Fixes the actions/checkout FP.)
    "removed_guard",       # removed-guard
    # --- Phase 2 coverage-expansion floors (v2.7.0). Each is a NEW, distinct category (never an
    # existing one) so it adds floor surface without re-classifying any current signal:
    "ci_secret_echo",           # CI secret echoed to logs (echo/printf `${{ secrets.* }}`) — leak
    "unsafe_deserialization_ml", # ML model deser RCE — joblib.load (CWE-502); torch.load stays advisory (see risk_signals.json)
    # --- Phase 3 coverage-expansion floors (v2.8.0, Mechanism M1 co-occurrence):
    "ci_pwn_request",           # GH Actions pwn-request: pull_request_target + untrusted-head checkout
    "pii_redaction_removed",    # PII masking/redaction call removed and not re-added (refactor-safe)
    # --- Phase 4 coverage-expansion RE-CATEGORIZATIONS (v2.9.0, Batch B — the one phase that changes
    # existing floor contracts; deliberated mcp_4b23ca82 + owner sign-off 2026-07-09, PHASE4-DESIGN.md):
    "migration_destructive",    # B2: DESTRUCTIVE-only migration (DROP TABLE/COLUMN, TRUNCATE, DROP
                                #   INDEX, FK/constraint drop, downgrade() removal, proto required-field
                                #   removal). ADDITIVE migrations → the non-floor `migration_additive`
                                #   advisory; migration_path DROPPED to non-floor. Replaces the old
                                #   all-migrations `migration_schema` / `migration_path` floor.
    "iac_exposure",             # B4: sharp public-exposure IaC literals (0.0.0.0/0, Action/Principal:*,
                                #   public-read, privileged:true, runAsUser:0) PROMOTED non-floor→floor.
    # --- Phase 5a coverage-expansion floor (v2.10.0, Mechanism M2; deliberated mcp_98973ec2 + owner
    # sign-off 2026-07-09, PHASE5A-M2-DESIGN.md):
    "memory_safety",            # self-precise C memory-corruption sinks (gets/strcpy/strcat — gets was
                                #   removed from C11 for having no safe use). PATH-UNCONDITIONAL BY
                                #   INVARIANT: a floor is NEVER path-gated (the loader forbids it) — the
                                #   self-precise patterns barely FP cross-language, and path-gating a
                                #   floor would be a silent bypass. M2 arms only the non-floor C-memory
                                #   ADVISORY (ffi_unsafe_c_memory); the floor fires everywhere.
    # --- Phase 5b coverage-expansion floor (v2.11.0; deliberated mcp_c2e4e9cd + owner sign-off 2026-07-09):
    "tls_pinning_removed",      # C4: TLS cert-pinning removed / arbitrary-loads bypass added — SELF-PRECISE
                                #   vendor tokens ONLY (CertificatePinner, NSAllowsArbitraryLoads,
                                #   TrustAllHostnameVerifier, ...), PATH-UNCONDITIONAL. Bare `pinning`/
                                #   `sslPinning` (which collide with UI/version/thread pinning) are EXCLUDED
                                #   from the floor. Directional: fire on enforcer-REMOVAL or bypass-ADDITION.
})

# SOFT_FLOOR: a floor category that can only fire at LOW confidence — blocks under 'thorough' with
# full floor enforcement (no recent_pass / judgment-skip release), ADVISORY under 'focused'.
#
# As of Phase 4 (v2.9.0, Batch B / B1) the sole former member `removed_conditional` was FOLDED OUT of
# the floor set entirely (deliberated mcp_4b23ca82 + owner sign-off 2026-07-09): a bare removed
# conditional is now a plain non-floor advisory (still surfaced via `removed_generic_conditional`), and
# the sink-ADJACENT guard-removal case is covered by the (Phase-4-BROADENED) strong `removed_safety_control`
# signal (category `removed_guard`, now matching payment/secret/critical-state sinks on the removed `if`
# line, not just auth). This SUPERSEDES the 2026-07-01 gate-tightness decision that KEPT it here — the
# broadened strong signal gives more comprehensive sink-adjacent protection than the blunt soft-floor did,
# while a bare generic conditional removal no longer floors under thorough. SOFT_FLOOR is retained as an
# (empty) mechanism so the soft-floor code paths stay available for a future member.
SOFT_FLOOR = frozenset()

# Categories whose matched span IS a secret value — the Fix 2A transparency line reports the
# category + line number for these but NEVER echoes the matched token (it would print the secret
# to the agent's console). Every other category echoes the matched identifier/keyword, which is
# the whole point of transparency. Client-side only.
_SECRET_CATS = frozenset({"hardcoded_secret", "secret_material"})

# Valid gate_tightness levels + the default. 'focused' = fire only on major decisions
# (floor + high-confidence non-floor); 'thorough' = block any risky change (legacy behavior).
GATE_TIGHTNESS_VALUES = frozenset({"focused", "thorough"})
DEFAULT_GATE_TIGHTNESS = "focused"


def is_hard_floor(category) -> bool:
    """True if `category` is a HARD-floor class — a floor category excluding SOFT_FLOOR. A
    hard-floor hunk blocks the commit at EVERY tightness level (even suppressed to LOW — a
    floor near-miss must still block); a soft-floor hunk blocks only under 'thorough'."""
    return category in FLOOR_CATEGORIES and category not in SOFT_FLOOR


# Fix 4 P-a: on a TEST or DOCS path, the non-secret token-shape floor classes are DOWNGRADED to
# non-floor (a fixtures/spec/doc file can't be an auth/billing/migration/removed-guard regression
# — it doesn't ship as prod logic). Secrets are NOT exempt: a real secret VALUE fires via
# auto-trigger regardless of path (the value_filter already discounts fakes), so hardcoded_secret
# and secret_material stay floor even in a test file (F-003 stage ordering: secret before path).
#
# EXPLICIT ALLOWLIST (audit F-001): listed literally, NOT derived as `FLOOR_CATEGORIES - secrets`.
# A NEW floor category added to FLOOR_CATEGORIES is therefore NOT silently exempt on test/docs —
# it must be added here deliberately (the conservative direction: a new floor class keeps full
# enforcement until someone opts it into the exemption). Enforced ⊆ FLOOR_CATEGORIES and
# secret-free by test_pa_path_floor.
_FLOOR_EXEMPT_TEST_DOCS = frozenset({
    "auth_security", "billing",
    "removed_guard",
    # Phase 2 (v2.7.0): a joblib.load in a tests/ fixture load is not a prod regression — exempt
    # (owner ruling 2026-07-08). NOTE ci_secret_echo is deliberately NOT here: .github/workflows/*
    # classifies as test_or_docs, so exempting it would defeat the floor in the files it targets
    # (advisor 2026-07-08) — it follows the secrets precedent and stays a hard floor everywhere.
    "unsafe_deserialization_ml",
    # Phase 3 (v2.8.0): a masking-call removal in a test fixture is not a prod PII leak — exempt
    # (like auth/billing/unsafe_deser; NOT secret-adjacent). ci_pwn_request is deliberately NOT
    # here (same reason as ci_secret_echo: .github/workflows/* is test_or_docs — exempting defeats it).
    "pii_redaction_removed",
    # Phase 4 (v2.9.0, Batch B): migration_schema/migration_path REPLACED by migration_destructive —
    # a DROP TABLE in a test fixture is test scaffolding, not a prod regression, so it stays exempt like
    # the old migration floor did; removed_conditional dropped (no longer a floor). iac_exposure exempt
    # (B4, unanimous deliberation mcp_4b23ca82): a K8s/Terraform misconfig in a test FIXTURE is not
    # shipped infra — same logic as auth/billing. (A `0.0.0.0/0` in a README doesn't fire at all — the
    # prose exclusion already skips non-auto content signals on .md/.rst/.txt, so no exempt entry needed.)
    "migration_destructive",
    "iac_exposure",
    # Phase 5a (v2.10.0): a gets/strcpy/strcat in a test FIXTURE is scaffolding, not shipped C — exempt
    # like auth/billing/migration (the test_path suppressor also demotes it). The floor still fires
    # everywhere on a SOURCE path (path-unconditional); exemption is a path_class check, not a path_gate.
    "memory_safety",
})


def floor_exempt(category, path_class) -> bool:
    """True when `category` is a floor class EXEMPT from floor enforcement because the hunk lives
    in a test/docs path (Fix 4 P-a). Applied by BOTH gates and the server floor derivation so the
    exemption is coherent everywhere; secrets are never exempt. `path_class` is the conservative
    bucket from classify_path_class (None → not exempt)."""
    return path_class == PATH_CLASS_TEST_OR_DOCS and category in _FLOOR_EXEMPT_TEST_DOCS


def hunk_blocks_under_tightness(category, confidence, tightness, path_class=None) -> bool:
    """Does an uncovered risky hunk BLOCK the commit under the given `gate_tightness`?

    'thorough' (and any unrecognized value → fail safe to blocking): every risky hunk blocks —
        the legacy commit-gate behavior.
    'focused': blocks only a HARD-floor hunk (any confidence) OR a non-floor HIGH-confidence
        hunk; a non-floor LOW/borderline hunk and a soft-floor hunk are advisory (non-blocking).

    Confidence is compared to the HIGH constant, so a suppressed-to-LOW hard-floor near-miss
    still blocks via the is_hard_floor branch (not via confidence)."""
    # Fail-safe (audit F-001): a missing/unknown category or a confidence that isn't one of the
    # known labels BLOCKS — an unclassifiable hunk must never silently become a non-blocking
    # advisory under 'focused'. This also covers the old-server fallback where hunks may lack a
    # confidence field.
    if not category or confidence not in (LOW, HIGH):
        return True
    # Fix 4 P-a: a test/docs-path non-secret floor hunk is NOT a hard floor (advisory under
    # 'focused'); it still blocks under 'thorough' via the fall-through below.
    if is_hard_floor(category) and not floor_exempt(category, path_class):
        return True
    if tightness == "focused":
        return confidence == HIGH
    return True  # 'thorough' or any unknown value: block every risky hunk (safe direction)

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "risk_signals.json")

# Prose/doc files don't gate on CONTENT keywords. A design doc / README / changelog / notes
# file that merely *mentions* a risky area (the words "session", "authorize", "migration",
# ...) is a false positive — code lives in code files, not the narrative about them. A
# keyword classifier can't tell prose from code, so we skip the content-keyword signals on
# prose paths.
#
# TWO things still fire on prose (Phase 6 — auto-trigger-aware exclusion):
#  - `auto_trigger` signals (a real secret VALUE): a leaked key pasted into a README is
#    still a leaked key, independent of being in prose — closes the secret-in-.md recall hole.
#  - `match: "path"` signals (the file's ROLE, not its prose): `requirements.txt` is a
#    dependency change; a doc under `secrets/` is sensitive. These apply regardless of content.
# So `_classify_hunk` restricts a prose hunk's scan to path + auto-trigger signals rather
# than returning None outright. `.txt` is included here BECAUSE the dependency PATH signal
# still catches `requirements.txt` (so adding `.txt` loses no recall).
_PROSE_PATHS = re.compile(r"\.(md|markdown|mdx|rst|adoc|asciidoc|txt|html|htm)$", re.IGNORECASE)


def _compile_config(cfg):
    signals = []
    for s in cfg["signals"]:
        # auto_trigger is honored ONLY on trigger-class signals (audit F-001): a
        # borderline-class signal can never reach the high band, by construction. If a
        # non-trigger signal is misconfigured with auto_trigger:true we strip it AND warn
        # (audit F-E) so the operator gets feedback instead of a silent demotion.
        auto = bool(s.get("auto_trigger", False))
        if auto and s["class"] != _TRIGGER_CLASS:
            warnings.warn("risk_classifier: auto_trigger ignored on non-trigger signal %r "
                          "(class=%r)" % (s.get("name"), s.get("class")))
            auto = False
        # --- Mechanism M1 (co-occurrence). A signal may carry AT MOST ONE of:
        #   all_of        : [[pat,...], [pat,...]] — AND-of-pattern-groups (OR within a group);
        #                   the signal fires only when EVERY group matches (partial => 0 weight).
        #                   Two-stage: if the diff satisfies some groups, the rest are checked
        #                   against the whole file via an injected file_content_fetcher.
        #   and_not_added : true (only on match:"removed") — fires when a removed line matches
        #                   `patterns` AND no added line ANYWHERE in the same file re-adds it.
        # These are resolved by the file-aware _resolve_m1_signals pass, NOT the per-hunk loop,
        # so legacy per-hunk scoring is byte-identical. Loader assertions below hard-fail a
        # misconfig (caught by the module-level fail-open wrapper -> loud warning + empty config).
        all_of_raw = s.get("all_of")
        and_not_added = bool(s.get("and_not_added", False))
        if all_of_raw is not None:
            if s.get("skeleton_match") or s.get("value_filter_patterns"):
                raise ValueError(
                    "risk_classifier: signal %r: all_of is incompatible with skeleton_match / "
                    "value_filter_patterns (index-based modifiers don't map onto all_of groups)"
                    % s.get("name"))
            if s.get("patterns"):
                raise ValueError(
                    "risk_classifier: signal %r: all_of replaces patterns[]; do not set both"
                    % s.get("name"))
            if not all_of_raw or not all(g for g in all_of_raw):
                raise ValueError(
                    "risk_classifier: signal %r: all_of must be a non-empty list of non-empty "
                    "pattern groups" % s.get("name"))
        if and_not_added:
            if all_of_raw is not None:
                raise ValueError("risk_classifier: signal %r: and_not_added and all_of are "
                                 "mutually exclusive" % s.get("name"))
            if s.get("match") != "removed":
                raise ValueError("risk_classifier: signal %r: and_not_added is only valid on a "
                                 "match:\"removed\" signal" % s.get("name"))
        # --- Mechanism M2 (path-gate). A content signal may carry `path_gate: [regex, ...]` — an
        # ALLOW-LIST matched against the hunk's file PATH (via _path_match, forward-slash-normalized).
        # The signal's patterns then score ONLY on a hunk whose path matches >=1 gate; absent =>
        # path-unconditional (byte-identical to today). M2's job is FP-management for ADVISORIES only.
        # THREE loader assertions (deliberated mcp_98973ec2 + owner sign-off 2026-07-09):
        #   (a) path_gate is incoherent on a match:"path" signal (it already matches on the path);
        #   (b) *** THE HARD INVARIANT: a FLOOR-category signal may NEVER carry path_gate ***. Path-
        #       gating a floor is a silent bypass — the same `strcpy(` in a file the gate doesn't
        #       recognize (.inc/.go.tmpl/extensionless) wouldn't floor. Self-precise patterns floor
        #       UNCONDITIONALLY; only advisories get path-gated. If a floor is too noisy to fire
        #       unconditionally, the fix is DEMOTE-to-advisory (a FLOOR_CATEGORIES edit), not path-gate.
        #   (c) path_gate is scoped OUT of the M1 primitives (all_of/and_not_added) for v1 — the
        #       interaction isn't needed yet (the M1 pass is file-aware; revisit when a signal requires it).
        # NOTE on authoring: a path_gate regex MUST be END-OF-STRING anchored (`\.c$`, not `\.c` which
        # also matches `.css`/`.c.orig`) — enforced by tests (multi-dot / Windows / extensionless), not
        # the loader (a generic "is anchored" check isn't reliable).
        path_gate_raw = s.get("path_gate")
        if path_gate_raw is not None:
            if s.get("match", "added") == "path":
                raise ValueError("risk_classifier: signal %r: path_gate is incoherent on a "
                                 "match:\"path\" signal (it already matches on the path)"
                                 % s.get("name"))
            if s["category"] in FLOOR_CATEGORIES:
                raise ValueError(
                    "risk_classifier: signal %r: path_gate is FORBIDDEN on a FLOOR category (%r) — "
                    "floors must be path-unconditional (a path-gated floor is a silent bypass). "
                    "Demote to a non-floor category if it must be path-scoped." % (s.get("name"), s["category"]))
            if all_of_raw is not None or and_not_added:
                raise ValueError("risk_classifier: signal %r: path_gate is not supported with the M1 "
                                 "primitives (all_of / and_not_added) in v1" % s.get("name"))
            if not path_gate_raw or not all(isinstance(p, str) for p in path_gate_raw):
                raise ValueError("risk_classifier: signal %r: path_gate must be a non-empty list of "
                                 "regex strings" % s.get("name"))
        signals.append({
            "name": s["name"],
            "cls": s["class"],
            "category": s["category"],
            "weight": int(s["weight"]),
            "auto": auto,
            "match": s.get("match", "added"),
            "patterns": [re.compile(p) for p in s.get("patterns", [])],
            # M1 co-occurrence groups (each group = list of compiled patterns), or [] for a
            # legacy signal. `and_not_added` is the removed-and-not-re-added flag. `is_m1` marks
            # a signal handled by the file-aware pass and SKIPPED by the legacy per-hunk loop.
            "all_of": [[re.compile(p) for p in group] for group in (all_of_raw or [])],
            "and_not_added": and_not_added,
            "is_m1": bool(all_of_raw is not None or and_not_added),
            # M2 path-gate: compiled allow-list of path regexes, or [] for a path-unconditional
            # signal. A non-empty list arms the signal ONLY on a matching hunk path (checked via
            # _path_match in _classify_hunk before the signal's patterns run).
            "path_gate": [re.compile(p) for p in (path_gate_raw or [])],
            # Pattern indices that match against the comment/string-stripped "code
            # skeleton" of a line instead of the raw line (design §4 precision pass):
            # a bare keyword inside prose / JSX text / a string literal / a comment is
            # NOT code and must not fire, while the SAME keyword as a real identifier or
            # call must. Applied ONLY to the listed indices (the bare-keyword unions);
            # every other pattern — and every signal without this key — stays on raw
            # lines. Secret/SQL/import-string patterns deliberately stay raw (they look
            # INSIDE strings), so they are never listed here.
            "skeleton_match": set(int(i) for i in s.get("skeleton_match", [])),
            # Pattern indices that, on a match, additionally require a real-secret-looking
            # quoted value on the line (literature #3 — the generic `key="value"` secret
            # pattern over-fires on $VAR interpolations and placeholders). Recall-safe.
            "value_filter_patterns": set(int(i) for i in s.get("value_filter_patterns", [])),
        })
    suppressors = []
    for s in cfg["suppressors"]:
        suppressors.append({
            "name": s["name"],
            "weight": int(s["weight"]),
            "patterns": [re.compile(p) for p in s["patterns"]],
        })
    th = cfg["thresholds"]
    return {
        "signals": signals,
        "suppressors": suppressors,
        # True iff any signal opts a pattern into skeleton matching — lets the
        # hot path skip the (regex-heavy) skeleton build entirely when no signal
        # needs it (and keeps the fail-open empty config zero-cost).
        "has_skeleton": any(sig["skeleton_match"] for sig in signals),
        # True iff any signal is an M1 co-occurrence signal — lets classify_diff skip the
        # file-aware M1 pass entirely when none exist (zero cost; regression stays byte-identical).
        "has_m1": any(sig["is_m1"] for sig in signals),
        # True iff any signal carries an M2 path_gate — lets _classify_hunk skip the per-signal
        # path-gate check entirely when none exist (zero cost; regression stays byte-identical).
        "has_path_gate": any(sig["path_gate"] for sig in signals),
        "trigger": int(th["trigger"]),
        "borderline_low": int(th["borderline_low"]),
        "version": cfg.get("version", "2"),
        "large_hunk_added_lines": int(cfg.get("large_hunk_added_lines", 0)),
        "large_hunk_weight": int(cfg.get("large_hunk_weight", 0)),
        # Floor-bounded threshold-override ceiling (F-011): the highest value a user may
        # raise the trigger threshold to. Sits just below the must-fire trigger weights
        # (auth/secrets/migration/removed-guard = 30) so those always fire; the gate can't
        # be silently disabled via the threshold. gate_lib clamps the user value to this.
        "trigger_threshold_ceiling": int(cfg.get("trigger_threshold_ceiling", int(th["trigger"]))),
    }


def _load(path=_CONFIG_PATH):
    with open(path, "r", encoding="utf-8") as fh:
        return _compile_config(json.load(fh))


# Fail-open config (audit F-004): a malformed / missing risk_signals.json must NEVER
# brick the blocking PreToolUse hook. On any load error we degrade to an empty signal
# set — classify_diff then returns not-risky for everything, so the gate fails open
# (consistent with gate_lib's whole posture). Loud warning so it's not silent.
_EMPTY_CFG = {"signals": [], "suppressors": [], "trigger": 25, "borderline_low": 5, "version": "0-failopen"}

try:
    _CFG = _load()
except Exception as _exc:  # noqa: BLE001 — deliberately broad; this is the fail-open guard
    warnings.warn("risk_classifier: config load failed (%s); failing open (no signals)" % _exc)
    _CFG = dict(_EMPTY_CFG)


# ---------------------------------------------------------------------------
# Hunk hashing — IDENTICAL to v1 (receipts depend on byte-stability). Do not change.
# ---------------------------------------------------------------------------

def clamp_threshold(user_value):
    """Floor-bound a user-supplied trigger threshold (F-011, §4.3).

    Returns a threshold clamped to [borderline_low+1, trigger_threshold_ceiling]. The
    ceiling sits just below the must-fire trigger weights so auth/secrets/migration/
    removed-guard always fire and the gate can't be silently disabled by raising the
    threshold. Returns None (→ config default) for a missing/unparseable value.
    """
    if user_value is None or user_value == "":
        return None
    try:
        v = int(user_value)
    except (TypeError, ValueError):
        return None
    lo = _CFG["borderline_low"] + 1
    hi = _CFG["trigger_threshold_ceiling"]
    if hi < lo:
        hi = lo
    return max(lo, min(v, hi))


def hunk_content_hash(lines):
    """Stable, whitespace-tolerant hash of a hunk's content.

    Strip each line's trailing whitespace, drop blank-only lines, join with '\\n',
    sha256, first 16 hex chars. Shared by the classifier and the coverage check so both
    sides agree. (v1-compatible: callers pass the *added* lines; classify_diff falls back
    to the *removed* lines for pure-deletion hunks so they get a distinct hash.)
    """
    norm = [ln.rstrip() for ln in lines]
    norm = [ln for ln in norm if ln.strip() != ""]
    blob = "\n".join(norm)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Normalized hash (Phase 9) — a SECOND, cosmetic-drift-tolerant hash per hunk.
#
# Why: coverage today binds to hunk_content_hash, which is sensitive to every byte
# (incl. unicode) — so a gate_diff that drifts from the staged diff by a cosmetic
# character (a smart-quote, an em-dash) misses coverage and a GENUINELY reviewed
# change stays blocked. The normalized hash is a FALLBACK match key: identical strict
# hash → cover as today; else identical NORMALIZED hash → cosmetically the same → bind
# coverage to the fire's STORED strict hash (Phase 9 receipt path).
#
# SECURITY MODEL — the only thing that matters here is NO FALSE MATCH (two
# SEMANTICALLY DIFFERENT hunks must never share a normalized hash; that would let a
# review of one change cover a different change). Over-conservatism is free: if the
# normalized hash differs when it cosmetically "shouldn't", it just falls back to
# strict matching (and ultimately the human-override backstop) — never a false match.
# Therefore v1 does the MINIMAL safe normalization:
#   - NFC unicode normalization (canonical-equivalent forms collapse).
#   - Fold cosmetic dashes/smart-quotes to ASCII — ONLY OUTSIDE string/template
#     literals. String/template CONTENTS are preserved VERBATIM, so a new secret
#     value / token / SQL literal ALWAYS changes the hash.
#   - Leading + internal whitespace preserved (only trailing stripped, like strict),
#     so indentation-significant code can't collapse to a false match.
#   - Comment text is KEPT (folded like code, never stripped). Comment-STRIPPING was
#     evaluated and REJECTED for v1 (audit mcp_4204070b): it created deterministic
#     false-coverage holes — bare `#` is a C/C++/JS preprocessor directive or private
#     field, not a comment, and pragma comments (`# type: ignore`, `# noqa`,
#     `//go:build`, `// nolint`) are semantically load-bearing; stripping any of these
#     collides distinct hunks. A future increment may add filetype-specific,
#     directive-preserving comment handling if telemetry shows comment drift is a real
#     pain — until then, comment drift just falls to the backstop.
#   - Bump NORM_VERSION on any change here (the hashed blob carries it, so stale
#     receipts mismatch diagnosably). Vendored byte-identical to plugin/hooks/.
# ---------------------------------------------------------------------------
NORM_VERSION = "1"

# Cosmetic unicode an LLM courier routinely mangles, folded ONLY outside string/
# template literals (inside a literal these are CONTENT and preserved verbatim).
_NORM_FOLD = str.maketrans({
    "—": "-", "–": "-", "‒": "-", "−": "-",   # em/en/figure/minus dash → hyphen
    "‘": "'", "’": "'", "‛": "'",                   # single smart quotes → '
    "“": '"', "”": '"', "‟": '"',                   # double smart quotes → "
})


def hunk_filetype(path):
    """The lowercase extension of `path` (no dot), or '' — stored on the fire as
    metadata for a possible future filetype-aware normalization pass. Not used by the
    v1 hash (which is filetype-agnostic)."""
    if not path:
        return ""
    base = path.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def _fold_span(span):
    """NFC + cosmetic-unicode-fold a CODE or COMMENT span — but ONLY when it contains no
    stray string delimiter (`"` `'` or backtick). A stray delimiter means we may be inside a
    MULTI-LINE string literal whose opener was on an earlier line (the per-line tokenizer can't
    see it — audit mcp_01b89c12 F-001): folding such a span could alter literal CONTENT, a
    false-match. In that case we preserve the span byte-for-byte. Conservative: the worst case
    is less drift-tolerance, never a false match. (NFC is applied per-span, never to a matched
    string token, so byte-distinct-but-NFC-equivalent literals stay distinct — F-002.)"""
    if '"' in span or "'" in span or "`" in span:
        return span
    return unicodedata.normalize("NFC", span).translate(_NORM_FOLD)


def _normalize_line(line):
    """Normalize ONE line for the cosmetic-tolerant hash. Tokenize the RAW line; preserve
    string/template literal tokens VERBATIM (content incl. a secret value, no NFC, no fold);
    everywhere else — code AND comment text — NFC + fold cosmetic unicode via _fold_span (which
    self-guards against multi-line-literal fragments). Nothing is stripped, so a directive /
    `#include` / pragma survives and can't collide with a different hunk. Only trailing
    whitespace is stripped (like the strict hash); leading + internal whitespace is preserved."""
    out = []
    pos = 0
    for m in _SK_TOKEN.finditer(line):
        out.append(_fold_span(line[pos:m.start()]))             # code gap before token
        if m.group("str") is not None or m.group("tmpl") is not None:
            out.append(line[m.start():m.end()])                 # literal — verbatim (raw bytes)
        else:
            out.append(_fold_span(line[m.start():m.end()]))     # comment — NFC+fold (delimiter-guarded)
        pos = m.end()
    out.append(_fold_span(line[pos:]))
    return "".join(out).rstrip()


def hunk_normalized_hash(lines, path=None, category=None):
    """Cosmetic-drift-tolerant hash of a hunk (Phase 9). NFC + cosmetic-unicode folding
    outside string/template literals; literal contents and all comment/code text are
    preserved (nothing stripped). Blob prefixed with NORM_VERSION so a bump invalidates
    stale records diagnosably. 128-bit (32 hex) for collision headroom in a long-lived
    store. `path`/`category` are accepted for signature stability + a future
    filetype-aware pass; v1 ignores them."""
    norm = [_normalize_line(ln) for ln in lines]
    norm = [ln for ln in norm if ln.strip() != ""]
    blob = "norm:v%s\n%s" % (NORM_VERSION, "\n".join(norm))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Diff parsing — yields (path, added_lines, removed_lines) per hunk.
# Hunk boundaries match v1 (one run under each @@), so added-hunk hashes are unchanged.
# ---------------------------------------------------------------------------

# Phase 9.1 — per-hunk STRUCTURAL position, the coarse-structural coverage fallback's match key.
# Parsed from the unified-diff hunk header `@@ -old_start,old_count +new_start,new_count @@`.
# Counts default to 1 when omitted (`@@ -1 +1 @@`). These integers survive byte-level corruption
# (mojibake / re-encoding) that breaks the content + normalized hashes, so when a reviewed diff's
# content hashes miss the fire, coverage can still bind on file-path + per-file hunk count + these
# ranges + net delta (deliberate mcp_009859f1, Option E). A combined/merge header (`@@@ ... @@@`)
# or any non-standard header → None. A consumer MUST treat structural=None as "no structural
# fallback available" (NEVER a zero-line hunk) — content lines that accumulate before the first
# valid @@ of a file (malformed input) also carry None.
# PREFIX-only ON PURPOSE (audit F-004): no `$` anchor, because a real header routinely carries a
# context label after the closing `@@` (e.g. `@@ -1,3 +1,4 @@ def foo():`) — do NOT "fix" it by
# anchoring. Counts use `is not None` (not `or 1`) so an explicit 0 (`@@ -0,0 ...`, a new file) is
# preserved, not corrupted to 1.
# VERSION NOTE (audit F-003): `structural` is unversioned today (inert — no consumer reads it). When
# the Phase-9.1 resolver starts binding on it, version it like NORM_VERSION so a parser change can't
# silently mis-bind a fire stored under the old shape.
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _parse_hunk_header(line):
    m = _HUNK_HEADER_RE.match(line)
    if not m:
        return None
    old_count = int(m.group(2)) if m.group(2) is not None else 1
    new_count = int(m.group(4)) if m.group(4) is not None else 1
    return {
        "old_start": int(m.group(1)), "old_count": old_count,
        "new_start": int(m.group(3)), "new_count": new_count,
        "net_delta": new_count - old_count,   # convenience; == new_count - old_count (audit F-005)
    }


def _iter_file_hunks(diff_text):
    """Yield (path, added_lines, removed_lines, structural) per hunk. `structural` is the parsed
    @@ position dict (or None for a header that doesn't parse / pre-Phase-9.1 callers ignore it)."""
    path = None
    old_path = None
    added = []
    removed = []
    in_hunk = False
    ranges = None
    results = []

    def flush():
        if path and (added or removed):
            results.append((path, list(added), list(removed), ranges))

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            flush()
            added, removed = [], []
            path = None
            old_path = None
            in_hunk = False
            ranges = None
            continue
        # File headers `--- `/`+++ ` only appear BEFORE the first @@ of a file, so we
        # match them only when not in a hunk. This is what lets an in-hunk content line
        # that itself starts with `---`/`+++` (a PEM `-----BEGIN ...`, a YAML `---`) be
        # collected as real content instead of mistaken for a header — and a removed
        # whole-file deletion still binds to the a-side path (audit F-003).
        if not in_hunk and line.startswith("--- "):
            p = line[4:].strip()
            if p.startswith("a/"):
                p = p[2:]
            old_path = None if p == "/dev/null" else p
            continue
        if not in_hunk and line.startswith("+++ "):
            # The flush here is redundant after a `diff --git` (which already flushed),
            # but load-bearing for plain `diff -u` input that has no `diff --git`
            # separators — it's the only inter-file boundary in that format.
            flush()
            added, removed = [], []
            ranges = None
            p = line[4:].strip()
            if p.startswith("b/"):
                p = p[2:]
            new_path = None if p == "/dev/null" else p
            path = new_path or old_path     # deleted file -> fall back to the a-side path
            continue
        if line.startswith("@@"):
            flush()
            added, removed = [], []
            ranges = _parse_hunk_header(line)
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    flush()
    return results


# ---------------------------------------------------------------------------
# Gate self-mutation (§6.1, audit F-005) + the synthesized self-coverage hash.
# A change touching the gate's OWN config/hooks can disable the gate from inside
# (raise the threshold, empty the signal sets, unhook it) — privilege escalation,
# so the gates force a review even when the content classifier finds nothing.
# These live in this vendored module (byte-identical client + server) so the
# client gate and the server receipt writer agree on BOTH detection and the
# self-coverage hash. Moved from gate_lib.py 2026-06-17 (Option 4 deliberation).
# ---------------------------------------------------------------------------

# The gate's release authority, end to end: the client classifier + decision lib + hook
# DRIVERS (weakening a driver disables the gate), AND the server-side files that decide
# coverage / what counts as a releasing receipt (receipt_writer / receipt_coverage /
# gate_skip). A change to any of these can disable the gate from inside, so they're all
# gate-self (Option 4 audit F-003, 2026-06-17). NOTE the broad multi-route file
# mcp_user_routes.py is intentionally NOT here (it carries dozens of unrelated routes);
# its /receipts/check endpoint is protected by PR review + the manual Replit deploy gate.
_GATE_SELF_PATHS = re.compile(
    # Basename-anchored, so the gate files match wherever they live — plugin/hooks
    # (the generated bundle), plugin-core/gate_core (the source of truth), or a
    # marketplace clone. Cross-platform additions (2026-07-29): the host adapter
    # package (host/*.py can rewrite every deny), every platform's manifest dir,
    # platforms.yaml + build_bundles.py (the generator composes what ships), and
    # the launchers that own the fail-open exit-code contract.
    r"(^|/)(risk_signals\.json|risk_classifier\.py|gate_lib\.py|hooks\.json"
    r"|audit_gate\.py|deliberate_gate\.py"
    r"|receipt_writer\.py|receipt_coverage\.py|gate_skip\.py"
    r"|post_commit_backstop\.py|stash_precommit_head\.py|gate_selfcheck\.py"
    r"|commit-detected\.sh|run_gate\.(sh|cmd)"
    r"|platforms\.yaml|build_bundles\.py"
    r"|gate_core/host/|hooks/host/"
    r"|\.claude-plugin/|\.codex-plugin/|\.cursor-plugin/|gemini-extension\.json"
    r"|\.git/hooks/)",
    re.IGNORECASE,
)


def is_gate_self_mutation(path):
    """True if `path` would modify the gate's own config/hooks (a bypass vector)."""
    if not path:
        return False
    return bool(_GATE_SELF_PATHS.search(path.replace("\\", "/")))


def _diff_paths(diff_text):
    """Yield every file path the diff references — BOTH the a/ (old) and b/ (new) sides of each
    `diff --git`, the `+++ b/` / `--- a/` headers, and `rename from` / `rename to` — so a
    path-membership check (gate-self, gate-core) catches a DELETION (its `+++` is /dev/null, so
    the a/ side is the real path) and a RENAME that moves a protected file OUT of its enforced
    location (a security-relevant act — audit mcp_2df9d33b F-001/F-002). /dev/null is skipped."""
    for line in (diff_text or "").splitlines():
        if line.startswith("diff --git "):
            rest = line[len("diff --git "):]
            if " b/" in rest:
                a_side, b_side = rest.split(" b/", 1)
                yield a_side[2:].strip() if a_side.startswith("a/") else a_side.strip()
                yield b_side.strip()
        elif line.startswith("+++ b/"):
            yield line[6:].strip()
        elif line.startswith("--- a/"):
            yield line[6:].strip()
        elif line.startswith("rename from ") or line.startswith("rename to "):
            yield line.split(" ", 2)[-1].strip()


def diff_touches_gate_self(diff_text):
    """True if any file the diff adds, removes, or renames is the gate's own config/hooks."""
    return any(is_gate_self_mutation(p) for p in _diff_paths(diff_text) if p and p != "/dev/null")


# Gate-CORE (Phase 9 increment 5): the subset of gate-self files where even a comment/whitespace
# edit must ALWAYS get a real review — the classifier itself, the decision logic, the hook
# entrypoints + config, the plugin manifest, and the real git hooks. A weakening here disables
# enforcement, and a comment in the signal DATA (risk_signals.json) can be load-bearing. The
# OTHER gate-self files (server receipt logic: receipt_writer / receipt_coverage / gate_skip) may
# take the trivial-edit skip. Conservative: gate-core is the default; only an explicitly inert
# edit to a non-core gate-self file skips.
_GATE_CORE_PATHS = re.compile(
    # Cross-platform additions (2026-07-29): the host adapters + launchers are
    # gate-CORE — an adapter emits the deny itself (a weakened emit_deny IS a
    # disabled gate) and the launchers own the fail-open exit-code contract; the
    # generator + platforms.yaml decide what ships in every bundle; the other
    # platforms' manifest dirs are the peer of .claude-plugin/.
    r"(^|/)(risk_signals\.json|risk_classifier\.py|gate_lib\.py|hooks\.json"
    r"|audit_gate\.py|deliberate_gate\.py|run_gate\.(sh|cmd)"
    r"|platforms\.yaml|build_bundles\.py"
    r"|gate_core/host/|hooks/host/"
    r"|\.claude-plugin/|\.codex-plugin/|\.cursor-plugin/|gemini-extension\.json"
    r"|\.git/hooks/)",
    re.IGNORECASE,
)


def is_gate_core_mutation(path):
    """True if `path` is a gate-CORE file (never eligible for the trivial-edit skip)."""
    if not path:
        return False
    return bool(_GATE_CORE_PATHS.search(path.replace("\\", "/")))


def diff_touches_gate_core(diff_text):
    """True if any file the diff adds, removes, or renames is gate-core (checks BOTH a/ and b/
    sides + rename headers via _diff_paths — a gate-core file moved/deleted must still review)."""
    return any(is_gate_core_mutation(p) for p in _diff_paths(diff_text) if p and p != "/dev/null")


def diff_is_inert(diff_text):
    """True iff EVERY added AND removed line is structurally INERT — a comment, a blank, or
    whitespace-only (its code skeleton is empty). A change that adds or removes any CODE — incl.
    a string-literal assignment (the skeleton keeps its delimiters, so a changed secret/version
    value is NOT inert) — returns False. An empty diff (no hunks) is NOT inert (nothing to skip).
    Used by the gate-self trivial-edit skip (increment 5): a comment/whitespace-only edit to a
    NON-gate-core gate-self file releases without a full review."""
    saw_hunk = False
    for path, added, removed, _r in _iter_file_hunks(diff_text or ""):
        saw_hunk = True
        is_jsx = bool(path) and path.lower().endswith((".jsx", ".tsx"))
        for line in _skeletonize(added, is_jsx) + _skeletonize(removed, is_jsx):
            if line.strip():
                return False
    return saw_hunk


# Namespace so a synthesized gate-self hash can never collide with — or be satisfied
# by — a normal hunk_content_hash (which is bare hex). The coverage check, the SKIP
# filter, and the deliberate-only-contributes-gate-self rule all key off the NAMESPACE
# (version-agnostic). The construction PREFIX is versioned (audit F-005): bump the
# version if the hashed inputs ever change, so stale receipts mismatch diagnosably
# (and a future migration can recognize both) rather than silently failing coverage.
GATE_SELF_HASH_NAMESPACE = "gself:"
GATE_SELF_HASH_PREFIX = GATE_SELF_HASH_NAMESPACE + "v1:"


def gate_self_coverage_hash(diff_text):
    """Deterministic self-coverage hash for a gate-self change whose CONTENT
    classifies to ZERO risky hunks (a comment, a deleted signal pattern, ...).

    The gate can't bind such a change to a normal hunk hash, so it binds to THIS
    hash; a real audit/deliberate PASS of the same diff writes the same hash into
    the receipt's covered_hunks (receipt_writer), and ONLY that releases the gate —
    never recent_pass, never a SKIP (Option 4, 2026-06-17 deliberation).

    Hashes per-file ADDED/REMOVED lines (sigil-tagged so add != remove; same
    rstrip / drop-blank normalization as hunk_content_hash), restricted to
    gate-self files, stable-sorted by normalized path. NOT the whole diff: context
    lines, `index <sha>` headers and CRLF all differ between the audit-time and
    commit-time diffs, but the per-file +/- content does not. Prefixed with
    GATE_SELF_HASH_PREFIX to namespace it away from hunk_content_hash.

    Byte-identical client (plugin/hooks/) and server (mcp_server/) — both import it
    from this vendored module (sync_risk_classifier.py --check enforces it).

    Limitation (re-audit F-003): a pure rename / mode-only change to a gate-self file has
    no +/- content, so it yields zero segments and a content-insensitive constant hash.
    Such changes are rare and still require a real PASS to release (no bypass), but two of
    them would collide on coverage — accepted as a known edge, not a security gap.
    """
    segments = {}
    for path, added, removed, _r in _iter_file_hunks(diff_text):
        norm_path = (path or "").replace("\\", "/")
        if not _GATE_SELF_PATHS.search(norm_path):
            continue
        tagged = (["+" + ln.rstrip() for ln in added if ln.strip() != ""]
                  + ["-" + ln.rstrip() for ln in removed if ln.strip() != ""])
        if tagged:
            segments.setdefault(norm_path, []).extend(tagged)
    h = hashlib.sha256()
    for p in sorted(segments):
        h.update(("\x00path:" + p + "\x00").encode("utf-8"))
        h.update(("\n".join(segments[p]) + "\n").encode("utf-8"))
    return GATE_SELF_HASH_PREFIX + h.hexdigest()[:40]


# ---------------------------------------------------------------------------
# Path-class tagging (gate-skip-tightening §4.B, deliberated 2026-06-26). A coarse,
# CONSERVATIVE classification of a hunk's file into the buckets the path-shaped skip
# reasons verify against: `test_or_docs` and (`generated` | `vendored`). Everything
# else — and anything ambiguous — is `source` (UNVERIFIABLE), so a path-claim over it is
# REJECTED, never falsely accepted (fail toward running the review). Computed CLIENT-side
# at gate-fire time (the server never sees paths — privacy invariant) and sent as a TAG
# per hunk; the server stores it on the fire and verifies the skip claim against it.
#
# `gate_self` is deliberately NOT a path_class — it's the orthogonal is_gate_self_mutation()
# predicate (a gate-self change satisfies NEITHER path reason and can never be skipped).
# `generated` and `vendored` are separate tags, composed at verify time.
PATH_CLASS_TEST_OR_DOCS = "test_or_docs"
PATH_CLASS_GENERATED = "generated"
PATH_CLASS_VENDORED = "vendored"
PATH_CLASS_SOURCE = "source"

# `generated` is content-marker PRIMARY (a path alone is too easily spoofed): the markers
# real generators emit near the top of a file. Checked against the first ~20 added lines.
_GENERATED_MARKERS = (
    "do not edit", "do not modify", "@generated", "code generated by",
    "this file is auto-generated", "this file is generated", "autogenerated by",
    "auto-generated file", "generated by protoc", "generated by the protocol buffer",
)
# Unambiguous generated FILE patterns (compiler/codegen outputs) — safe to tag by name.
_GENERATED_PATH_RE = re.compile(
    r"(\.pb\.(go|py|cc|h)|_pb2(_grpc)?\.py|\.generated\.(ts|js|jsx|tsx|cs|go|java)|\.g\.(cs|dart)|"
    r"\.freezed\.dart|\.designer\.cs)$",
    re.IGNORECASE,
)
_VENDORED_PREFIX_RE = re.compile(
    r"(^|/)(vendor|vendors|node_modules|third_party|third-party|bower_components|\.yarn)/",
    re.IGNORECASE,
)
_VENDORED_LOCKFILES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json",
    "pipfile.lock", "poetry.lock", "cargo.lock", "go.sum", "composer.lock",
    "gemfile.lock", "packages.lock.json",
})
_TEST_PREFIX_RE = re.compile(r"(^|/)(tests?|spec|specs|__tests__|e2e)/", re.IGNORECASE)
_TEST_FILE_RE = re.compile(
    r"((^|/)test_[^/]+\.py|_test\.(py|go)|\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs)|"
    r"(^|/)conftest\.py)$",
    re.IGNORECASE,
)
_DOCS_PREFIX_RE = re.compile(r"(^|/)(docs?|documentation|\.github)/", re.IGNORECASE)


def classify_path_class(path, content):
    """Return the conservative PATH_CLASS_* bucket for a hunk in `path` whose ADDED text is
    `content` (a string, or None/"" for a pure-deletion hunk). Ambiguous / unknown → SOURCE.

    Precedence (a file matching several rules takes the strictest evidence): generated →
    vendored → test_or_docs → source. Never raises — any error → SOURCE."""
    try:
        if not path:
            return PATH_CLASS_SOURCE
        # A pure-deletion hunk (no ADDED content) carries no evidence to classify — path-only
        # classification is deliberately distrusted, so a deletion is UNVERIFIABLE → SOURCE
        # unconditionally (audit F-002 + deliberated design). Deleting risky code is exactly
        # what should still be reviewed; this is the safe (false-reject) direction.
        if not content:
            return PATH_CLASS_SOURCE
        norm = path.replace("\\", "/")
        base = norm.rsplit("/", 1)[-1]
        low = norm.lower()

        # --- generated: content-marker primary. Scan ONLY the first ~5 added lines — real
        # generators stamp the marker at the very top; scanning deeper would false-accept a
        # source file that merely QUOTES the marker string in a comment (audit F-001). Then a
        # few unambiguous codegen file patterns.
        header = "\n".join(content.splitlines()[:5]).lower()
        if any(m in header for m in _GENERATED_MARKERS):
            return PATH_CLASS_GENERATED
        if _GENERATED_PATH_RE.search(norm):
            return PATH_CLASS_GENERATED

        # --- vendored: well-defined dependency dirs + lockfiles.
        if _VENDORED_PREFIX_RE.search(low) or base.lower() in _VENDORED_LOCKFILES:
            return PATH_CLASS_VENDORED

        # --- test_or_docs: test dirs/file patterns, or a docs dir. A `.md`/`.rst` OUTSIDE a
        # docs dir is NOT docs-only (e.g. a top-level README beside source) → SOURCE.
        if _TEST_PREFIX_RE.search(low) or _TEST_FILE_RE.search(norm):
            return PATH_CLASS_TEST_OR_DOCS
        if _DOCS_PREFIX_RE.search(low):
            return PATH_CLASS_TEST_OR_DOCS

        return PATH_CLASS_SOURCE
    except Exception:
        return PATH_CLASS_SOURCE


# ---------------------------------------------------------------------------
# Secret-value false-positive filter (literature #3 — the recall-SAFE subset of
# the gitleaks / detect-secrets model). Applies ONLY to the hardcoded_secret
# signal's GENERIC `key = "value"` pattern (the one that over-fires); the specific
# key-format patterns (sk_live_, ghp_, AKIA, PEM) are precise and never filtered.
#
# We deliberately ship only filters that CANNOT drop a real secret (audit
# mcp_8ddbcfae found three recall holes in an earlier draft — all fixed here):
#  - sigil interpolation (`${VAR}`, `$(...)`, `{{ }}`, `%(...)s`, `#{}`, `<% %>`) or a
#    whole-value format field (`"{settings.KEY}"`) -> a REFERENCE, not the secret. A
#    real credential never contains a sigil; a bare `{word}` is only treated as a
#    reference when it is the ENTIRE value (so `"Tr0ub4dor{Blue}99"` still fires).
#  - placeholders matched ONLY whole-value or as a START-of-value `word + delimiter`
#    prefix (`your-...`, `changeme_...`) — NEVER a bare substring, since a real token
#    can contain `null`/`your` via a `-`/`/`/`.` delimiter (`"v1.null.xK9..."` is real).
#  - all-filler / single-char-repeated values.
# No entropy floor: a weak real password (`"111122223333"`) is low-entropy but real,
# so an entropy gate would drop it (audit F-001). The repeated-char anchor below
# covers degenerate filler without that recall cost.
# ---------------------------------------------------------------------------

# Interpolation: a sigil anywhere, OR a value that is ENTIRELY a `{...}` format field.
_INTERP_RE = re.compile(r"\$\{|\$\(|\{\{|\}\}|%\([^)]*\)|%[sd]\b|#\{|<%|^\{[^}]{1,60}\}$")
# Backslash-aware quote scan (audit F-004): an escaped quote inside the value must not
# truncate it (`"ab\"cd..."`). Matches `\\.` (any escaped char) or a non-quote char.
_QUOTED_RE = re.compile(r"""(['"])((?:\\.|(?!\1).)*)\1""")
# Placeholder shapes — all anchored (audit F-002): whole-value filler / repeated char /
# bare placeholder word, OR a value that STARTS with a placeholder word + a delimiter.
_PLACEHOLDER_RE = re.compile(
    r"^[xX*.\-_0\s]+$"                                       # all filler chars
    r"|^(.)\1{6,}$"                                          # one char repeated
    r"|^(none|null|undefined|changeme|placeholder|redacted|tbd|todo)$"   # whole-value word
    r"|^(your|my|the|example|sample|dummy|fake|test|placeholder|change[_-]?me|"
    r"replace[_-]?me|insert|enter|put|set)[_-]",             # placeholder-word + delimiter
    re.IGNORECASE,
)

# Don't scan pathological minified lines (audit F-005): a quote-dense line is O(n^2) for
# the backtracking scan. Conservatively treat an over-long line as possibly-real (fires).
# Raised 2000 -> 10000 (single-call redesign, 2026-07-02): the prior cap made long-but-legit
# lines (minified vendor code, long data literals) fire spuriously; 10000 scans real source
# lines properly while still short-circuiting truly pathological minified blobs.
_SECRET_LINE_SCAN_CAP = 10000


def _has_real_secret_value(line):
    """True if `line` contains a quoted literal that looks like an ACTUAL secret —
    not a $VAR/sigil interpolation, not an anchored placeholder, not all-filler.
    Recall-safe: returns True for anything that could plausibly be a real credential
    (no entropy floor — a weak real password must still fire)."""
    if len(line) > _SECRET_LINE_SCAN_CAP:
        return True
    for m in _QUOTED_RE.finditer(line):
        v = m.group(2)
        if len(v) < 12:
            continue
        if _INTERP_RE.search(v):
            continue
        if _PLACEHOLDER_RE.search(v):
            continue
        return True
    return False


def _any_match(patterns, lines):
    for pat in patterns:
        for ln in lines:
            if pat.search(ln):
                return True
    return False


def _path_match(patterns, path):
    if not path:
        return False
    norm = path.replace("\\", "/")
    return any(p.search(norm) for p in patterns)


# ---------------------------------------------------------------------------
# Code-skeleton normalization (design §4 precision pass — deliberated 2026-06-23,
# agreement 0.82). Produces a "code skeleton" of a line for the bare-keyword union
# signals ONLY: it blanks the *content* of comments, string literals, and JSX text
# nodes (preserving delimiters / token boundaries) so a keyword that is prose — a
# marketing string, a JSX label, a comment — disappears, while the SAME keyword used
# as a real identifier or call (outside any quote/comment) survives unchanged. This
# cuts the dominant false-positive class (keyword-in-prose hard-walls) at ZERO recall
# loss on executable code.
#
# Guards (each a deliberation finding):
#  - Interpolated literals are kept RAW: a Python f-string with `{...}` and a JS
#    template literal with `${...}` are left untouched, so `f"...{session}"` /
#    `` `SELECT ${role}` `` keep their keyword. Only non-interpolated literals are
#    blanked (those are UI/log strings).
#  - Only patterns listed in a signal's `skeleton_match` use the skeleton; the
#    hardcoded-secret, SQL, and import-string patterns match the RAW line (they look
#    INSIDE strings — skeletonizing would blind them).
#  - `--` is intentionally NOT treated as a comment (it is JS/C decrement); `#` is a
#    comment only when not preceded by `.`/word char (so JS private fields `this.#x`
#    are not stripped). These keep the skeleton from ever eating real code.
# ---------------------------------------------------------------------------

# One alternation over the lexical regions we blank. Order matters: a `#`/`//` inside
# a string is consumed by the string branch first (earlier start position), so it is
# not mistaken for a comment.
_SK_TOKEN = re.compile(
    r"(?P<lc>//[^\n]*|(?<![\w.])#[^\n]*)"             # line comments (// , bare #)
    r"|(?P<bc>/\*.*?\*/)"                              # inline /* ... */ block comment
    r"|(?P<tmpl>`(?:\\.|[^`\\])*`)"                    # JS/TS template literal
    r"|(?P<str>(?P<pre>[A-Za-z]{0,3})(?P<q>['\"])(?:\\.|(?!(?P=q)).)*(?P=q))"  # quoted string (opt. f/r/b prefix)
)

# A JSX text node: the text between an element's opening `>` and its CLOSING tag `</`.
# Applied ONLY in .jsx/.tsx files (audit F-002) — elsewhere `a > x < b` is a comparison.
# Requiring the trailing `<` to begin a closing tag (`</`) — not just any `<` — is what
# distinguishes `<p>Login</p>` (real text node) from a compact comparison `a>role<b`
# (audit F-A): the latter has `<b`, not `</`, so `role` is never blanked. The lookbehind
# keeps `>` tag-adjacent (preceded by a word / quote / `}` attr-expr / `/`). Real
# marketing-copy FPs are all `>text</tag>`, so this loses no FP coverage.
_SK_JSX_TEXT = re.compile(r"(?<=[\w\"'}/])>([^<>{}]+)<(?=/)")


def _sk_blank(m):
    if m.group("lc") is not None or m.group("bc") is not None:
        return " "
    t = m.group("tmpl")
    if t is not None:
        return t if "${" in t else "``"          # keep interpolated template raw
    s = m.group("str")
    if s is not None:
        pre = m.group("pre") or ""
        q = m.group("q")
        if "f" in pre.lower() and "{" in s:
            return s                               # keep interpolated f-string raw
        return pre + q + q                         # blank content, keep prefix+delimiters
    return m.group(0)


def _code_skeleton(line, in_block, is_jsx):
    """Return (skeleton, in_block) for one line. `in_block` carries multi-line
    block-comment state across the lines of a hunk. `is_jsx` enables JSX-text-node
    blanking — only safe in .jsx/.tsx, since in other languages `>x<` is a comparison
    (`a > x < b`), not a tag boundary (audit F-002)."""
    if in_block:
        close = line.find("*/")
        if close == -1:
            return "", True                        # whole line still inside /* ... */
        line = line[close + 2:]                     # inside a comment, string syntax is inert
        in_block = False
    # Blank comments/strings/templates FIRST, so a `/*` that lives *inside a string*
    # is already gone before we look for an unterminated block comment (audit F-001:
    # scanning the raw line for `/*` would truncate real code after a string literal
    # like "/*", silently dropping a keyword that follows it on the same line).
    s = _SK_TOKEN.sub(_sk_blank, line)
    open_idx = s.find("/*")
    if open_idx != -1 and "*/" not in s[open_idx:]:
        s = s[:open_idx]                            # unterminated /* — drop the tail
        in_block = True
    if is_jsx:
        s = _SK_JSX_TEXT.sub("><", s)
    return s, in_block


def _skeletonize(lines, is_jsx):
    out = []
    in_block = False
    for ln in lines:
        sk, in_block = _code_skeleton(ln, in_block, is_jsx)
        out.append(sk)
    return out


def _any_match_lines(compiled_patterns, lines):
    """True if any compiled pattern matches any line. M1 helper — M1 signals (all_of /
    and_not_added) match RAW lines only (the loader forbids skeleton_match/value_filter on them),
    so this is a plain OR scan with no skeleton/value-filter branch."""
    for pat in compiled_patterns:
        for ln in lines:
            if pat.search(ln):
                return True
    return False


def _signal_hit(patterns, skeleton_idx, value_filter_idx, raw_lines, sk_lines):
    """True if any of `patterns` matches. A pattern whose index is in
    `skeleton_idx` matches against `sk_lines` (the code skeleton); all others
    match against `raw_lines`. A pattern whose index is in `value_filter_idx`
    additionally requires a real-secret-looking quoted value on the matched line
    (literature #3 secret FP filter), else that match is skipped."""
    for i, pat in enumerate(patterns):
        targets = sk_lines if (skeleton_idx and i in skeleton_idx) else raw_lines
        vfilter = bool(value_filter_idx) and i in value_filter_idx
        for ln in targets:
            if pat.search(ln):
                if vfilter and not _has_real_secret_value(ln):
                    continue  # placeholder / $VAR interpolation — not a real secret
                return True
    return False


def _signal_first_match(signal, path, added, removed, sk_added, sk_removed):
    """Return (token, line_no) for the first pattern of `signal` that fires — the matched
    substring and the 1-based line index within the hunk side (line_no is None for a
    path-match signal). CLIENT-SIDE TRANSPARENCY ONLY (Fix 2A): this returns source text used
    to build the LOCAL deny message; it is NEVER added to the coverage POST (_hunk_evidence
    sends hashes only). Mirrors _signal_hit's skeleton/value-filter logic so the reported span
    is the one that actually fired."""
    # M1 signals (all_of / and_not_added) fire via the file-aware pass, not patterns[]/skeleton —
    # report the first co-occurrence match on the signal's match side so the deny line points at
    # real code (Rule 8: mirror the firing logic). all_of => first match of any group; and_not_added
    # => first match of the removed alternation (which lives in patterns[]).
    if signal.get("is_m1"):
        lines = removed if signal["match"] == "removed" else added
        groups = signal["all_of"] if signal["all_of"] else [signal["patterns"]]
        for group in groups:
            for pat in group:
                for idx, ln in enumerate(lines):
                    hit = pat.search(ln)
                    if hit:
                        return (hit.group(0), idx + 1)
        return (None, None)
    m = signal["match"]
    if m == "path":
        for pat in signal["patterns"]:
            hit = pat.search(path or "")
            if hit:
                return (hit.group(0), None)
        return (None, None)
    raw, sk = (removed, sk_removed) if m == "removed" else (added, sk_added)
    skmatch = signal["skeleton_match"]
    vfilter = signal["value_filter_patterns"]
    for i, pat in enumerate(signal["patterns"]):
        targets = sk if (skmatch and i in skmatch) else raw
        vf = bool(vfilter) and i in vfilter
        for idx, ln in enumerate(targets):
            hit = pat.search(ln)
            if hit:
                if vf and not _has_real_secret_value(ln):
                    continue
                return (hit.group(0), idx + 1)
    return (None, None)


def _resolve_m1_signals(parsed_hunks, file_content_fetcher=None):
    """Mechanism-M1 file-aware pass. Determines which M1 signals (all_of / and_not_added) fire and
    on WHICH hunk index. Returns (fires, failsafe_names) where fires = {hunk_index: [signal, ...]}.
    The returned fires are merged into that hunk's verdict as ordinary trigger-class signals, so
    scoring / hashing / floor-derivation all reuse the existing per-hunk path.

    `parsed_hunks` is the materialized list of (path, added, removed, structural) tuples.

    - **and_not_added** (match:"removed"): fires on a hunk whose REMOVED lines match `patterns`,
      UNLESS an equivalent is re-added anywhere in the SAME FILE's added lines (patch-wide union
      across all the file's hunks — zero extra I/O). Closes the cross-hunk mask-MOVE false-floor.
    - **all_of**: fires when EVERY group matches across (the file's diff match-side lines) ∪ (the
      fetched file), AND the DIFF itself contributes >=1 group. The diff-contribution requirement
      is load-bearing: without it, editing an UNRELATED line in a file that already contains a
      pre-existing co-occurrence (e.g. a workflow that already has both a pwn trigger and an
      untrusted checkout) would falsely floor. If some groups are satisfied only OUTSIDE the diff
      and a `file_content_fetcher` is provided, the file is fetched ONCE per path to confirm. If no
      fetcher is available and the diff alone can't confirm, the signal FIRES conservatively
      (fail-safe — never silently drop a floor) and its name is returned in failsafe_names.
      Attribution: the first hunk whose match-side lines match a diff-satisfied group.
    """
    m1_signals = [s for s in _CFG["signals"] if s.get("is_m1")]
    if not m1_signals:
        return {}, []
    fires = {}
    failsafe_names = []
    by_path = {}
    for i, (path, _a, _r, _hr) in enumerate(parsed_hunks):
        by_path.setdefault(path, []).append(i)

    for path, idxs in by_path.items():
        file_added = [ln for i in idxs for ln in parsed_hunks[i][1]]
        file_removed = [ln for i in idxs for ln in parsed_hunks[i][2]]
        fetched = None       # None = not fetched yet; a list once a read SUCCEEDS (may be empty)
        fetch_failed = False  # True once a read raised or returned None -> route to fail-safe

        for s in m1_signals:
            if s["and_not_added"]:
                pats = s["patterns"]
                if _any_match_lines(pats, file_added):
                    continue  # equivalent re-added somewhere in the file -> refactor, not removal
                for i in idxs:
                    if _any_match_lines(pats, parsed_hunks[i][2]):
                        fires.setdefault(i, []).append(s)
                continue

            groups = s["all_of"]
            match_added = (s["match"] != "removed")
            diff_corpus = file_added if match_added else file_removed
            diff_hits = [_any_match_lines(g, diff_corpus) for g in groups]
            if not any(diff_hits):
                continue  # the change contributes none of the groups
            # EVERY hunk that contributed a diff-satisfied group carries the floor (audit F-001).
            # Attribution binds receipt/release by content_hash, so attaching to ALL contributors
            # (not just the first) means any contributing hunk's review covers the floor and both
            # halves of a split co-occurrence stay reviewable — the least-surprising, auditable rule.
            contributors = [
                i for i in idxs
                if any(diff_hits[gi] and _any_match_lines(
                        groups[gi], parsed_hunks[i][1] if match_added else parsed_hunks[i][2])
                       for gi in range(len(groups)))]
            if not contributors:
                continue  # defensive (any(diff_hits) implies >=1 contributor)
            if all(diff_hits):
                for i in contributors:  # fully satisfied by the diff
                    fires.setdefault(i, []).append(s)
                continue
            missing = [g for g, hit in zip(groups, diff_hits) if not hit]
            # Lazily read the file ONCE per path (cache scoped to THIS classify_diff call — audit
            # F-006). The fetcher contract (audit F-002): return the file's CURRENT on-disk content
            # (a str, possibly "") on SUCCESS, or None / raise on FAILURE — never a stale/cached
            # version. A clean read that simply lacks the co-signal is a genuine "not a
            # co-occurrence" -> no fire. A FAILED read must NOT be mistaken for that (else a floor
            # silently vanishes on a file we couldn't read — the dangerous direction) -> it routes
            # to the fail-safe below, same as no fetcher.
            if file_content_fetcher is not None and fetched is None and not fetch_failed:
                try:
                    raw = file_content_fetcher(path)
                except Exception:
                    raw = None
                if raw is None:
                    fetch_failed = True
                else:
                    fetched = raw.splitlines()
            if file_content_fetcher is not None and not fetch_failed:
                if all(_any_match_lines(g, fetched) for g in missing):
                    for i in contributors:
                        fires.setdefault(i, []).append(s)
                # clean read, co-signal genuinely absent -> not a real co-occurrence -> no fire
            else:
                # FAIL-SAFE (owner-approved): no fetcher, OR the read failed -> can't confirm ->
                # fire conservatively (never silently drop a floor) and surface the name.
                for i in contributors:
                    fires.setdefault(i, []).append(s)
                failsafe_names.append(s["name"])
    return fires, failsafe_names


def _classify_hunk(path, added, removed, trigger_threshold=None, extra_fired=None):
    """Return a per-hunk verdict dict, or None if not risky.

    `extra_fired` (Mechanism M1): trigger-class signals resolved by the file-aware
    _resolve_m1_signals pass that fire on THIS hunk. Merged into `fired` after the legacy per-hunk
    loop, so they score/hash/floor via the same path. None/empty => byte-identical to the pre-M1
    engine (the regression invariant). M1 signals are SKIPPED by the legacy loop below.

    `trigger_threshold` overrides _CFG["trigger"] for this call (the floor-bounded
    user override, F-011 — clamped by the caller in gate_lib; the engine just honors
    whatever effective threshold it's handed).
    """
    # POLICY (audit F-003 + Phase 6): on a doc/prose path a CONTENT keyword is a false
    # positive (a keyword in a README/notes file is narrative, not gated risk). Phase 6
    # makes this AUTO-TRIGGER-AWARE rather than a blanket exclusion: a prose hunk is scanned
    # ONLY against `match: "path"` signals (the file's role — e.g. requirements.txt is a
    # dependency change) and `auto_trigger` signals (a real secret VALUE is a leak even in
    # prose — closes the secret-in-.md recall hole). The content-keyword signals are skipped.
    # A non-prose hunk runs the full scan. The F-007 floor still applies only to what fires.
    prose = bool(path) and bool(_PROSE_PATHS.search(path))

    threshold = _CFG["trigger"] if trigger_threshold is None else int(trigger_threshold)

    # Build the comment/string-stripped "code skeleton" once per side (only when a
    # signal actually opts a pattern into skeleton matching — else it's free).
    if _CFG.get("has_skeleton"):
        # JSX text-node blanking is scoped to .jsx/.tsx (audit F-002); comment/string
        # blanking is universal (safe in every language).
        is_jsx = bool(path) and path.lower().endswith((".jsx", ".tsx"))
        sk_added = _skeletonize(added, is_jsx)
        sk_removed = _skeletonize(removed, is_jsx)
    else:
        sk_added, sk_removed = added, removed

    fired = []
    for s in _CFG["signals"]:
        # M1 signals (all_of / and_not_added) are resolved by the file-aware _resolve_m1_signals
        # pass and merged via `extra_fired` below — never by this per-hunk loop. Skipping them here
        # is what keeps legacy scoring byte-identical.
        if s.get("is_m1"):
            continue
        # Prose path: skip CONTENT-keyword signals; keep path-role + auto-trigger (secret
        # value) signals so requirements.txt still fires (dependency) and a leaked key in a
        # README still fires (auto_trigger), but "the docs mention auth" does not.
        if prose and s["match"] != "path" and not s["auto"]:
            continue
        # M2 path-gate: a signal armed with path_gate scores ONLY on a hunk whose path matches the
        # allow-list. A None/unmatched path skips it (safe — path_gate is only on ADVISORIES by the
        # loader's floor-forbid invariant, so a skipped path-gated signal can never silence a floor).
        # Inert when no signal carries path_gate (has_path_gate False).
        if s["path_gate"] and not _path_match(s["path_gate"], path):
            continue
        m = s["match"]
        if m == "path":
            hit = _path_match(s["patterns"], path)
        elif m == "removed":
            hit = _signal_hit(s["patterns"], s["skeleton_match"],
                              s["value_filter_patterns"], removed, sk_removed)
        else:  # "added"
            hit = _signal_hit(s["patterns"], s["skeleton_match"],
                              s["value_filter_patterns"], added, sk_added)
        if hit:
            fired.append(s)

    # Merge Mechanism-M1 fires (from the file-aware _resolve_m1_signals pass) into this hunk's
    # fired set — they then score/hash/floor via the same path below. M1 signals are content
    # signals, so on a prose path they're dropped, consistent with the prose exclusion above.
    if extra_fired and not prose:
        fired.extend(extra_fired)

    fired_names = {s["name"] for s in fired}
    # Double-count guard (design §4.1): a removed line that matches a real risk pattern is
    # the Tier-3 trigger-class `removed_safety_control`; the Tier-4 borderline
    # `removed_generic_conditional` must NOT also count for the same deletion — the
    # trigger-class match wins. Drop the borderline signal when the trigger one fired.
    if "removed_safety_control" in fired_names and "removed_generic_conditional" in fired_names:
        fired = [s for s in fired if s["name"] != "removed_generic_conditional"]

    if not fired:
        return None

    # POLICY (audit F-002): suppressors are path-based exclusions with a DUAL effect, both
    # intentional — (a) added to trigger_score they demote HIGH->LOW (the F-007 floor still
    # keeps a trigger-class signal at >= LOW); (b) added to borderline_score they can silence
    # a borderline-ONLY hunk to PASS (e.g. idiomatic concurrency in tests/). They never
    # silence a trigger-class signal.
    supp = [s for s in _CFG["suppressors"] if _path_match(s["patterns"], path)]
    supp_weight = sum(s["weight"] for s in supp)  # negative

    auto = [s for s in fired if s["auto"]]
    trigger_fired = [s for s in fired if s["cls"] == _TRIGGER_CLASS]
    borderline_fired = [s for s in fired if s["cls"] in _BORDERLINE_CLASSES]

    # large_hunk (design Tier 4 §4.1, weight in config): a large added-line hunk that ALSO
    # carries a risk signal is a borderline *significance* amplifier — "this consequential
    # change has blast radius." Implemented as an amplifier (gated on an existing signal),
    # not a standalone, so a big benign refactor doesn't fire (the design's high-fan-in
    # proxy without a cross-file graph). Counts as significance for the §6.5 spike rule.
    large_lines = _CFG.get("large_hunk_added_lines", 0)
    large_weight = _CFG.get("large_hunk_weight", 0)
    is_large_hunk = bool(fired) and large_lines > 0 and len(added) >= large_lines

    trigger_score = sum(s["weight"] for s in trigger_fired) + supp_weight
    borderline_score = sum(s["weight"] for s in borderline_fired) + (large_weight if is_large_hunk else 0)

    # F1 (floor-masking fix): when several trigger signals fire in ONE hunk (e.g. git merged a
    # guard removal and an os.system() into a single hunk), the DECIDING signal sets the hunk's
    # category, and floor status is derived from that category downstream. Picking the max-WEIGHT
    # signal let a co-located non-floor signal win a weight tie (broken by array order) and MASK a
    # real floor — the gate then printed "0 floor" and a free skip shipped it unreviewed. Prefer an
    # EFFECTIVE-floor signal (a floor category NOT path-exempt here) so the floor can't be masked;
    # tie-break by weight. Only ever PROMOTES a hunk to floor (fail-safe). EFFECTIVE floor, not raw
    # is_hard_floor: else an exempt floor (removed_guard in tests/) could win over a non-exempt one
    # (tls_pinning_removed) and re-open the fail-open.
    _f1_pc = classify_path_class(path, "\n".join(added))

    def _floor_first(s):
        c = s["category"]
        return (is_hard_floor(c) and not floor_exempt(c, _f1_pc), s["weight"])

    if auto:
        # POLICY (audit F-001): an auto-trigger signal (e.g. a hardcoded secret) is ALWAYS
        # HIGH, bypassing suppressors by design — a leaked key in a test fixture is still a
        # leaked key. auto is compile-enforced to trigger-class only.
        deciding_signal = auto[0]
        confidence, cat = HIGH, deciding_signal["category"]
        reason = "auto_trigger:" + deciding_signal["name"]
        score = deciding_signal["weight"]
    elif trigger_score >= threshold:
        confidence = HIGH
        deciding_signal = max(trigger_fired, key=_floor_first)
        cat = deciding_signal["category"]
        reason = "trigger_score_%d" % trigger_score
        score = trigger_score
    elif trigger_fired:
        # F-007 lower bound: a trigger-class signal present floors the band at BORDERLINE.
        # Suppressors demoted it below the threshold; they cannot silence it to PASS.
        confidence = LOW
        deciding_signal = max(trigger_fired, key=_floor_first)
        cat = deciding_signal["category"]
        reason = "trigger_near_miss_%d" % trigger_score
        score = trigger_score
    elif (borderline_score + supp_weight) >= _CFG["borderline_low"]:
        confidence = LOW
        deciding_signal = max(borderline_fired, key=lambda s: s["weight"]) if borderline_fired else None
        cat = deciding_signal["category"] if deciding_signal else "significance"
        reason = "borderline_score_%d" % borderline_score
        score = borderline_score + supp_weight
    else:
        return None

    # near_miss: a trigger-class signal fired but landed at LOW (suppressed below the
    # threshold). Note the (intended) semantic — a near-miss *raises* the borderline tier
    # toward Heavy: a change adjacent to real risk deserves the closer look (audit F-F).
    near_miss = bool(trigger_fired and confidence == LOW)
    has_sig = is_large_hunk or any(s["cls"] == "significance" for s in borderline_fired)
    has_dom = any(s["cls"] == "domain" for s in borderline_fired)
    signal_names = sorted({s["name"] for s in fired} | ({"large_hunk"} if is_large_hunk else set()))
    # Fix 2A transparency: the matched span of the DECIDING signal (the identifier/keyword that
    # fired + its 1-based line within the hunk) for the LOCAL deny message and the cheap-confirm
    # input. A secret VALUE is never echoed (see _SECRET_CATS — report the line + category only).
    # Client-side only: not added to _hunk_evidence, so it never leaves the machine at fire time.
    matched = None
    if deciding_signal is not None:
        mtoken, mline = _signal_first_match(
            deciding_signal, path, added, removed, sk_added, sk_removed)
        if cat in _SECRET_CATS:
            mtoken = None
        if mtoken or mline:
            matched = {"signal": deciding_signal["name"],
                       "token": (mtoken[:80] if mtoken else None), "line": mline}
    return {
        "category": cat,
        "confidence": confidence,
        "signals": signal_names,
        "score": score,
        "reason": reason,
        "suppressed": sorted(s["name"] for s in supp),
        # Per-hunk spike (audit F-006/F-B): a genuine borderline spike is THIS (LOW) hunk
        # being a near-miss OR significance+domain co-occurring IN THE SAME HUNK. Gated to
        # LOW so a HIGH hunk that happens to carry significance+domain never elevates an
        # unrelated borderline hunk's tier.
        "spike": confidence == LOW and (near_miss or (has_sig and has_dom)),
        # v1-compatible hash basis: hash the ADDED lines (v1 hashed added-only, so every
        # added-bearing hunk keeps its v1 hash). Pure-deletion hunks (new in v2) fall back
        # to the removed lines so they get a distinct, stable hash.
        "content_basis": added if added else removed,
        # Fix 2A transparency (client-side only; see _signal_first_match) — None or
        # {signal, token, line}. Never sent to the server (privacy: only hashes leave).
        "matched": matched,
    }


def classify_diff(diff_text, trigger_threshold=None, file_content_fetcher=None):
    """Classify a unified diff. Returns:

        {
          "risky": bool,
          "score": int,                                  # max deciding score across hunks
          "max_confidence": "high" | "low" | None,
          "trigger_reason": str | None,                  # reason of the deciding hunk
          "risk_categories": [str, ...],                 # union across hunks
          "suppressed_by": [str, ...],                   # union of suppressors applied
          "classifier_version": str,
          "borderline_tier": "heavy" | "lite" | None,   # §6.5 synthesize gate
          "hunks": [ {path, content_hash, normalized_hash, structural, category, confidence,
                      signals, path_class}, ... ],   # `structural`: @@ position or None (Ph 9.1)
        }

    Empty / trivial diffs return risky=False with no hunks. `max_confidence` and per-hunk
    {path, content_hash, category, confidence} preserve the v1 contract; the additive
    fields (`score`, `trigger_reason`, `risk_categories`, `suppressed_by`,
    `classifier_version`) feed telemetry + the skip-log training signal (design §4.4, §5.3).
    `trigger_threshold` is the floor-bounded user override (F-011), None = config default.
    `file_content_fetcher` (Mechanism M1): optional callable path->str returning the target file's
    content, used by two-stage `all_of` co-occurrence to confirm a co-signal that lives OUTSIDE the
    diff (e.g. a pre-existing CI trigger). None => two-stage falls back to the fail-safe (an
    unconfirmable all_of FLOOR fires conservatively; its name lands in the `m1_failsafe` list).
    """
    hunks = []
    verdicts = []
    any_spike = False

    # Materialize hunks once so the Mechanism-M1 file-aware pass can look ACROSS a file's hunks
    # (and, via file_content_fetcher, the whole file) before per-hunk scoring. M1 fires are merged
    # into the owning hunk's verdict as ordinary trigger-class signals. When no M1 signal exists
    # (has_m1 False) this is inert and the per-hunk path stays byte-identical to the pre-M1 engine.
    parsed = list(_iter_file_hunks(diff_text or ""))
    m1_by_hunk, m1_failsafe = ({}, [])
    if _CFG.get("has_m1"):
        m1_by_hunk, m1_failsafe = _resolve_m1_signals(parsed, file_content_fetcher)

    for hidx, (path, added, removed, hrange) in enumerate(parsed):
        verdict = _classify_hunk(path, added, removed, trigger_threshold=trigger_threshold,
                                 extra_fired=m1_by_hunk.get(hidx))
        if verdict is None:
            continue
        verdicts.append(verdict)
        hunks.append({
            "path": path,
            "content_hash": hunk_content_hash(verdict["content_basis"]),
            # Phase 9.1: the per-hunk @@ position (old/new start+count, net_delta) or None — the
            # coarse-structural coverage fallback's match key, additive + inert until the Phase-9.1
            # resolver reads it. Survives mojibake/re-encoding drift that the hashes above do not.
            "structural": hrange,
            # Phase 9: a cosmetic-drift-tolerant fallback match key + the filetype that
            # drove its comment-stripping decision. Additive — inert until the Phase 9
            # coverage path reads it; the v1 fields above are unchanged.
            "normalized_hash": hunk_normalized_hash(
                verdict["content_basis"], path, verdict["category"]),
            "filetype": hunk_filetype(path),
            "category": verdict["category"],
            "confidence": verdict["confidence"],
            "signals": verdict["signals"],
            # §4.B path-class tag (conservative; ambiguous → 'source'). Sent per hunk in the
            # coverage POST so the server can verify a test_or_docs_only / generated_or_
            # vendored_code skip claim against fire-time evidence (never raw paths).
            "path_class": classify_path_class(path, "\n".join(added)),
            # Fix 2A: matched span for the LOCAL deny message (client-side; never POSTed —
            # _hunk_evidence sends hashes only).
            "matched": verdict.get("matched"),
        })
        if verdict["spike"]:
            any_spike = True

    max_conf = None
    if any(h["confidence"] == HIGH for h in hunks):
        max_conf = HIGH
    elif hunks:
        max_conf = LOW

    # Borderline sub-tier (§6.5): only when there's a borderline (low) hunk. Heavy = a
    # genuine per-hunk spike (a near-miss, or significance+domain in one hunk); else Lite.
    # NEVER primitive density.
    borderline_tier = None
    if any(h["confidence"] == LOW for h in hunks):
        borderline_tier = "heavy" if any_spike else "lite"

    # The "deciding" hunk drives trigger_reason/score: prefer a HIGH hunk (the one that
    # actually walls), else the highest-scoring borderline hunk.
    deciding = None
    if verdicts:
        high_v = [v for v in verdicts if v["confidence"] == HIGH]
        deciding = max(high_v or verdicts, key=lambda v: v["score"])

    return {
        "risky": bool(hunks),
        "score": deciding["score"] if deciding else 0,
        "max_confidence": max_conf,
        "trigger_reason": deciding["reason"] if deciding else None,
        "risk_categories": sorted({h["category"] for h in hunks}),
        "suppressed_by": sorted({s for v in verdicts for s in v["suppressed"]}),
        "classifier_version": _CFG.get("version", "0"),
        "borderline_tier": borderline_tier,
        "hunks": hunks,
        # Mechanism M1: names of all_of FLOOR signals that fired via the fail-safe (no fetcher +
        # unconfirmable from the diff alone). Empty on the normal path; a non-empty list tells the
        # gate a floor fired CONSERVATIVELY so it can surface a capability note. Additive field.
        "m1_failsafe": sorted(set(m1_failsafe)),
    }


def _cli_file_fetcher(path):
    """M1 two-stage fetcher for the CLI/diagnostic path: read the file relative to CWD, or None on
    any failure (-> classifier fail-safe). Keeps `python -m mcp_server.risk_classifier` behaving
    like the real gates (which wire gate_lib.file_content_fetcher) instead of fail-safe-firing every
    two-stage floor. NOTE the SERVER's own classify_diff calls (receipt_writer / floor_confirm)
    pass NO fetcher BY DESIGN — the server never reads user files (privacy: only hashes leave the
    client). There, an M1 two-stage floor fail-safe over-fires, which is SAFE: those are
    coverage/overlap computations over the reviewed diff, so over-firing over-covers the fire's own
    hunks (never a false block; it's what lets a client-fetched edit-case pwn floor be released —
    prevents a coverage deadlock). See docs/MCP/Classifier/PHASE3-M1-DESIGN.md."""
    try:
        with open(os.path.join(os.getcwd(), str(path).replace("/", os.sep)),
                  "r", encoding="utf-8", errors="strict") as fh:
            return fh.read()
    except Exception:
        return None


def _main(argv):
    diff_text = sys.stdin.read()
    print(json.dumps(classify_diff(diff_text, file_content_fetcher=_cli_file_fetcher), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
