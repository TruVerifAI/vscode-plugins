"""OpenAI Codex CLI host adapter.

Codex's PreToolUse contract is character-identical to Claude Code's on the
OUTPUT side (`hookSpecificOutput.permissionDecision` + reason — learn.chatgpt.com
/docs/hooks), so every emit inherits from the base class unchanged. What
differs:

- config: no CLAUDE_PLUGIN_OPTION_* env. Options come from the shared chain
  (TVAI_* env / ~/.truverifai/config.json); there is no documented per-plugin
  userConfig env injection to read natively.
- input: same snake_case common fields (session_id/cwd/tool_name/tool_input,
  plus tool_use_id we don't need). Shell is `Bash` (canonical) — identity. The
  write tool is `apply_patch`, whose input IS a patch envelope — converted here
  to a unified diff and handed to the core as PrebuiltDiff. Codex builds may
  also expose claude-style Edit/Write; those pass through untouched.
- capabilities: hooks are BETA upstream (pin + watch; the nightly doc-diff job
  covers drift).

apply_patch envelope (the documented "*** Begin Patch" format):

    *** Begin Patch
    *** Update File: path/to/file.py
    @@ optional locator
     context line
    -removed line
    +added line
    *** Add File: path/new.py
    +every line of the new file
    *** Delete File: path/old.py
    *** End Patch

Conversion failure -> the ORIGINAL payload passes through with its native tool
name, which the write gate doesn't recognize -> allow (fail open). A wrong
guess would classify the wrong content — worse than no gate.

THREAT MODEL (audit mcp_653e9bc4 F-002 — stated explicitly, not assumed):
the apply_patch envelope is authored by the Codex HOST from the model's tool
call; this adapter trusts the host to emit its documented format. An attacker
who can shape envelope CONTENT (adversarial prompting producing a deliberately
unparseable envelope) can reach the fail-open path — that is an ACCEPTED v1
boundary, consistent with the product-wide invariant that the gate never
blocks on its own uncertainty. Mitigations shipped with v1: every parse
failure emits a loud, greppable stderr signal (below) so the fail-open is
OBSERVABLE in the transcript, and the server-side gate-health panel surfaces
the systemic signature (reviews landing, no gate fires). A config flag for a
fail-closed posture on parse failure is a planned follow-up once telemetry
shows the real-world parse-failure rate — flipping it is a product decision,
not an adapter default.
"""

import sys

from host.base import Host

# Machine-greppable marker for the parse-failure fail-open (mirrors the
# registry's TVAI_GATE_MISCONFIGURED pattern; `tvai doctor` greps for it).
PARSE_FAILOPEN_MARKER = "TVAI_APPLY_PATCH_UNPARSEABLE"


class CodexHost(Host):
    name = "codex"

    capabilities = dict(Host.capabilities, **{
        "install": "marketplace_plugin",   # codex plugin marketplace add TruVerifAI/codex-plugins
        "stability": "beta",               # upstream hooks are beta
    })

    manifest_paths = (
        "/".join((".codex-plugin", "plugin.json")),
    ) + Host.manifest_paths

    def normalize_input(self, raw):
        out = dict(raw or {})
        if out.get("tool_name") == "apply_patch":
            ti = out.get("tool_input") or {}
            patch = ti.get("input") or ti.get("patch") or ""
            diff, first_path = _apply_patch_to_unified(patch)
            if diff:
                out["tool_name"] = "PrebuiltDiff"
                out["tool_input"] = {"prebuilt_diff": diff, "file_path": first_path}
            elif patch:
                # Fail-open MUST be observable (audit mcp_653e9bc4 F-001): the
                # write proceeds ungated, and this line is the transcript's
                # record of that fact. Greppable marker for doctor/telemetry.
                sys.stderr.write(
                    "TruVerifAI: %s — apply_patch envelope could not be parsed; "
                    "this write was NOT gated (failing open, never blocking). "
                    "If this repeats, run `tvai doctor`.\n" % PARSE_FAILOPEN_MARKER)
            # leave as apply_patch -> the gates don't recognize it -> allow
        return out


def _apply_patch_to_unified(patch_text):
    """Convert Codex's apply_patch envelope to a unified diff.

    Returns (diff, first_path) or ("", "") when the envelope can't be parsed
    confidently. Hunk bodies (context/+/- lines) are carried VERBATIM — the
    classifier reads added lines and per-hunk hashes must match what a natural
    agent gate_diff over the same change produces. `@@` locator lines become
    hunk headers; a file section with +/- lines but no `@@` gets one synthetic
    all-encompassing header (correct for Add File, which is all-adds)."""
    if not patch_text or "*** Begin Patch" not in patch_text:
        return "", ""
    files = []       # (path, kind, body_lines)
    path = None
    kind = None
    body = []
    try:
        for ln in patch_text.splitlines():
            if ln.startswith("*** Begin Patch") or ln.startswith("*** End Patch"):
                continue
            m = None
            for marker, k in (("*** Update File: ", "update"),
                              ("*** Add File: ", "add"),
                              ("*** Delete File: ", "delete"),
                              ("*** Move to: ", "move")):
                if ln.startswith(marker):
                    m = (ln[len(marker):].strip(), k)
                    break
            if m:
                if m[1] == "move":
                    # Rename target for the CURRENT file section — record and move on.
                    continue
                if path is not None:
                    files.append((path, kind, body))
                path, kind = m
                body = []
                continue
            if path is not None:
                body.append(ln)
        if path is not None:
            files.append((path, kind, body))
        if not files:
            return "", ""

        chunks = []
        for path, kind, body in files:
            if kind == "delete":
                # A deletion has no added content to classify; representing it as
                # a removal-only hunk keeps removed-guard signals visible.
                old_lines = [l for l in body if l.strip()]
                header = ("diff --git a/%s b/%s\n--- a/%s\n+++ /dev/null\n"
                          % (path, path, path))
                hunk = "@@ -1,%d +0,0 @@\n" % max(len(old_lines), 1)
                chunks.append(header + hunk +
                              "\n".join("-" + l for l in old_lines) + "\n")
                continue
            header = ("diff --git a/%s b/%s\n--- %s\n+++ b/%s\n"
                      % (path, path,
                         "/dev/null" if kind == "add" else "a/" + path, path))
            if kind == "add":
                adds = ["+" + l for l in body]
                chunks.append(header + "@@ -0,0 +1,%d @@\n" % len(body)
                              + "\n".join(adds) + "\n")
                continue
            # update: keep @@ sections; if none, wrap the body in one hunk header
            # (line numbers are approximate — the classifier hashes CONTENT, and
            # coverage binding is content-hash-based, not position-based).
            if any(l.startswith("@@") for l in body):
                out_lines = []
                for l in body:
                    out_lines.append(l if l[:1] in ("@", "+", "-", " ") else " " + l)
                chunks.append(header + "\n".join(out_lines) + "\n")
            else:
                plus_minus = [l for l in body if l[:1] in ("+", "-")]
                if not plus_minus:
                    continue  # nothing changed that we can see
                chunks.append(header + "@@ -1,1 +1,1 @@\n" + "\n".join(
                    l if l[:1] in ("+", "-", " ") else " " + l for l in body) + "\n")
        if not chunks:
            return "", ""
        return "".join(chunks), files[0][0]
    except Exception:
        return "", ""
