# Isolated reproduction / migration / recovery

Base#26 `34ed8a6c47eb7c90f7eaa5a6807bb1eee78f97a8`; source `cae6337157fa55d6a5cb4bb9e25fce9712f1975e`. Use a separate clone/checkout; never production `.env`, DB, journal, webhook or credentials.

Install existing `requirements-integration.lock`, existing package; build/import verified patched SQLite following Integration02 runbook. Actual lab run used Python venv `/tmp/blago-life013-venv`, patched driver `/tmp/blago-sqlite-driver-3541`, SQLite3.54.1. No dependency changes.

```sh
export PYTHONPATH=.:src
python -m xblago.core.sqlite_safety --require-safe
python -m tools.freeze_integration
python -m unittest tests.test_collective_calculator -v
python -m unittest discover -s tests -v
python -m tools.collective_calculator_lab --output /tmp/calculator-lab.json
```

On macOS with prepared driver prepend its directory to PYTHONPATH; temporary `caffeinate -i` can prevent suspend invalidating unrelated authentication TTL. Do not change session/challenge TTL to make tests pass.

The demo creates an in-memory goods DB and a new disposable guarded app fixture. New synthetic Help participants use existing invitation/session/authentication. No real users/LLM/bot transport, no network, keys or seeds for financial instruments. `/tmp/calculator-lab.json` contains synthetic rates/quantities and schema, not session tokens or journal keys. App fixture deleted at end. LabRegistry.close destroys memory; there is no persistent stock DB to clean or migrate.

CI runs full suite, existing browser/synthetic Mini App/G09/privacy/ops/unified checks plus calculator demo, uploads its public evidence. Docker additionally runs calculator with `--network none`, tools mounted read-only; image still includes original guards. Physical clients and actual deployment are not qualified.

## Migration and rollback

**No new application migration in this PR.** Do not call arbitrary migration loops on #26: Help migrator after Context/Lifecycle may refuse safely. Existing checksums and schema remain frozen. `metadata calculator_lab_001` and schema checksum describe ephemeral lab only, not permission to install into app DB.

Do not back up/restore/import lab records as permissions. `restore(snapshot)` always raises UNKNOWN_RECOVERY. After process crash start a fresh empty synthetic lab; never reconstruct consent from an export, LLM text or asset claim. This intentionally sacrifices availability and is not a substitute for durable accounting.

Help restore uses existing original journal/key/head/witness and offline privacy restore only. New test: take completed Help app backup → withdraw → restore old copy → old session invalidated → reauthenticate → accounting shows no completion/publication. Missing witness must block read. Full regression retains crash/replay/G09 tests. No new permanently cached projection can bypass those controls.

Rollback source to reviewed base only in a separate isolated checkout. No app rows/schema changed by this feature, so no rollback migration. Existing denials/withdrawals cannot be rolled back alongside code. Production rollout is NOT authorized.

## Next integration PR acceptance

1. Map canonical existing assets/resources one-to-one, quarantine ambiguous title/duplicates; no automatic financial migration.
2. Approved retention policy and durable consent/write-ahead denial+witness protocol with crash/restart/old-snapshot tests.
3. Version-aware migration dispatcher, checksum checks and main-derived upgrade rehearsal.
4. Context fencing/account deletion/merge integration; reader authorization under same transaction; concurrent independent workers.
5. Mutually confirmed measurement extension within Help, including partial/dispute corrections.
6. Auth/CSRF/consent API/chat cards and aggregate privacy review; no silent LLM execution.

Until those and inherited release gates close: **GO isolated development, NO-GO real accounting/pilot**.
