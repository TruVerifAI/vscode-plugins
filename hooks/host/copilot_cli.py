"""GitHub Copilot CLI / Cloud Agent host adapter.

THE FAIL-CLOSED HOST (implementation plan §3.5, docs.github.com hooks
reference): exit 2 = deny, timeout = fail open, but ANY OTHER NON-ZERO EXIT
FAILS CLOSED — it denies the user's edit. Our hard invariant is the opposite
(the gate never blocks on its own error), so this adapter carries a total
BaseException containment in run(): any crash emits an explicit allow and
exits 0. This is the ONE host that needs it, and it lives here — putting it
in the core would mask real errors on every other platform. run_gate.sh/.cmd
coercing every interpreter exit to 0 is the second belt (launcher level).

Wire differences from the base (Claude Code) contract:
- stdin is camelCase: sessionId / toolName / toolArgs / cwd (+ timestamp).
- deny is a TOP-LEVEL {"permissionDecision": "deny",
  "permissionDecisionReason": ...} — not nested under hookSpecificOutput.
- there is no documented advisory (additionalContext) channel -> advisory
  degrades to a stderr note.
- `ask` IS a documented permissionDecision value; kept top-level like deny.

Tool names on this host are not exhaustively documented; normalization is
by-shape (map_write_input) with a shell-name allowlist, and anything
unrecognized passes through -> the gates allow it (fail open, never
misclassify).

Surfaces: Copilot CLI (all events) and the Cloud Agent (preToolUse only,
.github/hooks only, Linux bash only). VS Code has its own adapter — same
input casing, OPPOSITE exit-code semantics (vscode.py).
"""

import json
import sys

from host.base import Host

# Lowercased tool names that mean "run a shell command" on this host family.
# DATED POLICY DEBT MARKER (2026-07, audit mcp_6510d831 F-006): Copilot's tool
# names are not exhaustively documented, so normalization is allowlist+hints
# and ANY unrecognized tool passes through -> the gates ALLOW it (fail open;
# misclassifying the wrong content would be worse). When the upstream tool
# surface stabilizes, tighten these lists and re-date this comment — silent
# accumulation here is un-gated tool surface.
_SHELL_NAMES = {"bash", "shell", "run_in_terminal", "execute", "exec",
                "run_command", "terminal"}
_WRITE_HINTS = ("edit", "write", "create", "replace", "str_replace", "patch",
                "apply")


class CopilotCliHost(Host):
    name = "copilot_cli"

    capabilities = dict(Host.capabilities, **{
        "install": "hooks_config_file",          # .github/hooks/*.json via tvai init
        "supports_advisory_context": False,
        "generic_nonzero_fails_closed": True,    # drives the containment below
        "stderr_reaches_model": "unknown",
    })

    # -- lifecycle ----------------------------------------------------------

    def run(self, fn):
        """§3.5 fail-closed containment — AN EXPLICIT SECURITY TRADEOFF, by
        design and not by accident (audit mcp_6510d831 F-001). Do not "fix"
        this to re-raise:
          1. Copilot interprets ANY non-zero hook exit as DENY (fail closed).
          2. An uncontained exception would therefore spuriously DENY the
             user's edit on OUR bug — violating the product-wide invariant
             that the gate never blocks on its own error.
          3. So containment converts host/runtime failure into an EXPLICIT
             allow (stderr says so; stdout carries a well-formed allow JSON
             rather than a truncated stream Copilot might misparse).
          4. BaseException — not Exception — is caught ON PURPOSE: a
             MemoryError or KeyboardInterrupt must not deny an edit either.
        The gate normally sys.exit(0)s itself via emit_*; SystemExit(0/None)
        passes through untouched so a clean deny is never double-emitted."""
        try:
            fn()
            sys.exit(0)
        except SystemExit as e:
            if e.code in (0, None):
                raise
            sys.stderr.write(
                "TruVerifAI: gate error (exit %r), failing open\n" % e.code)
            print(json.dumps({"permissionDecision": "allow"}))
            sys.exit(0)
        except BaseException as exc:
            try:
                sys.stderr.write("TruVerifAI: gate error (%s), failing open\n"
                                 % exc.__class__.__name__)
                print(json.dumps({"permissionDecision": "allow"}))
            except BaseException:
                pass
            sys.exit(0)

    # -- input -------------------------------------------------------------

    def normalize_input(self, raw):
        out = self._camel_common(raw)
        tool = str(out.get("tool_name") or "")
        low = tool.lower()
        ti = out.get("tool_input") or {}
        if low in _SHELL_NAMES:
            out["tool_name"] = "Bash"
            if "command" not in ti:
                cmd = ti.get("cmd") or ti.get("commandLine") or ti.get("input")
                if isinstance(cmd, str):
                    ti = dict(ti)
                    ti["command"] = cmd
            out["tool_input"] = ti
            return out
        if any(h in low for h in _WRITE_HINTS):
            mapped_tool, mapped_ti = self.map_write_input(ti)
            if mapped_tool:
                out["tool_name"] = mapped_tool
                out["tool_input"] = mapped_ti
            # unrecognized write shape: pass through -> gates allow (fail open)
        return out

    # -- output ------------------------------------------------------------
    # Top-level permissionDecision (GitHub docs schema), NOT hookSpecificOutput.

    def emit_deny(self, reason, system_message=None):
        print(json.dumps({
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }))
        sys.exit(0)

    def emit_ask(self, reason, system_message=None):
        print(json.dumps({
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }))
        sys.exit(0)

    def emit_allow_advisory(self, additional_context):
        # No documented model-visible advisory channel: degrade to stderr.
        try:
            sys.stderr.write("TruVerifAI: " + additional_context + "\n")
        except Exception:
            pass
        sys.exit(0)


