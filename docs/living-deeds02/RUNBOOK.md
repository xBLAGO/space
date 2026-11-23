# Living Deeds 02 — synthetic migration / delivery / recovery

1. Checkout exact runtime SHA из RESULTS/PR, отдельная рабочая копия. Никакой рабочей базы. Проверить `python -m xblago.core.sqlite_safety --require-safe`; использовать SQLite из integration lock/CI.
2. Воспроизвести тесты без ручной конфигурации production:

```sh
PYTHONPATH=.:src python -m unittest tests.test_living_deeds_agent -v
PYTHONPATH=.:src python -m unittest tests.test_agent_context tests.test_agent_context_live_check -v
PYTHONPATH=.:src python -m tools.living_deeds_agent_lab --output /tmp/living02-evidence.json
PYTHONPATH=.:src python -m tools.uel03_lab_check --full --repeat 2 --output /tmp/living02-full.json
PYTHONPATH=.:src python -m tools.freeze_integration
PYTHONPATH=.:src python -m tools.living_contract_check
PYTHONPATH=src python docs/ledger01/generate_vectors.py --check
PYTHONPATH=src python docs/ledger01/check_vectors.py --output /tmp/living02-offline.json
```

Tests создают новые temporary profiles, ключи, SQLite, retained journal/witness и локальный checkpoint fixture. Это один rollback domain в части приложения; local custodian fixture не равен независимому оператору.

3. Для исследовательской конфигурации вне тестов нужен уже сертифицированный synthetic Living01 стенд: Identity → privacy/journal/witness → Help/context/lifecycle → Asset → UEL → checkpoint enrollment → `living_deeds_001`. Лишь затем с `XBLAGO_AGENT_CONTEXT_ENABLED=false` применить `python -m xblago.core.agent_context_schema /absolute/synthetic/blago.sqlite3`. Требуются original retained journal/witness и все прежние flags/holds. Повтор agent migration возвращает unchanged.
4. Явно включить `XBLAGO_AGENT_CONTEXT_ENABLED=true`, `XBLAGO_LIVING_AGENT_ENABLED=true`; для local pull отдельно `XBLAGO_LIVING_DELIVERY_ENABLED=true`. Оба новых флага по умолчанию OFF. Внешний push-адаптер отсутствует. Не подключать реальные пользовательские sessions или Telegram credentials.
5. Владелец выдаёт ограниченный по сроку MATCH_PRIVATE через prepare/commit. `candidates` возвращает лишь owner-private drafts. Дальнейшая NOTE создаётся отдельной подтверждённой командой, без автоматического импорта memory. Автор Help подтверждает forwarding text; владелец NOTE — recipient и раскрытие рекомендации. INVITE_ONCE и queue также требуют подтверждения.
6. Получатель делает local pull под своей свежей session. До durable commit ACK не возвращается. Lost ACK → повтор с тем же delivery ID; при актуальном согласии квитанция идентична. Не создавать новое приглашение автоматически при retry. Отказ/отзыв/expiry запрещают выдачу старого payload даже при наличии старой квитанции.

## Сбой / восстановление

- OSError до flush/commit: нет ACK, операция откатывается. Повтор разрешён только после нового чтения актуального состояния; тестом проверяется прежняя QUEUED запись.
- Crash после journal, до SQL commit: journal-ahead положительное состояние нельзя восстановить из hashes. Ожидается UNKNOWN_RECOVERY, доставка остановлена.
- Crash после SQL commit, до ACK: повтор читает долговечную ACKED запись, не создаёт второй event.
- Старый backup после revoke/deletion: использовать существующий `privacy_maintenance.restore` с retained журналом; источник без положительного payload получает recovery hold. Не заменять witness/checkpoint старой копией вместе с SQL.
- Не удалять holds, не переинициализировать journal/witness/custodian, не реконструировать task/полномочия из HMAC. Для чистого повторного эксперимента создать новую синтетическую lineage.
- Отозванные/удалённые данные не возвращаются для «починки» приглашения. Получатель должен дать новое разрешение по будущему отдельному контракту; сейчас recontact заблокирован.

Rollback: сначала отключить оба новых адаптера. Установленная agent_context migration продолжает требовать совместимого reader и своих privacy barriers. Возврат старого #42 binary поверх этой базы не является поддержанным downgrade: preflight должен остановить его. Не удалять таблицы/триггеры или переписывать старые migration ledgers. Безопасный rollback эксперимента — новая отдельная synthetic copy без переноса permissive state.
