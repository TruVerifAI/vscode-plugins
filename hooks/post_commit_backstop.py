#!/usr/bin/env python3
"""PostToolUse / PostToolUseFailure hook — post-commit backstop.

Catches a floor change that reached a commit WITHOUT a review — the fused
`create-a-file && git commit` that slips past the commit gate (the file doesn't
exist on disk at PreToolUse time, so the gate can't classify it). This hook runs
AFTER the command: it classifies the REAL committed diff (via git — no shell
parsing) and, on an uncovered FLOOR hunk (auth / secrets / money / migrations /
removed-guard with no review receipt), surfaces a NON-BLOCKING, content-based
advisory to the agent and logs a row for the human dashboard (the real product).

It CANNOT block — PostToolUse runs after the tool. Registered on BOTH PostToolUse
(exit 0) and PostToolUseFailure (non-zero exit, e.g. `commit && push` where push
fails), because PostToolUse does not fire on a non-zero exit (verified on CC
2.1.203). Fail-open on everything (exit 0, silent). Worst-case error is a false
advisory — strictly weaker harm than a false deny.

Design: docs/MCP/PROD-SMOKE-HANDOVER/BASH-WRITE-BACKSTOP-IMPLEMENTATION-PLAN.md.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# git's canonical empty-tree object — diff a root (first) commit against this. Per
# object format (F-007): a sha256 repo has a different empty-tree hash, and the sha1
# constant is invalid there. _empty_tree() picks the right one; a wrong/unknown format
# just yields an empty diff → silent (never a false advisory).
EMPTY_TREE_SHA1 = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
EMPTY_TREE_SHA256 = "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321"
# A stashed base wider than this many commits is implausible for one command → fall back
# to the tip commit (D9-FAILSAFE span guard).
MAX_HANDSHAKE_SPAN = 25
# Best-effort backstop: don't churn the classifier on a giant diff (e.g. a huge root
# commit) — skip silently above this size (F-010).
MAX_DIFF_BYTES = 2_000_000


def _empty_tree(g, cwd):
    fmt = (g._git(["rev-parse", "--show-object-format"], cwd) or "sha1").strip()
    return EMPTY_TREE_SHA256 if fmt == "sha256" else EMPTY_TREE_SHA1


def main():
    try:
        import gate_lib as g
        cfg = g.config()
        if not cfg["enabled"] or not cfg["token"]:
            return  # feature off / not configured
        inp = g.read_hook_input()
        cwd = (inp.get("cwd") or os.getcwd())
        decision = evaluate(g, cfg, inp, cwd)
        if not decision:
            return
        # Human dashboard row (the real product — agents rationalize past nudges). Best-effort.
        _post_dashboard_event(g, cfg, decision["repo"], decision["uncovered_floor"],
                              inp.get("session_id"), decision["pushed"])
        _emit_advisory(inp.get("hook_event_name"), decision["cats"], decision["pushed"])
    except Exception:
        pass  # fail-open: never raise
    sys.exit(0)


def evaluate(g, cfg, inp, cwd):
    """The full decision, side-effect-free except the coverage network read (which tests
    monkeypatch): does this commit ship an UNCOVERED FLOOR hunk that warrants a backstop
    advisory? Returns a decision dict {repo, cats, pushed, uncovered_floor} or None (silent).
    Kept as one testable function so the non-self-confirming tests can drive real committed
    diffs through it without stdin/exit."""
    from risk_classifier import classify_diff, is_hard_floor, floor_exempt

    if inp.get("tool_name") != "Bash":
        return None
    command = (inp.get("tool_input") or {}).get("command", "") or ""
    if not g.command_invokes_git(command, ("commit", "merge")):
        return None  # not a commit/merge command
    session_id = inp.get("session_id")

    base = _resolve_base(g, cwd, inp, session_id)
    if base is None:
        return None  # merge commit / no HEAD / undeterminable → silent

    diff = g._git(["diff", base, "HEAD"], cwd)
    if not diff.strip():
        return None  # command didn't advance HEAD (e.g. a step failed before the commit) → silent
    if len(diff) > MAX_DIFF_BYTES:
        return None  # F-010: best-effort — don't churn the classifier on a giant diff

    classification = classify_diff(diff, trigger_threshold=g.effective_threshold(cfg),
                                   file_content_fetcher=g.file_content_fetcher(cwd))
    if not classification.get("risky"):
        return None

    # Only FLOOR hunks matter (D6). Exclude test/docs-exempt floor (same rule as the gates).
    def _is_floor(h):
        return is_hard_floor(h.get("category")) and not floor_exempt(
            h.get("category"), h.get("path_class"))

    if not any(_is_floor(h) for h in classification["hunks"]):
        return None  # no floor content → skip the coverage call entirely

    repo = g.repo_fingerprint(cwd)
    hashes = [h["content_hash"] for h in classification["hunks"]]
    resp = g.check_audit_coverage(cfg, repo, hashes,
                                  classification=classification, session_id=session_id)
    uncovered, coverage_known = g._uncovered_risky_hunks(classification, resp)
    # Backstop posture (differs from the commit gate): on AMBIGUOUS/unknown coverage stay SILENT.
    # The commit gate BLOCKS on ambiguity (fail-safe pre-ship); a post-hoc advisory must not nag on
    # "maybe covered" — the worst case here is a false advisory, so minimize it.
    if not coverage_known:
        return None
    # Receipt correlation (gate-usability §3.6, the 2026-07-22 A1 false positive): a real
    # audit PASS landed SECONDS before this commit, yet the backstop re-classifies the
    # COMMITTED base..HEAD diff, whose hunk boundaries can differ from the staged diff the
    # PASS covered (git re-hunks around context) — a strict-hash miss the normalized tier
    # can't always rescue (boundary drift ≠ byte drift). The server already reports
    # `recent_pass` (a real audit PASS in the recency window, the same signal the commit
    # gate's escape valve uses). A fresh PASS makes an "unreviewed floor commit" claim
    # unreliable, and this hook's stated posture is to minimize false advisories — so
    # stay silent and DON'T log a dashboard row the owner has to triage. Trade-off,
    # accepted: a genuinely-fused unreviewed floor commit within minutes of an unrelated
    # PASS goes un-flagged; the pre-commit gates (which do NOT honor recent_pass on
    # floor) remain the enforcement layer — the backstop is advisory-only.
    if resp.get("recent_pass") is True:
        return None
    uncovered_floor = [h for h in uncovered if _is_floor(h)]
    if not uncovered_floor:
        return None  # the floor content was reviewed (receipt present) → silent

    cats = ", ".join(sorted({h.get("category") for h in uncovered_floor if h.get("category")})) \
        or "auth / secrets / migrations / removed-guard"
    return {
        "repo": repo,
        "cats": cats,
        "pushed": g.command_invokes_git(command, ("push",)),
        "uncovered_floor": uncovered_floor,
    }


def _resolve_base(g, cwd, inp, session_id):
    """Return the diff base for the tip commit's introduced content, preferring the
    fail-safe handshake base (every commit THIS command made) when provable, else the
    tip commit's parent. None → skip (merge / no HEAD)."""
    out = (g._git(["rev-list", "--parents", "-n", "1", "HEAD"], cwd) or "").strip()
    if not out:
        return None  # no HEAD / not a repo
    parents = out.split()[1:]  # first token is HEAD itself; whitespace-split drops the trailing \n
    if len(parents) >= 2:
        return None  # merge commit → no newly-authored content; skip (avoid first-parent noise)
    tip_base = _empty_tree(g, cwd) if len(parents) == 0 else parents[0]  # root → empty tree
    return _safe_handshake_base(g, cwd, inp, session_id) or tip_base


