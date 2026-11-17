# Foundation 09 — миграция, испытания и восстановление

## Текущая итерация

Application schema не меняется. Новых SQL таблиц, startup migrations, triggers и checksums нет. Обновляется только source-files manifest. Порядок прежних миграций сохраняется.

Работать на отдельной копии exact base #32 с исправленной SQLite и действующими original journal/witness/custodian. XBLAGO_UNIVERSAL_BALANCE_ENABLED по умолчанию OFF. Runtime не включает новый модуль из web/chat/bot routes.

```sh
PYTHONPATH=.:src python -m tools.freeze_integration
PYTHONPATH=.:src python -m unittest tests.test_universal_balance -v
PYTHONPATH=.:src python -m tools.foundation09_lab --output /tmp/foundation09-lab.json
PYTHONPATH=.:src python -m tools.uel03_lab_check --full --repeat 2 --output /tmp/foundation09-full.json
PYTHONPATH=src python docs/ledger01/generate_vectors.py --check
PYTHONPATH=src python docs/ledger01/check_vectors.py --output /tmp/foundation09-offline72.json
```

Lab создаёт временные synthetic users, source records и свежие ключи. Используется local provider. Все роли и 10 млн RUB являются входами эксперимента. JSON содержит исходы, условный баланс и ограничения. Сессии и реальные UID туда не экспортируются.

## Восстановление

1. Остановить writers и сохранить закрытую согласованную forensic-копию source SQLite и WAL.
2. Проверить оригинальные journal/witness и independently retained checkpoint по runbook UEL 0.3. При утрате доказательств сохранить UNKNOWN_RECOVERY.
3. Применить штатный privacy_maintenance.restore только к синтетической копии. Старые сессии и неподтверждённые generations должны остаться отвергнутыми.
4. Проверить integrity_check, foreign_key_check, Help/Asset denials и отсутствие возвращённого приватного содержимого. Повреждённый индекс восстанавливать только через проверенный UEL repair.
5. Старый Scenario не считать разрешением. Получить новый authorized snapshot и новые исходные предположения. Модель пересчитывается с нуля, фактические источники она не изменяет.

Rollback кода выполняется выключением нового флага и возвратом совместимого исходного runtime при сохранённых актуальных controls. Новые on-disk состояния капитала отсутствуют.

## Следующая миграция

До durable capital потребуются опубликованная версия UEL source contract, минимальные доменные таблицы capital lots/restrictions/reservations/approvals/projects, уникальные source references и FK к текущей identity. Утверждение amount/destination/purpose/version/expiry должно потребляться атомарно вместе с доменным событием и audit outbox. Предыдущие migration checksums сохраняются.

Нужны испытания на пустой и существующей базе, повтор, rollback DDL, concurrent reservations, утрата ACK, crash до/после journal и commit, отзыв во время исполнения, неподтверждённое внешнее исполнение, независимый checkpoint и полный отказ при согласованном откате. Legacy payment/contribution rows требуют явного mapping и проверки происхождения до любого переноса. Без этих доказательств переход блокируется.
