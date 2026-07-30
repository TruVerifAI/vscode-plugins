# Example — deliberating on long-term framework selection

A worked example for choosing between competing libraries/frameworks where the decision commits the project for years.

## Scenario

You're building a new admin dashboard for an existing Next.js 15 app. The dashboard needs charts, data tables, modals, and form components. The team has to pick a component library that the dashboard will be built on. Three options on the table: Tailwind CSS + headless primitives (Radix UI), Chakra UI, or shadcn/ui (which is built on Radix). The decision commits the project for the next 2-3 years minimum.

## How to populate the inputs

```python
mcp__truverifai__deliberate_coding(
    question=(
        "Which component library should we use for our new admin "
        "dashboard? Choice commits us for 2-3 years. Team currently uses "
        "Tailwind for utility classes on the public site."
    ),
    relevant_code=(
        "# Existing Next.js 15 app uses Tailwind CSS for utility classes\n"
        "# on the public marketing site and the V2 frontend.\n"
        "# No component library currently in use; we hand-roll components\n"
        "# with Tailwind + ad-hoc primitives.\n\n"
        "# Dashboard requirements:\n"
        "# - Data tables with sort/filter/pagination\n"
        "# - Time-series and bar charts (likely Recharts or Tremor)\n"
        "# - Modal/dialog patterns (existing app has 4 custom dialogs)\n"
        "# - Forms with validation (react-hook-form already used)\n"
        "# - Accessibility: WCAG 2.1 AA minimum\n"
        "# - Dark mode support"
    ),
    architectural_context=(
        "Next.js 15 with App Router. TypeScript strict mode. Tailwind 4 "
        "configured via @theme block. React 19. The dashboard will be "
        "internal-only initially (admin users); may go partner-facing in "
        "V2. The team is small (2 devs); maintenance overhead matters."
    ),
    options_considered=(
        "Option A: Tailwind + Radix UI primitives (headless, BYO styling)\n"
        "  Pros: Maximum flexibility — every component styled to our design. "
        "Radix handles a11y. Bundle size small. Already aligned with our "
        "existing Tailwind setup.\n"
        "  Cons: Have to build every component from primitives. Slower "
        "development. No prebuilt charts (need separate chart library).\n\n"
        "Option B: Chakra UI\n"
        "  Pros: Comprehensive component library out of the box (charts, "
        "tables, forms). Good a11y defaults. Dark mode built in.\n"
        "  Cons: Chakra's styling system is incompatible with Tailwind — "
        "we'd run two style systems in the same app. Bundle size is "
        "significant. The team is unfamiliar.\n\n"
        "Option C: shadcn/ui (Radix primitives + pre-styled with Tailwind, "
        "copy-paste-the-code pattern)\n"
        "  Pros: Pre-styled components for everything we need. Compatible "
        "with our Tailwind setup. Code lives in our repo (no library to "
        "lock in to). Active community + frequent updates.\n"
        "  Cons: Not a 'library' in the traditional sense — we own the code. "
        "Means we maintain components long-term, including a11y bugs. "
        "Versioning is per-component, not per-library.\n\n"
        "Option D: Status quo — keep hand-rolling Tailwind components\n"
        "  Pros: No new dependency. Maximum consistency with existing code.\n"
        "  Cons: Slow dashboard development. Risk of poor a11y on complex "
        "components like data tables and modals. Doesn't scale."
    ),
    constraints=(
        "Dashboard MVP must ship in 3 weeks. Bundle size for the admin "
        "route should stay under 200kb gzipped. Team has React + Tailwind "
        "experience; nobody has used Chakra. Existing public site is "
        "Tailwind — don't break style consistency for users who eventually "
        "see partner-facing dashboard pages."
    ),
)
```

## What a good deliberation response looks like

```json
{
  "conclusion": "Recommend Option C (shadcn/ui). Pros: Pre-styled accelerates the 3-week MVP timeline. Tailwind-compatible — no style-system conflict with the public site. Copy-paste-into-repo pattern means no lock-in. Cons: We own the component code long-term — accept this as the trade-off for flexibility and Tailwind alignment. Option B is rejected on the Tailwind incompatibility. Option A is the strong second choice if you're willing to accept the slower velocity. Option D is rejected on the maintenance trajectory.",
  "recommendation": "split",
  "findings": [
    { "severity": "minor", "summary": "Owning the copied component code is a long-term maintenance burden (a11y fixes, version drift) the team will tend to underestimate — capture it as an ADR, not a blocker." }
  ],
  "action": "proceed_with_caveats",
  "action_basis": "derived",
  "action_reason": "",
  "agreement_score": 0.79,
  "dimensions_of_disagreement": [
    {
      "model": "gemini-3-flash",
      "severity": "medium",
      "model_stance": "Recommended Option A (Tailwind + Radix from scratch) over shadcn/ui on the grounds that shadcn's copy-paste pattern creates a maintenance burden the team will underestimate.",
      "consensus_stance": "shadcn's maintenance burden is real but manageable for a small focused dashboard; the velocity win outweighs it.",
      "disagreement": "Whether the long-term maintenance cost of shadcn (owning the component code) exceeds the upfront velocity gain."
    }
  ]
}
```

## How to act on this

`recommendation = split`, `action = proceed_with_caveats` → proceed with the lean (shadcn), but the contest is real and surfaced. A `minor` finding (maintenance burden) sits against a real velocity win — a trade-off to capture, not a blocker (which is why it's `minor` and the action stays `proceed_with_caveats`). Three models agree on shadcn; one (Gemini) made a substantive case for Option A. `agreement_score = 0.79` is auxiliary — it confirms the panel was only moderately converged, consistent with the `split`.

1. **This is a borderline case for user escalation.** The recommendation is `split` on a real trade-off (maintenance cost) — that's the signal to consider surfacing, even though `action` is only `proceed_with_caveats`. Consider surfacing to the user:
   > "The deliberation recommends shadcn/ui (3 models) for velocity. Gemini argued for raw Tailwind + Radix on maintenance grounds — shadcn's 'we own the component code' pattern creates real long-term burden. The trade-off is real. Want me to go with shadcn for MVP speed, or invest in the slower-but-more-controlled raw Radix approach?"
2. **If the user defers to your judgment, go with shadcn for the MVP.** The velocity win is real. Document that we'll re-evaluate the maintenance burden after the MVP ships — if shadcn's update cadence is painful, migrate specific components to raw Radix incrementally.
3. **Lock the design system early.** Pick spacing, color, and typography tokens before you copy in shadcn components. That way the components inherit your tokens consistently.
4. **Set up a process for shadcn updates.** Track which components you've copied; have a regular cadence (monthly?) for checking if newer shadcn versions fix bugs you should pull in.
5. **Capture the trade-off in an ADR.** "We chose shadcn/ui for velocity. Acknowledged maintenance burden; will revisit at MVP+3-months."

The lesson here: when the `recommendation` is `split` on a real trade-off, that's the boundary case where surfacing to the user is worth the friction — even though `action` stays `proceed_with_caveats`. Don't auto-adopt the lean when the deliberation is signaling "this is a judgment call." (The auxiliary `agreement_score` in the 0.7-0.8 band is consistent with that read, but it's the `recommendation` + `findings` — not the score — that make the call.)
