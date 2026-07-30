"""Host base class — the adapter contract every platform implements.

The base class IS the Claude Code wire behavior (Codex's PreToolUse contract is
character-identical, and several others are near-identical), so most adapters
override only what genuinely differs. The contract (implementation plan §2.1):

    normalize_input(raw)   stdin JSON -> claude-shaped payload (the core's
                           internal vocabulary: Bash / Write / Edit / MultiEdit /
                           PrebuiltDiff + claude-shaped tool_input fields)
    native_option(name)    host-native config lookup (env injected by the host's
                           plugin system), or None
    emit_deny / emit_ask / emit_allow / emit_allow_advisory
                           final wire output + exit. Receives FINAL strings —
                           version stamping etc. is composed by gate_lib BEFORE
                           delegation, so adapters stay dependency-free.
    capabilities           dict of static facts the core may consult

Hard invariants every adapter MUST keep:
  - fail OPEN: any internal error ends in exit(0) allow, never a stuck agent.
  - the deny reason must REACH THE MODEL (that is the entire product — a block
    without routing text is a wall, not a router).
  - never print secrets.

NOTE: adapters must not import gate_lib (gate_lib imports host — keep the
dependency one-way).
"""

import json
import os
import sys


class Host(object):
    name = "base"

    # -- capabilities ------------------------------------------------------
    # Static facts about the host. `write_gate`/`commit_gate` describe what the
    # host DELIVERS today; per plan §2.5 these drive COPY and doctor output only,
    # never control flow — hooks stay registered even where an event is not
    # currently delivered, so an upstream fix turns the gate on with no release.
    capabilities = {
        "write_gate": True,
        "commit_gate": True,
        "structured_deny": True,
        "supports_ask": True,
        "supports_advisory_context": True,
        "generic_nonzero_fails_closed": False,
        "stderr_reaches_model": "yes",
    }

    # Manifest filenames plugin_version() probes, relative to the plugin root
    # (first hit wins). Ordered: own-host manifest first, then the others so a
    # mixed install still stamps a version.
    manifest_paths = (
        os.path.join(".claude-plugin", "plugin.json"),
        os.path.join(".codex-plugin", "plugin.json"),
        os.path.join(".cursor-plugin", "plugin.json"),
        "plugin.json",
        "gemini-extension.json",
    )

    # -- lifecycle ---------------------------------------------------------

    def run(self, fn):
        """Execute a gate entrypoint. Base: no wrapper — the launcher's
        exit-0 coercion is the belt on fail-open hosts. Fail-CLOSED hosts
        (copilot_cli) override with total exception containment (§3.5)."""
        fn()

    # -- config ------------------------------------------------------------

    def native_option(self, name):
        """Host-native value for option `name` (lowercase snake, e.g. 'api_token'),
        or None when the host has no native mechanism / no value."""
        return None

    # -- input -------------------------------------------------------------

    def normalize_input(self, raw):
        """Map the host's PreToolUse-equivalent payload onto the core vocabulary.

        Returns a dict with (at least) tool_name / tool_input / cwd / session_id.
        Base = Claude Code shape = identity. Adapters translate tool names, field
        casing, and write-tool input shapes; an unrecognized tool passes through
        untouched (the gates allow anything they don't recognize — fail open)."""
        return raw or {}

    # -- helpers shared by camelCase hosts ----------------------------------

    @staticmethod
    def _camel_common(raw):
        """Map Copilot-family camelCase common fields onto snake_case."""
        out = dict(raw or {})
        for camel, snake in (("toolName", "tool_name"), ("toolArgs", "tool_input"),
                             ("toolInput", "tool_input"), ("sessionId", "session_id"),
                             ("workingDirectory", "cwd")):
            if camel in out and snake not in out:
                out[snake] = out[camel]
        return out

    @staticmethod
    def map_write_input(tool_input):
        """Best-effort mapping of a foreign write-tool input onto claude Write/Edit
        fields. Returns (normalized_tool_name, normalized_tool_input) or (None, None)
        when the shape is unrecognized (caller falls through -> gate allows).

        Key aliases seen across hosts' file tools; unknown shapes fail open by
        design — a wrong guess here would classify the WRONG content, which is
        worse than no gate (silently misleading)."""
        ti = dict(tool_input or {})
        path = ti.get("file_path") or ti.get("filePath") or ti.get("path") or ""
        old = ti.get("old_string") if ti.get("old_string") is not None else (
            ti.get("oldText") if ti.get("oldText") is not None else ti.get("old_str"))
        new = ti.get("new_string") if ti.get("new_string") is not None else (
            ti.get("newText") if ti.get("newText") is not None else ti.get("new_str"))
        content = ti.get("content") if ti.get("content") is not None else (
            ti.get("contents") if ti.get("contents") is not None else ti.get("text"))
        if path and old is not None and new is not None:
            return "Edit", {"file_path": path, "old_string": old, "new_string": new}
        if path and content is not None:
            return "Write", {"file_path": path, "content": content}
        return None, None

    # -- output ------------------------------------------------------------
    # Base implements the Claude Code / Codex wire format.

    def emit_deny(self, reason, system_message=None):
        out = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
        if system_message:
            out["systemMessage"] = system_message
        print(json.dumps(out))
        sys.exit(0)

    def emit_ask(self, reason, system_message=None):
        out = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": reason,
            }
        }
        if system_message:
            out["systemMessage"] = system_message
        print(json.dumps(out))
        sys.exit(0)

    def emit_allow(self, note=None):
        if note:
            sys.stderr.write("TruVerifAI: " + note + "\n")
        sys.exit(0)

    def emit_allow_advisory(self, additional_context):
        """Allow + model-visible advisory. Base uses Claude Code's additionalContext
        (no permissionDecision — the normal permission flow still applies). Hosts
        without an advisory channel override to a stderr note."""
        try:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": additional_context,
            }}))
            sys.stdout.flush()
        except Exception:
            pass
        sys.exit(0)
