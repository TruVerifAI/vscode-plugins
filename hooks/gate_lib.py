"""Shared library for the TruVerifAI proactive-invocation PreToolUse gates.

Pure stdlib (urllib, subprocess, hashlib) so it runs anywhere python3 does, with
no pip installs — matching the plugin's "drop-in" promise.

Design (docs/MCP/adoption solve/proactive-invocation-v2-hybrid.md):
- The gate classifies the change LOCALLY (vendored risk_classifier) and sends the
  backend only a repo *fingerprint* + hunk content *hashes* — never source.
- It NEVER hard-fails the agent on our own infra: missing python/token, network
  errors, or our server being down all FAIL-OPEN (allow). The escape valve
  (`recent_pass`) ensures a hash/area *misalignment* also can't deadlock.
- The decision functions here are pure and unit-tested; the hook drivers
  (audit_gate.py / deliberate_gate.py) just do I/O + translate the decision into
  a PreToolUse output.
"""

import difflib
import hashlib
import json
import os
import random
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request

import host as host_registry  # per-platform adapter layer (same dir, host/ package)
from risk_classifier import (  # vendored, same dir
    classify_diff,
    hunk_content_hash,
    is_hard_floor,          # per-hunk floor membership — the uncovered floor/non-floor split
    floor_exempt,           # ...and the PATH demotion that makes that membership *effective*
    NORM_VERSION,
    clamp_threshold,
    # Gate-self detection + the synthesized self-coverage hash live in the vendored
    # classifier so the client gate and the SERVER receipt writer agree byte-for-byte.
    is_gate_self_mutation,
    diff_touches_gate_self,
    diff_touches_gate_core,
    diff_is_inert,
    gate_self_coverage_hash,
    GATE_SELF_HASH_PREFIX,
    # gate_tightness (commit-gate confidence tier): the floor set + per-hunk block/advise
    # partition live in the vendored classifier so client + server agree byte-for-byte.
    hunk_blocks_under_tightness,
    GATE_TIGHTNESS_VALUES,
    DEFAULT_GATE_TIGHTNESS,
)


DEFAULT_BASE_URL = "https://api.truverif.ai"


# ---------------------------------------------------------------------------
# Config — cross-platform resolution chain (implementation plan §7.4 / §3.3)
# ---------------------------------------------------------------------------
# Every option resolves through the same three levels, first non-empty wins:
#   1. TVAI_<NAME> env var        — explicit override (CI / Docker / MDM)
#   2. host-native mechanism      — e.g. Claude Code's CLAUDE_PLUGIN_OPTION_<NAME>
#   3. ~/.truverifai/config.json  — written by `tvai login` (device flow); the
#                                   universal path for hosts with no native secrets
# EMPTY values are treated as unset at every level and fall through — a blank
# override must never blank a gate POST (the 2026-07-23 dev-setup defect class).

_CONFIG_FILE_CACHE = None

# The token's TVAI env var is TVAI_API_KEY (the canonical cross-platform name in
# the docs/setup copy), not TVAI_API_TOKEN; the config-file key accepts both.
_TVAI_ENV_ALIASES = {"api_token": "TVAI_API_KEY"}
_CONFIG_FILE_ALIASES = {"api_token": ("api_key", "api_token")}


def _user_config_file():
    """~/.truverifai/config.json parsed once per process, {} on any error (missing,
    corrupt, unreadable — the gate never traps the agent on its own config)."""
    global _CONFIG_FILE_CACHE
    if _CONFIG_FILE_CACHE is None:
        try:
            p = os.path.join(os.path.expanduser("~"), ".truverifai", "config.json")
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
            _CONFIG_FILE_CACHE = data if isinstance(data, dict) else {}
        except Exception:
            _CONFIG_FILE_CACHE = {}
    return _CONFIG_FILE_CACHE


def _opt(name):
    """Resolve option `name` (lower_snake) through the chain. Returns a stripped
    string, or None when unset at every level."""
    env_name = _TVAI_ENV_ALIASES.get(name, "TVAI_" + name.upper())
    val = (os.environ.get(env_name) or "").strip()
    if val:
        return val
    try:
        val = (host_registry.current().native_option(name) or "").strip()
    except Exception:
        val = ""  # a broken adapter must not kill config resolution — fail open
    if val:
        return val
    cfg = _user_config_file()
    for key in _CONFIG_FILE_ALIASES.get(name, (name,)):
        v = cfg.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = str(v)
        if isinstance(v, bool):
            v = "true" if v else "false"
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def config():
    return {
        "token": _opt("api_token") or "",
        # Unset -> enabled (the shipped default). Any explicit value other than the
        # exact string "true" disables — matching v0.17.0's strict comparison.
        "enabled": (_opt("enable_gates") or "true") == "true",
        # The env override is an INTERNAL escape hatch (not a declared userConfig
        # option — users never see it); dev builds get their URL rewritten at sync.
        "base_url": ((_opt("api_base_url") or "").strip() or DEFAULT_BASE_URL).rstrip("/"),
        # Borderline (low-confidence) tier routing to synthesize (design §6.5):
        # 'synthesize_gate' (soft-gate Borderline-Heavy -> synthesize_coding),
        # 'advisory' (surface a suggestion only — the default until the F-001
        # output-quality pre-validation passes), or 'off' (ignore borderline).
        "borderline_mode": _opt("borderline_mode") or "advisory",
        # Floor-bounded trigger-threshold override (F-011, §4.3): a user may RAISE the
        # threshold to cut borderline noise in a noisy repo. clamp_threshold() pins it to
        # [borderline_low+1, ceiling] so the must-fire signals (auth/secrets/migration/
        # removed-guard) always fire — the gate can't be silently disabled this way.
        # Empty -> config default.
        "trigger_threshold": _opt("gate_threshold") or "",
        # §6.5 borderline throttles (only active when borderline_mode='synthesize_gate'):
        # fractional sampling of Heavy events + a per-session soft-gate budget cap. Keep
        # the trigger rate flat on a high-volume band (design §6.5 "three throttles").
        "borderline_sampling_rate": _parse_rate(_opt("borderline_sampling_rate") or "", 0.5),
        "borderline_session_budget": _parse_int(_opt("borderline_session_budget") or "", 3),
        # Gate tightness (fatigue lever, GATE-TIGHTNESS-DESIGN.md) — governs BOTH gates now
        # (Inc 8, Fix 5: the retired deliberate_mode is subsumed here). 'focused' (default) blocks
        # only floor + high-confidence non-floor and downgrades non-floor low-confidence changes to
        # a non-blocking advisory; 'thorough' blocks every risky change (legacy). The floor always
        # blocks at both levels. Invalid → default (fail safe: not looser).
        "gate_tightness": _resolve_gate_tightness(),
    }


def precommit_stash_path(session_id):
    """Session-scoped temp file where the PreToolUse stash hook records the pre-command
    HEAD, and the post-commit backstop reads it (multi-commit handshake). SINGLE source of
    the path so both hooks agree by construction. Sanitized session id; overwritten each
    commit command (self-healing, no accumulation)."""
    import tempfile
    safe = "".join(c for c in str(session_id or "nosession") if c.isalnum() or c in "-_")[:80] \
        or "nosession"
    return os.path.join(tempfile.gettempdir(), "truverifai_precommit_" + safe)


def _resolve_gate_tightness():
    """The active gate_tightness for BOTH gates (Inc 8, Fix 5). Read from its own env var; if that
    is unset, MIGRATE from the retired `deliberate_mode` so an install that still sets the old env
    var keeps an equivalent posture until it updates its config. An explicit gate_tightness always
    wins. Mapping (design §Fix-5 — load-bearing):
      - tiered (the OLD default) → focused (the NEW default) — the write-gate posture is UNCHANGED;
      - block                    → thorough (block every risky change);
      - advisory / unset / unknown → focused — there is no 'never-block' tightness (the floor always
        blocks under both levels), so the always-advisory mode maps to the least-strict tightness.
    Mapping tiered→focused (NOT thorough) is the load-bearing bit: it keeps the default unchanged so
    Fix 5 doesn't silently make the default stricter (it only adds floor-awareness — a hard-floor
    hunk now blocks the write at ANY confidence, matching the commit gate)."""
    raw = _opt("gate_tightness") or ""
    if raw.strip():
        return _parse_tightness(raw)
    legacy = (_opt("deliberate_mode") or "").strip().lower()
    if legacy:
        # audit F-004: the deliberate_mode userConfig is retired. If the option was dropped from
        # plugin.json but a stale saved value is still exported (Claude Code behavior varies), tell
        # the user their old setting is being migrated so a lost 'block' posture is never silent.
        mapped = "thorough" if legacy == "block" else DEFAULT_GATE_TIGHTNESS
        sys.stderr.write(
            "TruVerifAI: 'deliberate_mode' is retired — migrating deliberate_mode=%r to "
            "gate_tightness=%r. Set gate_tightness directly to silence this.\n" % (legacy, mapped))
        return mapped
    return DEFAULT_GATE_TIGHTNESS  # unset → the default


def _parse_tightness(val):
    """Validate the gate_tightness config value. Two distinct cases (audit F-002):
    - EMPTY / unset → DEFAULT_GATE_TIGHTNESS ('focused') — the intentional product default (the
      user chose not to set it).
    - Non-empty but UNRECOGNIZED (typo / corruption / injection, e.g. 'focsed') → 'thorough' —
      fail SAFE to the STRICTER mode, never silently to the looser 'focused'. A garbage value
      must not loosen the gate.
    The floor is enforced regardless of this value. Lowercased + stripped so 'Focused' /
    ' thorough ' work."""
    v = (val or "").strip().lower()
    if not v:
        return DEFAULT_GATE_TIGHTNESS
    if v in GATE_TIGHTNESS_VALUES:
        return v
    sys.stderr.write(
        "TruVerifAI: unrecognized gate_tightness %r — failing safe to 'thorough'\n" % (val,))
    return "thorough"


def _parse_rate(val, default):
    try:
        r = float(val)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, r))


def _parse_int(val, default):
    try:
        return max(0, int(val))
    except (TypeError, ValueError):
        return default


def effective_threshold(cfg):
    """The clamped (floor-bounded) trigger threshold to hand classify_diff, or None
    for the config default (F-011)."""
    return clamp_threshold(cfg.get("trigger_threshold", ""))


# Structured skip reason codes (design §5.2). The agent releases a gate by acting
# OR by recording a skip with one of these + free-form text (the free-form is the
# training signal for the §3.4 classifier-improvement model).
#
# MUST stay byte-identical with mcp_server.gate_skip.REASON_CODES and the record_gate_skip
# tool enum (Rule 9). §4.A renamed `already_reviewed_this_session` →
# `prior_pass_receipt_match` via add-alias-then-deprecate: both are listed during the
# transition (server accepts both), but the plugin no longer EMITS the deprecated alias —
# the deny messages / skill stop offering it; it remains here only so an in-flight skip
# still validates. The alias is removed in the Phase-2 cleanup deploy.
SKIP_REASON_CODES = (
    "false_positive_not_risky",
    "trivial_change",
    "prior_pass_receipt_match",
    "reviewed_outside_truverifai",
    "generated_or_vendored_code",
    "test_or_docs_only",
    "time_critical_hotfix",
    "disagree_with_classification",
    "tool_unavailable",
    "other",
    # Single-call review model (2026-07-02): the two non-PASS branches of ONE panel-review call.
    # recommendations_applied = ran a review, applied its findings, moving on (server-verified
    # against a real review receipt). review_deferred_to_commit = defer ALL review to the commit
    # gate (session/area-scoped write-gate release; the batch is re-reviewed at commit).
    "recommendations_applied",
    "review_deferred_to_commit",
    # Inc 7 (Fix 2B): the last-rung FLOOR escape — ships a floor change un-reviewed, logged +
    # accountable (distinct ACCEPT_RISK receipt, substantive pre-mortem, minutes TTL). Byte-identity
    # with mcp_server/gate_skip.REASON_CODES + the record_gate_skip tool Literal (Rule 9).
    "accept_risk_no_review",
    # Wave 3 (gate-usability §3.3): merge-commit release for branch content whose
    # per-commit receipts don't hash-match the merge diff. Merge fires only
    # (server-enforced); floor-denied like every judgment skip.
    "branch_already_reviewed",
    "already_reviewed_this_session",  # DEPRECATED alias of prior_pass_receipt_match (no longer emitted)
)


# ---------------------------------------------------------------------------
# Git / repo helpers
# ---------------------------------------------------------------------------

def _git(args, cwd):
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=20,
            # Decode git's output as UTF-8 explicitly, NOT the platform locale. Without
            # this, text=True decodes with locale.getpreferredencoding() (cp1252 on
            # Windows, ASCII under a C/POSIX locale in CI/containers), which mojibakes any
            # non-ASCII in the diff (em-dash, section sign, accented identifiers) BEFORE it
            # is hashed. The commit gate tolerated that via the structural coverage tier
            # (its fire and the agent's `git diff --staged` share hunk line-ranges), but a
            # mojibaked fire hash is still wrong; git emits UTF-8 content by default, so
            # utf-8 is the correct, locale-independent decode on every OS. errors="replace"
            # never raises on a stray non-UTF-8 byte (matches _read_file_safe /
            # repo_fingerprint), so a weird file cannot turn the diff into "" and fail open.
            encoding="utf-8", errors="replace",
        )
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


