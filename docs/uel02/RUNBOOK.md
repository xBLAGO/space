# UEL 0.2 — миграция и восстановление

1. Остановить процессы синтетического стенда. Сделать согласованную SQLite backup-копию по существующей процедуре Privacy Recovery, сохранить оригинальный журнал, ключи и witness с раздельным хранением. Проверить, что экземпляр и все пути относятся к синтетическому стенду.
2. Проверить patched SQLite командой `python -m xblago.core.sqlite_safety --require-safe`. Установить `XBLAGO_INTEGRATION_MODE=synthetic`, `XBLAGO_LLM_PROVIDER=local`, `XBLAGO_PILOT_MODE=1`, `XBLAGO_REQUIRE_SECURE_IDENTITY=1` и `XBLAGO_REQUIRE_PRIVACY_JOURNAL=1`. Использовать исходные параметры журнала/witness этого стенда.
3. На копии с полной схемой PR #30 выполнить `python -m xblago.core.uel_schema /path/to/synthetic.sqlite`. Миграция `uel_local_002` сверяет контрольные суммы предшественников и целостность, выполняется транзакционно и не запускается при старте приложения. Повторный вызов возвращает `unchanged`. Существующие управляемые строки без наблюдений требуют явно согласованного `--record-baseline` при первой установке. Он фиксирует только проверенное текущее состояние. Legacy-данные автоматически не импортируются.
4. Сверить `PRAGMA integrity_check`, `PRAGMA foreign_key_check` и результаты тестов ниже. Для выдачи проекции установить `XBLAGO_UEL_LOCAL_ENABLED=1`. Выключение флага закрывает API, а установленный индекс продолжает фиксировать наблюдения. Все процессы должны использовать один и тот же совместимый runtime и исходный control state.
5. Для восстановления остановить все процессы. Проверить оригинальный журнал и witness, применить штатное восстановление 05.4 к отдельной копии. Если backup сделан до UEL, но имеет полную схему PR #30, сначала явно установить UEL с оригинальным control state. Старый backup с неполной схемой предшественников требует их сертифицированной процедуры миграции. Повторить проверки целостности, доступа, отзыва, отсутствия возвращённых payload и результатов проекции.
6. При `UNKNOWN_RECOVERY` остановить экземпляр и сохранить доказательства. Нужен подтверждённый snapshot с оригинальными журналом и witness. Не обнулять индекс и не запускать старый бинарник против изменённого control state. Если исходные доказательства утрачены, экземпляр остаётся заблокированным. Безопасный обратимый шаг — выключить выдачу через флаг.

```sh
PYTHONPATH=.:src python -m unittest tests.test_uel_local -v
PYTHONPATH=.:src python -m unittest discover -s tests -v
PYTHONPATH=src python docs/ledger01/generate_vectors.py --check
PYTHONPATH=src python docs/ledger01/check_vectors.py --output /tmp/uel-offline-results.json
```
