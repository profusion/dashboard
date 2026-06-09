# Run Schema Refactor Production Runbook

This runbook covers staging and production rollout for the commit and build/test run schema refactor. It assumes the code is deployed with legacy reads first, then historical data is backfilled, validated, and each read surface is enabled only after equivalence checks pass.

## Rollout Principles

- Keep API reads on the legacy path during migration and backfill.
- Do not run overlapping `backfill_build_test_runs --phase tests` jobs. Test definition inserts are index-heavy and can block each other.
- Enable `DB_SCHEMA_REFACTOR_DUAL_WRITE` before or during the historical backfill unless ingestion is paused and a catch-up window is planned.
- Treat `DB_HARDWARE_LISTING_READ_PATH` and `DB_HARDWARE_TREES_READ_PATH` separately from the other read paths. Local benchmarks showed those run paths were still slower than legacy.
- If `kernelCI_app.0020_add_build_test_run_tables` has already shipped to any shared environment, do not edit it in place. Move the delta into a new migration before deploying.

## Target Flags

Start with the safe default:

```env
DB_SCHEMA_REFACTOR_DUAL_WRITE=false
DB_SCHEMA_REFACTOR_READ_PATH=legacy
DB_TREE_READ_PATH=
DB_NOTIFICATION_READ_PATH=
DB_HARDWARE_DETAILS_READ_PATH=
DB_HARDWARE_LISTING_READ_PATH=
DB_HARDWARE_TREES_READ_PATH=
```

After schema deploy, enable dual-write when new ingestion should populate run tables:

```env
DB_SCHEMA_REFACTOR_DUAL_WRITE=true
DB_SCHEMA_REFACTOR_READ_PATH=legacy
```

After backfill and endpoint equivalence pass, enable only the proven read surfaces:

```env
DB_TREE_READ_PATH=runs
DB_NOTIFICATION_READ_PATH=runs
DB_HARDWARE_DETAILS_READ_PATH=runs
DB_HARDWARE_LISTING_READ_PATH=legacy
DB_HARDWARE_TREES_READ_PATH=legacy
```

## Preflight

1. Confirm the migration strategy:
   - Fresh unshipped branch: applying the current migration files is acceptable.
   - Already shipped `0020`: create a follow-up migration for the skinny table and payload-table delta.
2. Announce the migration window because backend containers run migrations on startup.
3. Confirm backup and restore path for the PostgreSQL database.
4. Confirm ingestion state:
   - If ingestion keeps running, deploy with `DB_SCHEMA_REFACTOR_DUAL_WRITE=true`.
   - If ingestion is paused, keep it paused until historical backfill is complete or plan a catch-up loop before reads switch.
5. Check no other large backfill or DDL job is active:

```bash
poetry run python manage.py shell -c "from django.db import connection; cur=connection.cursor(); cur.execute(\"SELECT pid, state, wait_event_type, wait_event, now() - query_start AS age, left(query, 160) AS query FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND state <> 'idle' ORDER BY query_start NULLS LAST\"); print(cur.fetchall())"
```

6. Run dry-run probes from `backend/`:

```bash
poetry run python manage.py backfill_commits --batch-size 1000 --dry-run
poetry run python manage.py backfill_build_test_runs --phase builds --batch-size 1000 --max-batches 1 --dry-run
poetry run python manage.py backfill_build_test_runs --phase tests --batch-size 1000 --max-batches 1 --dry-run
poetry run python manage.py backfill_build_test_runs --phase incidents --dry-run
```

## Deployment

1. Deploy schema and code with legacy reads:

```env
DB_SCHEMA_REFACTOR_READ_PATH=legacy
DB_SCHEMA_REFACTOR_DUAL_WRITE=false
```

2. Apply migrations through the normal deploy path.
3. Run Django checks:

```bash
poetry run python manage.py check
poetry run python manage.py makemigrations --check --dry-run
```

4. If ingestion is not paused, restart workers with dual-write enabled:

```env
DB_SCHEMA_REFACTOR_DUAL_WRITE=true
```

## Historical Backfill

Run from `backend/`. Use small chunks first, then increase only if DB load stays healthy.

1. Backfill commits from existing checkout columns. This command is data-only and never fetches git repositories.

```bash
poetry run python manage.py backfill_commits --batch-size 5000
poetry run python manage.py validate_commits_backfill --fail-on-mismatch
```

2. Backfill build definitions, runs, and payloads.

