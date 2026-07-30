# Example — auditing a database migration

A worked example for migrations that touch populated production tables.

## Scenario

Adding a `marketing_opt_in` boolean column to the `users` table with a NOT NULL constraint. New users default to false; existing users will be backfilled before the constraint is enforced. The migration runs as part of normal deploy.

## The migration (illustrative)

```python
# migrations/0042_user_marketing_opt_in.py
from alembic import op
import sqlalchemy as sa

revision = "0042"
down_revision = "0041"

def upgrade():
    op.add_column(
        "users",
        sa.Column("marketing_opt_in", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Remove the server_default so future inserts must specify the value
    op.alter_column("users", "marketing_opt_in", server_default=None)

def downgrade():
    op.drop_column("users", "marketing_opt_in")
```

## How to populate the inputs

```python
mcp__truverifai__audit_coding(
    proposed_action=(
        "Adding marketing_opt_in boolean column to users table with NOT NULL "
        "constraint. New users default to false; the server_default handles "
        "backfill of existing rows during the ALTER TABLE, then we strip the "
        "server_default so future inserts must specify the value explicitly. "
        "Goal: comply with email marketing opt-in regulations for new signups."
    ),
    relevant_code=(
        "# migrations/0042_user_marketing_opt_in.py\n"
        "def upgrade():\n"
        "    op.add_column('users', sa.Column('marketing_opt_in', sa.Boolean(), "
        "nullable=False, server_default=sa.false()))\n"
        "    op.alter_column('users', 'marketing_opt_in', server_default=None)\n\n"
        "def downgrade():\n"
        "    op.drop_column('users', 'marketing_opt_in')"
    ),
    tests=(
        "No migration tests exist. The standard test_models tests cover the "
        "User model; they'll be updated to set marketing_opt_in=False in fixtures. "
        "No data-correctness test runs against the production-equivalent dataset."
    ),
    architectural_context=(
        "PostgreSQL 16. users table has approximately 850k rows in production. "
        "Migration framework: Alembic, run as part of deploy via the standard "
        "alembic upgrade head step. We have no separate maintenance-window "
        "process for migrations; everything runs during normal deploys. The "
        "users table is hot — login traffic hits it constantly during business hours."
    ),
    constraints=(
        "Production deploy window is Tuesday 10am Pacific (peak login traffic). "
        "p99 login latency must stay under 500ms during the deploy. Cannot take "
        "the application offline. No DB read replica failover for migrations."
    ),
)
```

## What a good audit response looks like

**Minor findings:**

1. *Verify the production PostgreSQL version is 11+.* `op.add_column` with a NOT NULL constraint + server_default is metadata-only and fast on PostgreSQL 11+ — and the scenario specifies PG16, so this migration is safe during peak traffic. The finding is to *confirm* the prod version really is 11+: on pre-11 the ADD COLUMN rewrites every row → multi-minute ACCESS EXCLUSIVE lock → login outage. A one-line version check de-risks it. (Note how the audit rates this `minor`, not `critical`: given the confirmed PG16, the lock risk is conditional, so it caveats rather than blocks.)

2. *Destructive downgrade is undocumented.* The `downgrade()` drops the column, which is destructive — any data captured in `marketing_opt_in` between deploy and rollback is lost. Acceptable for a boolean opt-in, but the migration doesn't document this trade-off.

3. *Migration sets the column to false for all existing users.* This is a regulatory choice — opt-in defaults to "no consent" for existing users — which is correct from a legal standpoint but should be documented in the migration message or a linked compliance note. A reviewer looking at this in 6 months should understand WHY the default was false.

4. *No application-level fallback if the migration hasn't run yet.* If the deploy serves the new code before the migration finishes, queries against `User.marketing_opt_in` will fail. Alembic typically runs migrations before code starts serving, but verify your deploy pipeline.

**Preference findings:**

5. The migration filename uses an integer prefix (`0042`); your repo convention is timestamps (`20260520_...`). Cosmetic but worth aligning.

**Response shape:**

```json
{
  "verdict": "approve_with_caveats",
  "findings": [
    { "severity": "minor", "summary": "Verify the prod PostgreSQL version is 11+; on PG16 the add-column is metadata-only and safe, but a pre-11 server would rewrite 850k rows and lock the table." },
    { "severity": "minor", "summary": "Destructive downgrade (drops the column) is undocumented; opt-in data between deploy and rollback would be lost." },
    { "severity": "minor", "summary": "False-default rationale undocumented; a future reviewer won't know why opt-in defaults to no-consent." },
    { "severity": "minor", "summary": "No application-level fallback if the migration hasn't run before the new code serves." },
    { "severity": "preference", "summary": "Migration filename uses an integer prefix; repo convention is timestamps." }
  ],
  "action": "proceed_with_caveats",
  "action_basis": "derived",
  "action_reason": "",
  "agreement_score": 0.91,
  "dimensions_of_disagreement": []
}
```

## How to act on this

Action is `proceed_with_caveats` → minor issues to address, then ship.

1. **Verify the PostgreSQL version is 11+.** If yes, the metadata-only optimization applies and the migration is safe during peak traffic. If no, defer the migration to a maintenance window. This is a one-line check against the prod DB — do it before committing.
2. Document the false-default rationale in the migration's docstring (finding 3).
3. Verify the deploy pipeline runs migrations BEFORE serving the new code (finding 4). This is usually true but worth confirming.
4. Align the filename to the timestamp convention if your linter cares (finding 5).
5. After addressing, commit.

The `approve_with_caveats` verdict, with only `minor` and `preference` findings, means the audit judges the change fundamentally sound — the findings are scoping/safety nits, not correctness bugs. Because no finding is `critical` or `major`, none tightened the action (`action_reason` is empty) and it derives straight to `proceed_with_caveats`. The high `agreement_score` (0.91) is auxiliary telemetry here — it tells you the panel converged, but the verdict and findings are what you act on. This is what a good audit response looks like for routine schema work.