def repo_fingerprint(cwd):
    """A stable, non-identifying repo id shared across hook + receipt. Prefer the
    origin remote URL; fall back to the repo top-level path. Hashed so no URL or
    path leaves the machine."""
    basis = _git(["remote", "get-url", "origin"], cwd).strip()
    if not basis:
        basis = _git(["rev-parse", "--show-toplevel"], cwd).strip() or (cwd or ".")
    return "repo_" + hashlib.sha256(basis.encode("utf-8", "replace")).hexdigest()[:24]


def repo_relative_area(path, cwd):
    """The gate's `area` for a write: the target's directory, REPO-RELATIVE and `/`-separated.

    The area is matched against a proactive `deliberate_coding` receipt, whose own area is
    `dirname(relevant_paths[0])` — i.e. literally what the agent passed, which is naturally a
    repo-relative path (`smoke/dirA`). Claude Code hands this hook an ABSOLUTE file_path, so a raw
    `os.path.dirname` produced `C:/repo/smoke/dirA` and the two could never be equal: the proactive
    downgrade and the deliberate area-unlock silently did nothing for anyone who didn't guess that
    the paths had to be absolute (prod runbook 2026-07-13; deliberation mcp_fd6de1da).

    Sending the repo-relative form makes the common case an EXACT match. Falls back to the
    `/`-normalized absolute dirname when the repo root can't be resolved or the file lies outside
    it — the server reconciles that rooted form against a relative receipt area, so an unresolvable
    root degrades to the old behavior instead of breaking the match.

    'repo-root' is preserved as the sentinel for a file directly at the top level (a receipt for the
    root behaves the same as before).
    """
    d = os.path.dirname(path or "")
    if not d:
        return "repo-root"
    root = _git(["rev-parse", "--show-toplevel"], cwd).strip()
    if root:
        try:
            rel = os.path.relpath(d, root)
        except ValueError:      # different drives on Windows -> not in this repo
            rel = None
        if rel and not rel.startswith(".."):
            rel = rel.replace("\\", "/")
            return "repo-root" if rel == "." else rel
    return d.replace("\\", "/") or "repo-root"


def is_out_of_repo_scope(path, cwd):
    """True ONLY when confident the write target is OUTSIDE the working repo, or under a
    temp/scratch dir — i.e. it cannot be committed/merged, so it cannot SHIP. Used by the
    write gate (P6.3) to suppress out-of-repo / scratchpad false positives: the gate's
    threat model is "review before it ships," and an unversioned file has nothing to ship.

    Fails toward REVIEW: any uncertainty / error -> False (the gate proceeds, doesn't
    suppress). The CALLER keeps firing on a real secret VALUE (auto_trigger) regardless of
    location — a leaked key written anywhere is still a leak (that carve-out is the caller's,
    not this function's).

    Scope is the repo the agent is operating IN (resolved from `cwd`), not whatever repo
    happens to contain the target path — correct for a safety gate (a file inside an
    unrelated repo elsewhere on disk is out-of-scope for THIS session). Audit F-006.
    """
    try:
        if not path:
            return False
        abspath = path if os.path.isabs(path) else os.path.join(cwd or ".", path)
        # realpath (not abspath) so a symlinked temp (/tmp -> /private/tmp on macOS) is
        # resolved consistently on both sides before the prefix compare (audit F-003).
        low = os.path.realpath(abspath).replace("\\", "/").lower().rstrip("/")
        # 1) The git working-tree root is AUTHORITATIVE when resolvable (audit F-001): a file
        #    INSIDE it is in scope — it can be committed/merged and therefore can ship — even
        #    if the whole repo lives under /tmp (CI checkouts, containers). A file outside a
        #    known root is out of scope. The temp heuristic must NOT override this.
        root = (_git(["rev-parse", "--show-toplevel"], cwd) or "").strip()  # F-005: never None
        if root:
            root_low = os.path.realpath(root).replace("\\", "/").lower().rstrip("/")
            return not (low == root_low or low.startswith(root_low + "/"))
        # 2) No git root resolvable (cwd isn't in a repo) -> fall back to the temp/scratch
        #    heuristic so a scratchpad write is still recognized as non-shipping.
        temp_roots = {os.path.realpath(tempfile.gettempdir()).replace("\\", "/").lower().rstrip("/")}
        for env in ("TMPDIR", "TEMP", "TMP"):
            v = os.environ.get(env)
            if v:
                temp_roots.add(os.path.realpath(v).replace("\\", "/").lower().rstrip("/"))
        for t in temp_roots:
            if t and (low == t or low.startswith(t + "/")):
                return True
        return False
    except Exception:
        return False


def is_merge_in_progress(cwd):
    """Wave 3 (§3.3): True when the imminent commit is a MERGE commit (MERGE_HEAD
    present — an in-progress `git merge` about to be concluded by `git commit`,
    or the `git merge` command itself). Uses `git rev-parse --git-path` so
    worktrees / submodule .git files resolve correctly. Fails CLOSED (False) on
    any error — an ordinary commit must never gain the merge-only release."""
    try:
        gp = _git(["rev-parse", "--git-path", "MERGE_HEAD"], cwd)
        if not gp:
            return False
        path = gp.strip()
        if not os.path.isabs(path):
            path = os.path.join(cwd, path)
        return os.path.exists(path)
    except Exception:
        return False


def staged_diff(cwd, command=""):
    """Return the diff the imminent commit will record.

    `git diff --staged` is correct ONLY when files are already staged by a prior
    SEPARATE `git add`. But this is a PreToolUse hook — it fires BEFORE the Bash
    command runs, so `git add X && git commit`, `git commit -a`, and
    `git commit <path>` all leave nothing staged at hook time. The old code then
    saw an empty diff and waved the commit through — the exact gap that let risky
    commits slip past (the whole reason the audit gate "never triggered").

    When nothing is staged, fall back to the full working-tree diff vs HEAD so the
    about-to-be-committed change is still classified. Over-inclusion (flagging a
    tracked change a path-scoped commit won't include) is the SAFE direction for a
    risk gate; under-inclusion (the old behavior) is not.

    Brand-new UNTRACKED files don't appear in `git diff HEAD`, so we ALSO synthesize
    add-diffs for the untracked files THIS commit will stage (design §6.1 loophole
    closure) — but ONLY those. `command` is the Bash command being gated; the
    untracked sweep is scoped to what a `git add` in that same command stages
    (`git add X && git commit` -> just X; `git add .`/`-A` -> all). A bare
    `git commit` / `git commit -a` stages no untracked files, so we sweep NONE —
    otherwise pre-existing untracked working-tree cruft (eval fixtures, screenshots)
    gates an unrelated scoped commit (the over-inclusion bug).

    A `git commit -a` / `git commit <pathspec>` records WORKING-TREE content that is
    NOT in the index (it stages inline, after the hook fires). If something else was
    already staged, `git diff --staged` would be non-empty yet MISS those worktree
    changes -> under-coverage. So when the commit targets the worktree
    (`commit_targets_worktree`), classify `git diff HEAD` (HEAD..worktree, a superset
    of both the index and the inline-staged content) rather than the index alone.
    """
    if commit_targets_worktree(command):
        base = _git(["diff", "HEAD"], cwd)
    else:
        staged = _git(["diff", "--staged"], cwd)
        base = staged if staged.strip() else _git(["diff", "HEAD"], cwd)
    return base + _untracked_diff(cwd, command)


# Max bytes we'll read from an untracked file to synthesize a diff. Large/binary
# files are skipped — the gate is for source-shaped changes, and we never want a
# blocking hook to choke on a multi-MB artifact. Raised 200KB -> 1MB (single-call
# redesign, 2026-07-02): the prior cap silently skipped large-but-legit new source
# files, so a floor change in one wouldn't fire. 1MB covers real source; genuine
# multi-MB artifacts are still skipped.
_UNTRACKED_MAX_BYTES = 1_000_000

# Sentinel: a `git add .` / `-A` / `--all` stages every untracked file.
_ADD_ALL = "ALL"


# ---------------------------------------------------------------------------
# Git command parsing — robust to GLOBAL options before the subcommand.
# `git -C <path> commit`, `git --no-pager commit`, `git -c k=v commit`,
# `sudo git commit`, ... all invoke commit, but a naive `git\s+commit` regex
# misses them. If the gate's command filter / add-scope / worktree check were
# fooled by a global option, the commit would BYPASS THE GATE ENTIRELY (audit
# F-001, 2026-06-17). All three share this parser so none of them is fooled.
# ---------------------------------------------------------------------------

# git GLOBAL options (BEFORE the subcommand) that consume the NEXT token as a value
# (space-separated form). The `=`-joined form (`--git-dir=.git`, `-C=foo`) is a single
# token that starts with `-`, so it's handled by the generic flag branch (i += 1) — do
# NOT special-case it here, or the embedded value would wrongly skip the following token.
_GIT_GLOBAL_VALUE_OPTS = frozenset({
    "-c", "-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix",
    "--config-env", "--exec-path",
})

# `git commit` options that CONSUME the next token as their value, so that token is not
# a pathspec. (`=`-joined forms like `--message=x` and short-bundled `-mx` are single
# tokens that start with `-`, so they're handled as flags without special-casing.)
_COMMIT_VALUE_OPTS = frozenset({
    "-m", "--message", "-F", "--file", "-C", "--reuse-message", "-c", "--reedit-message",
    "--author", "--date", "-t", "--template", "--fixup", "--squash", "--trailer",
})

# Identity sentinel (compared with `is`, NOT ==): a segment that mentions git + a target
# subcommand but can't be shlex-parsed, so callers take their safe/over-inclusive branch.
_GIT_PARSE_ERROR = object()


def _is_git_token(tok):
    """True if `tok` is the git executable — `git`, an absolute path like `/usr/bin/git`,
    or a Windows `...\\git.exe`. Structural (basename) so we don't lean on the parse-error
    sentinel for a fully-qualified git path (audit F-001)."""
    base = tok.replace("\\", "/").rsplit("/", 1)[-1]
    return base in ("git", "git.exe")


def _segment_git_subcommand_args(seg, subcommands):
    """For ONE shell segment, return (subcommand, args_after_it) if it invokes
    `git <sub>` for some sub in `subcommands` — skipping leading wrapper tokens
    (sudo / env) and git GLOBAL options (incl. value-taking `-C <path>` / `-c k=v`).
    Return (None, None) otherwise. Raises ValueError if the segment can't be parsed."""
    toks = shlex.split(seg.strip(), posix=True)  # may raise ValueError
    gi = next((idx for idx, t in enumerate(toks) if _is_git_token(t)), None)
    if gi is None:
        return (None, None)
    i = gi + 1
    while i < len(toks):
        t = toks[i]
        if t in subcommands:
            return (t, toks[i + 1:])
        if t in _GIT_GLOBAL_VALUE_OPTS:
            i += 2  # global option + its value token
            continue
        if t.startswith("-"):
            i += 1  # other global flag (--no-pager, --paginate, --bare, ...)
            continue
        return (None, None)  # a different git subcommand (git log / status / ...)
    return (None, None)


def _iter_git_subcommands(command, subcommands):
    """Yield (subcommand, args_after) for each shell segment of `command` that invokes
    `git <sub>` for sub in `subcommands`. A segment that mentions git + a target
    subcommand but is unparseable yields (_GIT_PARSE_ERROR, None) so callers fail safe."""
    # NOTE: this split is not quote-aware (a `;`/`&&` inside a quoted commit message splits
    # the segment), but that only makes the segment unparseable -> the sentinel below fires
    # the gate (safe over-inclusion), same as the prior regex approach.
    for seg in re.split(r"&&|\|\||;|\n", command or ""):
        try:
            sub, args = _segment_git_subcommand_args(seg, subcommands)
        except ValueError:
            # unparseable segment: fire safe iff it plausibly invokes `git <sub>`. Word-
            # boundary `git` (not a bare substring, so `/home/digit/...` doesn't match).
            if re.search(r"\bgit\b", seg) and any(s in seg for s in subcommands):
                yield (_GIT_PARSE_ERROR, None)
            continue
        if sub is not None:
            yield (sub, args)


def command_invokes_git(command, subcommands):
    """True if `command` invokes `git <sub>` for any sub in `subcommands` — robust to git
    GLOBAL options (so `git -C repo commit` is NOT mistaken for a non-commit). Used by the
    audit gate's command filter; a parse error counts as a match (the gate then fires and
    classifies the real diff — the safe direction)."""
    for _ in _iter_git_subcommands(command, subcommands):
        return True
    return False


def parse_git_add_targets(command):
    """What untracked paths will a `git add` in `command` stage?

    None when there is no `git add` (bare `git commit` / `-a` stage nothing untracked).
    _ADD_ALL for `git add .` / `-A` / `--all`. Else the explicit path tokens. Robust to
    git global options. Conservative: an unparseable `git add` -> _ADD_ALL (recall-safe).
    """
    saw_add = False
    targets = []
    for sub, args in _iter_git_subcommands(command, ("add",)):
        if sub is _GIT_PARSE_ERROR:
            return _ADD_ALL  # unparseable -> sweep all (recall-safe)
        saw_add = True
        for t in args:
            if t in (".", "-A", "--all", ":/", "*"):
                return _ADD_ALL
            if t.startswith("-"):
                continue  # other flags: -u (tracked only), -p, -f, -v, ...
            targets.append(t)
    return targets if saw_add else None


