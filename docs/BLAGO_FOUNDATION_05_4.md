# Foundation 05.4 — Pilot Safety Gate

Дата проверки: 23 октября 2026. База: PR #6,
`224ad9d2a1775a8372354af35d542f433cb8bdde`.
Отдельная ветка `feat/foundation-05-pilot-safety-gate`; PR зависит от #6.

**Решение: NO-GO для закрытого пилота.** Исправлены воспроизводимые дефекты
протокола восстановления, добавлены обязательные технические gates и процедура
аварийного восстановления. Реальный HF runtime, независимое эксплуатационное
хранение, реальные устройства и качество провайдера ещё не прошли приёмку.
Успех автоматических тестов не заменяет эту приёмку. Production SQLite, secrets,
настройки Space и deployment не изменялись; merge не выполнялся.

## Фактическая база и совместимость

[Карта PR #2–#6](validation05_4/pr-chain.json) фиксирует открытые PR и SHA.
Все пять head-коммитов являются предками #6. Старые 206 тестов остаются частью
полного набора. Добавлены 17 негативных/интеграционных проверок безопасности и
два накопительных интеграционных теста; итоговый набор — **225/225 PASS**, 108.262 секунд.
Результат фактического запуска записан в [local-tests.txt](validation05_4/local-tests.txt).

Первый накопительный тест запускает **исходный код PR #2** через `git archive`
в отдельном процессе и создаёт настоящую историческую SQLite: Web password и
session, Telegram ID, память, историю, ресурс. Текущий код и миграции сохраняют
ID, пароль, память, историю и ресурс. CI использует полный git history.
Второй тест использует одну существующую SQLite для всего стека: симулированная
завершённая оплата, ровно один contribution, migrations 05.1–05.4, account merge,
Telegram update dedupe, Mini App proof/challenge, Web session, WebAuthn,
общая память/история, удаление отдельного участника, backup/restore/restart.
Экономические события сравниваются целиком; допустимо только уже предусмотренное
merge изменение владельца. Нет реальных денег или Telegram network updates.

## SQLite WAL-reset: официальное исправление и фактические библиотеки

[Официальная документация WAL](https://www.sqlite.org/wal.html) описывает
WAL-reset bug: затронуты SQLite 3.7.0–3.51.2; исправление — 3.51.3 и новее,
backports — 3.44.6 и 3.50.7. Риск относится к конкурентной работе нескольких
соединений с WAL. Отключение WAL не принято как замена исправлению.
В [официальной истории релизов](https://www.sqlite.org/changes.html) проверены
релиз 3.54.1 от 2026-10-16, source ID и SHA3-256 amalgamation.

Поставляется фиксированная SQLite **3.54.1**, source ID:
`2026-10-16 14:45:20 555e07ef33f69a0957d24d8e555b702464a131d6f1607f4c3c1e4551e8e6ca76`.
`tools/build_sqlite.py` проверяет SHA256 ZIP и опубликованный SHA3-256 `sqlite3.c`,
компилирует только проверенный исходник, не запускает скрипты архива.
Docker и Linux CI подключают библиотеку к Python `_sqlite3`; проверка вызывает
саму Python SQLite и `sqlite_source_id()`, а не отдельную утилиту `sqlite3`.

| Среда | Наблюдение | Решение |
|---|---|---|
| Исходный локальный Python 3.12.10 | SQLite 3.49.1, статически связанный macOS `_sqlite3` | Не разрешён для pilot |
| Изолированный Python driver локального прогона | SQLite 3.54.1, точный source ID совпал | Исправление подтверждено для этого процесса |
| Docker PR #6 | SQLite 3.46.1, старый CI Tests #42 | Backport не подтверждён; не разрешён |
| Docker этого PR | Проверяется отдельным CI шагом внутри собранного image | При несовпадении CI красный |
| Действующий HF Space | Python/SQLite/source ID **не установлены** | Блокер; floating `python:3.12-slim` не доказательство |

[Runtime inventory](validation05_4/runtime-inventory.json) содержит исходные
наблюдения. На macOS простая `DYLD_LIBRARY_PATH` не меняет статически связанный
драйвер: для тестов отдельно собран `_sqlite3` из проверенного CPython 3.12.10
через `tools/build_macos_sqlite_driver.py`, без изменения системного Python.

`python -m xblago.core.sqlite_safety --require-safe` возвращает ненулевой код
на неподтверждённой библиотеке. CI выполняет его до тестов и внутри Docker.
`XBLAGO_PILOT_MODE=true` повторяет gate до открытия application DB и journal.
Нельзя обойти проверку строковым утверждением о vendor backport. Более новые
релизы тоже требуют явного обновления allowlist и review. Это намеренный
fail-closed pin, не универсальный анализ всех поставок SQLite.
Legacy development mode сохранён; он **не является разрешённым pilot runtime**.

HF проверен только read-only Hub metadata/source GET: работающий SHA
`b95c630eb69ec4a505e2008a15a4417b12a74553`, stage RUNNING, Docker SDK,
`/data` имеет mount type `bucket`. Попытка read-only SSH probe остановилась на
неизвестном ED25519 host key при `StrictHostKeyChecking=yes`; проверка доверия
не отключалась. Нужны доверенный host key и санкционированная runtime attestation.
Отдельно требуется квалифицировать WAL locks/fsync/atomic rename на выбранном
volume: bucket mount сам по себе не доказывает POSIX durability.

## Повторный security review Privacy Recovery

Независимый от автора изменений read-only reviewer воспроизвёл ошибки на
замороженном SHA #6 и повторил проверки после исправлений. Это отдельный
технический reviewer-agent, **не внешний человеческий аудит**.
Доказательства: [baseline review](validation05_4/independent-baseline-review.md),
[повторный review](validation05_4/independent-recheck.md).

| Находка | Исправление и негативная проверка |
|---|---|
| HIGH: backup старой legacy memory → изменение → удаление → restore возвращал старый факт | 05.4 BEFORE UPDATE сохраняет HMAC lineage; replay удаляет все предшествующие версии до tombstone |
| HIGH: merge менял владельца/ключ; backup до merge открывал удалённые данные alias через export | Lineage фиксирует фактические OLD/NEW fingerprints, включая динамический `telegram.city.2`; проверены memory и history |
| MEDIUM: `/history` возвращался из Telegram response cache после clear | Сбрасываются response/error при сохранении update dedupe; отдельный cache-clear intent повторяется после restore |
| LOW: подписанная отрицательная вершина использовала Python negative index | Seq строго int, не bool, неотрицательный; shape/head/MAC проверяются |
| LOW: ошибка открытия lock не преобразовывалась в закрытый отказ | Ошибка нормализована в JournalUnavailable; отдельный PermissionError тест |
| MEDIUM: retry после опубликованного checkpoint и ошибки directory fsync мог подтвердить недолговечную запись | Повторная синхронизация checkpoint file и directory до ACK; fault injection и независимая перепроверка |

Lineage содержит HMAC fingerprints, не открытые тексты. Обход графа ограничен
позицией удаления, защищён от циклов и не стирает заново сообщённый после удаления
факт. Совпадающий текст другого участника не является основанием удаления.
Новая миграция добавляет только `pilot_safety_state`, `pilot_schema_migrations`
и три trigger. DDL/checksums 05.1–05.3 не переписываются. Повторный запуск
идемпотентен; schema drift/неправильный cursor закрывают доступ.

**Предел исправления:** lineage работает с момента 05.4. Она не восстанавливает
неизвестные переходы до миграции. Старые backup без 05.4 marker блокируются в
pilot mode. Нельзя применить marker к произвольной старой копии и считать её
безопасной: нужен независимый аудит старых удалений/merge и certified golden
baseline. При недостатке доказательств — quarantine, без обслуживания запросов.

HMAC защищает от изменения/потери цепи при сохранности ключа. При компрометации
хоста вместе с ключом противник может подписывать данные; неподконтрольная
оператору копия и независимый witness обязательны. Утрата ключа без escrow —
закрытый отказ, не создание новой цепи. Прерывание записи/fsync может сохранить
критический intent при откате application transaction: протокол предпочитает
консервативный отзыв/удаление, а не потенциальное раскрытие после неопределённого
сбоя. Незавершённую операцию оператор сверяет с журналом до повторного открытия.

## Независимое хранение и восстановление сервера

`journal_witness` хранит подписанные immutable-by-protocol checkpoints отдельно
от journal tree. Откат одновременно journal и локального head обнаруживается
по внешней максимальной вершине. Отсутствующий/недоступный witness в pilot mode
закрывает доступ; автоматической инициализации взамен потерянного нет.
Checkpoint фиксируется до подтверждения критического изменения приложения.
POSIX locks, exclusive publish, file/directory fsync покрыты fault-injection.

`journal_custody` создаёт authenticated AES-GCM DR envelope из согласованной
SQLite backup журнала, HMAC key и head, с hashes/manifest. Wrapping key — отдельный
32-byte 0600 файл для независимого escrow/KMS. Recover принимает только новый
каталог, ограничивает размер, проверяет AEAD, manifest, HMAC chain и текущий
внешний witness. Повреждённый пакет, неверный ключ или пакет позади witness
отклоняются. Незашифрованные ключи не попадают в Git, UI, LLM или application export.

Автоматический тест уничтожает основной application DB и исходные journal/key/head,
восстанавливает их из независимых синтетических копий в **другом процессе и
каталогах**, проверяет доступ оставшегося участника, невозможность входа
удалённого, отсутствие удалённых фактов и FK/integrity. Это проверка протокола
отдельного экземпляра; физически независимые серверы, облачный WORM/IAM и потеря
целого дата-центра в этом окружении не проверялись.

Полная [инструкция миграции и DR](BLAGO_FOUNDATION_05_4_MIGRATION.md) разделяет
application backups, encrypted journal bundles, wrapping-key escrow и witness.
Локальный путь вне journal tree не доказывает независимый fault domain и не
защищает от root. Реальный closed pilot требует независимого администратора
checkpoint storage, retention/object lock, запрета rollback/delete основному
runtime и подтверждённого recovery drill. Потерянную последнюю вершину нельзя
молча пересоздать из доступного старого journal.

## Экономика, legacy merges и приватность

[Политика retention и legacy merges](BLAGO_FOUNDATION_05_4_RETENTION.md) — проект
для явного утверждения владельцем. Экономические правила/суммы/события не меняются.
05.3 по-прежнему отклоняет account deletion при финансовых зависимостях; 05.4
не выдаёт это за полное право удаления для всех участников. Legacy merge без
доказуемой alias chain требует ручной проверки/quarantine. Не используются
имя, IP, fingerprint или LLM для автоматического отождествления людей.
Сбор необязательной активности не включён.

## WebAuthn: фактически проверенные и непроверенные платформы

[Результат Chrome](validation05_4/browser-results.json),
[desktop](validation05_4/chrome-desktop.png),
[mobile viewport](validation05_4/chrome-mobile-viewport.png).
Проверен настоящий установленный Chrome 150.0.7871.125 на macOS, headless,
изолированный профиль, localhost с одноразовой SQLite. Через реальный UI и
WebAuthn API прошли registration, cookie rotation/отказ старому token,
discoverable credential login после logout, revoke других sessions/отказ их
tokens, скачивание personal export; JS page errors отсутствуют.
Authenticator — **виртуальный CTAP2 CDP с resident key/UV**. Физический ключ,
Touch ID, passkey sync и реальная биометрия этим не проверены.

| Сценарий | Статус |
|---|---|
| Chrome desktop, виртуальный CTAP2 | PASS, 8 checks; повторяется в Linux CI |
| Chrome mobile viewport | Только layout; не Android и не Telegram |
| Safari/macOS/iOS, Firefox | Не проверены |
| Telegram WebView iOS/Android | Не проверены; открытие во внешнем браузере тоже требует реального acceptance |
| Physical passkeys, Touch ID, platform recovery | Не проверены |

Существующие отрицательные WebAuthn signature/origin/challenge/replay/IDOR тесты
сохранены. Они не доказывают совместимость hardware/browser. Серверный Telegram
initData/challenge обмен проверен интеграционно синтетической подписью; реальные
клиенты не подключались. Исправлен устаревший UI текст: после unlink новое
Telegram подключение требует явного `/start`, как уже требует сервер.

## Повторная живая HF-интеграция

`tools/pilot_hf_check.py --live` использует только синтетических участников
в одной временной SQLite и существующую локальную HF authentication. Запросы
идут напрямую в inference router (`Qwen/Qwen3-14B:cheapest`), а не в chat API
production Space. Секреты/реальные идентификаторы модели не передавались.
[Сырые диалоги, latency и manual review](validation05_4/hf-integration.json).

10 сценариев: знакомство, уточнение потребности, изменение предпочтения,
исправление города, запрет запоминания с продолжением разговора, забывание,
чужая потребность, повторное обращение, сбой и восстановление. Всего 14 turns,
13 provider invocations, **12 реальных HTTP обращений**, 11 успешных ответов,
**один настоящий HTTP 503** при знакомстве; ещё один сбой отдельно инъецирован.
Дополнительных extractor LLM calls — 0. Median turn 1040.065 ms, максимум
4057.60 ms. Детерминированные проверки identity/memory/no implicit needs/
failure safety/restart/integrity прошли. Это один малый прогон, не SLO/нагрузочный тест.

Естественность не признана удовлетворительной: первый вопрос о велосипеде
получил повторное приветствие; ответ после восстановления оборвался на середине
фразы; deterministic acknowledgement города заканчивается «Казани» без полной
фразы. `finish_reason` не сохранялся, поэтому причина обрыва не установлена.
Нет достаточных данных приписать всё новым фильтрам или исключить их влияние.
Нужны исправление и повторная продуктовая приёмка. Сбой провайдера не изменил
память/экономические заявки; безопасный fallback не считается успешным HF ответом.

## Проверки и CI

Локально: весь unittest набор на проверенной SQLite, отдельный независимый
прогон 17 safety tests, два stack tests, Chrome acceptance и живая HF интеграция.
CI выполняет полный набор, compileall, patched-library gate, Chrome UI и
публикует browser-evidence artifact; Docker job собирает image и проверяет
фактическую Python SQLite, health/PWA, Identity Runtime, Privacy Recovery и
строгую pilot конфигурацию с 05.4 baseline + независимым witness.
Точный CI run и SHA приводятся в draft PR; зелёный статус относится только к
проверенному коммиту и не является разрешением deployment.

## Таблица блокеров и доказательства

| Gate | Состояние | Доказательство / условие закрытия |
|---|---|---|
| Проверенный SQLite candidate и fail-closed CI/runtime | Исправлено в коде; image подтверждает CI | sqlite_safety, official hashes, runtime inventory, CI Docker log |
| HIGH resurrection после изменения/merge | Исправлено для 05.4 lineage | Independent recheck + old-backup negative tests |
| Журнал/head rollback, повреждение, key loss, fsync failures | Тесты PASS в заявленной trusted-host модели | 17 safety tests, независимый review |
| Синтетический новый экземпляр DR | PASS локально/автоматически | Отдельный процесс, AES bundle + внешняя вершина |
| Реальный HF Python/SQLite и durable volume | **OPEN, блокирует** | Trusted runtime probe + WAL/locks/fsync qualification |
| Независимое offsite custody/IAM/key escrow | **OPEN, блокирует** | Отдельные custodian/fault domains, retention, recovery drill |
| Исторические pre-05.4 backups/deletions/legacy merges | **OPEN, блокирует** | Reviewed golden baseline, доказанная alias lineage, retirement unsafe copies |
| Retention для финансовых аккаунтов | **OPEN, блокирует такой scope** | Утверждение политики и безопасный процесс; текущий API отказывает |
| Реальные поддерживаемые browser/WebView/passkeys | **OPEN, блокирует заявленную поддержку** | iOS/Android Telegram + выбранные реальные devices, публичный HTTPS RP |
| HF доступность и естественность | **OPEN, блокирует приёмку агента** | Повторные диалоги после исправления, причины 503/обрыва, критерии качества |

Следующие PR: продуктовый HF quality/fallback, approved retention/legacy remediation,
remote custody adapter с проверяемыми IAM/retention, device/browser acceptance.
Изменение operational gate с NO-GO возможно только по новым доказательствам и
отдельному решению владельца, а не по числу unit-тестов.
