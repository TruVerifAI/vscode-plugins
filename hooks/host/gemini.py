"""Gemini CLI host adapter.

Wire contract (google-gemini/gemini-cli docs/hooks/reference.md):
- events are BeforeTool / AfterTool (not PreToolUse); matchers are REGEX over
  tool names — the generated configs must not assume exact-match semantics.
- input: snake_case base fields (session_id, cwd, hook_event_name) +
  tool_name / tool_input. Built-in tools: run_shell_command {command},
  write_file {file_path, content}, replace {file_path, old_string,
  new_string} — mapped onto the core vocabulary below.
- deny: {"decision": "deny", "reason": ...} — the reason is surfaced to the
  agent as a tool error (the routing text reaches the model). Exit 2 with
  stderr also blocks; we use the JSON path.
- `ask` is not a documented decision -> degrades to allow-with-warning.
- hooks live in the extension's hooks/hooks.json or settings.json; the
  gemini-extension repo root IS the extension (plan §2.1).
"""

import json
import sys

from host.base import Host


class GeminiHost(Host):
    name = "gemini"

    capabilities = dict(Host.capabilities, **{
        "install": "gemini_extension",     # gemini extensions install <url>
        "supports_ask": False,
        "supports_advisory_context": False,
        "matcher_syntax": "regex",         # unlike every other host
        "stderr_reaches_model": "likely",  # exit-2 stderr is a documented deny path
    })

    manifest_paths = ("gemini-extension.json",) + Host.manifest_paths

    def normalize_input(self, raw):
        out = dict(raw or {})
        tool = str(out.get("tool_name") or "")
        ti = out.get("tool_input") or {}
        if tool == "run_shell_command":
            out["tool_name"] = "Bash"
            return out
        if tool == "write_file":
            out["tool_name"] = "Write"
            return out
        if tool == "replace":
            # Gemini's replace(file_path, old_string, new_string) IS an Edit.
            out["tool_name"] = "Edit"
            return out
        if tool in ("edit_file", "edit"):
            mapped_tool, mapped_ti = self.map_write_input(ti)
            if mapped_tool:
                out["tool_name"] = mapped_tool
                out["tool_input"] = mapped_ti
        return out

    def emit_deny(self, reason, system_message=None):
        # `reason` is surfaced to the agent as the tool error; system_message
        # has no separate channel here and reason already carries the routing.
        print(json.dumps({"decision": "deny", "reason": reason}))
        sys.exit(0)

    def emit_ask(self, reason, system_message=None):
        sys.stderr.write(
            "TruVerifAI: TVAI_ASK_DEGRADED — human-confirmation requested but "
            "this host has no 'ask' decision; allowing with warning: "
            + reason + "\n")
        sys.exit(0)

    def emit_allow_advisory(self, additional_context):
        try:
            sys.stderr.write("TruVerifAI: " + additional_context + "\n")
        except Exception:
            pass
        sys.exit(0)