def commit_targets_worktree(command):
    """True if a `git commit` in `command` records WORKING-TREE content not in the index —
    `git commit -a/--all` (incl. short bundles like `-am`) or `git commit <pathspec>`. For
    those the staged diff under-covers (see staged_diff). A bare `git commit` /
    `git commit -m ...` records only the index. Robust to git global options. Bias-to-True
    (unparseable / ambiguous -> True; over-inclusion is the SAFE direction for a risk gate).
    """
    for sub, args in _iter_git_subcommands(command, ("commit",)):
        if sub is _GIT_PARSE_ERROR:
            return True
        i = 0
        while i < len(args):
            t = args[i]
            if t == "--":
                return i + 1 < len(args)  # paths after `--`
            if t in ("-a", "--all"):
                return True
            if re.fullmatch(r"-[A-Za-z]*a[A-Za-z]*", t):  # short bundle with 'a' (-am, -av)
                return True
            if t in _COMMIT_VALUE_OPTS:
                i += 2  # skip the option AND its value token
                continue
            if t.startswith("-"):
                i += 1  # other no-value flag (-v, --amend, --no-verify, -q, -S, ...)
                continue
            return True  # a bare (non-flag) token = a pathspec -> records worktree content
    return False  # bare `git commit` (index-only) or no commit segment


def _add_covers(spec, rel):
    """True if an untracked `rel` path is staged by the parsed add `spec`."""
    if spec == _ADD_ALL:
        return True
    rel_n = rel.replace("\\", "/")
    for p in spec or []:
        p_n = p.replace("\\", "/").rstrip("/")
        if rel_n == p_n or rel_n.startswith(p_n + "/"):
            return True
    return False


def _untracked_diff(cwd, command=""):
    """Synthesize add-diffs for the untracked files THIS commit stages (scoped to
    the command's `git add`; see staged_diff). Brand-new risky files added by the
    commit are still classified; unrelated working-tree cruft is not."""
    spec = parse_git_add_targets(command)
    if spec is None:
        return ""  # no `git add` in this command -> no untracked files staged
    porcelain = _git(["status", "--porcelain", "--untracked-files=all"], cwd)
    if not porcelain.strip():
        return ""
    out = []
    for line in porcelain.splitlines():
        if not line.startswith("?? "):
            continue
        rel = line[3:].strip().strip('"')
        if not rel or rel.endswith("/"):
            continue
        if not _add_covers(spec, rel):
            continue
        try:
            full = os.path.join(cwd or ".", rel)
            if os.path.getsize(full) > _UNTRACKED_MAX_BYTES:
                continue
            with open(full, "r", encoding="utf-8", errors="strict") as fh:
                content = fh.read()
        except Exception:
            continue  # binary / unreadable / vanished — skip (fail open)
        out.append(synth_write_diff(rel, content))
    return "".join(out)


_M1_FETCH_MAX_BYTES = 1_000_000  # M1 two-stage: don't read a pathological file for a co-signal check


def file_content_fetcher(cwd):
    """Return a callable `path -> str | None` for classify_diff's Mechanism-M1 two-stage
    co-occurrence (e.g. the CI pwn-request floor confirming a pre-existing `pull_request_target`
    trigger that lives OUTSIDE the diff). The callable reads the file's CURRENT on-disk content
    relative to `cwd` and returns it, or None on ANY failure (missing / too large / non-UTF-8 /
    permission / vanished) so the classifier's fail-safe FIRES the floor rather than silently
    missing it — a failed read must NEVER be mistaken for 'co-signal absent'. Best-effort; the
    callable never raises. Mirrors the untracked-file read above (Rule 8 — one read pattern).

    Hardening (audit mcp_3aa59d3a): reads are CONFINED to the repo root (realpath + commonpath —
    a `../` traversal or a symlink that escapes cwd resolves outside base -> None -> fail-safe, never
    reads e.g. /etc/passwd); only REGULAR files are opened (a named pipe / device -> None, so the
    PreToolUse hook can't hang on a blocking read); size-capped. `errors="strict"` is tied to the
    CI-YAML scope this fetcher serves (a non-UTF-8 byte in a workflow is corruption/adversarial ->
    None -> fail-safe); revisit if the fetched scope ever expands to arbitrary source files (it would
    then risk fail-safe fatigue). Commit-gate note: reads the WORKING-TREE file, not the staged blob —
    for a pre-existing unchanged co-signal they're equal; a stage-then-worktree-revert divergence is a
    documented residual (the backstop compensates). See docs/MCP/Classifier/PHASE3-M1-DESIGN.md."""
    base = os.path.realpath(cwd or ".")  # realpath (not abspath) so a symlinked cwd stays consistent

    def _fetch(path):
        try:
            full = os.path.realpath(os.path.join(base, str(path).replace("/", os.sep)))
            # Confine to the repo root: a ../ traversal or symlink escape -> outside base -> fail-safe.
            # commonpath raises on different drives (Windows) -> caught below -> None -> fail-safe.
            if os.path.commonpath([full, base]) != base:
                return None
            st = os.stat(full)
            if not stat.S_ISREG(st.st_mode):
                return None  # named pipe / device / directory -> don't open (avoid a blocking read)
            if st.st_size > _M1_FETCH_MAX_BYTES:
                return None
            with open(full, "r", encoding="utf-8", errors="strict") as fh:
                return fh.read()
        except Exception:
            return None  # missing / binary / unreadable / vanished / off-repo -> None -> fail-safe

    return _fetch


# Gate self-mutation detection (`is_gate_self_mutation` / `diff_touches_gate_self`)
# and the synthesized self-coverage hash (`gate_self_coverage_hash`) now live in the
# vendored `risk_classifier` module (imported above), so the SERVER receipt writer
# computes the SAME hash for the SAME gate-self diff. Writes targeting the gate's own
# config/hooks can disable it from inside (raise the threshold, empty the signal sets,
# unhook it) — always-risky regardless of content (design §6.1, audit F-005).


def synth_write_diff(path, added_text):
    """Synthesize a unified diff for a Write/Edit so the classifier (which speaks
    unified-diff) can score the content being written. All-adds shape (`@@ -0,0
    +1,N @@`). CORRECT for a brand-new file (a new file's natural diff IS all-adds);
    for an EDIT it over-marks unchanged context as added — see build_change_diff, which
    computes a real delta so the fire's hunk hashes match a natural agent gate_diff
    (write-gate-deadlock-fix-v2, Option D)."""
    if not path:
        path = "unknown"
    lines = (added_text or "").splitlines()
    body = "\n".join("+" + ln for ln in lines)
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n{body}\n"
    )


def _read_file_safe(path, cwd):
    """The current on-disk contents of `path` (resolved against `cwd`), or None if it
    doesn't exist / isn't readable. Used to compute a real delta for an OVERWRITE Write.
    The write is BLOCKED (PreToolUse), so this baseline is stable between the deny and the
    retry; if it shifts, the delta hash changes and coverage misses → re-review (fail
    CLOSED), never a false release."""
    try:
        p = path if os.path.isabs(path) else os.path.join(cwd or os.getcwd(), path)
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
    except Exception:
        pass
    return None


def _unified_delta(path, old_text, new_text):
    """A real unified diff of old_text -> new_text (only genuinely-changed lines are +/-),
    so the classifier's per-hunk content_hash is over the SAME added-line set a natural
    agent gate_diff produces. Falls back to all-adds when there's no computable delta."""
    diff = difflib.unified_diff(
        (old_text or "").splitlines(), (new_text or "").splitlines(),
        fromfile="a/" + (path or "unknown"), tofile="b/" + (path or "unknown"), lineterm="")
    text = "\n".join(diff)
    # An empty/degenerate delta (identical texts, or difflib produced no @@) → all-adds so
    # the content still classifies (the empty-content guard already ran upstream).
    return text if (text.strip() and "@@" in text) else synth_write_diff(path, new_text)


def _hunk_body(delta_text):
    """Everything from a delta's first `@@` on — i.e. the hunks with the file header removed,
    so several deltas over the SAME file can share one header (see build_change_diff)."""
    lines = (delta_text or "").splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("@@"):
            return "\n".join(lines[i:])
    return ""


def build_change_diff(inp, path, content):
    """Write-gate-deadlock-fix-v2 (Option D): synthesize the diff the classifier scores so
    its hunk hashes MATCH what a natural agent gate_diff produces — the root-cause fix for
    the floor write-gate deadlock (the old synth_write_diff all-adds shape never matched a
    delta the agent could author). Reuses the commit gate's principle: classify the REAL
    change, not a synthetic all-adds block.
      - Edit:      difflib(old_string -> new_string)  (both are in tool_input)
      - MultiEdit: per-edit difflib, concatenated (one hunk per edit)
      - Write:     overwrite -> difflib(on-disk -> content); new file -> all-adds (correct)
    Fails safe to synth_write_diff(path, content) on any error (never raises)."""
    ti = inp.get("tool_input") or {}
    tool = inp.get("tool_name")
    cwd = inp.get("cwd")
    try:
        # PrebuiltDiff: the host adapter already produced a unified diff (e.g. Codex
        # apply_patch, whose input IS a patch). Classify it verbatim — re-deriving a
        # delta would change the hunk hashes a natural agent gate_diff produces.
        if tool == "PrebuiltDiff":
            pre = (ti.get("prebuilt_diff") or "").strip()
            if pre and "@@" in pre:
                return pre if pre.endswith("\n") else pre + "\n"
            return synth_write_diff(path, content)
        if tool == "Edit":
            return _unified_delta(path, ti.get("old_string", "") or "", content)
        if tool == "MultiEdit":
            # ONE file header, then every edit's @@ block — the shape git itself emits for a
            # single file with several changed regions. Concatenating the per-edit diffs
            # WHOLE (headers and all) instead puts a `+++ b/<path>` line INSIDE the preceding
            # hunk, where the classifier — correctly — reads any `+`-prefixed line as added
            # content (its `not in_hunk` header guard is what lets a PEM `-----BEGIN ...` or a
            # YAML `---` inside a hunk count as content). That polluted every hunk but the
            # last, so the same edit hashed differently alone vs. followed by another: the
            # write gate stored the polluted hash, audit_coding PASSed over it, and then the
            # COMMIT gate — classifying the real git diff — computed the clean hash, found no
            # coverage, and re-blocked the code it had just passed.
            bodies = []
            for e in (ti.get("edits") or []):
                o = e.get("old_string", "") or ""
                n = e.get("new_string", "") or ""
                if n.strip() or o.strip():
                    delta = _unified_delta(path, o, n)
                    body = _hunk_body(delta)
                    if delta.strip() and not body.strip():
                        # An edit produced content we could not turn into a hunk. Dropping it
                        # would UNDER-classify — the one direction that is a real bypass — and
                        # the global `@@` check below would not notice, because the OTHER edits
                        # still supply a `@@`. Fall back to all-adds over the whole content: it
                        # over-marks, which only ever over-blocks.
                        return synth_write_diff(path, content)
                    bodies.append(body)
            joined = "\n".join(b for b in bodies if b.strip())
            if "@@" not in joined:
                return synth_write_diff(path, content)
            p = path or "unknown"
            return "--- a/%s\n+++ b/%s\n%s\n" % (p, p, joined)
        if tool == "Write":
            old = _read_file_safe(path, cwd)
            if old is not None:
                return _unified_delta(path, old, content)   # overwrite → real delta
            return synth_write_diff(path, content)           # new file → all-adds is correct
    except Exception:
        pass
    return synth_write_diff(path, content)


# ---------------------------------------------------------------------------
# Backend coverage calls (fail-open on any error)
# ---------------------------------------------------------------------------

