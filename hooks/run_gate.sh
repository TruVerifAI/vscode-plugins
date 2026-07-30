#!/usr/bin/env bash
# TruVerifAI gate launcher — resolve a WORKING Python and run the named gate
# script, forwarding the PreToolUse JSON on stdin.
#
# Usage:
#   run_gate.sh <gate>.py          # legacy single-arg form -> host = claude_code
#   run_gate.sh <host> <gate>.py   # explicit host (codex, cursor, gemini, ...)
#
# The host is passed EXPLICITLY at the call site (never sniffed from the payload
# — a misdetected host emits the wrong deny JSON, which on most hosts degrades
# to a silently dead gate). It is exported as TVAI_PLATFORM for the gate's host
# adapter registry. The single-arg form keeps a legacy Claude Code hooks.json
# byte-compatible: no TVAI_PLATFORM -> claude_code default.
#
# Why the probe: hooks config must not hardcode `python3`. On Windows `python3`
# is the App-Execution-Alias stub (prints "Python was not found", exits 49), so
# a `python3 ...` hook command errors and the host fails OPEN — the gate
# silently no-ops. `"$c" -c ''` actually invokes the interpreter, so the stub
# fails the probe and we fall through to `python`. Fails open (exit 0) if no
# Python is found — the gate must never trap the agent.
DIR="$(cd "$(dirname "$0")" && pwd)"
if [ $# -ge 2 ]; then
  export TVAI_PLATFORM="$1"
  SCRIPT="$2"
else
  SCRIPT="$1"
fi
# No `exec`, and ALWAYS exit 0: our gates deny via stdout JSON, never via exit
# codes, so any non-zero exit from the interpreter is an ERROR (traceback, bad
# install) — and on fail-CLOSED hosts (Copilot CLI: any non-zero exit DENIES the
# tool call) propagating it would block the user's edit on our own bug. Coercing
# to 0 is the interpreter-level fail-open belt; the in-process BaseException
# catch in the copilot adapter is the other (implementation plan §3.5).
for c in python3 python py; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c '' >/dev/null 2>&1; then
    "$c" "$DIR/$SCRIPT"
    exit 0
  fi
done
exit 0
