#!/usr/bin/env bash
# TruVerifAI plugin — PostToolUse hook for commit-detected adherence telemetry.
#
# What this script does (privacy-locked design — see plugin README and the
# A11 data-handling document):
#
#   IF the agent invoked Bash with a git-commit command AND it succeeded:
#     POST { occurred_at, session_id, plugin_version, event_id } to
#     https://api.truverif.ai/api/mcp/usage/commit-detected
#
# What this script DOES NOT report (privacy lock):
#
#   - Commit message
#   - File paths / changed files
#   - Diff content / patch text
#   - Branch name
#   - Repository identifier or URL
#   - Working directory
#   - Raw command text
#   - Commit SHA
#
# Failure modes are intentionally silent and non-fatal so the hook never
# blocks Claude Code:
#   - Missing jq:                 exit 0 silently
#   - Missing api_token:          exit 0 silently
#   - Non-Bash tool invocation:   exit 0 silently (filter)
#   - Non-commit Bash command:    exit 0 silently (filter)
#   - Bash command that failed:   exit 0 silently (avoid counting reverted attempts)
#   - Telemetry disabled by user: exit 0 silently
#   - Network failure:            exit 0 silently after curl --max-time 5
#
# Idempotency: each invocation gets a v4 UUID; the backend's partial unique
# index dedupes any retries scoped to (user_id, event_type, client_event_id).

set -u

# Read the hook input JSON (Claude Code pipes it to stdin).
input=$(cat 2>/dev/null) || exit 0
[ -z "$input" ] && exit 0

# jq must be available. If not, exit silently — degraded telemetry is
# always preferable to a broken hook.
command -v jq >/dev/null 2>&1 || exit 0

# Check the user-config toggle. The user-facing enable_adherence_telemetry
# option was removed in 0.1.16 (the adherence card it fed was retired —
# see mcp-roadmap.md item #4), so the env var is normally unset and this
# hook no-ops by default. The commit-detected ingestion stream is kept
# forward-compatible: telemetry only fires if the var is explicitly set.
enabled="${CLAUDE_PLUGIN_OPTION_ENABLE_ADHERENCE_TELEMETRY:-false}"
[ "$enabled" = "true" ] || exit 0

# Filter: only Bash tool invocations.
tool_name=$(printf '%s' "$input" | jq -r '.tool_name // empty')
[ "$tool_name" = "Bash" ] || exit 0

# Filter: only commands that look like a git commit. Match "git commit"
# preceded by start-of-string or whitespace (so "ggit commit" or
# "agitcommit" don't false-positive, but "git add . && git commit -m..."
# does match).
command=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')
[ -z "$command" ] && exit 0
printf '%s' "$command" | grep -qE '(^|[[:space:]])git[[:space:]]+commit' || exit 0

# Filter: the bash command must have succeeded. Claude Code surfaces a
# non-zero exit / error via .tool_response.error or by including stderr
# noise. Be conservative — if there's any error indication, skip.
error_field=$(printf '%s' "$input" | jq -r '.tool_response.error // empty')
[ -n "$error_field" ] && exit 0

# Required: API token from userConfig. If missing, we have no auth so
# we can't post anything — exit silently.
token="${CLAUDE_PLUGIN_OPTION_API_TOKEN:-}"
[ -z "$token" ] && exit 0

# session_id is provided by Claude Code in the hook input — opaque
# string we don't parse.
session_id=$(printf '%s' "$input" | jq -r '.session_id // empty')

# Generate event_id (v4 UUID) for idempotency. Fall back to the kernel
# UUID source if uuidgen isn't installed.
event_id=$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid 2>/dev/null || true)

# occurred_at = now in ISO-8601 UTC. BSD `date` doesn't support -Iseconds
# uniformly, so explicit format string.
occurred_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Build the request body. NO command text, NO commit message, NO file
# paths. ONLY timing + opaque session identifier.
body=$(jq -nc \
    --arg event_id "$event_id" \
    --arg occurred_at "$occurred_at" \
    --arg session_id "$session_id" \
    --arg plugin_version "0.1.0" \
    '{
        event_id: $event_id,
        occurred_at: $occurred_at,
        session_id: ($session_id | select(. != "")),
        plugin_version: $plugin_version
    }')

# POST silently with a tight timeout so the hook never blocks Claude
# Code. Output, stderr, exit status all suppressed — telemetry MUST be
# best-effort.
curl -sS \
    --max-time 5 \
    -X POST \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json" \
    -d "$body" \
    "https://api.truverif.ai/api/mcp/usage/commit-detected" \
    >/dev/null 2>&1 || true

exit 0
