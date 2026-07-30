"""git pre-commit host adapter — the universal fallback (plan §7.5 / phase 3).

Covers every surface with no hook API at all (Zed, Aider, JetBrains, web
IDEs) and commits made OUTSIDE any agent: `tvai hook install` writes a
.git/hooks/pre-commit that runs the COMMIT gate against the staged diff.
Commit gate ONLY — there is no write event at commit time, so the deliberate
gate has no role here (plan §7.5 scope split).

Differences from hook hosts:
- there is NO stdin payload: normalize_input synthesizes a Bash/git-commit
  payload so audit_gate's flow (staged_diff etc.) applies unchanged.
- "deny" = a DISTINCTIVE exit code 21 with the routing text on stderr. The
  installed pre-commit wrapper maps 21 -> exit 1 (git aborts) and EVERY other
  exit -> 0 (fail open): a crash/traceback must never block a commit, and a
  plain exit-1 deny would be indistinguishable from a crash. The reader may
  be a human or an agent watching command output; stderr is the only channel.
  `git commit --no-verify` remains the human escape hatch (bypass is a
  deliberate, auditable act — plan §7.5).
"""

DENY_EXIT_CODE = 21  # mapped to 1 by the wrapper; every other exit -> 0

import os
import sys

from host.base import Host


class GitPrecommitHost(Host):
    name = "git_precommit"

    capabilities = dict(Host.capabilities, **{
        "install": "git_hook",             # tvai hook install
        "write_gate": False,               # structurally: no write event exists
        "supports_ask": False,
        "supports_advisory_context": False,
        "structured_deny": False,          # deny is exit code + stderr text
        "stderr_reaches_model": "depends_on_caller",
    })

    def normalize_input(self, raw):
        # No stdin from git. Synthesize the commit-gate payload; audit_gate
        # then reads the staged diff itself (same as every host).
        return {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit"},
            "cwd": os.getcwd(),
            "session_id": "git-precommit-" + str(os.getpid()),
        }

    def emit_deny(self, reason, system_message=None):
        sys.stderr.write("\n" + ("=" * 72) + "\n")
        sys.stderr.write("TruVerifAI commit gate: this commit needs a review first.\n\n")
        sys.stderr.write(reason + "\n")
        if system_message:
            sys.stderr.write("\n" + system_message + "\n")
        sys.stderr.write(
            "\nAfter the review passes, retry the commit. Deliberate bypass "
            "(logged to no one — your call): git commit --no-verify\n")
        sys.stderr.write(("=" * 72) + "\n")
        # Distinctive code, mapped to 1 by the wrapper; a crash exits with
        # anything else and the wrapper fails OPEN (module docstring).
        sys.exit(DENY_EXIT_CODE)

    def emit_ask(self, reason, system_message=None):
        # No interactive channel in a git hook; the never-hard-trap contract
        # sends ask toward allow-with-warning.
        sys.stderr.write("TruVerifAI: TVAI_ASK_DEGRADED — " + reason + "\n")
        sys.exit(0)

    def emit_allow_advisory(self, additional_context):
        try:
            sys.stderr.write("TruVerifAI: " + additional_context + "\n")
        except Exception:
            pass
        sys.exit(0)
