"""Host adapter registry — resolves WHICH AI coding agent this gate is running under.

The gate core is platform-agnostic (classification, hashing, receipts, floor logic);
everything host-specific — env-var names, stdin payload shape, deny-output JSON,
exit-code semantics — lives in one small adapter per host (implementation plan §2,
docs/MCP/Cross platform adoption/).

Resolution is EXPLICIT, never sniffed (a misdetected host emits the wrong deny
payload, which on most hosts degrades to fail-open — a silently dead gate):

  1. `TVAI_PLATFORM` env var — set by the generated hook config at the call site
     (e.g. `run_gate.sh codex audit_gate.py` exports it).
  2. UNSET -> `claude_code` — the platform the original plugin shipped on, so a
     legacy hooks.json with no host arg keeps today's exact behavior.

UNSET vs EXPLICITLY-WRONG are different cases (audit F-002, 2026-07-29):
  - unset            -> claude_code, silently (absent configuration, valid default)
  - set-but-unknown or set-but-broken -> that is a CONFIGURATION ERROR. We do NOT
    silently run claude_code semantics (a rollout bug would masquerade as "the
    gate allowed it") and we do NOT hard-fail (on fail-CLOSED hosts a non-zero
    exit DENIES the user's edit — the never-deadlock invariant wins). Instead: a
    machine-greppable `TVAI_GATE_MISCONFIGURED` stderr marker + a NullHost that
    allows everything and emits NO wire JSON. `tvai doctor` greps the marker.
"""

import os
import sys

_CURRENT = None

MISCONFIGURED_MARKER = "TVAI_GATE_MISCONFIGURED"

# name -> (module, class). Lazy import so a broken adapter for host X can never
# break gates running under host Y.
_REGISTRY = {
    "claude_code": ("host.claude_code", "ClaudeCodeHost"),
    "codex": ("host.codex", "CodexHost"),
    "copilot_cli": ("host.copilot_cli", "CopilotCliHost"),
    "vscode": ("host.vscode", "VsCodeHost"),
    "cursor": ("host.cursor", "CursorHost"),
    "cursor_cli": ("host.cursor", "CursorCliHost"),
    "gemini": ("host.gemini", "GeminiHost"),
    "antigravity": ("host.antigravity", "AntigravityHost"),
    "git_precommit": ("host.git_precommit", "GitPrecommitHost"),
}


class NullHost(object):
    """The explicit-misconfiguration host: every gate action degrades to a plain
    allow (exit 0, NO stdout JSON — emitting another host's wire format would be
    exactly the silent-wrong-payload failure explicit resolution exists to
    prevent). The gates are OFF while this host is active, and stderr says so
    loudly on every hook invocation."""
    name = "misconfigured"
    capabilities = {"write_gate": False, "commit_gate": False,
                    "structured_deny": False, "supports_ask": False,
                    "supports_advisory_context": False,
                    "generic_nonzero_fails_closed": False,
                    "stderr_reaches_model": "unknown"}
    manifest_paths = ()

    def native_option(self, name):
        return None

    def normalize_input(self, raw):
        return raw or {}

    def _bail(self):
        sys.exit(0)

    def emit_deny(self, reason, system_message=None):
        self._bail()

    def emit_ask(self, reason, system_message=None):
        self._bail()

    def emit_allow(self, note=None):
        self._bail()

    def emit_allow_advisory(self, additional_context):
        self._bail()


def _misconfigured(name, why):
    sys.stderr.write(
        "TruVerifAI: %s — TVAI_PLATFORM=%r %s. The gates are NOT enforcing on "
        "this host until the platform config is fixed (run `tvai doctor`). "
        "Failing open, never blocking.\n" % (MISCONFIGURED_MARKER, name, why))
    return NullHost()


def resolve(name=None):
    """Instantiate the adapter for `name` (default: $TVAI_PLATFORM or claude_code)."""
    explicit = name if name is not None else os.environ.get("TVAI_PLATFORM")
    if explicit is None or not explicit.strip():
        # Absent configuration — the valid legacy default, silently.
        from host.claude_code import ClaudeCodeHost
        return ClaudeCodeHost()
    key = explicit.strip().lower()
    entry = _REGISTRY.get(key)
    if entry is None:
        return _misconfigured(explicit, "is not a known platform")
    mod_name, cls_name = entry
    try:
        import importlib
        mod = importlib.import_module(mod_name)
        return getattr(mod, cls_name)()
    except Exception as exc:  # never trap the agent on our own import error
        return _misconfigured(explicit, "adapter failed to load (%s)" % exc)


def current():
    """The process-wide host singleton (hooks are short-lived one-shot processes)."""
    global _CURRENT
    if _CURRENT is None:
        _CURRENT = resolve()
    return _CURRENT


def set_current(host_obj):
    """Test hook / explicit override (used by conformance tests)."""
    global _CURRENT
    _CURRENT = host_obj
