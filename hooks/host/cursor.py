"""Cursor host adapters — TWO surfaces with different event delivery (plan §7.2).

CursorHost (IDE): the marketplace plugin fires `preToolUse` for all tool types
(matcher `Write`) and `beforeShellExecution` for shell. The write gate binding
is ASSUMED to deny per the owner decision (plan §0) — the `write_gate_ASSUMED`
capability drives COPY/doctor output only, never registration.

CursorCliHost (cursor-agent): as of 2026-07 the CLI fires ONLY the shell
events, and marketplace-plugin hooks don't fire at all — so `tvai init`
writes repo-level `.cursor/hooks.json` and the CLI ships commit-gate-only.
The write hook is REGISTERED anyway (§2.5 rule 1): the day Cursor delivers
the event, the gate starts working with no release from us. The capability
below is a DATED OBSERVATION, not a switch.

Wire contract (cursor.com/docs/agent/hooks):
- input: snake_case (tool_name/tool_input/cwd), conversation-scoped ids; write
  tool type is `Write`, shell is `Shell` with camelish input shapes -> mapped
  by shape.
- deny: stdout {"permission": "deny", "agent_message": ..., "user_message":
  ...} — agent_message reaches the MODEL (the routing text), user_message the
  human. exit 0 = parsed; other non-zero = fail open. `ask` is accepted by the
  schema but NOT enforced -> emit_ask degrades to allow-with-warning (the base
  contract: an unsupported ask must never hard-trap).
"""

import json
import sys

from host.base import Host


class CursorHost(Host):
    name = "cursor"

    capabilities = dict(Host.capabilities, **{
        "install": "marketplace_plugin",
        "write_gate": "assumed_2026_07",   # owner decision; copy/doctor only
        "supports_ask": False,             # schema accepts, does not enforce
        "supports_advisory_context": False,
        "separate_user_message": True,
        "stderr_reaches_model": "unknown",
    })

    manifest_paths = (
        "/".join((".cursor-plugin", "plugin.json")),
    ) + Host.manifest_paths

    # -- input -------------------------------------------------------------

    def normalize_input(self, raw):
        out = dict(raw or {})
        tool = str(out.get("tool_name") or "")
        ti = out.get("tool_input") or {}
        if tool in ("Shell", "shell"):
            out["tool_name"] = "Bash"
            if "command" not in ti:
                cmd = ti.get("cmd") or ti.get("commandLine") or ti.get("input")
                if isinstance(cmd, str):
                    ti = dict(ti)
                    ti["command"] = cmd
            out["tool_input"] = ti
            return out
        if tool in ("Write", "write", "Edit", "edit"):
            # Cursor's Write input shape isn't exhaustively documented; if it
            # already looks claude-shaped, keep it, else map by shape.
            if "file_path" in ti and ("content" in ti or "new_string" in ti):
                return out
            mapped_tool, mapped_ti = self.map_write_input(ti)
            if mapped_tool:
                out["tool_name"] = mapped_tool
                out["tool_input"] = mapped_ti
        return out

    # -- output ------------------------------------------------------------

    def emit_deny(self, reason, system_message=None):
        out = {"permission": "deny", "agent_message": reason}
        if system_message:
            out["user_message"] = system_message
        print(json.dumps(out))
        sys.exit(0)

    def emit_ask(self, reason, system_message=None):
        # `ask` is schema-accepted but NOT enforced by Cursor — an emitted ask
        # would silently behave as something else. Base contract: an
        # unsupported ask degrades toward allow-with-warning, never deny.
        # TVAI_ASK_DEGRADED is a machine-greppable marker (audit mcp_6510d831
        # F-002): on this host the floor backstop's human-override channel
        # degrades to a warning, and that fact must be detectable in
        # transcripts / by tvai doctor — never silent.
        sys.stderr.write(
            "TruVerifAI: TVAI_ASK_DEGRADED — human-confirmation requested but "
            "this host does not enforce 'ask'; allowing with warning: "
            + reason + "\n")
        sys.exit(0)

    def emit_allow_advisory(self, additional_context):
        try:
            sys.stderr.write("TruVerifAI: " + additional_context + "\n")
        except Exception:
            pass
        sys.exit(0)


class CursorCliHost(CursorHost):
    name = "cursor_cli"

    capabilities = dict(CursorHost.capabilities, **{
        "install": "hooks_config_file",            # .cursor/hooks.json via tvai init
        # DATED OBSERVATION (plan §2.5 rule 2): drives copy/doctor ONLY. The
        # write hook stays registered; delivery is Cursor's side to fix.
        "write_gate": "not_delivered_2026_07",
    })
