"""Google Antigravity host adapter.

Wire contract (antigravity.google/docs/hooks):
- PreToolUse / PostToolUse / PreInvocation / PostInvocation / Stop.
- deny: {"decision": "allow" | "deny" | "ask" | "force_ask", "reason": ...}.
  `ask` IS enforced here (respects cached permissions), so emit_ask keeps it.
- config lives in hooks.json under .agents/ (workspace) or ~/.gemini/config/;
  plugins are drop-in bundles (no CLI install, no marketplace yet).
- no native secrets mechanism: the key comes from TVAI_API_KEY env or
  ~/.truverifai/config.json (the tvai-login path) — plan §3.3.
- tool naming follows the Gemini family (shared lineage); normalization
  mirrors gemini.py with the same fail-open-on-unknown posture.
"""

import json
import sys

from host.base import Host
from host.gemini import GeminiHost


class AntigravityHost(GeminiHost):
    name = "antigravity"

    capabilities = dict(GeminiHost.capabilities, **{
        "install": "dropin_plugin",        # .agents/plugins/ or ~/.gemini/config/plugins/
        "supports_ask": True,              # ask/force_ask are documented decisions
        "secrets": "none",                 # config-file / env only
        "stderr_reaches_model": "unknown",
    })

    manifest_paths = ("plugin.json",) + Host.manifest_paths

    def emit_ask(self, reason, system_message=None):
        print(json.dumps({"decision": "ask", "reason": reason}))
        sys.exit(0)
