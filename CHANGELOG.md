# Changelog — panel-review for VS Code

## 0.18.2
- Custom floor classes: adds the `define-custom-floors` skill. Gate enforcement
  ships via the npm `@truverifai/init` vendored gates (cli_vendor 0.19.39).

## 0.18.0 (first release)
- Agent plugin: skills + MCP connection + PreToolUse write/commit gates,
  generated from the shared cross-platform gate core.
- VS Code fails OPEN on non-zero hook exits (unlike Copilot CLI) — the gate
  additionally guarantees exit 0 at the launcher.
