# UEL 0.3 — синтетический стенд и recovery

## Воспроизводимые испытания

Использовать Python и исправленную SQLite по существующему CI-процессу сборки. Только временные синтетические данные.

```sh
python -m xblago.core.sqlite_safety --require-safe
PYTHONPATH=.:src python -m tools.freeze_integration
PYTHONPATH=.:src python -m tools.uel03_lab_check --output /tmp/uel03-lab.json
PYTHONPATH=.:src python -m tools.uel03_lab_check --full --repeat 2 --output /tmp/uel03-full.json
PYTHONPATH=src python docs/ledger01/generate_vectors.py --check
PYTHONPATH=src python docs/ledger01/check_vectors.py --output /tmp/uel03-offline72.json
```

Lab checker создаёт свежие синтетические ключи, отдельный процесс хранителя и временные хранилища для каждого теста. Итоговый JSON содержит имена тестов, ожидаемые и фактические исходы. Тесты делают process crash, имитацию ENOSPC/write/commit/fsync failures, rollback копий, повтор и конкуренцию. Имитированная ошибка диска не доказывает поведение физического заполненного носителя.

## Ручное подключение на синтетической копии

1. Убедиться в exact base #31, исправленной SQLite, полной установке прежних миграций и исходных journal/witness. Сохранить отдельные подтверждённые копии. Остановить writers.
2. Создать новый ключ отправителя через `uel_checkpoint.create_key` в закрытом каталоге приложения. Передать только публичный ключ хранителю по аутентифицированному внешнему каналу.
3. Создать случайный 32-byte hex `stream`. На стороне хранителя явно выполнить `python -m xblago.core.uel_checkpoint initialize --directory NEW_PRIVATE_CUSTODY --stream STREAM --source-key SOURCE_PUBLIC_KEY`. Каталог должен быть новым. Обменяться публичным ключом хранителя и закрепить его.
4. Запустить отдельный процесс `python -m xblago.core.uel_checkpoint serve --directory CUSTODY --endpoint ABSOLUTE_SOCKET_PATH`. Локальный socket adapter проверен на одном host. Удалённый транспорт и квалификация независимого оператора требуют следующего этапа.
5. Создать конфигурацию 0600 с точными полями `stream`, `custodian_key`, `source_key_file`, `endpoint`. Строки путей абсолютные. JSON канонический — `json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)`. Установить `XBLAGO_UEL_CHECKPOINT_CONFIG` во всех source workers и maintenance-процессах.
6. Проверить только синтетические флаги Integration 02, secure identity, privacy journal и local provider. Выполнить `python -m xblago.core.uel_checkpoint enroll --journal ORIGINAL_JOURNAL`. Закреплённая политика попадёт в исходный journal. Повтор идемпотентен. Потеря config после enrollment закрывает операции. Доверенная конфигурация должна храниться независимо от backup приложения, включая копии до enrollment.
7. Убедиться в read-only проекции и проверках отрицательных сценариев. Новых миграций application SQLite нет. Custody store имеет отдельную версионированную схемой профиля структуру, создаваемую только явным initialize. UEL feature flag остаётся OFF до отдельного включения на синтетическом стенде.

## Повреждение индекса и удаление

1. Остановить все source workers. Хранитель должен оставаться доступным. Сохранить закрытую forensic-копию повреждённой SQLite с согласованным WAL и исходными контролями.
2. Проверить pinned config и все оригинальные journal/witness/custody доказательства. При неизвестной актуальности остановить восстановление. Сброс контролей и повторная инициализация потерянного хранилища не являются recovery.
3. На отдельной копии выполнить `python -m xblago.core.uel_recovery /absolute/synthetic.sqlite`. Операция использует SQLite write lock и оставляет repair audit. Если не удаётся подтвердить источники, транзакция откатывается.
4. Проверить `integrity_check`, `foreign_key_check`, сохранение исходных записей и совпадение UEL. После восстановления применить действующую процедуру Privacy Recovery к старому backup с обязательной инвалидизацией сессий. Проверить Help holds, memory deletion и context fencing.
5. Вернуть обычный доступ только к проверенному экземпляру. Удаление выполняет сам авторизованный пользователь через штатные preview/confirm PrivacyService. Repair CLI не выдаёт пользовательских полномочий.
6. При сбое после custody/journal commit повторить проверенное восстановление. При потере доказательств результат остаётся UNKNOWN_RECOVERY. Для отката версии использовать проверенный совместимый runtime и сохранённые текущие контроли. Удаление enrollment policy или запуск старого бинарника создаёт недопустимый обход.

## Частный экспорт и внешняя проверка

На стороне хранителя выполнить `python -m xblago.core.uel_checkpoint export --directory CUSTODY --output NEW_PRIVATE_PROOF`. Экспорт создаётся с режимом 0600. Публичный ключ, stream и последний tip закрепить у проверяющего отдельно от файла доказательства.

```sh
python tools/uel_checkpoint_verify.py PRIVATE_PROOF --key PINNED_PUBLIC_KEY --stream PINNED_STREAM --tip PINNED_TIP
```

Подлинный старый префикс отклоняется при более новом pinned tip. Вывод OFFLINE_INTEGRITY_ONLY означает проверку конкретного закреплённого префикса. Для текущей онлайн-проверки необходим свежий nonce-bound ответ хранителя. Потеря ключа, custody DB или retained head требует исходной подтверждённой копии и независимого tip. Проверенное автоматическое восстановление хранителя из устаревших копий не предоставляется.
