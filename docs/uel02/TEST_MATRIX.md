# UEL 0.2 — матрица проверок

Источник runtime `6e52b3c8961110e48a89329b5c48bfcb14d367e0`. [GitHub Actions PR #31](https://github.com/xBLAGO/space/actions/runs/42922671424). Команды приведены в [runbook](RUNBOOK.md).

| Группа | Ожидаемое поведение | Доказательство |
|---|---|---|
| Frozen UEL 0.1 | Точное совпадение генерации и ожидаемых результатов | 72/72 локально; CI публикует `uel-offline-results.json` |
| Регрессия | Все 565 прежних тестов сохраняются | `unittest discover -s tests`; CI jobs Tests и Docker |
| Миграция | Идемпотентность, rollback DDL, проверка целостности, проверенный baseline | `test_repeat_migration_and_integrity`, `test_migration_ddl_failure_rolls_back`, `test_explicit_current_baseline_and_legacy_exclusion` |
| Сверка | Каждая версия наблюдаема; изменение вне shadow не принимается автоматически | `test_source_rows_and_every_indexed_revision_match`, `test_causal_links_and_global_sequence`, `test_positive_write_outside_shadow_is_not_backfilled` |
| Повтор и CAS | Одна версия при повторе, максимум одна конкурирующая мутация | `test_duplicate_commit_from_three_workers`, `test_competing_revisions_from_two_workers` |
| Сбой записи | До журнала обе записи откатываются; между журналом и commit не возникает разрешения; после commit исходная квитанция сохраняется | Три теста `test_crash_*`, два `test_abort_*` |
| Восстановление | Нет выдачи при потере frontier/witness; старый backup не возвращает payload или доступ | `test_missing_retained_index_frontier_blocks_projection`, `test_witness_loss_returns_unknown_without_numbers`, `test_old_backup_after_asset_erase_keeps_no_payload`, `test_pre_migration_backup_requires_install_and_replays_only_headers`, `test_account_deletion_and_restore_remove_access` |
| Приватность | Существующие ACL, удаление, retention и G09 действуют; несогласованный hold блокируется | `test_private_audience_and_fresh_session`, `test_no_plaintext_or_control_references_in_user_view`, `test_retention_cascades_without_copying_old_payload`, `test_g09_owner_generation_fenced_by_existing_source_change`, `test_forged_help_hold_cannot_hide_unscrubbed_payload` |
| Учёт | Актив учитывается один раз, повтор Help не меняет итог, неизвестные количества остаются неизвестными | `test_asset_resource_and_help_occurrence_no_double_count`, `test_help_completion_receipt_replay_has_one_effect`, `test_unknown_amount_remains_unknown`, `test_restart_determinism` |

## Покрытие 22 отложенных сценариев UEL 0.1

Все исходные статусы в [замороженном наборе](../ledger01/design-scenarios.json) сохранены. Локальные аналоги ниже имеют более узкую область доказательства.

| Сценарий | Доказательство UEL 0.2 |
|---|---|
| A01 | NOT EXECUTED — переносимое подтверждение раскрытия |
| A02 | LOCAL ONLY — авторизованные подтверждения Help, локальная маркировка наблюдения |
| A03 | NOT EXECUTED — подпись мандата чужой ролью |
| A04 | NOT EXECUTED — подстановка сетевой квитанции |
| A05 | NOT EXECUTED — привязка межузлового nonce |
| A06 | NOT EXECUTED — сверка внешнего статуса полномочий |
| A07 | NOT EXECUTED — межузловая потеря связи |
| A08 | NOT EXECUTED — доставка до межузлового отзыва |
| A09 | NOT EXECUTED — межузловой отзыв до доставки |
| A10 | LOCAL ONLY — повтор после SQLite commit с потерянным ответом, исходная квитанция |
| R01 | LOCAL ONLY — старый SQLite и сохранённые исходные контроли, отзыв остаётся в силе |
| R02 | NOT EXECUTED — совместный откат всех контрольных копий |
| R03 | LOCAL ONLY — SQLite rollback после записи в журнал, восстанавливается только заголовок |
| R04 | LOCAL ONLY — стирание в восстановленной копии, без повторной выдачи payload |
| R05 | NOT EXECUTED — переносимый переход ключей и эпохи полномочий |
| H01 | NOT EXECUTED — сетевой импорт Help отсутствует |
| H02 | LOCAL ONLY — повтор подтверждения Help, одна учётная запись исполнения |
| H03 | LOCAL ONLY — количество неизвестно, измеряемые частичные исполнения отсутствуют |
| P01 | NOT EXECUTED — барьер публикации якоря |
| P02 | NOT EXECUTED — отзыв после публичного якорения |
| P03 | NOT EXECUTED — дифференциальное раскрытие публичного агрегата |
| F01 | NOT EXECUTED — два независимых оператора |
