# Изменённые файлы относительно exact #35

- `.github/workflows/tests.yml`
- `docs/BLAGO_LIVING_DEEDS_01.md`
- `docs/living-deeds01/BASELINE.json`
- `docs/living-deeds01/CHANGED_FILES.md`
- `docs/living-deeds01/DEPENDENCIES.json`
- `docs/living-deeds01/EVENT_PROFILE.json`
- `docs/living-deeds01/LEGACY_CONTRACTS.json`
- `docs/living-deeds01/MIGRATION.md`
- `docs/living-deeds01/PREDECESSOR_LEDGERS.json`
- `docs/living-deeds01/READINESS.json`
- `docs/living-deeds01/RESULTS.json`
- `docs/living-deeds01/RUNBOOK.md`
- `docs/living-deeds01/SCENARIOS.md`
- `docs/living-deeds01/THREAT_MODEL.md`
- `docs/living-deeds01/evidence/living-runtime.json`
- `docs/living-deeds01/evidence/offline-72.json`
- `integration/candidate.json`
- `src/xblago/core/integration.py`
- `src/xblago/core/living_schema.py`
- `src/xblago/core/privacy_control.py`
- `src/xblago/core/uel_local.py`
- `src/xblago/core/uel_recovery.py`
- `src/xblago/core/uel_schema.py`
- `src/xblago/domain/asset_registry.py`
- `src/xblago/domain/help_service.py`
- `src/xblago/domain/living_deeds.py`
- `src/xblago/domain/local_ledger.py`
- `src/xblago/integrations/privacy.py`
- `tests/test_living_contracts.py`
- `tests/test_living_deeds.py`
- `tools/freeze_integration.py`
- `tools/living_contract_check.py`
- `tools/living_deeds_lab.py`

Новая миграция: `living_deeds_001`. Старые DDL/ledgers проверены в PREDECESSOR_LEDGERS.json; UEL loader расширен явно, а не подменой прежних checksum.
