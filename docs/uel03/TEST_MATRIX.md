# UEL 0.3 — проверка и доказательства

Exact base `aa92d96bca0ae8a07e27f8efa724131afb32ad57`. Первый проверенный source `7b3e229c43f68671ac1087bd093fb484543c4366`. Финальный runtime source `8ed6131e4617e0b47b157e9625ceb8845de22306`. Проверки выполняются в изолированной синтетической среде. [CI runtime](https://github.com/xBLAGO/space/actions/runs/43376632499) — PASS. Два полных прогона по 638 тестов за 178,149 и 178,267 с. Сохранены 600 тестов базы, добавлены 38. Docker — PASS, включая 35 прежних UEL и 38 новых проверок. Frozen UEL 0.1 — 72/72, ожидаемые исходы сохранены. Browser/WebAuthn и synthetic Mini App — PASS. Проверка физических устройств — NOT EXECUTED.

Локально первый source прошёл два полных прогона по 635 тестов за 689,645 и 736,928 с. После review добавлены три граничных проверки — предел с учётом genesis, SQL generation и обязательный SQLite safety guard хранителя. Финальный source прошёл все 38 новых проверок локально за 116,467 с и два полных набора в CI. Полный локальный набор финального source после этих дополнений не повторялся.

Численные результаты, точные SHA, индивидуальные исходы новых тестов, хэши логов и CI artifact digest находятся в [evidence.json](evidence.json). На этапе финальной локальной проверки временное окружение потеряло файлы пакетов и metadata venv. Оно создано заново с точными версиями прежнего lock-файла. Обновления зависимостей проекта отсутствуют.

| Проверка | Ожидаемый результат | Автоматическое доказательство |
|---|---|---|
| Причина двух ошибок сессии | 401 при сдвиге на 1801 s, успех при возврате; 403 при fresh TTL 301 s | `test_wall_clock_suspension_reproduces_both_original_errors`, `test_session_freshness_still_expires_at_original_boundary` |
| Повторяемость | Два полных прогона без ошибок | `tools.uel03_lab_check --full --repeat 2` |
| Границы и storage guard | Genesis не расходует лимит checkpoint, SQL generation проверяется, непроверенная SQLite отклоняется до доступа к custody DB | `test_genesis_does_not_consume_checkpoint_limit`, `test_sql_generation_corruption_is_rejected_before_advance`, `test_custodian_requires_verified_sqlite_before_storage_access` |
| Подписи и приватность | Корректная цепочка, payload/токены отсутствуют | `test_signed_prefix_and_source_headers` |
| Replay/substitution | Неверные nonce, key, domain, stream и изменённая подпись отвергаются | `test_fresh_nonce_and_request_binding`, `test_wrong_key_domain_stream_and_changed_signature` |
| Ограничение запроса | Неканонический или чрезмерный запрос отвергается | `test_bounded_and_strict_serialization` |
| Согласованный rollback | Старые три локальные копии согласуются, сохранённый custodian frontier блокирует | `test_full_coordinated_local_rollback_is_detected` |
| Backup SQLite | Удалённый актив не возвращается | `test_application_backup_only_replays_denials` |
| Потеря контролей | Отказ без автоинициализации | `test_missing_*`, `test_lost_config_*`, `test_lost_local_witness_*` |
| Повреждение history/head | Отказ | `test_checkpoint_chain_corruption_blocks`, `test_truncated_custody_database_rejected_by_retained_head` |
| ENOSPC/write failure | Отсутствует успешная квитанция | `test_full_disk_open_emits_no_receipt`, `test_sql_write_failure_emits_no_receipt` |
| Crash и потерянный ACK | Durable checkpoint сохраняется и повторяется | `test_crash_after_custody_commit_before_head`, `test_lost_ack_keeps_same_committed_checkpoint` |
| Source crash | Положительное разрешение не воспроизводится | `test_crash_source_after_journal_remains_non_grant` |
| Ремонт и удаление | Полная проверка, обычная авторизация, удаление сохраняется после restore | `test_repair_state_and_headers_then_authorized_account_deletion` |
| Неисправный source | Repair откатывается | `test_repair_rollback_on_source_corruption` |
| Crash ремонта | Audit сохранён; повторная проверка и ремонт возможны | `test_repair_crash_after_retained_audit_and_replay` |
| Конкуренция | Одно продолжение, одинаковая квитанция для дублей | `test_custody_two_processes_same_request`, `test_custody_concurrent_conflicting_extensions`, `test_concurrent_duplicate_commit_and_restart` |
| Repair против source write | Сериализация SQLite, отсутствие расхождения | `test_concurrent_repair_and_commit` |
| Offline proof | Обе реализации проверяют golden и отрицательные примеры; отдельный процесс запускается без импорта приложения | `tests.test_uel_checkpoint_vectors` |
| Frozen UEL 0.1 | 72 ожидаемых исхода сохраняются | `docs/ledger01/check_vectors.py` |
| Help/Asset, G09, privacy/recovery | Существующая регрессия сохраняется | Полный набор тестов и действующие CI browser/Mini App/recovery шаги |

R02 прежней матрицы UEL 0.1 получает локальную fault-injection проверку для отката трёх исходных хранилищ при сохранённом четвёртом. Согласованный откат хранителя и его всех закреплённых свидетельств остаётся за доказанной моделью. Исходные 22 статуса NOT EXECUTED и golden results не переписаны. Независимые операторы, удалённый транспорт, независимый аудит, физический отказ носителей и реальные пользователи — NOT EXECUTED.
