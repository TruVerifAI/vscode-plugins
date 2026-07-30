"""VS Code (Copilot agent) host adapter.

Built SEPARATELY from the Claude Code plugin by owner decision (plan §2.2):
a VS-Code-specific fix must never touch a plugin with live Claude Code
installs. The bundle is `TruVerifAI/vscode-plugins`.

Wire contract (code.visualstudio.com/docs/agent-customization/hooks, Preview):
- input casing is the Copilot-family camelCase (sessionId/toolName/toolArgs) —
  VS Code parses Copilot-CLI-style configs and converts event names itself.
- deny/ask use the Claude-style `hookSpecificOutput.permissionDecision`
  envelope — the BASE class emits exactly that, so emits are inherited.
- exit semantics are the OPPOSITE of the CLI sibling: exit 2 = blocking
  error, any other non-zero = non-blocking warning -> CONTINUES (fail open).
  No containment override needed; the base lifecycle + launcher belt suffice.
- hooks are Preview upstream: pin + watch (nightly doc-diff job).

Same vendor as copilot_cli, near-identical contract, inverted safety
semantics — which is precisely why they are two adapters, not one with flags
(the difference is load-bearing and must be impossible to blur).
"""

from host.base import Host
from host.copilot_cli import _SHELL_NAMES, _WRITE_HINTS


class VsCodeHost(Host):
    name = "vscode"

    capabilities = dict(Host.capabilities, **{
        "install": "agent_plugin",               # Chat: Install Plugin From Source
        "generic_nonzero_fails_closed": False,   # OPPOSITE of copilot_cli
        "supports_advisory_context": False,      # not documented; degrade
        "stderr_reaches_model": "unknown",
        "stability": "preview",
    })

    manifest_paths = ("plugin.json",) + Host.manifest_paths

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
        return out

    def emit_allow_advisory(self, additional_context):
        import sys
        try:
            sys.stderr.write("TruVerifAI: " + additional_context + "\n")
        except Exception:
            pass
        sys.exit(0)