def _post(cfg, path, body):
    """POST JSON to the backend. Returns the parsed dict, or None on any error
    (the caller fails open)."""
    try:
        # Cross-platform: stamp WHICH host this gate ran under on every POST.
        # The server may ignore it today; when the fires table gains a platform
        # column, per-platform gate-health lights up with NO client re-release
        # (plan §2.5 forward-compatibility, applied to ourselves). PRIVACY,
        # stated precisely (audit mcp_cd6dd4a6 F-002): the value is the host
        # ADAPTER name from our own fixed registry ("claude_code", "codex",
        # "cursor_cli", ...) — a coarse host-APP identifier. It is NEVER a
        # machine hostname, username, or anything derived from the user's
        # environment. Server-side ingestion must still allowlist/normalize
        # when the column lands (F-006) — never trust the wire value blindly.
        if isinstance(body, dict) and "platform" not in body:
            try:
                body = dict(body, platform=host_registry.current().name)
            except Exception:
                pass  # never let telemetry stamping break a gate POST
        req = urllib.request.Request(
            cfg["base_url"] + path,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + cfg["token"],
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _classifier_meta(classification, session_id=None):
    """Fire-time classifier metadata to ride along on a coverage POST so the server
    can mint a COMPLETE gate-fire context (Step 0, design §2.2): `floor_class` is
    derived from `risk_categories` AT FIRE TIME, so the server needs them here, not
    reconstructed on a later skip. Privacy: this adds NO source content — only the
    classifier's own labels (the SAME ones the deny message already shows the agent
    via `gate_signal_line`) plus the opaque `session_id`.

    Additive: when metadata is present the POST body is a SUPERSET of the old
    {repo,hunks} / {repo,area,session_id} body, so the OLD body shape is the subset —
    an old server must simply ignore the extra keys. That holds because the endpoints
    deserialize permissively (`request.get_json()` + `.get()`, no strict schema /
    reject-extra). Empties are omitted (and `score=0` is preserved via `is not None`),
    and this never raises — a missing/odd field is dropped, not coerced — so metadata
    enrichment can't break the fail-open contract before `_post()` runs."""
    meta = {}
    if isinstance(classification, dict):
        cats = classification.get("risk_categories")
        if cats:
            meta["risk_categories"] = list(cats)  # shallow copy — never alias caller state
        cver = classification.get("classifier_version")
        if cver:
            meta["classifier_version"] = cver
        score = classification.get("score")
        if score is not None:  # keep 0 — truthiness would drop a legitimate score of 0
            meta["score"] = score
    if session_id:
        meta["session_id"] = session_id
    return meta


def _hunk_hashes(classification):
    """The risky-hunk content hashes for a classification (empty list when none)."""
    if not classification:
        return []
    return [h["content_hash"] for h in classification.get("hunks", [])
            if h.get("content_hash")]


def _hunk_evidence(classification):
    """Per-hunk evidence fields the server stores on the gate-fire + uses for coverage and
    skip verification: path-class (§4.B), category+confidence (§4.E floor + tightness),
    normalized hash (Phase 9 drift-tolerant binding), and @@ structural ranges (Phase 9.1
    coarse fallback). SHARED by the commit gate (check_audit_coverage) and the write gate
    (check_deliberate_unlock) so both mint fires with identical, drift-tolerant hunk metadata
    (Rule 8 — one builder, not two). Additive; an old server ignores every field."""
    body = {}
    if not classification:
        return body
    hunks = classification.get("hunks", [])
    pcs = [{"content_hash": h["content_hash"], "path_class": h.get("path_class")}
           for h in hunks if h.get("content_hash") and h.get("path_class")]
    if pcs:
        body["hunk_path_classes"] = pcs
    cats = [{"content_hash": h["content_hash"], "category": h.get("category"),
             "confidence": h.get("confidence")}
            for h in hunks if h.get("content_hash") and h.get("category")]
    if cats:
        body["hunk_categories"] = cats
    nhs = [{"content_hash": h["content_hash"], "normalized_hash": h.get("normalized_hash"),
            "filetype": h.get("filetype")}
           for h in hunks if h.get("content_hash") and h.get("normalized_hash")]
    if nhs:
        body["hunk_normalized"] = nhs
        body["norm_version"] = NORM_VERSION
    hst = [{"content_hash": h["content_hash"], "old_start": h["structural"]["old_start"],
            "old_count": h["structural"]["old_count"], "new_start": h["structural"]["new_start"],
            "new_count": h["structural"]["new_count"]}
           for h in hunks if h.get("content_hash") and isinstance(h.get("structural"), dict)]
    if hst:
        body["hunk_structural"] = hst
    return body


def _uncovered_bucket_line(uncovered_hunks):
    """The one line that makes a block legible: of the hunks STILL uncovered, how many are FLOOR
    and how many are ordinary, and which tool clears each. Returns "" when there's nothing left.

    The two buckets have different releases and neither tool can do the other's job:
      - FLOOR (auth / secrets / money / migrations / removed-guard) → audit_coding PASS,
        confirm_floor / synthesize_coding (a low-risk verdict), or accept_risk_no_review.
      - NON-floor risky → audit_coding PASS, or a one-line record_gate_skip judgment reason.
    A floor tool CANNOT release a non-floor hunk (receipt_coverage.audit_covered_hashes ignores
    synthesize/accept_risk receipts), which is precisely the trap the 2026-07-10 deadlock fell into:
    the agent cleared the floor, kept re-running floor tools against a non-floor remainder, watched
    the count refuse to move, and proposed disabling the gates."""
    if not uncovered_hunks:
        return ""
    # EFFECTIVE floor = floor by category AND not demoted by its path (`floor_exempt`) — the same
    # definition the server enforces (`_floor_hashes_from_body`) and that deliberate_gate already
    # uses to choose WHICH message to print. Counting by category alone (the bug this fixes, found
    # by the 2026-07-13 prod runbook) over-states the floor: a `removed_guard` under `tests/` is
    # exempt, so the server sees no floor hunk while this line claimed one. That is not cosmetic —
    # it routes the agent to floor tools that then REFUSE (`gate_skip_accept_risk_not_floor`,
    # confirm_floor releasing nothing), which is the exact "the count won't move" trap this bucket
    # line exists to prevent. The two definitions must not drift again.
    def _is_effective_floor(h):
        return (is_hard_floor(h.get("category"))
                and not floor_exempt(h.get("category"), h.get("path_class")))

    floor_n = sum(1 for h in uncovered_hunks if _is_effective_floor(h))
    # Counted, not subtracted (audit mcp_2ac350d4 F-002): `len(hunks) - floor_n` is only correct
    # while the split is strictly binary. If a third tier is ever added (a soft-floor / warning
    # class), the subtraction would silently fold it into "non-floor" and mis-route the agent again.
    plain_n = sum(1 for h in uncovered_hunks if not _is_effective_floor(h))
    parts = ["Still uncovered: %d floor, %d non-floor." % (floor_n, plain_n)]
    if floor_n and plain_n:
        parts.append("These clear SEPARATELY — an `audit_coding` PASS covers BOTH in one call "
                     "(simplest here). Otherwise clear the floor (`confirm_floor` / "
                     "`synthesize_coding` / `accept_risk_no_review`) AND the non-floor "
                     "(`record_gate_skip` with a judgment reason) — a floor tool does NOT release "
                     "a non-floor hunk, so neither alone will unblock this.")
    elif floor_n:
        parts.append("FLOOR only: `audit_coding` PASS, or `confirm_floor` (FREE) / "
                     "`synthesize_coding` if you believe it mis-fired, or "
                     "`accept_risk_no_review` as a logged last resort.")
    else:
        parts.append("NON-floor only: `audit_coding` PASS, or `record_gate_skip` with a judgment "
                     "reason (e.g. false_positive_not_risky) — free, one line. Floor tools "
                     "(confirm_floor / accept_risk) release NOTHING here.")
    return "  " + " ".join(parts) + "\n"


def release_options_block(check_response):
    """Gate-usability section 3.5, closing the loop: the server sends machine-readable
    release_options (the exact releasing calls, gate_context_id pre-filled) on every
    block — render them verbatim so the AGENT actually receives the structure, not
    just this hook's prose. Compact one-line-JSON per option; returns "" when the
    server (old build) didn't send any."""
    opts = (check_response or {}).get("release_options")
    if not isinstance(opts, list) or not opts:
        return ""
    lines = ["Release options (machine-readable, gate_context_id pre-filled):"]
    for o in opts[:6]:
        try:
            lines.append("  " + json.dumps(o, sort_keys=True))
        except Exception:
            continue
    return "\n".join(lines) + "\n"


def transparency_block(classification, check_response, max_items=8):
    """Fix 2A: a short, LOCAL itemization of what fired vs. what's already reviewed, for the
    deny message. Uses the client-side `matched` span (identifier/keyword + 1-based line; secret
    VALUES were redacted to category+line by the classifier). The agent reasons about the minimal
    NEW surface instead of re-litigating covered code. Purely local — `matched` is never sent to
    the server (`_hunk_evidence` posts hashes only). Returns "" when there's nothing to show.

    When the server told us which hunks are `uncovered`, itemize those (the ones that actually
    need review) and note the covered count; otherwise list every fired hunk."""
    if not isinstance(classification, dict):
        return ""
    hunks = classification.get("hunks") or []
    if not hunks:
        return ""
    uncovered_set = None
    if isinstance(check_response, dict) and isinstance(check_response.get("uncovered"), list):
        uncovered_set = set(check_response["uncovered"])
    if uncovered_set is not None:
        target = [h for h in hunks if h.get("content_hash") in uncovered_set]
        covered_n = len(hunks) - len(target)
        header = ("What fired (%d hunk%s; %d already reviewed, %d need review):\n"
                  % (len(hunks), "" if len(hunks) == 1 else "s", covered_n, len(target)))
        # 2026-07-12: split what's LEFT into floor vs non-floor. Without this the agent sees only
        # "N need review" and cannot tell WHICH tool can clear them — in the 2026-07-10 deadlock it
        # covered every floor hunk, kept reading a floor-flavored block, and re-ran floor tools that
        # by construction cannot touch a non-floor hunk. The two buckets have DIFFERENT releases, so
        # a block that doesn't name the bucket is a block with no legible exit.
        header += _uncovered_bucket_line(target)
    else:
        target = hunks
        header = "What fired (%d hunk%s):\n" % (len(hunks), "" if len(hunks) == 1 else "s")
    lines = []
    for h in target[:max_items]:
        cat = h.get("category") or "risk"
        path = h.get("path") or "?"
        m = h.get("matched") or {}
        ln = m.get("line")
        tok = m.get("token")
        loc = "%s:%s" % (path, ln) if ln else path
        lines.append(("  - %s - matched `%s` at %s" % (cat, tok, loc)) if tok
                     else ("  - %s at %s" % (cat, loc)))
    extra = len(target) - max_items
    if extra > 0:
        lines.append("  - ... +%d more" % extra)
    return (header + "\n".join(lines) + "\n") if lines else ""


def check_audit_coverage(cfg, repo, hunk_hashes, classification=None, session_id=None,
                         is_merge=False):
    body = {"repo": repo, "hunks": hunk_hashes}
    if is_merge:
        # Wave 3 (§3.3): stamp the fire as a merge so branch_already_reviewed is
        # admissible on it (server-enforced; additive — old servers ignore it).
        body["is_merge"] = True
    body.update(_classifier_meta(classification, session_id))
    # gate_tightness (GATE-TIGHTNESS-DESIGN.md §6b): send the active tightness so the server can
    # label the minted fire's gate_decision ('advisory' when uncovered-but-all-advisory under
    # 'focused') and keep fire-rate telemetry honest — otherwise it would mint an orphan 'deny'
    # fire for a change the client only advises on. Additive; an old server ignores it (and just
    # keeps minting 'deny', harmless). The CLIENT still makes the actual gate decision locally
    # (audit_decision) — this field is for the server's telemetry labeling, not the gate outcome.
    tightness = cfg.get("gate_tightness") if isinstance(cfg, dict) else None
    if tightness:
        body["gate_tightness"] = tightness
    # Per-hunk evidence (path-class / category+confidence / normalized / structural) — shared
    # builder so the commit + write gates stay in lockstep.
    body.update(_hunk_evidence(classification))
    return _post(cfg, "/api/mcp/receipts/check", body)


def check_deliberate_unlock(cfg, repo, area, session_id, classification=None,
                            gate_type="deliberate"):
    # session_id already rides in the body, so don't duplicate it via _classifier_meta.
    body = {"repo": repo, "area": area, "session_id": session_id}
    body.update(_classifier_meta(classification, session_id=None))
    # The write gate splits into a deliberate tier (high-confidence fork) and a
    # synthesize tier (borderline); the caller knows which fired, so the fire records
    # the right gate_type. The server defaults to 'deliberate' on anything else.
    if gate_type in ("deliberate", "synthesize"):
        body["gate_type"] = gate_type
    # Write-gate-deadlock fix: a Write/Edit is FINISHED code, so its natural review is
    # `audit`. Send the risky-hunk hashes + the same per-hunk evidence the commit gate sends,
    # so the server can (a) release the write gate on audit/SYNTH_CONFIRM hunk coverage and
    # (b) mint a HUNK-bound fire (so a later gate_context_id-bound audit_coding/synthesize_coding
    # resolves these hunks and its coverage releases the retry). Additive: an old server ignores
    # these and runs the pure area-unlock path. The deliberate area-unlock stays valid too.
    hh = _hunk_hashes(classification)
    if hh:
        body["hunk_hashes"] = hh
        body.update(_hunk_evidence(classification))
        tightness = cfg.get("gate_tightness") if isinstance(cfg, dict) else None
        if tightness:
            body["gate_tightness"] = tightness
    return _post(cfg, "/api/mcp/receipts/deliberate-check", body)


# ---------------------------------------------------------------------------
# Pure decision logic (unit-tested in tests/test_gate_logic.py)
# ---------------------------------------------------------------------------

def _uncovered_risky_hunks(classification, check_response):
    """Return (uncovered_hunks, coverage_known).

    `coverage_known` is False when the server did NOT return an `uncovered` set (`None` → an old
    server / malformed response → the coverage state is AMBIGUOUS). On ambiguity the caller must
    NOT downgrade — it blocks (fail-safe: BLOCK on ambiguous coverage; audit F-A/F-B). This is a
    deliberate distinction from an explicit empty list `[]`, which is the server unambiguously
    confirming full coverage (→ no uncovered hunks, known=True)."""
    hunks = classification.get("hunks") or []
    uncovered_hashes = check_response.get("uncovered") if isinstance(check_response, dict) else None
    if uncovered_hashes is None:
        return (hunks, False)  # missing set → ambiguous → caller must block, not downgrade
    uc = set(uncovered_hashes)
    return ([h for h in hunks if h.get("content_hash") in uc], True)


# The reason string every "we couldn't issue a gate context" release carries. One constant so the
# commit gate, the write gate, the gate-self path and the borderline tier can't drift.
NO_GATE_CONTEXT_ALLOW = (
    "TruVerifAI blocked this change but could not issue a gate context (our failure, not yours) — "
    "allowing it through unreviewed rather than trapping you. Nothing is required of you. If you "
    "want the review anyway, run audit_coding with gate_repo + gate_diff."
)


def gate_context_missing(check_response):
    """True when the server BLOCKED a change and DECLARED that it could not issue a gate context —
    i.e. WE failed, and we said so.

    The gate_context_id is minted best-effort (mcp_user_routes._mint_gate_fire returns None on any
    failure — a DB fault, a serialization error) and the block is returned regardless. Since
    2026-07-12 the gate_context_id is the ONLY handle that releases a skip, so a block without one
    leaves the agent with no skip at all: it would have to buy a full audit_coding review to pay
    for OUR database hiccup. That is the gate punishing the user for our bug, and an agent that
    keeps hitting an unreleasable block is an agent that tells the user to disable the gates.

    So this fails OPEN — the same posture the gate already takes when our server is unreachable
    entirely (check_response is None → allow). A half-broken server must never be STRICTER than a
    fully broken one; that would be an incoherent safety story.

    POSITIVE SIGNAL, not absence (owner decision 2026-07-12). We key on the server explicitly
    asserting `gate_context_minted: false`, NOT on the id merely being absent. An absent key is a
    WEAK signal — a dropped field, a proxy rewriting the body, an unexpected response shape, or a
    backend rolled back past gate-fire minting would all read as "we failed" and SILENTLY switch
    the gates off, which is the one failure that never shows up in telemetry. Requiring the server
    to say it out loud means an unknown shape denies (the status quo) and only a declared failure
    opens. Deploy the SERVER before republishing the plugin: new hook + old server (no field) just
    denies as it does today, which is the safe skew direction.

    NOT agent-forgeable: this reads OUR OWN server's HTTPS response inside the hook process. The
    agent never sees it, supplies nothing to it, and has no argument that reaches it — unlike
    record_gate_skip, where the agent passes the id itself and is therefore REFUSED without one.
    (An agent that could forge this response could already forge `covered: true` and release
    everything, so it adds no attack surface.)"""
    return isinstance(check_response, dict) and check_response.get("gate_context_minted") is False


# Fail-open sentinels (2026-07-23 dev-setup defect follow-up): when the coverage /
# unlock POST fails, the gates ALLOW by design ("never trap the agent") — but that
# outage used to be COMPLETELY SILENT (the plain-allow path drops `detail`), which is
# how the gates ran dark against a wrong backend URL for an entire smoke session with
# nobody noticing. The entrypoints compare the returned detail against these exact
# constants (sentinel equality) and surface a NON-blocking advisory, so fail-open stays fail-open but
# never invisible.
FAIL_OPEN_AUDIT_DETAIL = "coverage check unavailable; failing open"
FAIL_OPEN_WRITE_DETAIL = "unlock check unavailable; failing open"


def audit_decision(classification, check_response, force_risky=False, tightness=None):
    """Return (action, detail). action ∈ {'allow', 'allow_warn', 'advise', 'deny'}.

    - not risky → allow.
    - network/None response → allow (FAIL-OPEN; never block on our infra).
    - covered → allow.
    - recent_pass (escape valve) → allow_warn (a recent audit passed; hashes
      didn't align, but we don't deadlock).
    - `tightness='focused'` AND every uncovered risky hunk is advisory-class (non-floor
      low-confidence + soft-floor) → advise (non-blocking, model-visible; GATE-TIGHTNESS-DESIGN.md).
    - else → deny (route the agent to audit_coding).

    `force_risky` (gate self-mutation, §6.1, audit F-005): treat the change as
    risky even if the classifier found nothing, so a commit touching the gate's
    own config/hooks ALWAYS requires a review — but is RELEASABLE (covered /
    recent_pass / fail-open), not the old unconditional deny (which made the gate's
    own files un-maintainable through the gate). The tightness downgrade NEVER applies to a
    gate-self change — it always blocks (loosening gate-self would be privilege escalation).
    """
    if not classification.get("risky") and not force_risky:
        return ("allow", "no risky hunks")
    if check_response is None:
        return ("allow", FAIL_OPEN_AUDIT_DETAIL)
    # `covered` is only meaningful when there are hunks to cover. A gate-self
    # change may classify to zero risky hunks, and "all of [] covered" is vacuously
    # true — don't let that wave it through; require recent_pass (a real review).
    if classification.get("hunks") and check_response.get("covered"):
        return ("allow", "covered by a prior audit")
    # Phase 9: a FLOOR hunk that's uncovered must NOT release on the recent_pass valve (an
    # UNRELATED recent audit in this repo) — that was the Phase-8 floor bypass. A floor change
    # needs its OWN coverage (a real review of THIS change — now drift-tolerant via the
    # gate_context_id binding, so a cosmetically-drifted gate_diff still releases it). recent_pass
    # still releases a NON-floor change (the hash-misalignment deadlock-avoidance it exists for).
    # The post-review-deadlock cell (floor_uncovered + recent_pass — reviewed but coverage still
    # missed) is caught by the hook's maybe_human_override BEFORE this deny, so a genuinely
    # reviewed floor change is never hard-trapped (it asks a human instead).
    #   `is not True` (NOT `is False`) is deliberate: a response that OMITS floor_uncovered — a
    #   gate-self change (no floor category), a non-floor change, or an old server — must still
    #   release on recent_pass (else gate-self/non-floor recent_pass deadlocks). The server always
    #   sends a proper bool floor_uncovered when uncovered hunks exist (it's set from `any(...)`),
    #   so a real floor change always reads True here; the only "fail-open" case (a non-bool
    #   truthy) is a server bug, not a reachable client state.
    #   ACCEPTED EXCEPTION (audit F-003): a None response (our gate SERVER unreachable) already
    #   fail-OPENs above (no-deadlock invariant) — a floor change can ship on a gate-server outage.
    if check_response.get("recent_pass") and check_response.get("floor_uncovered") is not True:
        return ("allow_warn", "a recent audit passed but coverage could not be "
                              "confirmed (hash misalignment) — allowing")
    # gate_tightness (GATE-TIGHTNESS-DESIGN.md §3/§6b): under 'focused', a commit whose UNCOVERED
    # risky hunks are ALL advisory-class (non-floor low-confidence + soft-floor) downgrades from a
    # hard block to a non-blocking, model-visible advisory. A single blocking-class uncovered hunk
    # (hard-floor at any confidence, OR non-floor HIGH) keeps the deny. Never for a gate-self change
    # (force_risky) — gate-self always blocks. Fails safe: an empty uncovered set falls back to all
    # hunks (_uncovered_risky_hunks), and an unknown tightness is treated as blocking by
    # hunk_blocks_under_tightness — so this branch can only ever make the gate LOOSER for the exact
    # focused/all-advisory case, never for floor or high-confidence risk.
    if tightness == "focused" and not force_risky:
        uncovered, coverage_known = _uncovered_risky_hunks(classification, check_response)
        # Defense-in-depth (audit F-003): NEVER downgrade a gate-self change, even if a caller
        # mis-passes force_risky=False. A gate-self hunk carries the `gself:` hash namespace, so
        # its presence forces the block regardless of category/confidence. (The audit gate already
        # routes gate-self commits to audit_decision_gate_self and never reaches here — this is a
        # belt-and-suspenders guard local to the decision, not the primary enforcement.)
        gate_self_hunk = any(
            (h.get("content_hash") or "").startswith(GATE_SELF_HASH_PREFIX) for h in uncovered)
        # `coverage_known` (audit F-A/F-B): if the coverage state is ambiguous (server omitted the
        # `uncovered` set), do NOT downgrade — fall through to deny (block on ambiguity).
        if coverage_known and uncovered and not gate_self_hunk and not any(
            hunk_blocks_under_tightness(h.get("category"), h.get("confidence"), "focused",
                                        h.get("path_class"))
            for h in uncovered
        ):
            return ("advise", "focused: only non-floor low-confidence changes are uncovered "
                              "— advisory, not blocking")
    # We would BLOCK — but if we never issued a gate context, the agent has no skip handle and
    # would have to buy a review to pay for OUR failure. Fail open (see gate_context_missing).
    if gate_context_missing(check_response):
        return ("allow_warn", NO_GATE_CONTEXT_ALLOW)
    return ("deny", "uncovered")


def audit_decision_gate_self(check_response):
    """Decision for a GATE-SELF change (a write/commit touching the gate's own
    config/hooks). Return (action, detail). action ∈ {'allow', 'allow_warn', 'deny'}.

    Gate-self changes are the highest-stakes — weakening these files disables the
    gate from inside (privilege escalation). Unlike `audit_decision`, this path
    releases ONLY on `covered`: a real audit/deliberate PASS of THIS exact change
    (its `gate_self_coverage_hash`, written into the receipt by the server). It
    deliberately does NOT honor `recent_pass` or a logged SKIP — a repo-wide recent
    audit of unrelated code, or a one-line skip, must not release a gate-self change
    (Option 4, 2026-06-17 deliberation; closes the gate-self bypass).

    Still FAILS OPEN on infrastructure error (None response) — our own server being
    unreachable must never permanently trap the agent (no-deadlock constraint). The
    real "out" is `audit_coding`/`deliberate_coding` on the change itself → the
    server writes its coverage hash → the retry sees `covered` → released.
    """
    if check_response is None:
        return ("allow_warn", "coverage check unavailable (infra error); failing open — "
                              "review this gate-self change manually")
    if check_response.get("covered"):
        return ("allow", "gate-self change covered by a real audit PASS of this change")
    # Version-skew safety (audit F-001): an OLD server (pre-Option-4) neither reports
    # `gate_self_coverage` support nor writes the gself coverage hash, so `covered` can
    # never become true against it. Hard-denying would DEADLOCK a gate-self change under
    # healthy infra during a server-before-client rollout. Fail OPEN instead (the scoped
    # gate-self protection just isn't active until the server ships) — never trap the agent.
    # Key on the capability flag's ABSENCE, not its falsiness (F-NEW-001): a healthy server
    # always sends True, so a present-but-False value must NOT silently downgrade to
    # allow_warn — only a missing key (an old server) does.
    if "gate_self_coverage" not in check_response:
        return ("allow_warn", "server has not deployed scoped gate-self coverage yet; "
                              "failing open — review this gate-self change manually")
    # Same rule as the ordinary commit gate: our own mint failure must not trap the agent. A
    # gate-self change is the highest-stakes class, so this is the one place the fail-open stings —
    # but the alternative (a hard block with NO release handle) is the deadlock we are eliminating,
    # and a gate-self change reaching here can still be released by an audit_coding PASS. The
    # release is logged, and the post-commit backstop still records a floor change that shipped
    # unreviewed.
    if gate_context_missing(check_response):
        return ("allow_warn", NO_GATE_CONTEXT_ALLOW)
    return ("deny", "uncovered")


def deliberate_decision(classification, check_response, *, force_risky=False, tightness=None):
    """Return (action, detail). action ∈ {'allow', 'allow_warn', 'advise', 'deny'}.

    (audit F-001) force_risky/tightness are KEYWORD-ONLY: Fix 5 reordered them (the 3rd param used
    to be a `mode` string), so a stale positional caller `deliberate_decision(c, r, "x")` must fail
    LOUDLY (TypeError) rather than silently pass "x" into force_risky and bypass the gate.

    Fix 5 (Inc 8): the WRITE gate now blocks on the SAME per-hunk predicate as the commit gate —
    `hunk_blocks_under_tightness(...)` under `gate_tightness` — replacing the legacy
    whole-classification `max_confidence` / `deliberate_mode` tiering. The per-hunk predicate is
    strictly better: **floor-aware** (a hard-floor hunk blocks at ANY confidence — which
    `max_confidence` could not express, so a low-confidence floor write used to slip through as
    advisory) and it evaluates only the **uncovered** hunks. `deliberate_mode` is retired; both
    gates read `gate_tightness` (focused/thorough). Mirrors `audit_decision`, plus the write-gate-
    only `proactive_consulted` downgrade.

    `force_risky` (gate self-mutation): the change touches the gate's own config/hooks — ALWAYS
    block until reviewed regardless of tightness (privilege-escalation risk), but RELEASABLE via
    unlock / recent_pass / fail-open (not the old unconditional deny). (In practice the write hook
    routes gate-self through `audit_decision_gate_self`, so this path sees force_risky=False; the
    guard stays as defense-in-depth.)
    """
    if not classification.get("risky") and not force_risky:
        return ("allow", "no risky design change")
    if check_response is None:
        return ("allow", FAIL_OPEN_WRITE_DETAIL)
    # Write-gate-deadlock fix: a Write/Edit is finished code, so its natural review is `audit`.
    # `covered` means every risky hunk is covered by an audit-PASS (or, for a floor hunk, an
    # audit-PASS / fresh SYNTH_CONFIRM) of THIS change — a first-class release, symmetric with
    # the commit gate. Checked BEFORE the floor recent_pass scoping so an actually-reviewed floor
    # write releases (it no longer depends on a `deliberate` area-unlock existing). This is what
    # restores the "run the review -> release" invariant at the write gate and removes the
    # deliberate-only deadlock.
    if check_response.get("covered"):
        return ("allow", "change reviewed (audit / SYNTH_CONFIRM covers every risky hunk)")
    # The coarse `area` deliberate-unlock releases NON-floor hunks — but NOT an uncovered floor
    # hunk (audit F-001): a floor hunk needs its own audit-PASS / SYNTH_CONFIRM (via `covered`),
    # matching the commit gate. `is not True` (not `is False`) so a response that OMITS
    # floor_uncovered (old server / non-floor) still releases on the area-unlock.
    if check_response.get("unlocked") and check_response.get("floor_uncovered") is not True:
        return ("allow", "area already deliberated (no uncovered floor hunk)")
    # Phase 9: same floor-scoping as audit_decision — a FLOOR write doesn't release on an
    # unrelated recent deliberation. The commit (audit) gate is the real ship-time enforcement,
    # but the write gate matches it for consistency. `floor_uncovered` is server-asserted on the
    # deliberate-check response (the floor taxonomy is server-side, derived from the client's
    # risk_categories — the Layer-1 cooperative trust assumption, audit F-004). `is not True`
    # (NOT `is False`) so a gate-self / non-floor / old-server response that omits floor_uncovered
    # still releases on recent_pass. A floor write that denies here is releasable via a real
    # deliberate_coding of the design (→ area unlock), so the write gate needs no human-override
    # (unlike the ship-time audit gate). Non-floor still releases.
    if check_response.get("recent_pass") and check_response.get("floor_uncovered") is not True:
        return ("allow_warn", "a recent deliberation passed; area unverified — allowing")

    # Fix 5 (Inc 8): per-hunk `gate_tightness` predicate — the SAME floor-aware
    # `hunk_blocks_under_tightness` the commit gate uses, replacing the legacy max_confidence tiering.
    # A hunk BLOCKS the deliberate (write) tier iff it blocks under the active tightness: a hard-floor
    # hunk (ANY confidence — the key floor-awareness fix, which max_confidence couldn't express), a
    # non-floor HIGH hunk under 'focused', or any risky hunk under 'thorough'. If NOTHING blocks, the
    # deliberate tier is not this change's concern → 'advise', which lets a non-floor LOW-confidence
    # change flow to the BORDERLINE (synthesize) tier below (its own soft-gate) exactly as before.
    #
    # WRITE-GATE-SPECIFIC (vs audit_decision): we do NOT hard-block on AMBIGUOUS coverage (an old
    # server that omits `uncovered`). _uncovered_risky_hunks returns ALL hunks in that case, so a
    # floor / non-floor-HIGH change still denies (its hunk blocks), but a non-floor LOW change still
    # advises → the borderline tier handles it. This preserves the pre-Fix-5 write-gate routing (the
    # deliberate tier never blocked low-confidence changes) and is safe because the COMMIT gate is the
    # authoritative ship-time checkpoint (it DOES block on ambiguity). Never downgrades a gate-self
    # change (force_risky); the gself-hash guard is belt-and-suspenders.
    if not force_risky:
        uncovered, _coverage_known = _uncovered_risky_hunks(classification, check_response)
        gate_self_hunk = any(
            (h.get("content_hash") or "").startswith(GATE_SELF_HASH_PREFIX) for h in uncovered)
        if uncovered and not gate_self_hunk and not any(
            hunk_blocks_under_tightness(h.get("category"), h.get("confidence"), tightness,
                                        h.get("path_class"))
            for h in uncovered
        ):
            return ("advise", "%s: no blocking-class hunk uncovered — advisory, not blocking"
                              % (tightness or "focused"))
    # A blocking-class uncovered hunk remains (a floor hunk, a non-floor HIGH hunk, 'thorough', or
    # gate-self). Advisory-downgrade (2026-06-23 deliberation): a PROACTIVE deliberation covered
    # this area this session — a real review ran before the gate fired — so soften the block to an
    # advisory nudge. NOT for a gate-self change (proactive area receipts can't release gate-self).
    if not force_risky and check_response.get("proactive_consulted"):
        return ("advise", "proactive deliberation this session; downgraded to advisory")
    # Our mint failure must not trap a WRITE either (see gate_context_missing).
    if gate_context_missing(check_response):
        return ("allow_warn", NO_GATE_CONTEXT_ALLOW)
    return ("deny", "uncovered")


def borderline_decision(classification, mode, sampled=True, area_consulted=False):
    """Decide the BORDERLINE (low-confidence) tier action for the synthesize gate
    (design §6.5). action ∈ {'allow', 'advise', 'deny'}.

    - 'off'             -> never act on borderline.
    - 'advisory'        -> surface a suggestion (advise) for any borderline change.
    - 'synthesize_gate' -> soft-gate Borderline-HEAVY (deny -> route to
                           synthesize_coding OR a logged skip); advise on
                           Borderline-LITE.

    Throttles (design §6.5 — borderline is the high-volume band, so a Heavy spike only
    *soft-gates* when all of these pass; otherwise it degrades to advisory):
    - `area_consulted`  -> a consultation/PASS receipt already exists for this area this
                           session (from check_deliberate_unlock) -> advisory.
    - `sampled`         -> fractional sampling let this event through (else advisory; also
                           the A/B signal for whether the gate adds value).
    The per-session BUDGET cap is the third throttle; it's stateful, so the hook applies
    it (borderline_budget_consume) AFTER a 'deny' verdict here.

    High-confidence changes are handled by the audit/deliberate gate, so this defers
    (allow) on them. Heavy vs Lite is the classifier's spike sub-tier, never primitive
    density (design §4.2/§6.5). Activating 'synthesize_gate' is gated on the F-001
    output-quality pre-validation (design §6.5); default config is 'advisory'.
    """
    if not classification.get("risky"):
        return ("allow", "no borderline risk")
    if classification.get("max_confidence") == "high":
        return ("allow", "handled by audit/deliberate gate")
    tier = classification.get("borderline_tier")
    if mode == "off" or tier is None:
        return ("allow", "borderline tier off")
    if mode == "synthesize_gate" and tier == "heavy":
        if area_consulted:
            return ("advise", "borderline-heavy (area already consulted this session)")
        if not sampled:
            return ("advise", "borderline-heavy (not sampled)")
        return ("deny", "borderline-heavy: synthesize or skip")
    return ("advise", "borderline (%s)" % tier)


def borderline_sampled(rate):
    """Fractional-sampling throttle (design §6.5). True ~`rate` of the time."""
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return random.random() < rate


def _borderline_state_dir():
    """A best-effort writable dir for per-session borderline budget counters. Never
    raises — falls back to the system temp dir; the caller fails open if even that
    can't be written."""
    base = os.path.join(os.path.expanduser("~"), ".truverifai", "gate_state")
    try:
        os.makedirs(base, exist_ok=True)
        return base
    except Exception:
        return tempfile.gettempdir()


def borderline_budget_consume(session_id, cap):
    """Per-session synthesize soft-gate budget cap (design §6.5, third throttle).

    Returns True and increments the session's counter if budget remains (< cap);
    returns False once the session has hit the cap (all further borderline degrades to
    advisory). cap <= 0 disables the cap (always True). Fails OPEN (returns True) if the
    counter file can't be read/written — a throttle must never *create* a wall.

    SOFT cap by design (audit F-001/F-002): this counts soft-*gates* (deny verdicts), not
    cheap advisory nudges — advisories are intentionally uncapped. The read-incr-write is
    not locked, so two truly-concurrent same-session hooks could share a slot; PreToolUse
    hooks fire sequentially in practice, and the cap is a backstop (design §6.5), not a
    hard guarantee, so an occasional off-by-one is acceptable and never blocks.
    """
    if not cap or cap <= 0:
        return True
    path = os.path.join(_borderline_state_dir(),
                        "borderline_budget_%s.json" % _safe_session_id(session_id))
    try:
        count = 0
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                count = int(json.load(fh).get("count", 0))
        if count >= cap:
            return False
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"count": count + 1}, fh)
        return True
    except Exception:
        return True  # fail open — never let the budget file brick the gate


