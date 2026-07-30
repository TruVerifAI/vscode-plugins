# When to deliberate vs. when to just implement

Deliberation is for decisions where multiple approaches are genuinely defensible and the cost of choosing wrong is meaningful. It's NOT for every multi-step decision.

## Filters for "deliberate-worthy"

A decision warrants deliberation if **at least two** of these hold:

1. **Multiple defensible approaches exist.** Two or more options have real proponents in the broader community. If there's one canonical answer, you don't need four models reasoning about it.

2. **Reversibility cost is high.** Undoing the decision later requires touching more than one file, running a migration, breaking compatibility for callers, or rewriting a non-trivial amount of code.

3. **Long-term lock-in.** The decision commits the project for months or years (framework choice, API contract shape, persistence model, data ownership boundary).

4. **Asymmetric downside.** If you pick wrong, the cost of recovery exceeds the cost of pausing a few minutes for deliberation.

5. **You can't easily simulate.** A POC or spike won't decide the question because the consequences only manifest at production scale, over time, or under conditions you can't replicate locally.

If fewer than two hold, just implement and move on.

## Clear-deliberate triggers (per the skill description)

- Schema or table design (column types, indexing strategy, sharding boundaries).
- API contract shape (REST vs GraphQL, field naming, versioning model).
- Module or service boundary placement.
- State-management architecture (server-state vs client-state libraries; single store vs federated).
- Library or framework selection where the choice commits the project long-term.
- Caching strategy (where, how invalidated, consistency model).
- Concurrency model (sync, async, actor-based, etc.).
- Migration or refactoring strategy for load-bearing code.

## Clear-don't-deliberate scenarios

These don't warrant deliberation; just implement:

- **One sensible answer exists.** "Should I use list comprehension or a manual loop for this 5-line aggregation?" — use a list comprehension. No deliberation needed.
- **Choices with no meaningful trade-off.** Renaming a private function, splitting a long file into two, formatting changes.
- **Decisions reversible in a single file.** If you can undo the choice without touching anything else, the reversibility cost is low. Just try one.
- **Decisions a quick search would answer.** Idiomatic patterns, standard ways to do X — that's `synthesize-quick-check` territory.
- **You're already aligned with the user.** If the user said "use Postgres" and you're picking between Postgres options, you're not deliberating on framework choice — that's already decided.

## Boundary cases

**"Should I add a unique index here?"** — depends. If the column has been around for a while and you have data to inform whether duplicates exist, it's a routine choice. If the column is new and the index is part of the data-model design, that's deliberate-worthy (it affects the schema contract).

**"Which test framework should I use?"** — depends. If the project already uses one (`pytest`, `vitest`, `jest`), use it. If you're starting fresh, that's deliberate-worthy.

**"REST endpoint shape: nested or flat?"** — generally deliberate-worthy for public-facing APIs (breaking change cost is high) and skip-worthy for internal services (you can refactor).

## Escalation from synthesize to deliberate

If you ran `synthesize-quick-check` first and got an `answer_status` of `contested` or `unresolved` (a low `agreement_score` < 0.7 is a corroborating hint, not the trigger — agreement_score is telemetry only), that's a signal to escalate to deliberate. The synthesize result told you the question is harder than it looked.

Don't repeat the synthesize call as a deliberate — the inputs are different. Deliberate wants `options_considered` enumerated; synthesize doesn't. Take what you learned from the synthesize result, structure the options, and call deliberate.