def _safe_handshake_base(g, cwd, inp, session_id):
    """The stashed pre-command HEAD, but ONLY when we can prove it belongs to THIS
    command and is safe to diff from. Any doubt → None (caller uses the tip base). This is
    what keeps the widened window from ever reaching back into a prior command's commits →
    no re-nag. (D9-FAILSAFE.)

    Concurrency: Claude Code runs tools sequentially and THIS command's PreToolUse stash hook
    overwrites the file before this PostToolUse runs, so the stash we read is normally our own.
    The tool_use_id match below is what makes that a proof rather than an assumption — a stash
    from any other call (or a failed/absent write) fails the match and we degrade to tip-only."""
    try:
        import json
        path = g.precommit_stash_path(session_id)
        with open(path, "r", encoding="utf-8") as fh:
            rec = json.load(fh)
    except Exception:
        return None  # no / unreadable stash → tip-only
    # F-001: require an AIRTIGHT tool_use_id match (Inc 0 confirmed CC shares it across the
    # PreToolUse/PostToolUse of one call). No match — including BOTH ids missing (None != None
    # is False, which must NOT count as a match) — means we can't prove ownership → tip-only.
    cur_tuid = inp.get("tool_use_id")
    stash_tuid = rec.get("tool_use_id")
    if not cur_tuid or not stash_tuid or cur_tuid != stash_tuid:
        return None

    stash_head = rec.get("head_sha")
    if not stash_head:
        return None
    if stash_head == "ROOT":
        # ROOT means THIS call's PreToolUse saw an empty repo (rev-parse HEAD failed). Because the
        # tool_use_id matched above, this stash is provably ours, so every commit now in HEAD's
        # history was made by this command → the empty tree is the correct base (covers 1 OR many
        # commits from an empty repo). (F-001's tool_use_id match subsumes the auditor's F-002
        # "reuse across commands" concern — a prior command's ROOT stash has a different id.)
        return _empty_tree(g, cwd)
    base = stash_head

    # Ancestry: base must be an ancestor (or equal) of HEAD. `merge-base --is-ancestor` signals via
    # exit code, which _git hides — so compare the merge-base instead (rc-0, stdout-based).
    if (g._git(["merge-base", base, "HEAD"], cwd) or "").strip() != base:
        return None  # not an ancestor (rewound / other branch / unrelated / bogus sha) → tip-only
    # Plausible span: a single command shouldn't have made a huge number of commits. Also reject
    # span < 1 (base == HEAD → nonsensical handshake; harmless empty diff, but be explicit — F-009).
    try:
        span = int((g._git(["rev-list", "--count", base + "..HEAD"], cwd) or "0").strip() or "0")
    except Exception:
        return None
    if span < 1 or span > MAX_HANDSHAKE_SPAN:
        return None
    return base