# ---------------------------------------------------------------------------
# Borderline ADVISORY visibility (Option B, 2026-06-19) — model-facing nudge + throttle
# ---------------------------------------------------------------------------
# In advisory mode the borderline tier used to write its "consider synthesize_coding"
# note to stderr, which only reaches the user transcript — the MODEL never saw it, so
# synthesize was never called. Option B surfaces it via PreToolUse `additionalContext`
# (model-facing, non-blocking, no auto-approve). Because borderline is the high-VOLUME
# band, an unthrottled per-write nudge would train the model to dismiss it (alarm
# fatigue), so the hook shows the model-facing advisory only for Borderline-HEAVY spikes,
# at most ONCE PER AREA PER SESSION (deliberate_coding mcp_f044c940, 0.88). All state here
# is best-effort and fails toward showing — a throttle must never wall the agent.

def _safe_session_id(session_id):
    """Filesystem-safe session id for per-session state filenames (shared by the
    advisory-seen state, the advisory log, and the borderline budget counter)."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(session_id or "nosession"))[:64]


def _advisory_seen_path(session_id):
    return os.path.join(_borderline_state_dir(), "advisory_seen_%s.json" % _safe_session_id(session_id))


def area_advisory_seen(session_id, area):
    """True if a synthesize advisory already fired for `area` this session (dedupe).
    Fail-open: any error -> False (show the advisory; a throttle never blocks)."""
    try:
        path = _advisory_seen_path(session_id)
        if not os.path.exists(path):
            return False
        with open(path, "r", encoding="utf-8") as fh:
            return area in set(json.load(fh).get("areas", []))
    except Exception:
        return False


def mark_area_advisory_seen(session_id, area):
    """Record that the synthesize advisory fired for `area` this session. Best-effort;
    persisted to disk so it survives a session resume (additionalContext is replayed on
    resume, so the dedupe state must be too). Never raises.

    Read-modify-write is unlocked: two truly-concurrent same-session hooks could both
    pass area_advisory_seen() and double-fire. PreToolUse hooks run sequentially in
    practice (same assumption as borderline_budget_consume), and the worst case is one
    duplicate non-blocking nudge — the throttle fails toward SHOWING, the safe direction."""
    try:
        path = _advisory_seen_path(session_id)
        areas = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                areas = json.load(fh).get("areas", [])
        if area not in areas:
            areas.append(area)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"areas": areas}, fh)
    except Exception:
        pass


def log_advisory_shown(session_id, area, categories):
    """Append-only LOCAL log of synthesize advisories shown — the denominator for the
    'advisory shown -> was synthesize then called?' experiment (Option B). Local only:
    the gate is client-side / privacy-preserving (no source, no network), so this never
    leaves the machine; aggregate it manually for now. Best-effort; never raises."""
    try:
        path = os.path.join(_borderline_state_dir(), "advisory_shown.log")
        rec = {
            "ts": int(time.time()),
            "session": _safe_session_id(session_id),
            "area": area,
            "categories": categories or [],   # classification.risk_categories can be None
            "plugin_version": plugin_version(),
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# §4.E human override (Phase 4 Increment 1) — the floor-class × SUSTAINED review-tool
# outage cell has NO agent self-release; the only path is a FAST, agent-inaccessible
# human via Claude Code's PreToolUse `permissionDecision:"ask"`. The DECISION is made
# server-side and read here (the hook never decides "is this floor" — the server does,
# via floor_uncovered + review_tool_health on the check response). Fails OPEN: a None /
# missing response (our infra down) is NOT the ask cell — the normal fail-open deny path
# runs instead (never trap the agent on our own outage; §5.1 deadlock invariant).
# ---------------------------------------------------------------------------

# Suppress a re-ask for the same repo+hunkset within this window so the agent retrying its
# commit doesn't re-prompt the human (the human already saw it). UX dedup ONLY — eligibility
# is the server's call; this never CREATES an ask, only throttles a repeat.
_OVERRIDE_DEBOUNCE_SECONDS = 60


# §4.E override-cell tags — constants so a typo can't silently break the message/reason branch
# (audit F-002). These exact strings are also the override-event reason discriminators downstream.
CELL_SUSTAINED_OUTAGE = "sustained_outage"
CELL_POST_REVIEW_DEADLOCK = "post_review_deadlock"


def human_override_cell(check_response):
    """WHICH §4.E human-override cell applies, or None. The two cells need DIFFERENT human
    messaging (Inc 3, audit-trail fix — the old code hardcoded 'sustained outage' for BOTH and
    misled the human on the common drift case):
      'sustained_outage'     — an UNCOVERED floor-class hunk AND review_tool_health reports a
                               SUSTAINED 'down': a real outage, the change can't be auto-reviewed.
      'post_review_deadlock' — an UNCOVERED floor hunk but a recent audit PASSed (recent_pass):
                               the review tool is HEALTHY; the agent likely DID review but this
                               change's own coverage missed (the reviewed diff drifted from what's
                               committed). Ask a human so a genuinely-reviewed floor change is
                               never hard-trapped — and so a real coverage miss isn't silent.
    Fail-open: a None/!dict response (our gate-server down) → None (the caller takes the normal
    fail-open path, never the ask). STRICT booleans (audit F-002): `floor_uncovered` / `sustained`
    / `recent_pass` must be the literal True, so a non-bool from a drifted/old server reads as 'no'
    and can't WIDEN this rare human prompt. (As of Inc 2 the post-review-deadlock cell is rarer
    still: a reviewed floor change now binds via the coarse-structural tier instead of reaching
    here — this is a genuine last resort.)"""
    if not isinstance(check_response, dict):
        return None
    if check_response.get("floor_uncovered") is not True:
        return None
    health = check_response.get("review_tool_health") or {}
    if health.get("status") == "down" and health.get("sustained") is True:
        return CELL_SUSTAINED_OUTAGE
    # A NON-sustained 'down' (a transient blip, sustained != True) is NOT the outage cell; if
    # recent_pass also holds it falls to the drift cell below — drift is the actionable signal, and
    # a transient blip isn't worth alarming the human about an "outage". Intentional (audit F-001).
    if check_response.get("recent_pass") is True:
        return CELL_POST_REVIEW_DEADLOCK
    return None


def should_ask_human_override(check_response):
    """True iff a §4.E human-override cell applies (see human_override_cell). Kept for the
    callers/tests that only need the boolean."""
    return human_override_cell(check_response) is not None


def _override_key(repo, hunk_hashes):
    h = hashlib.sha256("|".join(sorted(hunk_hashes or [])).encode("utf-8", "replace")).hexdigest()[:16]
    return "%s:%s" % (repo, h)


def _override_debounce_path(session_id):
    return os.path.join(_borderline_state_dir(),
                        "override_prompted_%s.json" % _safe_session_id(session_id))


def override_recently_prompted(session_id, repo, hunk_hashes, now=None):
    """True if a human was already prompted for this repo+hunkset within the debounce window.
    Fail-open: any error → False (show the prompt; a throttle must never wall the human gate)."""
    now = now if now is not None else time.time()
    try:
        path = _override_debounce_path(session_id)
        if not os.path.exists(path):
            return False
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        ts = data.get(_override_key(repo, hunk_hashes))
        return isinstance(ts, (int, float)) and (now - ts) < _OVERRIDE_DEBOUNCE_SECONDS
    except Exception:
        return False


def mark_override_prompted(session_id, repo, hunk_hashes, now=None):
    """Record that a human was prompted for this repo+hunkset. Prunes stale entries to bound
    the file. Best-effort; never raises (a failed write just means a possible duplicate
    prompt — the safe direction)."""
    now = now if now is not None else time.time()
    try:
        path = _override_debounce_path(session_id)
        data = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        data = {k: v for k, v in data.items()
                if isinstance(v, (int, float)) and (now - v) < _OVERRIDE_DEBOUNCE_SECONDS}
        data[_override_key(repo, hunk_hashes)] = now
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception:
        pass


def post_override_event(cfg, repo, *, reason_code="floor_review_tool_outage", gate_context_id=None,
                        server_reason=None, review_tool_health=None, hunk_hashes=None,
                        permission_mode=None):
    """POST the §4.E human-override PROMPT event (observability — the human-prompt-rate
    metric; NEVER a releasing receipt). `reason_code` is the cell-accurate code (Inc 3): the
    server stores it verbatim, so the dashboard distinguishes a real outage from a coverage-drift
    deadlock. `permission_mode` (Fix 3, Inc 9) is the RAW PreToolUse mode — sent verbatim so the
    server can DERIVE (at query time) whether a human actually decided the `ask` or it auto-proceeded
    in a non-interactive context; only sent when a real string is present. Best-effort via _post
    (returns None on any error); the prompt fires regardless."""
    body = {"repo": repo, "outcome": "prompted", "reason_code": reason_code}
    if gate_context_id:
        body["gate_context_id"] = gate_context_id
    if server_reason:
        body["server_reason"] = server_reason
    if isinstance(review_tool_health, dict):
        body["review_tool_health"] = review_tool_health
    if hunk_hashes:
        body["hunk_hashes"] = hunk_hashes
    if isinstance(permission_mode, str) and permission_mode:
        body["permission_mode"] = permission_mode
    return _post(cfg, "/api/mcp/receipts/override", body)


def override_ask_message(classification, cell=CELL_SUSTAINED_OUTAGE):
    """The HOOK/SERVER-authored stakes shown to the human on the `ask` prompt — deliberately NOT
    agent-authored (the agent must not be able to spin the human; root-cause #1 one layer up).
    Names the classifier categories the hook saw + WHY a human is needed, BRANCHED on the cell
    (Inc 3): the old single message hardcoded 'sustained outage', which was wrong — and alarming —
    on the common coverage-drift case. No source content — only labels. The decision is the
    human's; this is a rare, high-impact moment, framed for a single informed approve/deny."""
    cats = ", ".join(classification.get("risk_categories") or []) or "a protected high-risk area"
    head = ("TruVerifAI — HUMAN DECISION REQUIRED. This commit changes a PROTECTED high-impact area "
            f"({cats}: auth / secrets / money / migrations / a removed safety check)")
    if cell == CELL_POST_REVIEW_DEADLOCK:
        body = (" that is NOT covered by a review of THIS exact change — a recent unrelated review "
                "passed, but this change's own coverage is missing (most often the reviewed diff "
                "drifted from what's actually committed). It must not land unverified.\n"
                "Approve ONLY if you've personally verified THIS change is safe; otherwise deny and "
                "run a review of this exact change. Your call, not the agent's.")
    else:  # sustained_outage
        body = (" AND the review tool is currently unavailable (a sustained outage), so it can't be "
                "auto-reviewed and must not land un-reviewed.\n"
                "Approve ONLY if you've personally verified this change is safe; otherwise deny and "
                "wait for the review tool to recover (or review it out-of-band). Your call, not the "
                "agent's.")
    return head + body


def maybe_human_override(cfg, classification, check_response, session_id, repo,
                         permission_mode=None):
    """§4.E human override. If the floor × SUSTAINED-outage cell applies, prompt a HUMAN via
    emit_ask (which PRINTS the `ask` and EXITS the process) and never returns; otherwise
    return so the caller proceeds to the normal deny.

    `permission_mode` (Fix 3, Inc 9) is the RAW Claude Code PreToolUse mode, forwarded to the
    override event so the server can honestly label whether a human actually decided this `ask` or
    it auto-proceeded in a non-interactive context (bypassPermissions/dontAsk/headless).

    ROBUST by construction (audit F-001/F-004/F-007): every step is inside a try/except that
    falls through (returns) to the caller's normal deny on ANY failure — a malformed
    classification, a bad debounce file, a failed POST — so this branch can never crash the
    hook and can never auto-allow. emit_ask raises SystemExit (a BaseException), which is NOT
    caught by `except Exception`, so the `ask` exit propagates cleanly; only a genuine error
    short of the exit falls through to deny. Ordering is encapsulated here (debounce mark +
    event POST happen before the exit) so a future edit can't silently reorder it.

    Debounce/POST failure posture (audit F-R02/F-R03): mark_override_prompted and
    post_override_event are BOTH best-effort and NEVER raise (each swallows its own errors
    internally — see their defs + _post), so neither trips this function's `except` — a failed
    debounce write therefore still reaches emit_ask and PROMPTS (over-prompting a human is the
    SAFE direction vs. silently suppressing a floor-outage prompt), and a failed POST can't
    leave a mark-but-no-prompt window. The `except` here is for an UNEXPECTED error (e.g. a
    malformed classification), which falls through to the normal deny — never a crash, never
    an auto-allow. The debounce read/check/write is not locked, but PreToolUse hooks run
    sequentially in practice (same assumption as borderline_budget_consume / area_advisory_seen),
    so at worst a rare duplicate prompt — never a missed one."""
    try:
        cell = human_override_cell(check_response)
        if cell is None:
            return
        hunk_hashes = [h.get("content_hash") for h in (classification.get("hunks") or [])
                       if isinstance(h, dict) and h.get("content_hash")]
        if not hunk_hashes:
            return  # nothing to bind/debounce → normal deny
        if override_recently_prompted(session_id, repo, hunk_hashes):
            return  # human already prompted for this exact change → normal deny on retry
        mark_override_prompted(session_id, repo, hunk_hashes)
        # Cell-accurate reason + system_message (Inc 3) — never the misleading 'outage' wording on
        # the coverage-drift cell.
        if cell == CELL_POST_REVIEW_DEADLOCK:
            reason_code = "floor_uncovered_recent_pass"
            server_reason = ("floor-class change uncovered by a review of THIS change; a recent "
                             "unrelated audit passed (coverage drift)")
            sys_msg = ("TruVerifAI: a protected high-impact change needs a HUMAN decision — it "
                       "isn't covered by a review of this exact change.")
        else:  # sustained_outage
            reason_code = "floor_review_tool_outage"
            server_reason = "floor-class change, review tool in sustained outage"
            sys_msg = ("TruVerifAI: a protected high-impact change needs a HUMAN decision — the "
                       "review tool is down and it can't be auto-reviewed.")
        post_override_event(
            cfg, repo, reason_code=reason_code,
            gate_context_id=(check_response or {}).get("gate_context_id"),
            server_reason=server_reason,
            review_tool_health=(check_response or {}).get("review_tool_health"),
            hunk_hashes=hunk_hashes,
            permission_mode=permission_mode,
        )
        emit_ask(override_ask_message(classification, cell), system_message=sys_msg)
    except Exception:
        return  # any failure short of the ask-exit → fall through to the normal deny


# ---------------------------------------------------------------------------
# Hook input / output
# ---------------------------------------------------------------------------

def area_diagnostic_block(area, check_response):
    """The write gate's `area` — and, when it matters, why a proactive deliberation didn't apply.

    D (deliberation mcp_fd6de1da). The area is how a proactive `deliberate_coding` receipt is
    matched to a write, and it used to be INVISIBLE: the `area = ...` line was dropped when
    record_gate_skip retired its `area` param, so when the match failed there was no error, no
    warning, and nothing to compare — the feature just silently did nothing. Show the area (it is a
    diagnostic here, NOT a parameter to pass anywhere), and if the server says fresh proactive
    receipts exist under OTHER directories, say so: a near-miss is the single most likely reason an
    agent thinks it already deliberated this and the gate "ignored" it.

    Deliberately NOT emitted as `area = "..."`. That exact key is a RETIRED record_gate_skip
    parameter — tests/test_gate_hooks_e2e.py guards against it reappearing, because an agent that
    sees `key = value` in a gate message copies it into the next call and gets a schema error. This
    is a diagnostic, and it reads like one."""
    if not area:
        return ""
    out = '  ▸ this write is matched on area: %s\n' % area
    miss = [m for m in ((check_response or {}).get("proactive_area_miss") or []) if m]
    if not miss:
        return out

    # The server reports every fresh proactive receipt that did NOT apply. Two different reasons,
    # and telling the agent the wrong one is its own small lie: a receipt naming ANOTHER directory
    # is a path problem, while one naming THIS directory didn't apply because it was minted in a
    # different session.
    def _norm(a):
        return str(a).replace("\\", "/").rstrip("/")

    here = _norm(area)
    other = [m for m in miss if not _norm(m).endswith(here)]
    if other:
        shown = ", ".join('"%s"' % m for m in other[:3])
        more = (" (+%d more)" % (len(other) - 3)) if len(other) > 3 else ""
        out += ("  ⚠️  You have a recent `deliberate_coding` receipt, but for %s%s — not this "
                "area, so it does NOT soften this write. Pass `relevant_paths` that resolve to "
                "THIS directory (repo-relative is fine) if you meant to cover it.\n"
                % (shown, more))
    if len(other) < len(miss):
        out += ("  ⚠️  You have a recent `deliberate_coding` receipt for THIS area, but it was "
                "minted in a different session, so it does NOT soften this write. Re-run "
                "`deliberate_coding` in this session if the design question is still open.\n")
    return out


def gate_signal_line(classification):
    """The compact classifier-signal line for a deny message — the labels the agent
    forwards to record_gate_skip so the skip-log carries what the classifier saw
    (design §5.3). No source content; just version/score/categories."""
    cats = ",".join(classification.get("risk_categories") or [])
    return ('  gate_signal = classifier_version="%s" score=%s risk_categories="%s"'
            % (classification.get("classifier_version") or "",
               classification.get("score") if classification.get("score") is not None else 0,
               cats))


def skip_and_signal(classification, audit, area=None, gate_context_id=None):
    """The 'or log a skip' second branch + the gate_signal line for a deny message
    (design §5.1 second branch + §5.3). `audit` selects the gate context the agent
    passes (hunk_hashes for the commit gate, area for the write gate).

    Step 0 (design §2.2): when the server minted a gate-fire context, emit its
    `gate_context_id` as the PREFERRED copy-paste handle. A later `record_gate_skip`
    echoes it so the server verifies a gate truly fired (not a client-fabricated
    context) and binds the skip to the fire's OWN server-canonical hunks/area — the
    agent never recomputes them. The `hunk_hashes`/`area` line stays alongside it for
    the backward-compat window: an OLD server mints no id, so the message falls back to
    the locally-computed key; emitting both is harmless (the server prefers the id).

    1a (gate-skip usability, 2026-06-19): emit the ACTUAL release key the gate
    already computed — the hunk content-hashes (commit gate) or the area directory
    (write gate) — as a copy-pasteable value, instead of telling the agent to
    reconstruct it. Reconstruction was the fragile step: the agent re-ran the
    classifier in a shell that could be a different plugin VERSION or a different
    git COMMAND context than the live gate, so the rebuilt hash set diverged and the
    skip covered the wrong hunks (see docs/MCP/gate-skip-friction-findings.md). The
    retry recomputes the same hashes in THIS same hook process at THIS same version,
    so a value copied from here always matches — no server round-trip, no skew."""
    # 2026-07-12: the gate_context_id is the ONLY release handle. The legacy `hunk_hashes = [...]`
    # / `area = "..."` fallback keys are DELETED server-side (record_gate_skip refuses a call
    # without an id), so printing them would hand the agent arguments that no longer exist — and
    # an agent that pastes them gets a schema error, which is exactly the "the gate is broken"
    # spiral this whole change exists to prevent. Print the id, or route to the review.
    have_gcid = isinstance(gate_context_id, str) and bool(gate_context_id)
    if not have_gcid:
        # The server couldn't mint a fire (best-effort — a DB fault). There is no skip handle, so
        # don't dangle one: name the path that still works. audit_coding needs no gate context; it
        # hashes the diff you give it, and its PASS covers floor and non-floor hunks alike.
        return (
            "No gate context was issued for this block, so `record_gate_skip` can't release it "
            "(a skip only releases a gate the server can verify fired). Run `audit_coding` with "
            "gate_repo + gate_diff instead — a PASS releases the change. You are not stuck.\n"
            + gate_signal_line(classification) + "\n"
        )
    lines = ["  gate_context_id = %s" % json.dumps(gate_context_id),
             gate_signal_line(classification)]
    return (
        # NOT "if this is not a floor class" (2026-07-12): a judgment skip is denied only while a
        # FLOOR hunk is UNREVIEWED, and once the floor is covered it is the ONLY release for the
        # change's NON-floor hunks. Telling the agent not to use it on a floor-class change steers
        # it away from the exact call that clears the remainder.
        "Or, if the NON-floor hunks in 'Still uncovered' genuinely don't need review, call "
        "`record_gate_skip` (free) with a judgment reason_code, gate_repo, and the "
        "`gate_context_id` VALUE below (copy the gc_… string verbatim), then retry. (A judgment "
        "skip is denied while a FLOOR hunk is still unreviewed — cover the floor first, then it "
        "works.)\n"
        # Trailing newline (smoke-fixes F3, 2026-07-24): the deny assembly appends
        # release_options_block directly after this — without it the gate_signal line
        # and the "Release options" header fuse into one garbled line (dev smoke 4.1).
        + "\n".join(lines) + "\n"
    )


def read_hook_input():
    # Read the PreToolUse payload as UTF-8, NOT the platform locale. Claude Code always
    # writes UTF-8 JSON to the hook's stdin, but text-mode sys.stdin.read() decodes with
    # locale.getpreferredencoding() (cp1252 on Windows, ASCII under a C/POSIX locale in
    # CI/containers) -> non-ASCII in an Edit's old_string/new_string (em-dash, section
    # sign, accented identifiers) MOJIBAKES before build_change_diff hashes it, so the
    # write-gate fire's hunk hash never matches a natural (correctly-UTF-8) agent gate_diff
    # and a floor write deadlocks. Reading the raw byte buffer + decoding utf-8 is correct
    # and locale-independent on every OS. Fall back to the text stream when there is no
    # binary buffer (e.g. sys.stdin replaced by a TextIO in tests).
    try:
        buf = getattr(sys.stdin, "buffer", None)
        raw = buf.read() if buf is not None else None
    except Exception:
        raw = None
    try:
        if raw is not None:
            # STRICT decode (not errors="replace"): the payload is structured JSON, and a
            # replacement char inside a string value would yield VALID-but-corrupted JSON
            # (audit F-001) rather than failing. A genuinely malformed payload should raise
            # UnicodeDecodeError -> the except below returns {} -> the gate fails OPEN
            # (allow), consistent with the module's posture — never silently mutate content.
            parsed = json.loads(raw.decode("utf-8") or "{}")
        else:
            parsed = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return {}
    # Normalize the host's payload onto the core vocabulary (Bash / Write / Edit /
    # MultiEdit / PrebuiltDiff, claude-shaped tool_input). Claude Code = identity.
    # A normalizer error fails open to the RAW payload: the gates allow any tool
    # they don't recognize, so a broken adapter can never trap the agent.
    try:
        return host_registry.current().normalize_input(parsed)
    except Exception:
        return parsed


# ---------------------------------------------------------------------------
# Plugin-version self-diagnostics (2a, 2026-06-19)
# ---------------------------------------------------------------------------
# Claude Code does NOT hot-reload plugin hooks after an auto-update: a session
# that was running when the plugin updated keeps the OLD hooks registered (its
# `${CLAUDE_PLUGIN_ROOT}` still points at the now-superseded version) until
# `/reload-plugins` or a restart. The gate then silently runs stale classifier
# logic — the recurring "issues every upgrade" symptom. We can't force a reload
# from inside the hook, but we CAN make the staleness self-announcing so it's an
# actionable message instead of a silent mystery (and stamp the running version so
# "which version actually ran" is observable from the transcript/logs).
#
# Claude Code marks a superseded cache version with an `.orphaned_at` file in the
# plugin root and prunes it once no session holds it (a refcounted `.in_use/` dir);
# we do NOT touch that lifecycle (deleting an in-use version would break a live
# session). `.orphaned_at` is an undocumented marker, so every check here is
# best-effort and fails toward "not stale" — a missing/renamed marker just means
# the warning doesn't fire, never a false alarm or a blocked action.

def _plugin_root():
    """`${CLAUDE_PLUGIN_ROOT}` — the plugin dir. Layout assumption: this file lives at
    `<plugin_root>/hooks/gate_lib.py`, so the root is two dirs up. If the packaging
    layout ever changes, update this (every caller fails open, so a wrong root only
    suppresses the version stamp / staleness warning — never blocks)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def plugin_version():
    """The running plugin's version (from its bundle manifest), or 'unknown' on any
    error. Probes the host's manifest filenames in order (each platform names its
    manifest differently — .claude-plugin/, .codex-plugin/, .cursor-plugin/, root
    plugin.json, gemini-extension.json). Used to stamp deny messages / logs."""
    root = _plugin_root()
    try:
        paths = host_registry.current().manifest_paths
    except Exception:
        paths = (os.path.join(".claude-plugin", "plugin.json"),)
    for rel in paths:
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                v = json.load(fh).get("version")
            if v:
                return v
        except Exception:
            continue
    return "unknown"


# Memoized: the orphaned marker can't change during a single (short-lived) hook
# process, and is_stale_version() is consulted a few times per deny — stat once.
_STALE_CACHE = None


def is_stale_version():
    """Best-effort: True if this hook is running from a Claude-Code-orphaned
    (superseded) plugin version — i.e. the plugin updated but this session still has
    the old hooks loaded. Any error -> False (never a false 'stale' warning)."""
    global _STALE_CACHE
    if _STALE_CACHE is None:
        try:
            _STALE_CACHE = os.path.exists(os.path.join(_plugin_root(), ".orphaned_at"))
        except Exception:
            _STALE_CACHE = False
    return _STALE_CACHE


_STALE_WARNING = (
    "this TruVerifAI gate is running a SUPERSEDED version — the plugin updated but "
    "this session still has the old hooks loaded. Run `/reload-plugins` or restart "
    "so the gates run the latest classifier."
)


def version_suffix():
    """A short version stamp for a deny message — or a loud staleness warning when the
    running hook is a superseded (orphaned) version (2a)."""
    v = plugin_version()
    if is_stale_version():
        return "\n\n⚠️ NOTE: %s (currently loaded: v%s)" % (_STALE_WARNING, v)
    return "\n\n(TruVerifAI gate v%s)" % v


# User-facing one-liner shown alongside the deny via the top-level
# `systemMessage` field (rendered to the user, separate from the model-facing
# `permissionDecisionReason`). Framed positively so a gate reads as TruVerifAI
# doing its job, not erroring. NOTE: the "Error:" label on the blocked tool
# itself is Claude Code's own rendering of a PreToolUse deny and can't be
# changed from a hook; this softens the surrounding message, not that prefix.
_DENY_SYSTEM_MESSAGE = (
    "TruVerifAI flagged a high-risk change for a quick review — run the "
    "suggested check to proceed (some changes also allow a one-line skip)."
)


def host_run(fn):
    """Run a gate entrypoint under the active host's lifecycle wrapper. On the
    fail-CLOSED host (copilot_cli) this is total exception containment; on
    every other host it is a plain call (the launcher's exit-0 coercion is the
    belt). Resolution failure runs bare — never trap the agent on our error."""
    try:
        h = host_registry.current()
    except Exception:
        fn()
        return
    h.run(fn)


def emit_deny(reason, system_message=_DENY_SYSTEM_MESSAGE):
    """Emit a deny so the host blocks the tool and shows the MODEL the reason
    (the routing message — the entire product; a block without it is a wall, not
    a router). The model still holds full context and can act on it. A short,
    positive `system_message` accompanies the block for the user on hosts with a
    separate user channel.

    Composition happens HERE (version stamp, staleness warning); the host adapter
    only wire-formats the final strings + exits — so adapters stay tiny and
    dependency-free (host must not import gate_lib)."""
    # Stamp the running plugin version (or a staleness warning) on every deny
    # so "which version walled this" is visible and a stale hook self-announces.
    reason = reason + version_suffix()
    if system_message and is_stale_version():
        system_message = system_message + " (Gate is on a SUPERSEDED version — /reload-plugins.)"
    host_registry.current().emit_deny(reason, system_message)


def emit_ask(reason, system_message=None):
    """Emit a PreToolUse `permissionDecision:"ask"` — the §4.E human-override channel. In a NORMAL
    interactive session Claude Code shows a permission prompt that the AGENT CANNOT approve
    (permission decisions are evaluated by Claude Code, not the model) — a single human
    approve/deny, with `permissionDecisionReason` as the stakes.

    CAVEAT — corrected Inc 3 against the Claude Code hooks/permissions docs (an earlier version of
    this docstring WRONGLY claimed `ask` fails closed in headless): `ask` is only GUARANTEED to
    prompt in interactive mode. In `bypassPermissions` (operator ran `--dangerously-skip-permissions`)
    PreToolUse hooks cannot block or deny AT ALL, and the behavior of `ask` in headless / `-p` /
    autonomous runs is undocumented — so an `ask` may AUTO-PROCEED with no human when the operator
    has opted out of prompts. That is an ACCEPTED boundary: this gate protects high-impact decisions
    for operators who have not disabled the safety; it does not (and cannot) override a deliberate
    bypass. `deny` is the only unconditionally non-bypassable outcome, but the floor backstop must
    NOT hard-trap a genuinely-reviewed change, so it deliberately uses `ask`, not `deny`. The
    `reason` is hook/server-authored (never the agent's words).

    Hosts without an `ask` decision override Host.emit_ask; the base contract is
    that an unsupported ask must degrade toward allow-with-warning, never toward
    deny (it must NOT hard-trap)."""
    host_registry.current().emit_ask(reason + version_suffix(), system_message)


def emit_allow(note=None):
    """Allow (defer). For advisory / allow-with-warning, surface a note on stderr
    so it reaches the transcript without blocking. When the running hook is a
    superseded (orphaned) version, append the staleness warning to a meaningful note
    (2a) — but only when a note is already present, so trivial early-exit allows
    (every non-git Bash, every non-risky write) don't spam the transcript."""
    if note and is_stale_version():
        note = note + " | " + _STALE_WARNING
    host_registry.current().emit_allow(note)


def emit_allow_advisory(additional_context):
    """Allow the tool but inject a MODEL-VISIBLE advisory (Claude Code:
    PreToolUse `additionalContext`; hosts without an advisory channel degrade to
    a stderr note). Crucially emits NO permission decision, so the tool still
    goes through the user's normal permission flow — this does NOT auto-approve.
    Unlike emit_allow's stderr note (user-transcript only), the advisory reaches
    the model so it can choose to act. Degrades harmlessly on hosts that ignore
    the field, and fails open (a serialization error still exits 0 — the gate
    never traps the agent)."""
    host_registry.current().emit_allow_advisory(additional_context)
