# Foundation 05.2 — Secure Identity Runtime

База: `8820d7852f0db6c4fc5101f9e281211ba7b6b074` (05.1, draft PR #4).
Отдельная ветка: `feat/foundation-05-secure-identity-runtime`.
Базовый Actions Tests #38 успешен: 136 тестов, compileall, Docker build/health.
Это зависимый draft: PR #3/04.2 и #4/05.1 ещё не являются принятой production
базой. После их интеграции требуется retarget/rebase и повторный CI.

## Что реализовано

Сохраняется UUID `blago_user_id` и одна SQLite для Bot, Mini App и Web.
Подтверждается управление способом доступа, не паспортная личность и не
уникальность человека. Идентификационные решения не поручаются LLM.

`integrations/identity.py` содержит детерминированный runtime. Его включение
требует явной offline-миграции 05.2. Приложение не выполняет её при старте.
Без миграции прежние Web/Telegram способы работают; новые endpoint'ы отвечают
503. Для будущего включения можно задать `XBLAGO_REQUIRE_SECURE_IDENTITY=true`:
это запрещает незаметный fallback на legacy при отсутствии runtime-миграции.
Ни окружение, ни production-файл в рамках этой работы не менялись.

### Объединение аккаунтов

Существующий `/link` и одноразовый код остаются основой. Выдача кода в runtime
требует сессии, подтверждённой не более пяти минут назад; повторная проверка
делается внутри транзакции. Отправитель Bot должен иметь Telegram sender ID;
`chat:<id>` не годится для привязки.

Новые user_devices/passkey_credentials, consent_records, activity_events,
security_signals и Telegram proof-ledger меняют владельца в той же транзакции,
что память, история и экономические ссылки. Composite device FK проверяется
отложенно при commit; credential_id остаётся уникальным. Исходный user_handle
аутентификатора сохраняется: его нельзя переписать после merge. Поэтому у
объединённого участника допустимы разные immutable handles разных ключей.
Незавершённые WebAuthn challenges и link codes обеих сторон отзываются.

Все старые сессии обоих участников отзываются; Bot сообщает о необходимости
нового входа. Токен не переносится на другого principal. Новая сессия выдаётся
только после аутентификации. Источник получает прежний статус merged.
Старый исходный profiles остаётся исторической записью, но вход через него
запрещён. Экономические суммы, правила и статусы не пересчитываются.

Два Web password-аккаунта автоматически не объединяются. Конфликт UNIQUE,
неактивный участник или неизвестная таблица с FK на source user дают отказ
409/401 с откатом всех изменений, включая код. Схема 05.1 без runtime тоже
получает перенос новых FK; отзыв сессий включается только с runtime 05.2.

### Сессии

Расширяется существующая web_sessions: public_id, created_at, last_seen_at,
authenticated_at, auth_method. Public ID предназначен для отзыва, не является
bearer token или его hash. Секретный токен остаётся только в HttpOnly cookie;
SQLite хранит SHA256. CSRF и origin-проверки сохраняются.

Idle timeout — 30 минут, absolute — семь дней от выдачи, свежесть подтверждения
для изменений — пять минут. Каждый защищённый HTTP-запрос проверяет active
статус и сроки, затем обновляет last_seen. Bot также проверяет active перед
обработкой и перед возвратом кэшированного update. Merged/blocked аккаунты не
могут войти. Ротация удаляет старый токен и меняет CSRF. Операции управления
не продлевают authenticated_at без нового доказательства доступа.

При миграции старых сессий сохраняется absolute deadline, даётся 30 минут
idle-перехода с момента миграции; authenticated_at=0/auth_method=legacy не
позволяют изменять способы доступа до повторного входа. Истёкшие сессии не
оживают. Разрешены просмотр собственных сессий, отзыв выбранной и всех других.
ID чужой сессии возвращает 404; bearer hashes/CSRF других сессий не выдаются.

### Telegram / Mini App

Сначала Mini App восстанавливает действующую сессию через `/me`: повторное
открытие не является атакой и не требует обмена initData. При необходимости
входа получает случайный 256-bit challenge (TTL пять минут); сервер ставит
отдельную HttpOnly/SameSite browser-binding cookie на десять минут.

Сервер проверяет HMAC/initData/auth_date существующим verifier. В BEGIN
IMMEDIATE атомарно проверяются binding/TTL/single-use, фиксируется digest
подписанного hash, разрешается Telegram principal и выдаётся сессия. Resolver
SQLiteBlagoStore переиспользован через connection-aware helper, без вложенной
транзакции и без нового реестра участников. Канонический proof digest не
меняется при перестановке/повторном URL-кодировании полей.

Одинаковое signed initData нельзя обменять ещё раз даже с другим challenge.
Ответ 409 — просьба использовать сессию или получить свежий запуск Mini App,
без репутационного штрафа, блокировки человека или security-обвинения.
Потерявший cookie пользователь со старым initData должен закрыть и заново
открыть Mini App для свежего Telegram proof. Клиент не делает опасного
автоматического retry с тем же proof после неизвестного результата запроса.
Сбой доставки после commit требует свежего proof/другого способа входа.

Одноразовый challenge не превращает initData в proof-of-possession: украденный
действительный proof до первого обмена остаётся bearer credential. TLS,
защита от XSS и отсутствие initData в логах обязательны. Тест конкуренции
в отдельных процессах подтверждает ровно одну успешную фиксацию одного proof.

### WebAuthn, восстановление и способы доступа

Используется [Duo Labs py_webauthn](https://duo-labs.github.io/py_webauthn/)
`webauthn==2.7.1`; собственная проверка COSE/подписи не реализуется.
Верифицируются challenge, signature, RP ID hash, точный origin, UP, UV,
userHandle и counter. Cross-origin ceremonies запрещены. Registration требует
fresh-аутентификацию существующего владельца, attestation none и discoverable
credential с UV required. Ключи и непрозрачные user handles не содержат
Telegram ID; private keys/биометрия на сервер не поступают.

`XBLAGO_PUBLIC_ORIGIN` и `XBLAGO_WEBAUTHN_RP_ID` должны быть явно согласованы;
HTTPS обязателен, HTTP разрешён только для localhost/127.0.0.1 разработки.
Настройки не выводятся из клиентских полей или LLM. Challenge + browser binding
хешируются в SQLite; TTL и consume проверяются вместе с записью credential,
counter и выдачей сессии. Провал проверки откатывает все изменения. До 20
активных ключей на участника. Незавершённые challenges ограничены глобальным
лимитом 2000 для каждого вида обмена; TTL cleanup выполняется при их выдаче.

Версия 3.0.1 исследована, но её новые зависимости требуют отсутствующий Rust
на текущем Intel Mac. Выбрана совместимая 2.7.1 с ограниченными major-версиями
cbor2/cryptography/pyOpenSSL и успешным `pip check`. Это не аудит CVE и не
обещание поддержки PQC. Перед production нужен отдельный review актуальности
зависимостей и политика обновлений. Контракт проверки основан на
[регистрации](https://duo-labs.github.io/py_webauthn/registration.html),
[аутентификации](https://duo-labs.github.io/py_webauthn/authentication.html) и
[WebAuthn W3C](https://www.w3.org/TR/webauthn-3/).

Если пароль забыт, вход через ранее добавленный Telegram или passkey позволяет
установить новый пароль существующему BLAGO ID. Повторная регистрация личности
не нужна. Чужое занятое имя не переносится; старые сессии и pending link codes
при изменении/удалении способа доступа отзываются. Для удаления способа нужен
свежий вход другим видом ранее подтверждённого средства; последний способ
удалить нельзя. Это консервативнее удаления одного из нескольких passkeys
после входа другим passkey: нужен Telegram/пароль. Telegram unlink удаляет
только выбранную подтверждённую привязку; следующее обращение этого Telegram
создаст отдельный аккаунт, что явно объясняется в UI. Полное удаление аккаунта
и ручное восстановление без любого средства входа не добавлены.

## API и интерфейс

Все пути ниже имеют префикс `/api/v1`; изменения требуют JSON и origin-check.
Защищённые mutation endpoint'ы дополнительно требуют CSRF.

| Метод | Endpoint | Назначение |
|---|---|---|
| GET | /auth/capabilities | Runtime/passkey availability, без данных участника |
| POST | /auth/telegram/challenge | Одноразовый browser-bound обмен |
| POST | /auth/telegram | Существующий endpoint, в runtime требует challenge + binding cookie |
| POST | /auth/passkeys/authenticate/options, /verify | Discoverable вход |
| POST | /auth/passkeys/register/options, /verify | Добавление ключа после fresh auth |
| GET | /auth/sessions | Только собственные действующие сессии |
| POST | /auth/sessions/revoke | public ID либо others=true |
| POST | /auth/reauthenticate | Подтверждение текущего principal паролем + ротация |
| GET, DELETE | /auth/methods | Список / отзыв собственного способа |
| POST | /auth/password | Добавление или замена пароля после fresh auth |

Прежние register/login/logout/link-code сохранены. Новый login в текущем
браузере удаляет предыдущий cookie token; WebAuthn finish также ротирует его.
UI остаётся чатовым: добавлена кнопка подтверждения ключом и сворачиваемый блок
«Доступ и устройства» в профиле. Регистрация passkey доступна по явному нажатию,
не через вывод LLM. Не добавлены fingerprinting, паспортная анкета или новый
экран регистрации. JS не передаёт auth payload в чат; все динамические списки
рендерятся textContent.

## Миграция, backup и restore

Новая явная миграция `foundation05_runtime_002` требует уже применённую 05.1
и существующую Web-схему. Только на offline-копии:

```sh
PYTHONPATH=src python -m xblago.core.identity_runtime_schema /tmp/blago-copy.sqlite3
```

В BEGIN IMMEDIATE добавляются пять колонок существующей web_sessions,
unique index public_id, таблицы telegram_exchanges, telegram_proofs,
webauthn_pending, identity_recovery_state и отдельный runtime ledger.
Ни профили, ни память, ни экономические строки не копируются/пересчитываются.
Integrity/FK проверяются до/после; повторный запуск проверяет checksum, DDL,
типы/defaults колонок и state. Коллизии/ошибки приводят к полному rollback.
Схема 05.1 и её checksum остаются совместимыми.

SQLite online backup обязателен до миграции. Откат после commit — восстановление
проверенной полной копии на отдельный путь при остановленных writers; старый
бинарный код с четырёхколоночным INSERT sessions не предназначен для работы
с расширенной схемой. Старые аккаунты и токены поддерживаются новым кодом.

Проверка восстановления копии не означает, что её можно сразу открыть сети:
старый backup способен воскресить отозванные sessions/credentials. Перед
повторным открытием offline-восстановленной базы выполнить:

```sh
PYTHONPATH=src python -m xblago.core.identity_runtime_schema \
  /tmp/restored-copy.sqlite3 --invalidate-restored-authentication
```

Процедура отзывает все сессии и challenges. Telegram exchanges приостанавливаются
на 3661 секунду, покрывая максимальную жизнь proof с часовым TTL и clock skew:
proof, обменянный после backup, мог отсутствовать в восстановленном ledger.
Другие подтверждённые способы доступны после ревизии credentials. **Отзывы
credential, удаления пользователей и новые изменения после snapshot необходимо
сверить/повторить из внешнего операционного журнала до открытия сети.** Такого
журнала этот PR не внедряет; полное безопасное disaster recovery требует
отдельной процедуры и остаётся условием production-готовности.

## Проверки и решение о готовности

Автотесты используют временные SQLite, синтетический bot token и программный
P-256 authenticator. Crypto-verifier не подменяется: тесты строят настоящие
COSE/CBOR ответы и ECDSA-подписи. Покрыты валидный вход, неверные origin/RP/
signature/UV/userHandle/crossOrigin, replay, TTL, гонки, старые аккаунты,
idle/absolute/freshness, revoke/IDOR, восстановление доступа, merge новых FK,
экономические события/память/история, rollback конфликтов, backup и перезапуск
Bot/Web/Mini App. HTTP-тест проверяет cookie/CSRF и весь Telegram→WebAuthn flow.
Все 136 прежних тестов включены в полный прогон.
Локальный итог: **166/166 тестов** (30 новых), 46.418 секунды; compileall,
git diff --check, node --check для встроенного JS и pip check успешны.

CI запускается для зависимой base-ветки 05.1. Помимо прежнего Docker build/
health/static smoke добавлен runtime smoke на отдельной временной SQLite
в одноразовом контейнере: миграция, повтор, сессия/ротация, restart и backup.
Production workflow не меняется. Локально Docker отсутствует; итог Actions
фиксируется в описании draft PR. JS проверяется node --check; browser tool
в этой сессии не предоставлен, визуальный/device acceptance не заявляется.

Техническое решение: runtime подготовлен для review и дальнейшего закрытого
тестирования после зелёного CI. **Готовность production/пилота не подтверждена.**
Остаются следующие ограничения:

1. Нет испытания с реальными аппаратными/synced passkeys, iOS/Android Telegram
   WebView и браузерной визуальной проверки. Cross-origin WebView WebAuthn
   может требовать открытия Web в системном браузере.
2. Нужны независимый security review, dependency/CVE review, TLS/proxy/cookie
   конфигурация, тесты восстановления с журналом отзывов/удалений.
3. Вход после потери всех подтверждённых средств намеренно невозможен;
   паспорт/биография/LLM не являются альтернативным доказательством.
4. Rate limiting остаётся локальным на процесс; challenges/consume/unique proofs
   защищены SQLite между процессами, но распределённый throttling не добавлен.
   Challenge caps ограничивают рост состояния, не заменяют DDoS-защиту.
5. Отзыв не отменяет уже начатый запрос. Чатовый conversation_lock остаётся
   однопроцессным; многопроцессная атомарность обменов не доказывает корректность
   нескольких независимых LLM-chat workers при merge. Для пилота — один chat
   процесс до отдельного тестирования/протокола блокировок.
6. Строгая библиотечная проверка counter может отклонить некоторые sync-сценарии;
   использовать другой ранее подтверждённый способ, без автоматического person
   merge или социальной репутации.
7. Необязательная активность не собирается. Activity TTL jobs, полное account
   deletion, privacy export и backup deletion ledger относятся к 05.3.
8. Ограничения живого HF из 04.2 не устранены и не проверялись внешними auth
   данными. Реальные идентификаторы/секреты в LLM не отправлялись.

Deployment, production SQLite, HF secrets, merge и экономические правила
не затронуты.
