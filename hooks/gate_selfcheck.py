#!/usr/bin/env python3
"""Gate-endpoint self-check — verifies the HOOKS' half of the plugin.

`ping` and the MCP tools verify the MCP server half only. The review gates talk to
a DIFFERENT endpoint (the Flask backend's /api/mcp/* routes), and a failure there
is fail-open by design — so a wrong URL / bad key on the gate half is otherwise
invisible until the fail-open advisory fires. This script proves the gate half
end-to-end in one call: config resolution -> base_url -> auth -> routing.

Run it from the plugin's hooks directory (the setup command does this):
    python gate_selfcheck.py
Token resolution: CLAUDE_PLUGIN_OPTION_API_TOKEN, or TVAI_TOKEN, or argv[1].
Exit 0 = gate endpoint reachable + authorized; exit 1 = a problem it names.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_lib as g


def main():
    cfg = g.config()
    # ENV-FIRST (audit mcp_a5ee7682 F-002): setup.md injects TVAI_TOKEN explicitly —
    # it must win over any stale hook-context token. argv is debug-only (visible in
    # process listings; prefer the env forms).
    token = (os.environ.get("TVAI_TOKEN")
             or cfg.get("token")
             or (sys.argv[1] if len(sys.argv) > 1 else ""))
    if not token:
        print("FAIL: no API token (set CLAUDE_PLUGIN_OPTION_API_TOKEN / TVAI_TOKEN, "
              "or pass it as the first argument)")
        return 1
    cfg["token"] = token

    print(f"gate base_url : {cfg['base_url']}")
    # An empty-hunks coverage check is the cheapest authorized round-trip the gate
    # endpoint offers: it mints nothing, charges nothing, and a healthy server
    # answers {"covered": true, ...} immediately.
    resp = g.check_audit_coverage(cfg, "selfcheck", [])
    if resp is None:
        print("FAIL: the gate endpoint did not answer (wrong base_url, network, "
              "auth rejection, or server down). The gates will FAIL OPEN — they "
              "are NOT enforcing until this is fixed.")
        return 1
    if resp.get("covered") is not True or resp.get("gate_self_coverage") is not True:
        # gate_self_coverage proves the empty-hunks SHORT-CIRCUIT path answered
        # (audit mcp_a5ee7682 F-004) — an incidental covered:true from another
        # branch must not pass the self-check.
        print(f"FAIL: unexpected gate-endpoint response: {resp!r}")
        return 1
    print("PASS: gate endpoint reachable and authorized "
          f"(gate_self_coverage={resp.get('gate_self_coverage')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
