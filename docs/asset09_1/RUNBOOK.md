# Explicit synthetic migration / retention / recovery runbook

Base: exact #27 `00c665b369acb6465f9afa795a6046c43f4107ce`; source `34fb475455678dc024cf39f8fa27d2cd0c5af7dd`. No production authorization. Use a new disposable checkout/lab root, no production `.env`/DB/control paths/credentials.

## Reproduce

Existing locked dependencies; existing verified patched SQLite build/driver. Local environment used `/tmp/blago-life013-venv` and SQLite3.54.1 driver `/tmp/blago-sqlite-driver-3541`; do not replace system SQLite. Docker/CI build verified library using prior pinned procedure.

```sh
export PYTHONPATH=.:src
python -m xblago.core.sqlite_safety --require-safe
python -m tools.freeze_integration
python -m unittest tests.test_asset_registry -v
python -m unittest discover -s tests -v
python -m tools.durable_asset_lab --output /tmp/durable-asset-lab.json
python docs/asset09_1/channel_check.py --output /tmp/asset-channel-check.json
```

On macOS prepend the patched driver directory to PYTHONPATH. `caffeinate -i` can temporarily prevent suspend during TTL-sensitive regressions. Don't extend TTL/disable auth to make suspended-machine tests pass.

The lab initializes new synthetic users/controls/data, seeds memory/history and an unmapped legacy asset, explicitly migrates, and enables the flag only inside its patched temporary environment. It executes23 assertions on one shared **application** SQLite, plus existing separate journal/witness stores. Both participants also complete existing Help in that app DB. Tool deletes its private fixture at exit; output contains only synthetic aggregate/schema evidence, no tokens/keys/raw DB. Network and real LLM/Telegram transport not needed. CI runs the same demo and Docker `--network none` with tools read-only mounted.

## Existing complete #27 synthetic copy: installation

1. Stop all app writers/readers. Make authenticated managed backup and independently retained current original journal/key/head/witness under existing custody procedure; don't copy only `app.sqlite3` while ignoring WAL.
2. Confirm exact source/dependency/SQLite identity and complete Help/Context/Lifecycle predecessor schema. Fresh existing module SQL/ledger must match unchanged checksums. Unknown/partial state → stop.
3. Leave `XBLAGO_ASSET_REGISTRY_ENABLED` OFF. Configure original synthetic controls and existing required identity/privacy/integration flags.
4. Run only new installer on offline **test copy**:

```sh
python -m xblago.core.asset_registry_schema /path/to/disposable/blago.sqlite3
python -m xblago.core.asset_registry_schema /path/to/disposable/blago.sqlite3
```

First `applied`, next `unchanged`. It validates already-installed predecessors; never rerun arbitrary old migration loops. No backfill/import. DDL/ledger/reconciliation commit transactionally; SQL errors roll back. Do not change old checksums or use catch-and-ignore for schema drift.

5. Run guarded `preflight` in that test environment, integrity/FK checks, registry demo/negative tests. Review legacy rows excluded from projections. Only then enable `XBLAGO_ASSET_REGISTRY_ENABLED=true` for the **synthetic test process**. No production UI/route is installed.
6. Default limit:256 mapped records overall,32 per owner,128 reviewed versions before further edits blocked (revoke/erase remain possible),32 outstanding challenges per owner,120s challenge TTL. These bound lab costs, not production capacity claims.

Do not call old `migrate_help` after Context/Lifecycle: known frozen-baseline refusal remains. Do not rerun Context/Lifecycle on assetreg DB via old unrestricted dispatcher; new module explicitly validates existing predecessors. A future general dispatcher requires separate review.

## Recovery, ordinary failures, old backups

- **No ACK before original journal/witness and SQL commit.** Error around filesystem/head/witness append → no successful result; do not recreate missing controls.
- Before-journal crash: SQLite WAL rolls back; previous version survives and an unexpired valid challenge may retry.
- Durable external intent but lost SQL commit: checkpoint doesn't match ordinary row → scrub canonical asset and all versions/operations/challenges, retain HMAC hold, deny reuse. Reconstructing grant from the hash is prohibited.
- After-commit/lost ACK: current authenticated session may retry same challenge and gets original committed-version receipt, not another revision. Receipt is historical action result, not new permission.
- Restore only offline copy, never a second concurrently serving primary. Keep original up-to-date controls outside app rollback domain. Use existing `privacy_maintenance.restore`; it replays denials and invalidates sessions/challenges/generations under original policy. Reauthenticate afterward.
- A #27 snapshot **before registry migration** with retained asset_state events must fail closed. Explicitly install assetreg schema on the qualified offline baseline using new installer, then restore/reconcile; missing post-backup records become holds, not grants. Test proves old operation key cannot republish.
- Older snapshots without full Context/Lifecycle schema are **not supported by this installer**. Stop and develop a coordinated reviewed migration/recovery path; no ledger relabel/reset or assumed consent.
- If app row was edited/deleted outside guarded API with already-applied frontier: schema/fingerprint checks refuse access. Don't repair by assigning a new head. Preserve controls and review offline; privacy-safe erase is preferred to unexplained reconstruction.

## Deletion / retention / export

`AssetRegistry.erase` removes canonical row and all sidecars, retaining only HMAC denials; `revoke` keeps private version history until erase/account deletion/TTL. Existing PrivacyService account preview/delete also works with flag OFF. Unknown ownership/economic blockers still stop account deletion. Source account with claims cannot automatically merge into another account; no partial transfer.

Existing synthetic `legacy_record_days=90` retention applies to canonical created_at, including active statements; edits do not prolong that absolute maximum. Existing sweep removes sidecars through cascade and writes denial. Policy NOT APPROVED for real users. Scheduler remains OFF by default; this PR does not enable periodic cleanup. Existing cleanup expires registry challenges without exposing them in export/ops results. Backup copies/pages/client/provider/log retention still require independent operation and review.

Personal export includes current canonical own asset and separate own statement history, same canonical ID; not duplicate accounting and **not** an import or authority package. Export excludes seals/token/session hashes and private peer history; prior row/byte export limits apply. Registry erasure doesn't silently erase original independent chat/memory: use existing user forget/history procedures for those scopes. No asset payload is added to LLM context in09.1.

## Rollback / admission

Do not downgrade app binary to #27 and serve an extended DB: old code doesn't understand new critical intents. There is no destructive down migration. Stop service; keep original current control plane; recover only with reviewed forward-compatible reader, or remain blocked. Restoring old DB/code together with old journal is prohibited.

For real participants first approve audience including future members, retention/legacy ownership disposition and custody; perform target-host fsync/power/independent-copy DR; independent security/privacy review; asset-aware merge or clear refusal UX; authenticated CSRF-protected action cards and real client tests. Before any shared asset enters a recipient LLM context, implement recipient fencing. Existing release gates remain open. No deploy/merge is authorized here.
