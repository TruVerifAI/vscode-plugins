#!/usr/bin/env python3
"""PreToolUse gate — TruVerifAI deliberate-before-implementing (Layer 1).

Fires before a Write / Edit / MultiEdit and blocks on the same per-hunk `gate_tightness`
predicate as the commit gate (Inc 8, Fix 5 — the legacy `deliberate_mode` knob is retired;
both gates now read `gate_tightness`, `focused` default / `thorough`):
- A blocking-class hunk (a floor hunk at any confidence, or a non-floor HIGH hunk under
  `focused`; any risky hunk under `thorough`) -> block + route to `audit_coding` (a Write is
  finished code); `deliberate_coding` is accepted for a still-open design.
- LOW-confidence borderline change -> the synthesize tier (§6.5), governed by
  `borderline_mode` (`advisory` default | `synthesize_gate` | `off`): a Heavy spike may
  soft-gate (get a `synthesize_coding` second opinion, then release with a one-line skip);
  else an advisory nudge.

Fails OPEN on anything; the `recent_pass` escape valve prevents area-misalignment
deadlock. A Write already contains finished code, so this is pre-PERSISTENCE
(not pre-decision) — see v2-hybrid §2.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_lib as g
from risk_classifier import classify_diff, is_hard_floor, floor_exempt


def _content_and_path(inp):
    ti = inp.get("tool_input") or {}
    tool = inp.get("tool_name")
    path = ti.get("file_path") or ti.get("path") or ""
    if tool == "Write":
        return path, ti.get("content", "") or ""
    if tool == "Edit":
        return path, ti.get("new_string", "") or ""
    if tool == "MultiEdit":
        edits = ti.get("edits") or []
        return path, "\n".join((e.get("new_string", "") or "") for e in edits)
    if tool == "PrebuiltDiff":
        # Host adapter pre-built a unified diff (e.g. Codex apply_patch). The
        # "content" for the empty-check / gate-self hash is the ADDED lines.
        pre = ti.get("prebuilt_diff") or ""
        added = "\n".join(ln[1:] for ln in pre.splitlines()
                          if ln.startswith("+") and not ln.startswith("+++"))
        return path, added
    return path, ""


def main():
    cfg = g.config()
    if not cfg["enabled"] or not cfg["token"]:
        g.emit_allow()

    inp = g.read_hook_input()
    # Core vocabulary post-normalization: the host adapter has already mapped its
    # native write tools onto Write / Edit / MultiEdit / PrebuiltDiff (host/base).
    if inp.get("tool_name") not in ("Write", "Edit", "MultiEdit", "PrebuiltDiff"):
        g.emit_allow()

    path, content = _content_and_path(inp)
    if not content.strip():
        g.emit_allow()

    cwd = inp.get("cwd") or os.getcwd()
    # Write-gate-deadlock-fix-v2 (Option D): classify a REAL delta (not the all-adds
    # synth_write_diff) so the fire's per-hunk content hashes match what a natural agent
    # gate_diff produces — the root-cause fix for the floor write-gate deadlock.
    # file_content_fetcher (M1 two-stage): at write-gate time the on-disk file is PRE-edit, which
    # still carries the pre-existing co-signal (e.g. a pwn `pull_request_target` trigger the agent
    # is adding a checkout to) — so reading it is correct for confirming an out-of-diff co-signal.
    classification = classify_diff(
        g.build_change_diff(inp, path, content), trigger_threshold=g.effective_threshold(cfg),
        file_content_fetcher=g.file_content_fetcher(cwd))

    # P6.3 (repo-scope suppression): a write whose target resolves OUTSIDE the working repo
    # or into a temp/scratch dir cannot be committed/merged — it can't SHIP, and the gate's
    # threat model is "review before it ships." So it never gates — EXCEPT a real secret
    # VALUE (auto_trigger -> the hardcoded_secret category), which is a leak regardless of
    # where it is written. Recall-safe by construction (an out-of-repo file has nothing to
    # review-before-merge); the secret carve-out preserves the one location-independent risk.
    # is_out_of_repo_scope fails toward REVIEW (uncertain -> not out-of-scope), so this only
    # suppresses when confident. Checked before gate-self because an out-of-repo path is
    # never a gate-self file (those live in the repo), and before the risky/advisory tiers.
    if (g.is_out_of_repo_scope(path, cwd)
            and "hardcoded_secret" not in (classification.get("risk_categories") or [])):
        g.emit_allow()

    # Gate self-mutation (§6.1, audit F-005): writing the gate's own config/hooks is
    # privilege escalation — ALWAYS require a review even if the content classifier finds
    # nothing. The gate-self branch below releases ONLY on a real PASS of THIS exact change
    # (its synthesized self-coverage hash), never recent_pass / skip / area (Option 4).
    gate_self = g.is_gate_self_mutation(path)
    if not classification["risky"] and not gate_self:
        g.emit_allow()

    repo = g.repo_fingerprint(cwd)
    session_id = inp.get("session_id")

    # Gate self-mutation (§6.1, audit F-005): writing the gate's own config/hooks is
    # privilege escalation. It releases ONLY on a real review (audit/deliberate PASS) of
    # THIS exact write — keyed on the synthesized self-coverage hash of the content being
    # written — never recent_pass, never a skip, never the coarse area-unlock (Option 4,
    # 2026-06-17). The authoritative gate-self control is the commit gate; this is the
    # symmetric pre-write layer. Still fails OPEN on infra error (no deadlock).
    if gate_self:
        write_diff = g.synth_write_diff(path, content)
        # Phase 9 (inc 5): a purely INERT gate-self write (comment/whitespace only) to a
        # NON-gate-core file releases without a review (same rule as the commit gate). gate-CORE
        # always reviews. (For a whole-file Write this rarely fires — the content has code — so
        # it's conservative; it mainly helps a comment-only Edit to a non-core gate-self file.)
        if g.diff_is_inert(write_diff) and not g.diff_touches_gate_core(write_diff):
            g.emit_allow("trivial gate-self edit (comment/whitespace only, non-core) — released")
        self_hash = g.gate_self_coverage_hash(write_diff)
        gs_resp = g.check_audit_coverage(cfg, repo, [self_hash])
        gs_action, gs_detail = g.audit_decision_gate_self(gs_resp)
        if gs_action == "deny":
            g.emit_deny(
                "TruVerifAI flagged a high-risk change for a quick review before it ships — "
                "this write edits the review gate's own settings (risk_signals.json / "
                "risk_classifier.py / gate_lib.py / hooks.json / .claude-plugin), the "
                "highest-stakes area, so the review can't be skipped.\n"
                "This is finished code, so run `audit_coding` with your proposed_action + "
                "relevant_code, AND pass:\n"
                f'  gate_repo = "{repo}"\n'
                "  gate_diff = a unified diff ADDING the file's new contents "
                "(the change you're about to write)\n"
                "TruVerifAI records the result and the write proceeds on retry. "
                "(`deliberate_coding` is accepted if it's still an open design. Gate-self "
                "changes need a real review of THIS change — they can't be skipped, and an "
                "unrelated recent review won't release them.)"
            )
        g.emit_allow(gs_detail)  # covered / fail-open

    # Non-gate-self design fork: coarse area-unlock (recent_pass escape valve OK). Send the
    # fire-time classifier metadata so the minted gate-fire context is COMPLETE (Step 0), and
    # label it with the write gate's TIER. The two blocking tiers are deliberate (high-
    # confidence fork) and synthesize (low-confidence borderline). We only reach here for a
    # risky non-gate-self change, so max_confidence is "high" or "low" (None means non-risky,
    # which returned earlier); map ONLY those two and omit gate_type otherwise so the server
    # applies its own default rather than a guessed label (audit F-001/F-002, 2026-06-26).
    # Server contract: when gate_type is omitted, /receipts/deliberate-check defaults the
    # minted fire to 'deliberate' (mcp_user_routes.receipts_deliberate_check).
    # REPO-RELATIVE '/'-form, matching how the server derives a receipt's area from the agent's
    # `relevant_paths` (which are naturally repo-relative). The area is matched as a STRING, so both
    # the separators AND the root prefix have to agree. A raw dirname of the file_path Claude Code
    # hands us is absolute AND '\'-separated on Windows — it matched nothing, so the proactive
    # downgrade / area-unlock were silently dead (prod runbook 2026-07-13; deliberation
    # mcp_fd6de1da). Falls back to the absolute '/'-form if the repo root can't be resolved; the
    # server reconciles that against a relative receipt area.
    area = g.repo_relative_area(path, cwd)
    gate_type = {"high": "deliberate", "low": "synthesize"}.get(
        classification.get("max_confidence"))
    resp = g.check_deliberate_unlock(cfg, repo, area, session_id,
                                     classification=classification, gate_type=gate_type)

    # Write-gate-deadlock fix: a Write/Edit is FINISHED code, so its natural review is `audit`.
    # If the change is already reviewed — an `audit_coding` PASS or a `synthesize_coding`
    # SYNTH_CONFIRM covers every risky hunk (server `covered`, floor-aware) — release NOW, for
    # BOTH the deliberate and the borderline/synthesize tier, before any tier-specific logic.
    # This is the primary fix that removes the deliberate-only deadlock and restores the design
    # invariant "run the review -> release" at the write gate.
    if resp and resp.get("covered"):
        g.emit_allow("change reviewed — audit / SYNTH_CONFIRM covers every risky hunk")

    action, detail = g.deliberate_decision(classification, resp,
                                           tightness=cfg["gate_tightness"])
    cats = ", ".join(sorted({h["category"] for h in classification["hunks"]}))

    # 1. Blocking-class change (a floor hunk at any confidence, or a non-floor HIGH hunk under
    #    'focused'; any risky hunk under 'thorough') -> deny. This is FINISHED code, so the
    #    natural review is `audit_coding`; `synthesize_coding` (SYNTH_CONFIRM) also releases a
    #    low-risk floor; `deliberate_coding` is accepted for a still-open design. All three write
    #    a receipt the server now reads at the write gate (covered / unlocked).
    if action == "deny":
        gcid = (resp or {}).get("gate_context_id")
        gcid_line = ("  gate_context_id = %s\n" % json.dumps(gcid)) if gcid else ""
        # FLOOR-aware release paths. A floor hunk is released ONLY by a diff-level review (an audit
        # PASS or a synthesize SYNTH_CONFIRM) — a `deliberate` area-unlock can't cover one (server
        # F-001/F-006), so the floor message does NOT offer it.
        #
        # `target_hunk_hashes` carries ALL the change's risky hunks, NOT just the floor ones
        # (2026-07-12). Forwarding a floor-ONLY list rebuilt the commit-gate deadlock here: the
        # message recommends `audit_coding`, and receipt_writer._resolve_gate_bound_hunks binds an
        # audit's coverage to EXACTLY the forwarded list (Tier 0 is a hard intersection, and for an
        # audit every fire hunk is eligible). So the agent copied the floor-only list, the PASS
        # covered the floor hunks only, the NON-floor hunks stayed uncovered, the write re-blocked,
        # and this same message printed again — forever. Sending every hash costs the floor tools
        # nothing: confirm_floor / synthesize resolve with floor_only=True, so the server's own
        # floor map filters the non-floor hashes out. `floor_hashes` still decides WHICH message to
        # print; it just no longer narrows the review.
        floor_hashes = [h["content_hash"] for h in classification.get("hunks", [])
                        if h.get("content_hash") and is_hard_floor(h.get("category"))
                        and not floor_exempt(h.get("category"), h.get("path_class"))]
        if floor_hashes:
            all_hashes = [h["content_hash"] for h in classification.get("hunks", [])
                          if h.get("content_hash")]
            hh_line = "  target_hunk_hashes = %s\n" % json.dumps(all_hashes)
            g.emit_deny(
                f"TruVerifAI flagged a {cats} change (a floor class: auth / secrets / money / "
                "migrations / removed-guard).\n"
                + g.transparency_block(classification, resp)
                + g.area_diagnostic_block(area, resp) +
                "Match the tool to your situation:\n"
                "  • A GENUINE floor change you want reviewed → `audit_coding`. A PASS (final "
                "action proceed/proceed_with_caveats) covers FLOOR "
                "and NON-floor hunks alike, so it releases the whole write in one call. This is the "
                "recommended review.\n"
                "  • You believe the gate MIS-FIRED → `confirm_floor` (FREE, one model) or "
                "`synthesize_coding` (~15-30s). Each releases the FLOOR hunks, and only if it agrees "
                "the change isn't risky. Neither touches a NON-floor hunk: if 'Still uncovered' "
                "lists any, release those with an `audit_coding` PASS or "
                "`record_gate_skip(<judgment reason>, gate_context_id)`.\n"
                "Pass to whichever you run:\n"
                f'  gate_repo = "{repo}"\n'
                "  gate_diff = the change you're about to write\n"
                + gcid_line
                + hh_line +
                "Copy `target_hunk_hashes` verbatim — it binds coverage to this change's hunks, so "
                "a cosmetically-drifted diff still releases.\n"
                "After that ONE review you can, instead of a fresh review: apply its findings and "
                "call `record_gate_skip(recommendations_applied, gate_context_id)`, or "
                "`record_gate_skip(review_deferred_to_commit, gate_context_id)` to defer to commit "
                "— both release this floor WRITE, and the commit gate re-audits the floor hunk on "
                "the real staged bytes before it ships.\n"
                "LAST RESORT — only after the paths above genuinely don't fit (the gate mis-fired, "
                "you're deadlocked, or you're consciously shipping un-reviewed): "
                "`record_gate_skip(accept_risk_no_review, gate_context_id, reason_text=<pre-mortem>)`. "
                "It ships this UN-reviewed and logs an accountable OVERRIDE "
                "to the human (NOT a review); it needs a substantive pre-mortem (assume it IS a real "
                "issue — name the failure, who it affects, why it's acceptable) and expires in minutes.\n"
                + g.gate_signal_line(classification)
            )
        # F2 (single-call model): emit the deterministic hash token on EVERY deny (the floor
        # branch above already does). ALL risky-hunk content hashes — the agent forwards them to
        # a review (or carries them into a later record_gate_skip) so coverage binds by hash, not
        # by diff shape. Empty -> nothing to bind (line dropped).
        all_hashes = [h["content_hash"] for h in classification.get("hunks", [])
                      if h.get("content_hash")]
        thh_line = ("  target_hunk_hashes = %s\n" % json.dumps(all_hashes)) if all_hashes else ""
        g.emit_deny(
            f"TruVerifAI flagged a {cats} change worth a review before it ships.\n"
            + g.transparency_block(classification, resp)
            + g.area_diagnostic_block(area, resp) +
            "This is finished code, so the natural review is `audit_coding` — run it ONCE with your "
            "proposed_action, AND pass:\n"
            f'  gate_repo = "{repo}"\n'
            "  gate_diff = the change you're about to write\n"
            + gcid_line
            + thh_line +
            "A PASS (final action proceed/proceed_with_caveats — a major finding raises the "
            "action past that) releases the gate. After that ONE review you never need a second: if it "
            "returns findings, apply them and call "
            "`record_gate_skip(recommendations_applied, gate_context_id)` to proceed; or "
            "`record_gate_skip(review_deferred_to_commit, gate_context_id)` to defer ALL review to "
            "the commit gate (releases this and the rest of the session). Passing gate_context_id "
            "(or the target_hunk_hashes above) binds coverage to the gate's own hunks, so a "
            "cosmetically-drifted diff still releases. (If the design is still open, "
            f'`deliberate_coding` is accepted — pass gate_session_id = "{session_id or ""}". A '
            "`synthesize_coding` is a fast second opinion but does NOT release a non-floor gate — "
            "use `audit_coding` to release.)\n"
            + g.skip_and_signal(classification, audit=False, area=area,
                                gate_context_id=gcid)
        )
    # Fail-open must never be SILENT (2026-07-23 — mirror of the commit gate's
    # advisory): the write proceeds, but the outage is surfaced on the FIRST write.
    if action == "allow" and detail == g.FAIL_OPEN_WRITE_DETAIL:
        g.emit_allow_advisory(
            "TruVerifAI write gate: the coverage/unlock check was UNREACHABLE, so the "
            "gate FAILED OPEN — this write was NOT gated. If this repeats, the gates "
            "are not enforcing: verify connectivity/API key (the plugin's /setup "
            "command includes a gate-endpoint self-check). Tell the user about this."
        )
    if action == "allow_warn":
        g.emit_allow(detail)  # recent_pass escape valve

    # 2. Low-confidence (borderline) change -> the synthesize tier (§6.5), governed by
    #    borderline_mode. Heavy spikes may soft-gate (synthesize OR a one-line skip);
    #    everything else is an advisory nudge. Never the heavy deliberate block.
    if classification["max_confidence"] == "low":
        # §6.5 throttles: an area already consulted/passed this session, or an event the
        # fractional sampler dropped, degrades a Heavy spike to advisory. The per-session
        # budget cap is the third throttle, applied below only if we're about to deny.
        area_consulted = bool(resp and (resp.get("unlocked") or resp.get("recent_pass")))
        sampled = g.borderline_sampled(cfg["borderline_sampling_rate"])
        b_action, _ = g.borderline_decision(
            classification, cfg["borderline_mode"],
            sampled=sampled, area_consulted=area_consulted)
        if b_action == "deny" and not g.borderline_budget_consume(
                session_id, cfg["borderline_session_budget"]):
            b_action = "advise"  # session synthesize soft-gate budget exhausted
        # The borderline soft-gate's ONLY release is a record_gate_skip, which now REQUIRES the
        # gate_context_id. So if we failed to issue one, this tier has no exit at all — downgrade
        # it to the advisory it already falls back to elsewhere rather than hard-block the agent
        # for our own mint failure (gate_lib.gate_context_missing).
        if b_action == "deny" and g.gate_context_missing(resp):
            b_action = "advise"
        if b_action == "deny":
            gcid = (resp or {}).get("gate_context_id")
            gcid_line = ("  gate_context_id = %s\n" % json.dumps(gcid)) if gcid else ""
            g.emit_deny(
                f"TruVerifAI flagged a borderline-consequential {cats} change — worth a "
                "fast second opinion before building on it.\n"
                + g.transparency_block(classification, resp)
                + g.area_diagnostic_block(area, resp) +
                "Get a fast second opinion with `synthesize_coding` (~15-30s), then release by "
                "recording a one-line skip (`record_gate_skip`). Or run `audit_coding` — a PASS "
                "releases the gate directly. Pass:\n"
                f'  gate_repo = "{repo}"\n'
                "  gate_diff = the change you're about to write\n"
                f'  gate_session_id = "{session_id or ""}"\n'
                + gcid_line
                # 2026-07-12: the legacy `area` skip key is DELETED — record_gate_skip requires the
                # gate_context_id (gcid_line above), which the borderline fire always carries. An
                # `area = ...` line here would hand the agent a parameter the tool no longer
                # accepts, and a schema error on the release path is how an agent concludes the
                # gate is broken. If no id was minted, the skip isn't available; run the review.
                + g.gate_signal_line(classification) + "\n"
                "Then retry. (Both tools are in your MCP tools; passing gate_context_id binds "
                "coverage to the gate's own hunks.)"
            )
        if b_action == "advise":
            # Option B (2026-06-19): make the nudge MODEL-visible so synthesize can
            # actually get called — but only for a Borderline-HEAVY spike, once per area
            # per session, and not if the area was already consulted. Borderline is the
            # high-volume band, so an unthrottled per-write nudge would train the model to
            # dismiss it (deliberate_coding mcp_f044c940, 0.88). Scoped to borderline_mode
            # == 'advisory' (the default): in synthesize_gate mode 'advise' means the
            # soft-gate DEGRADED (not sampled / budget spent / area consulted), where a
            # "worth calling synthesize" nudge would be misleading — that path keeps the
            # old stderr note. Lite / repeat / consulted changes also keep the stderr note.
            if (cfg["borderline_mode"] == "advisory"
                    and classification.get("borderline_tier") == "heavy"
                    and not area_consulted
                    and not g.area_advisory_seen(session_id, area)):
                # Order matters: mark + log BEFORE emit (emit_allow_advisory calls
                # sys.exit, so anything after it is unreachable). Marking first makes the
                # advisory genuinely once-per-area even though the emit exits.
                g.mark_area_advisory_seen(session_id, area)
                g.log_advisory_shown(session_id, area, classification.get("risk_categories"))
                g.emit_allow_advisory(
                    f"`synthesize_coding` can give a fast, independent multi-model read on "
                    f"this {cats} change — worth calling if you're unsure it's correct "
                    "before building on it. Optional; it won't block you."
                )  # exits; the emit_allow below is the fall-through for every other case
            g.emit_allow(
                f"consider `synthesize_coding` for this {cats} change "
                "(fast second opinion; advisory — not blocking)."
            )
        g.emit_allow()

    # 3. Non-blocking under the active gate_tightness (a non-floor low-confidence change under
    #    'focused', or a proactive-consulted downgrade) -> deliberate nudge.
    if action == "advise":
        g.emit_allow(
            f"consider `deliberate_coding` for this {cats} change (advisory — not blocking)."
        )

    g.emit_allow()


if __name__ == "__main__":
    g.host_run(main)