```bash
poetry run python manage.py backfill_build_test_runs --phase builds --batch-size 50000
poetry run python manage.py validate_build_test_runs --phase builds --fail-on-mismatch
```

3. Backfill test definitions, runs, hardware links, and payloads in bounded chunks.

```bash
poetry run python manage.py backfill_build_test_runs --phase tests --batch-size 10000 --max-batches 20
poetry run python manage.py validate_build_test_runs --phase tests --fail-on-mismatch
```

Repeat the test chunk until validation shows `missing_runs=0`, `missing_payloads=0`, `missing_hardware_links=0`, and `mismatched_runs=0`.

4. Link incidents after build and test run rows exist.

```bash
poetry run python manage.py backfill_build_test_runs --phase incidents
poetry run python manage.py validate_build_test_runs --phase incidents --fail-on-mismatch
```

5. Run a full validation pass.

```bash
poetry run python manage.py validate_commits_backfill --fail-on-mismatch
poetry run python manage.py validate_build_test_runs --fail-on-mismatch
```

6. Analyze hot tables after large backfills finish.

```bash
poetry run python manage.py shell -c "from django.db import connection; connection.autocommit=True; cur=connection.cursor(); [cur.execute(f'VACUUM ANALYZE {t}') for t in ['commits','checkouts','build_definitions','build_runs','build_run_payloads','test_definitions','test_runs','test_run_payloads','test_run_hardwares','incidents']]"
```

## Catch-Up Loop

If ingestion was running while historical backfill ran, or if ingestion is enabled after a paused window, run missing-only chunks until validation is clean.

```bash
poetry run python manage.py backfill_build_test_runs --phase builds --missing-only --batch-size 50000 --max-batches 10
poetry run python manage.py backfill_build_test_runs --phase tests --missing-only --batch-size 10000 --max-batches 20
poetry run python manage.py backfill_build_test_runs --phase incidents
poetry run python manage.py validate_build_test_runs --fail-on-mismatch
```

Repeat until there are no missing run, payload, hardware-link, or incident links.

## Endpoint Equivalence

Before switching any read path, compare legacy and runs output at the endpoint level. Use the same database snapshot and the same request parameters.

Required surfaces:

- Tree listing and tree listing by checkout.
- Hardware details summary and full hardware records.
- Notification test history.

Recommended process:

1. Run with `DB_SCHEMA_REFACTOR_READ_PATH=legacy` and capture JSON responses.
2. Enable only one surface flag, capture the same responses, and compare after normalizing ordering where the endpoint does not guarantee order.
3. Confirm query time does not regress for representative ranges and origins.
4. Keep `DB_HARDWARE_LISTING_READ_PATH=legacy` and `DB_HARDWARE_TREES_READ_PATH=legacy` unless a new benchmark shows the run path is faster.

## Read-Path Enablement

Enable one surface at a time and watch API errors, response latency, DB CPU, lock waits, and slow queries.

Suggested order:

1. `DB_TREE_READ_PATH=runs`
2. `DB_NOTIFICATION_READ_PATH=runs`
3. `DB_HARDWARE_DETAILS_READ_PATH=runs`

Do not set `DB_SCHEMA_REFACTOR_READ_PATH=runs` globally until every surface has passed equivalence and performance checks. Leave slower or unproven surfaces on `legacy`.

## Rollback

Read-path rollback is environment-only:

```env
DB_SCHEMA_REFACTOR_READ_PATH=legacy
DB_TREE_READ_PATH=
DB_NOTIFICATION_READ_PATH=
DB_HARDWARE_DETAILS_READ_PATH=
DB_HARDWARE_LISTING_READ_PATH=legacy
DB_HARDWARE_TREES_READ_PATH=legacy
```

If dual-write causes ingestion problems, disable it while leaving schema and backfilled data in place:

```env
DB_SCHEMA_REFACTOR_DUAL_WRITE=false
```

Do not drop new tables during first rollback. Keep expanded schema available until the issue is fixed or a deliberate contraction migration is prepared.

## Acceptance Checklist

- Migrations apply cleanly in staging/production.
- `validate_commits_backfill --fail-on-mismatch` passes.
- `validate_build_test_runs --fail-on-mismatch` passes.
- Run, payload, hardware-link, and incident validation mismatches are zero.
- Endpoint equivalence passes for each enabled surface.
- Slow or unproven hardware listing/tree-head paths remain on legacy.
- Full backend CI passes before PR/production promotion.