def _post_dashboard_event(g, cfg, repo, uncovered_floor, session_id, pushed):
    """Log an unreviewed-floor-commit row for the owner (categories + hunk hashes + pushed
    flag; NO content, paths, or command text — the gate-fire privacy model). Best-effort."""
    try:
        body = {
            "repo": repo,
            "categories": sorted({h.get("category") for h in uncovered_floor if h.get("category")}),
            "hunk_hashes": [h.get("content_hash") for h in uncovered_floor if h.get("content_hash")],
            "pushed": bool(pushed),
            "session_id": session_id,
        }
        g._post(cfg, "/api/mcp/usage/unreviewed-floor-commit", body)
    except Exception:
        pass


def _emit_advisory(event_name, cats, pushed):
    """Non-blocking, model-visible advisory via hookSpecificOutput.additionalContext. Cannot
    block (post-hoc). Degrades harmlessly if a CC build ignores the field (the dashboard row is
    the reliable channel). Fails open."""
    import json
    if pushed:
        msg = ("⚠️ TruVerifAI backstop: the commit you just made AND PUSHED contains an "
               "unreviewed floor change (" + cats + ") — it is already public. Review it now (run "
               "`audit_coding` on the diff) and revert/patch if needed. Non-blocking notice: the "
               "pre-commit review gate couldn't see this because the file was created and committed "
               "in one command.")
    else:
        msg = ("TruVerifAI backstop: the commit you just made contains an unreviewed floor change ("
               + cats + "). It's local and unpushed — `git reset --soft HEAD~1` to review it, run "
               "`audit_coding` on the diff, or amend. Non-blocking notice: the pre-commit review gate "
               "couldn't see this because the file was created and committed in one command.")
    ev = event_name if event_name in ("PostToolUse", "PostToolUseFailure") else "PostToolUse"
    try:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": ev,
            "additionalContext": msg,
        }}))
        sys.stdout.flush()
    except Exception:
        pass


if __name__ == "__main__":
    g.host_run(main)
