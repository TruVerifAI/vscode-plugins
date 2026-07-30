#!/usr/bin/env python3
"""PreToolUse hook — record the pre-command HEAD for the post-commit backstop.

Part of the Bash-authored-file backstop (docs/MCP/.../BASH-WRITE-BACKSTOP-...). A
fused `create-a-file && git commit` (and even a `commit && commit` in one Bash
command) is invisible to the commit gate, because the file doesn't exist on disk
at PreToolUse time. The backstop catches it AFTER the fact by classifying the real
committed diff. To catch EVERY commit a single command made (not just the tip),
the post hook needs the HEAD from BEFORE the command ran — this hook stashes it.

ISOLATION (hard requirement): this hook is NOT the review gate. It emits no
permission decision, records at most one small file, wraps everything in
try/except, and ALWAYS exits 0. A crash/hang/bug here runs in its own process and
cannot change the enforcement gate's allow/deny. The backstop only ever USES this
stash when it can prove it belongs to the current command (shared tool_use_id, or
ancestor + freshness + plausible span); otherwise it falls back to the tip commit.
So a stale/absent/corrupt stash degrades to the safe default — never a re-nag,
never a block.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    # Isolation invariant: this hook can NEVER affect the gate — any error is swallowed and it
    # ALWAYS exits 0 with no decision. (SystemExit is BaseException, not caught by `except
    # Exception`, so _record's own control flow can't accidentally block the exit.)
    try:
        _record()
    except Exception:
        pass
    sys.exit(0)  # no output = default allow for PreToolUse; never emit a decision


def _record():
    import time
    import json
    import gate_lib as g

    cfg = g.config()
    if not cfg["enabled"] or not cfg["token"]:
        return  # feature off / not configured → nothing to stash

    inp = g.read_hook_input()
    if inp.get("tool_name") != "Bash":
        return
    command = (inp.get("tool_input") or {}).get("command", "") or ""
    if not g.command_invokes_git(command, ("commit", "merge")):
        return  # only a commit/merge command is worth stashing for

    cwd = inp.get("cwd") or os.getcwd()
    # Pre-command HEAD. Empty (no commits yet / not a repo) → "ROOT" sentinel so the post hook
    # diffs against the empty tree for a first commit.
    head = (g._git(["rev-parse", "HEAD"], cwd) or "").strip() or "ROOT"

    rec = {
        "tool_use_id": inp.get("tool_use_id"),  # shared with the PostToolUse of this call
        "ts": time.time(),
        "head_sha": head,
    }
    path = g.precommit_stash_path(inp.get("session_id"))
    # Overwrite each time → self-healing, no accumulation, no leak.
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh)


if __name__ == "__main__":
    g.host_run(main)
