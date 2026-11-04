# BLAGO Foundation 05.1 — Digital Identity & Activity

Дата аудита: 23 октября 2026. Статус: спецификация и offline-миграция;
новые способы входа, сбор событий и аналитика **не включены**.

## Интеграционная база и зависимости

GitHub на момент аудита: открыты PR #2 (Unified Web/PWA/Mini App) и
[PR #3](https://github.com/xBLAGO/space/pull/3) (Foundation 04.2).
PR #3 ещё не объединён, его head — `af94ac93dde649747b1a89104000f92f75ecb7fc`,
base — `feat/foundation-04-semantic-memory` / `25dcb0f`.
Ветки Foundation 02, 03, 04, fix 04.2 и main существуют отдельно.
Следовательно, main нельзя считать фактической интеграционной базой 05.
Этот PR создан от af94ac9 и адресован `fix/foundation-04-memory-pilot`:
это зависимый draft; после интеграции #3 его нужно перенаправить на принятую
интеграционную ветку и повторить CI. Не переносить только DDL на устаревший main.
Отчёт 04.2 содержит 121 успешный тест и нерешённые проблемы живого HF.

## Что уже реализовано и повторно используется

`SQLiteBlagoStore` — единый источник пользователей, Telegram-привязок,
памяти, истории и идемпотентности updates. `WebApp` использует тот же store.
`PersonalAssistantAgent` и `SemanticMemoryExtractor` не участвуют в проверке
личности. Экономические правила и HF-вызовы этим PR не изменяются.

`blago_user_id` — строковый UUID4; Telegram user ID и username не являются
BLAGO ID. Ключ `channel_links(channel, channel_user_id)` уникален; создание
происходит под `BEGIN IMMEDIATE`. Один sender сохраняет ID при смене chat.
Без sender существует fallback `chat:<id>`: это техническая привязка, её нельзя
объявлять подтверждённым участником. Имя, город, совпадение текста и LLM-вывод
также не доказывают связь аккаунтов.

Web уже имеет приглашение, username/password, PBKDF2-SHA256 (400000 итераций),
случайные сессии с SHA256 токена в SQLite и семидневным сроком, CSRF,
origin-проверку и HttpOnly/SameSite=Lax cookie (Secure по умолчанию).
Mini App проверяет HMAC initData и auth_date (час, допуск будущего 60 секунд),
получает Telegram ID и вызывает тот же resolver. Доверять initDataUnsafe нельзя;
источник: [Telegram Mini Apps](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).
Обмен initData на сессию пока не имеет отдельного одноразового replay-ledger.
Bot принимает updates через polling или секретный webhook; события имеют
UNIQUE(channel, external_update_id) и кэш ответов. Повтор может обновить
метаданные channel_links, но не должен повторять диалог/операцию.

Web→Telegram связывается явным `/link` в private chat с одноразовым
хешированным кодом на 10 минут. Текущий merge переносит память, историю,
привязки, сессии и экономические ссылки; исходный users.status становится
`merged`. Он ещё не знает о новых таблицах. **До любого writer в новые таблицы
нужно расширить merge либо запрещать merge при наличии новых данных**, покрыв
это отдельными тестами. В этом PR новые таблицы остаются пустыми.

## Карта существующих таблиц

| Таблицы | Назначение / владелец |
|---|---|
| users, profiles | ID, статус, профиль; SQLiteBlagoStore |
| channel_links | Авторитетная привязка Telegram; НЕ создавать identity_links-дубль |
| memory_facts, semantic_memory | Legacy и версионная персональная память |
| dialogue_turns | Общая история Telegram/Web/Mini App |
| events | Dedupe updates, состояние обработки и безопасный кэш ответа |
| migrations | Журнал импорта старых JSON, не журнал версий схемы |
| web_credentials | Парольная Web-аутентификация |
| web_sessions | Авторитетные Web/Mini App сессии; НЕ создавать user_sessions-дубль |
| account_link_codes | Одноразовое явное объединение аккаунтов |
| blago_needs, blago_resources | Потребности и ресурсы |
| blago_assets, blago_resource_offers | Активы и предложения |
| blago_payment_requests, blago_payment_request_offers | Заявки и предложения исполнения |
| blago_fulfillment_events, blago_contribution_events | Экономические события |

Core DDL находится в `core/storage.py`, Web/economy DDL — в
`integrations/web.py`. Их startup executescript не следует копировать для
миграций: sqlite3.executescript может неявно завершить транзакцию.

## Карта маршрутов до изменений

Все API имеют префикс `/api/v1`. Новых HTTP-маршрутов этот PR не добавляет.

| Метод | Путь | Проверка |
|---|---|---|
| POST | /auth/register, /auth/login, /auth/telegram | Origin, rate limit; приглашение/пароль или signed initData |
| POST | /auth/link-code, /auth/logout | Сессия + CSRF |
| GET, PATCH | /me | Сессия; CSRF для PATCH |
| GET, POST | /chat/messages | Сессия; CSRF для POST |
| GET, DELETE | /memory | Сессия; CSRF для DELETE |
| GET | /analytics/me, /analytics/community | Сессия; существующие агрегаты |
| GET, POST | /needs, /resources | Сессия; CSRF для POST |
| GET, POST | /economy/assets, /economy/resource-offers, /economy/payment-requests | Сессия; CSRF для POST |
| GET | /economy/payment-offers, /economy/analytics | Сессия |
| GET | /economy/payment-requests/{id}, /economy/payment-requests/{id}/events | Сессия + проверка доступа |
| POST | /economy/payment-requests/{id}/offer, /simulate-payment, /confirm | Сессия + CSRF + доступ; последние два суффикса также после {id} |
| POST | /economy/payment-offers/{id}/accept | Сессия + CSRF + доступ |

Вне API: GET `/`, `/app`, `/app/` и статические PWA-файлы;
GET `/healthz`; POST `/telegram/{secret}` с проверкой webhook-секрета.
IP используется только для существующего временного rate limit, не для ID.

## Модель участника и жизненный цикл

Подтверждённый цифровой участник означает проверенное управление конкретным
способом входа. Это не паспортная идентификация, не доказательство одного
человека на всей сети и не социальный рейтинг. Несколько аккаунтов человека
возможны; автоматическая дедупликация людей запрещена.

Существующий ID сохраняется навсегда в пределах аккаунта: создание →
подтверждение средства входа → добавление/отзыв средств → ограничение входа
при угрозе → восстановление по заранее подтверждённому средству → удаление
по отдельной политике. Значения users.status сейчас active/merged; новые
состояния и отметку verified этот PR не вводит и старые записи не повышает.
При явном объединении показывать сохраняемый ID и последствия, требовать
свежее доказательство контроля обеих сторон, ревокировать старые сессии,
транзакционно обработать все FK. Удалять последнее средство входа нельзя без
подтверждённой альтернативы или явного удаления аккаунта. Восстановления через
биографию, память агента, похожий IP или решение LLM нет.

## Passkeys и сессии: контракт следующих PR

WebAuthn реализовать библиотекой с поддержкой верификации COSE, не собственной
криптографией. Сервер фиксирует RP ID и allowlist HTTPS origins, генерирует
криптографический challenge >=32 случайных байт и отдельную browser binding.
TTL challenge <=10 минут; успешная проверка и consume атомарны. Проверять
type, challenge, origin, RP ID hash, подпись, UP и требуемый UV; registration
доступна только после свежей аутентификации владельца. Discoverable login
может начинаться без user ID; владелец определяется по credential_id, затем
сверяется user_handle. user_handle — случайные непрозрачные байты, стабильные
для участника/RP, не Telegram ID. Уникальность handle на участника должна
обеспечиваться будущим writer; разные credentials могут иметь один handle.
Аттестация none по умолчанию, без обязательного паспорта/модели устройства.
Счётчик и backup flags — сигналы для повторной проверки, не доказательство
нового человека. Основание: [W3C WebAuthn](https://www.w3.org/TR/webauthn-3/).

`user_devices` — добровольная случайная метка, не fingerprint и не средство
аутентификации. Не хранить IP/MAC/IMEI/canvas/browser fingerprint. Метка может
быть отозвана; существующая Web-сессия от этого пока не меняется.

Сессии продолжат использовать web_sessions; 05.2 должен добавить совместимые
метаданные created/last_used/revoked/auth_method, idle+absolute timeout,
ротацию после входа/step-up/link, список и отзыв всех сессий. Проверять статус
участника на каждом обращении. До этого merged-сессии и семь дней абсолютного
TTL остаются ограничением. Никогда не писать raw token/CSRF/initData в events
или LLM. См. [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

Подозрительные входы (replay, неудачи подписи, аномалия counter) дают ограниченный
по времени security_signal и запрос повторного подтверждения. Не объединять
людей и не вводить репутацию. Нужны rate limiting, понятное уведомление без
секретов, возможность оспорить блокировку и защита от account enumeration.

## Новая схема и совместимость

Миграция `foundation05_identity_001`, модуль `xblago.core.identity_schema`:

| Новая таблица | Данные и инварианты |
|---|---|
| identity_schema_migrations | Версия, checksum DDL, время; отдельно от JSON migrations |
| user_devices | Random device_id, FK участника, добровольная label, даты создания/отзыва |
| passkey_credentials | Уникальный binary credential_id, COSE public key, opaque handle, counter, backup flags, отзыв; composite FK запрещает чужое устройство |
| auth_challenges | SHA256 challenge и browser-binding, purpose, optional user, RP/origin, TTL/consume; registration/link требуют user |
| activity_events | Только allowlist события, канал, владелец, время и expires_at; без текста/metadata JSON |
| security_signals | Только тип сигнала, состояние рассмотрения, владелец, сроки; без IP и fingerprint |
| consent_records | Purpose, версия политики, granted/withdrawn, время; отдельные решения вместо перезаписи истории |

Тimestamps новых таблиц — UTC Unix seconds INTEGER; прежние ISO TEXT не
переписываются. FK включены мигратором; новые writers обязаны включать их на
каждом соединении. ID новых сущностей NOT NULL, constraints ограничивают
purpose, типы событий, время и размеры ключей. FK не заменяет серверную
авторизацию: любой SELECT/UPDATE должен scope по текущему blago_user_id.
Криптографическую валидность COSE/HTTPS/время consume нельзя доказать одним DDL.
Секретов authenticator/private keys в схеме нет; COSE содержит только public key.

Нет ALTER/DROP старых таблиц, триггеров и копирования данных, backfill не нужен.
Не вводятся identity_links/user_sessions: существующие компоненты авторитетны.
Привязки с chat fallback не переносятся и не маркируются проверенными.
Вся DDL и запись ledger выполняются через execute в BEGIN IMMEDIATE.
Перед/после — integrity_check и foreign_key_check; конфликт имён, дрейф SQL или
checksum, незнакомый ledger приводят к отказу. Повторный запуск — no-op.
При ошибке/аварии до commit SQLite откатывает всю новую схему.
Точная проверка SQL намеренно консервативна: даже эквивалентный ручной DDL
требует рассмотрения, не молчаливого принятия.

## Локальное воспроизведение и backup/restore

Миграция **не вызывается при старте приложения**, Docker или CI smoke.
Она принимает только уже существующий файл (mode=rw), с таблицей users из
Foundation. Инициализированная пустая база поддержана. Совсем пустой SQLite
отклоняется без записей: сначала на новом тестовом пути создать базу через
существующий SQLiteBlagoStore, не дублировать его DDL в миграторе.

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests
PYTHONPATH=src python -m xblago.core.identity_schema /tmp/offline-copy.sqlite3
```

Перед реальным применением (вне этого PR): остановить все writers, выполнить
SQLite online backup через существующий backup_database, проверить восстановление
на другом пути, выполнить миграцию только на offline-копии, сравнить старые
таблицы/числа строк и integrity/FK. Обычное копирование только .sqlite3 при WAL
не является безопасным backup. Нужны права файлов, шифрование резервных копий
и ограниченный доступ. Не запускать пример на production.

Откат после commit: остановить writers и восстановить проверенную полную копию
через SQLite backup API в отдельный файл, проверить и явно переключить путь.
Не удалять таблицы из живой базы и не терять новые пользовательские записи.
Пока новые writers выключены, восстанавливается только additive schema change.
В тестах восстановление подтверждается открытием независимой online-backup
копии до/после миграции и повторным запуском мигратора.

## Приватность, активность и удаление

Сбор активности выключен: таблица не заполняется ни UI, ни агентом.
05.3 включает только необходимые login/logout/link/security события с явным
объяснением. Необязательная аналитика требует отдельного consent; отказ не
лишает базового доступа. Никакого скрытого tracking между сайтами.
Предлагаемые верхние TTL: activity 30 дней, security 90 дней, consumed/expired
challenges удалить не позднее суток. Нужен отдельный протестированный cleanup:
expires_at сам по себе ничего не удаляет. Label устройства допускается только
по отдельному решению пользователя. Не копировать текст диалога в журнал.

Удаление памяти 04.2 не равно удалению аккаунта. Полное удаление требует
отзыва всех сессий/ключей, удаления ссылок, памяти и диалога, очищения новых
таблиц; экономические обязательства и законные сроки хранения разбираются
отдельно до включения такого API. Здесь FK default RESTRICT защищает от
частичного удаления, cascade не меняет экономику. Для backups нужна политика
сроков и повторного применения deletion ledger после restore; пока она не
реализована, обещать немедленное полное стирание нельзя.

LLM разрешено помогать формулировать ответы, но запрещено принимать решения
о principal, связывании, auth, consent, доступе, выдаче сессии и подтверждении
операций. Запрос «это мой второй аккаунт» не меняет идентичность. Реальные
идентификаторы, ключи, initData и сессии не отправляются внешней модели.

## Угрозы, критерии безопасности и оставшиеся ограничения

| Угроза | Критерий перед включением |
|---|---|
| Захват аккаунта/link-code phishing | Fresh proof обеих сторон, TTL, single-use, ротация сессий, подтверждение результата |
| Replay Telegram/WebAuthn | Проверка подписи/времени + атомарный consume с binding |
| Cross-user ссылки / IDOR | FK на владельца устройства, scope авторизации, негативные API-тесты |
| CSRF/XSS/session theft | HTTPS, cookie, CSP/review UI, CSRF/origin, отзыв и step-up |
| Ошибка миграции / диск / конкуренция | BEGIN IMMEDIATE, rollback, checksum/schema checks, backup/restore |
| Скрытая слежка / ложная идентификация | Allowlist без raw metadata, opt-in, TTL; никаких автоматических person merges |
| Утечка backup / LLM | Ограничение доступа/шифрование, отсутствие токенов в events и prompt |

Этот PR не исправляет ограничения HF, существующий shared invite, локальный
rate limiter, одно-процессную conversation_lock и отсутствие WebAuthn в runtime.
SQLite защищает транзакции нескольких соединений, но это не доказательство
корректности многопроцессного чата или полного account merge. Новые таблицы
не должны начать заполняться до совместимого merge/revoke/delete-контракта.
Успешные unit/интеграционные тесты не подтверждают пилотную готовность.

## Отчёт и следующие PR

Добавлены явный мигратор, семь таблиц с ledger, constraints/индексы,
русскоязычные автоматические проверки. Существующий runtime не изменён.
CI pull_request теперь также принимает базу fix/foundation-04-memory-pilot,
чтобы зависимый PR запускал tests и Docker smoke; production workflow не менялся.
Локальная проверка: 136 тестов (121 прежний + 15 новых), compileall и
git diff --check. Новые проверки охватывают пустую и заполненную Foundation
SQLite, безопасный отказ на неинициализированном файле, повтор миграции,
конкурентные миграции/Telegram-привязки/credential, повтор Telegram update,
rollback после DDL, коллизии и drift/checksum, неверные FK, изоляцию,
сохранение старой схемы/строк/памяти/истории, integrity_check и
foreign_key_check, online backup до/после и Web/Telegram/signed Mini App
с перезапуском. Все данные синтетические; HF не вызывается новыми тестами.
HTTP-тестам нужен доступ к loopback: в sandbox они могут завершиться ошибкой
bind, поэтому полный прогон выполняется с разрешением локальных сокетов.
Локально Docker отсутствует; результат Docker smoke проверяется отдельно
по GitHub Actions текущего SHA, не выводится из unit-тестов.

05.2: библиотечный WebAuthn, challenge consume, Telegram replay protection,
совместимые расширения web_sessions, список/отзыв/ротация, двухсторонний link,
полный тест миграции/merge всех новых FK, статус merged и отказ последнего
credential; review безопасности и браузерные интеграционные тесты.
05.3: прозрачные события/consent, cleanup TTL, минимальные агрегаты, export,
политика удаления и восстановления backups. Отдельно повторить живую HF
интеграцию 04.2 на стабильном провайдере перед решением о пилоте.
