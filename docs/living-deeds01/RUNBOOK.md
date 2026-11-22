# Изолированный запуск и восстановление

1. Создать отдельную рабочую копию exact source SHA из PR, не production DB.
2. Использовать проверенную Python/SQLite связку; выполнить
   `python -m xblago.core.sqlite_safety --require-safe`. Не заменять system SQLite.
3. Создать новую synthetic Integration/Help базу и отдельные journal/witness
   directories. Применить существующие миграции в документированном порядке.
4. Мигрировать Asset и UEL; сгенерировать новые synthetic checkpoint keys и
   enroll live pinned custodian. Не использовать публичные fixture seeds runtime.
5. Остановить всех writers. На копии выполнить
   `living_schema.migrate(path)` при выключенном новом флаге. Повтор должен быть
   no-op, integrity / foreign_key checks должны пройти. Исходные SHA и checksum
   записать вне самой базы. Сохранить прежние headers для сравнения.
6. Только для стенда явно установить `XBLAGO_LIVING_DEEDS_ENABLED=true`.
   `XBLAGO_INTEGRATION_MODE=synthetic`, local provider и прежние guards обязательны.
7. Вызовы выполняются существующими session tokens: `LivingDeeds(app).prepare`
   возвращает exact preview; `commit(token, challenge, 'ПОДТВЕРЖДАЮ')` его фиксирует.
   Raw token/challenge не передавать в отчёты. Idempotent commit возвращает
   исходную content-free receipt; повтор prepare того же key отвечает conflict
   и предлагает повторять первоначальный commit, не создавать новое действие.
8. Просматривать `view(token)` только с актуальной сессией. Приглашение существует
   в защищённом inbox; внешний Telegram/email delivery не реализован.

## Воспроизводимые проверки

Все команды используют новые временные synthetic stores, не рабочие данные:

```sh
PYTHONPATH=.:src python -m tools.living_contract_check
PYTHONPATH=.:src python -m tools.living_deeds_lab --output /tmp/living-evidence.json
PYTHONPATH=.:src python -m tools.uel03_lab_check --full --repeat 2 --output /tmp/living-full.json
PYTHONPATH=src python docs/ledger01/generate_vectors.py --check
PYTHONPATH=src python docs/ledger01/check_vectors.py --output /tmp/living-offline.json
PYTHONPATH=.:src python -m tools.freeze_integration
```

Основной synthetic test harness поднимает собственный pinned checkpoint process,
проверяет разрешения A/B/приглашённого, закрытие Help, race, restore и отсутствие
утечек. Это не browser acceptance или независимая федерация.

## Recovery

* Нормальный restart: тот же certified SQLite + оригинальные journal/witness и
  текущий checkpoint. Не копировать secrets между реальными узлами.
* Актуальный backup: восстановить только в новую isolated директорию существующим
  `privacy_maintenance.restore`, сохранив оригинальные независимо retained controls.
  Сессии после restore переавторизовать штатно; не продлевать их SQL-правкой.
* Повреждённый derivative UEL index: остановить writers, сохранить повреждённую
  копию, вызвать существующий `uel_recovery.repair_index(path)`. Оригинальные
  private source payloads должны быть целы. Проверить идентичные headers/проекцию.
* Старый backup с отсутствующими событиями: не backfill положительных grants.
  Retained denial доминирует; UNKNOWN_RECOVERY означает остановку новых Living
  операций. Сохранить evidence и независимые controls для ручного review.
* Утрата journal/witness/current custodian: не подменять их новыми пустыми файлами.
  Отсутствие доказательства не является успешной проверкой.
* Удаление: owner erasure / existing account deletion; отдельно retire старые
  backup bytes. Erasure не обещает перезапись WAL, блоков диска или копий оператора.

Rollback приложения: выключить новую логику, сохранить расширенную базу и
контрольные доказательства. Не запускать старый бинарник на новой схеме и не
откатывать одновременно DB и независимые anchors. Для отказа от эксперимента
удалять только его disposable копию по отдельному явному решению.

## Обязательные будущие решения

Политика retention / backup retirement; шифрование at rest; review shared/private
историй; безопасное снятие do-not-disturb новым согласием; post-dispute resolution;
multiple capacity resources; физические устройства; независимый security review;
интеграция с #41 и решение о реальных участниках. До этого real pilot NO-GO.
