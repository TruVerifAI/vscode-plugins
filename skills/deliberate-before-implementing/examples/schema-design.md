# Example — deliberating on schema design

A worked example for designing a new database table where column types and index strategy are genuinely contested decisions.

## Scenario

You're adding a `events` table to track user interaction events for analytics. The questions you face: what should the columns look like, what's the indexing strategy, and how do you partition (or do you)? Expected volume: 5-10M rows per month, growing 30% quarterly. Most queries are time-range scans by user_id.

This is deliberate-worthy because multiple defensible approaches exist (normalized vs JSONB blob; multi-column index vs partial; time-partitioned vs single table), reversibility cost is high (data migration if you pick wrong), and you'll commit to the schema for years.

## How to populate the inputs

```python
mcp__truverifai__deliberate_coding(
    question=(
        "What schema and index strategy should we use for a high-volume "
        "events table (5-10M rows/month, growing 30%/quarter, mostly "
        "time-range scans by user_id)?"
    ),
    relevant_code=(
        "# Current models\n"
        "class User(db.Model):\n"
        "    id = db.Column(db.Integer, primary_key=True)\n"
        "    ...\n\n"
        "# Query patterns expected (today's analytics queries):\n"
        "# SELECT * FROM events WHERE user_id = ? AND created_at > NOW() - INTERVAL '7 days'\n"
        "# SELECT event_type, COUNT(*) FROM events WHERE user_id = ? GROUP BY event_type\n"
        "# SELECT * FROM events WHERE created_at > NOW() - INTERVAL '1 hour' (admin)\n"
    ),
    architectural_context=(
        "PostgreSQL 16. Currently 850k users, ~50% active monthly. Existing "
        "analytics tables (query_analytics, mcp_assessment_telemetry) are "
        "single-table with simple indexes — they're at ~2M rows and starting "
        "to slow on time-range queries. We have no current partitioning "
        "strategy across any table. Our hosting is managed Postgres on Replit; "
        "we don't have direct DBA access for advanced operations."
    ),
    options_considered=(
        "Option A: Normalized columns + multi-column B-tree index\n"
        "  Schema: events(id, user_id, event_type VARCHAR, payload JSONB, "
        "created_at TIMESTAMPTZ). Index: (user_id, created_at DESC).\n"
        "  Pros: Simple. Index covers the dominant query pattern directly. "
        "Query planner picks it without tuning.\n"
        "  Cons: Index size grows linearly. JSONB payload search is slow. "
        "Single table will hit row count where ALTER TABLE becomes painful "
        "(~50M rows in our experience).\n\n"
        "Option B: Same schema as A, plus monthly time-based partitioning\n"
        "  events_2026_05, events_2026_06, etc., union'd via a parent table.\n"
        "  Pros: Per-partition operations (vacuum, reindex) stay fast. "
        "Old-data archival becomes trivial. Time-range queries can prune "
        "partitions.\n"
        "  Cons: Partition management overhead — need cron job to create "
        "next month's partition. Schema changes have to apply to all "
        "partitions. Replit's managed Postgres may not have pg_partman.\n\n"
        "Option C: Wide row with denormalized event_type columns\n"
        "  events(user_id, login_count, click_count, search_count, ...).\n"
        "  Pros: Trivial aggregation queries. Tiny row count (one per user).\n"
        "  Cons: Doesn't support time-range queries at all. Can't surface "
        "raw events. Schema rigid — every new event type is a new column.\n\n"
        "Option D: Status quo — log events to a JSONL file + batch ETL to "
        "a separate warehouse (not Postgres)\n"
        "  Pros: Decouples analytics from prod DB. Cheaper at scale.\n"
        "  Cons: Real-time queries impossible. Adds operational complexity "
        "(separate system to monitor). No current warehouse to ETL into."
    ),
    constraints=(
        "Cannot exceed Replit's managed Postgres connection pool limit. "
        "Cannot rely on Postgres extensions not in the standard distribution "
        "(check pg_partman availability before assuming B). Cannot add a "
        "separate analytics system in this sprint — operational bandwidth "
        "is limited."
    ),
)
```

## What a good deliberation response looks like

```json
{
  "conclusion": "Recommend Option A (normalized + multi-column B-tree) for V1, with a documented migration path to Option B (monthly partitioning) at the 10M-row threshold. Option C is rejected as too rigid for the analytics query patterns. Option D is deferred until volume justifies the warehouse complexity.",
  "recommendation": "qualified",
  "findings": [
    { "severity": "minor", "summary": "Single-table Option A will hit a painful ALTER TABLE / vacuum threshold (~10M rows) without a partitioning migration committed; the path should be documented, not assumed." }
  ],
  "action": "proceed_with_caveats",
  "action_basis": "derived",
  "action_reason": "",
  "agreement_score": 0.82,
  "dimensions_of_disagreement": [
    {
      "model": "grok-4-1-fast",
      "severity": "medium",
      "model_stance": "Recommended starting with Option B (partitioning) from day one to avoid the 'we'll migrate later' tech debt.",
      "consensus_stance": "Start with Option A; document the partition threshold; migrate when volume justifies it.",
      "disagreement": "Whether partitioning is cheap-enough-now or expensive-enough-later."
    }
  ]
}
```

## How to act on this

`recommendation = qualified`, `action = proceed_with_caveats` → adopt Option A with the migration-path caveat the `minor` finding calls out. Real disagreement on one dimension, three-against-one in favor of Option A. `agreement_score = 0.82` is auxiliary, confirming the panel mostly converged.

1. **Adopt the consensus recommendation: Option A.** Schema is normalized columns + (user_id, created_at DESC) index.
2. **Capture the partition threshold as a follow-up.** Add a comment in the migration or an ADR: "Migrate to monthly partitioning when row count exceeds 10M, or when ALTER TABLE on events takes longer than 30 seconds, whichever comes first." This addresses Grok's concern asynchronously.
3. **Surface the disagreement to the user if you're committing the schema right now.** "Three models recommended starting simple; one (Grok) recommended partitioning from day one to avoid migration cost later. I'm going with simple-first per the majority. Worth flagging in case you'd rather pay the partitioning cost up front."
4. **Verify the pg_partman constraint** before locking the migration path — if Replit's managed Postgres doesn't have it, the migration path is more painful and the case for starting with partitioning gets stronger.

This is a representative deliberate response: a `qualified` recommendation the panel converged on, a `minor` finding to capture as a documented trade-off, and a clear `action` next step.
