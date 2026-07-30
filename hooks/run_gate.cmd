@echo off
rem TruVerifAI gate launcher (Windows cmd) — mirror of run_gate.sh for hosts
rem whose hook commands run under cmd.exe rather than a POSIX shell.
rem
rem Usage:
rem   run_gate.cmd <gate>.py          (host defaults to claude_code)
rem   run_gate.cmd <host> <gate>.py   (explicit host, exported as TVAI_PLATFORM)
rem
rem Probes py -> python -> python3 (py first: the Windows launcher is the most
rem reliable; `python3` is often the App-Execution-Alias stub). Fails OPEN
rem (exit 0) when no Python works — the gate must never trap the agent.
setlocal
set "DIR=%~dp0"
if "%~2"=="" (
  set "SCRIPT=%~1"
) else (
  set "TVAI_PLATFORM=%~1"
  set "SCRIPT=%~2"
)
rem ALWAYS exit 0: our gates deny via stdout JSON, never via exit codes, so any
rem non-zero interpreter exit is an ERROR — and on fail-CLOSED hosts (Copilot
rem CLI) propagating it would deny the user's edit on our own bug (plan 3.5).
for %%c in (py python python3) do (
  %%c -c "" >nul 2>&1 && (
    %%c "%DIR%%SCRIPT%"
    exit /b 0
  )
)
exit /b 0
