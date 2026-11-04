# Foundation 05.3 — Privacy, Revocations & Recovery

База: PR #5, `8b63b8795490a11ad2b5a709b9a258033b7d6381`.
На момент аудита #5 открыт как draft; его Tests #40 успешен (166 тестов,
Docker build, identity/health/PWA smoke). Эта работа — отдельный зависимый
draft от `feat/foundation-05-secure-identity-runtime`, без merge/deployment.

## Результат и совместимость

Добавлены четыре направления: независимый журнал отзывов/удалений;
контролируемые personal export/account deletion; проверяемый TTL cleanup;
минимальная аналитика существующих записей. BLAGO ID, архитектура агента,
WebAuthn library, единая SQLite Telegram/Web/Mini App и экономические правила
сохранены. Новые пути не вызывают LLM и не отправляют наружу auth-данные.

05.3 включается только явной миграцией и привязкой внешнего журнала. Startup
не выполняет миграцию и не создаёт потерянный журнал заново. Legacy-режим без
05.3/config остаётся совместимым. Настроенный journal directory сам запрещает
fallback при восстановлении старой SQLite без privacy schema. Дополнительный
`XBLAGO_REQUIRE_PRIVACY_JOURNAL=true` закрывает такой fallback и при утрате
directory-настройки. Для будущего включения оба параметра должны быть частью
проверенной конфигурации; рабочее окружение сейчас не менялось.

## Независимый журнал: модель угроз и протокол

Журнал находится **вне восстанавливаемого файла приложения**: отдельная SQLite,
32-byte случайный HMAC key, UUID журнала и подписанная контрольная вершина.
Каталог имеет права 0700, файлы — 0600. Ключ не хранится в application SQLite,
экспорте, Git, LLM или ответе API. Symlink/unsafe permissions отклоняются.
Поддерживаются проверенные POSIX filesystem locks (Linux/macOS); legacy stores
продолжают импортироваться на Windows, но активация журнала там отклоняется.

Записи содержат тип, UTC-время, случайный event ID и domain-separated HMAC
subject/target. Нет открытых UUID участника, Telegram ID, текста факта/диалога,
пароля, session token, password hash или private key. HMAC-псевдонимы всё ещё
считаются защищаемыми данными; это не заявление об анонимности.

Последовательность защищённой операции:

1. `BEGIN IMMEDIATE` основной SQLite; проверка версии/триггеров и исходного
   journal ID/cursor/head. Применение внешних, ещё не применённых намерений.
2. Уже зафиксированные намерения сохраняются до проверки нового запроса,
   чтобы отказ авторизации не откатил восстановленное удаление.
3. Обычная операция создаёт минимальные записи в transactional outbox через
   SQL triggers. Изменение principal не поручается LLM.
4. Проверка FK до внешней записи; затем сериализованный append независимого
   журнала с `synchronous=FULL`, HMAC-chain, commit, fsync/atomic replace signed
   head и fsync каталога.
5. Только после этого очищается outbox, обновляется application cursor и
   подтверждается SQLite commit. Ответ об успехе отправляется после возврата
   из транзакции.

Две SQLite не объявляются общей атомарной транзакцией: в WAL нет общей atomic
commit для ATTACHed databases, см. [SQLite WAL](https://www.sqlite.org/wal.html).
Выбран write-ahead intent: после durable append отзыв/удаление может считаться
действующим даже при последующем сбое app commit. Следующий защищённый вход
или offline restore повторяет его. При сбое append основная транзакция
откатывается. Это намеренная потеря доступности в пользу запрета воскрешения.
Неизвестный результат запроса не означает, что отзыв можно отменить retry.

Проверяются полная цепочка и signed head. Удаление хвоста journal.sqlite3 при
сохранённой вершине, подмена payload/key, чужой journal ID, пропавший файл,
дрейф триггеров или неизвестный ownership-конфликт закрывают доступ. Журнал
никогда не инициализируется автоматически вместо утраченного.

Покрываются logout/rotation/session revoke; изменение/удаление пароля;
отзыв/удаление passkey; отзыв/смена Telegram-привязки; статусы merged/blocked/
deleted; удаление legacy и semantic memory, истории; alias-связь аккаунтов.
Withdrawal/удаление consent также оставляют запрет: при восстановлении
добавляется withdrawn recovery-tombstone после retained grant, включая
совпадение секунд. Фактическое время операции остаётся во внешнем журнале.
Необычные DELETE технических сессий тоже оставляют безопасный revoke marker.
В журнал не добавлены необязательные login/page-view/чатовые события.

Триггеры запрещают позднюю вставку/обновление данных удалённого UUID: ответ
LLM, начатый до удаления, не может заново записать память, историю или создать
экономическую запись. Все штатные writers используют guarded store transaction.
Прямой административный SQL, подмена кода, root/key compromise и искусственно
составленная из разных версий база находятся за этой границей доверия.

## Экспорт и удаление

Доступны authenticated/CSRF/origin-protected API:

| Endpoint | Действие |
|---|---|
| POST `/api/v1/privacy/export` | Собственные данные и подтверждённые aliases, fresh auth <=5 минут |
| POST `/api/v1/privacy/delete-preview` | Scope/блокеры и одноразовое подтверждение на 5 минут |
| DELETE `/api/v1/privacy/account` | Nonce + точное `confirm: "УДАЛИТЬ"`, привязка к текущей сессии |
| GET `/api/v1/privacy/analytics` | Собственные счётчики и подавленные малые общие когорты |

User ID из тела запроса не выбирает scope. Экспорт/preview принимают пустой
JSON; delete — только challenge/confirm. Чужой nonce, другой token того же
участника, старая/отозванная сессия, неверный Origin/CSRF и отсутствие fresh
auth не позволяют стирать или скачивать данные.

Экспорт использует whitelist: профиль, свой username, собственные привязки,
память со всеми статусами, историю, потребности/ресурсы/активы, собственные
экономические связи, consent/security/device metadata и метаданные сессий.
Чужие principal IDs в связанных экономических строках заменяются константой
`other_participant`; посторонние несвязанные строки не выбираются. Служебные
session/CSRF/proof/password hashes, salts, COSE keys, user handles, provider
diagnostics и filesystem paths не экспортируются. Из внешнего журнала выдаются
только собственные action/timestamp, без ключа, MAC и target tags.

Лимит онлайн-экспорта — 20 000 записей / 32 MiB выбранных данных. Превышение
даёт 413 без частичного файла; большой архив требует отдельного защищённого
offline export по тому же scope/whitelist. JSON содержит scope и признак
неразобранного legacy merge. Legacy JSON/backups вне SQLite не включаются и
не объявляются автоматически обработанными.

В профиле добавлен сворачиваемый блок приватности: скачать JSON, посмотреть
минимальные показатели, предварительно просмотреть последствия удаления и
ввести подтверждение. Auth payload и результаты export не передаются агенту.

Удаление одной транзакцией очищает профиль, память/версии, историю, update
cache, credentials/sessions/challenges/devices, привязки, consent/technical
records и не связанные с финансовыми записями собственные resources/assets/
needs/offers. Остаётся минимальный UUID/status=deleted tombstone без исходного
created_at; journal хранит необходимые псевдонимные запреты восстановления.
Это явная политика минимального удержания, не обещание удаления любого байта.

**Финансовые связи не удаляются и не переписываются молча.** Наличие payment/
fulfillment/contribution либо ссылок на принадлежащие участнику need/offer
блокирует всю операцию до отдельного retention review; никаких частичных
успешных стираний. Неизвестная FK-таблица или cross-user supersedes FK также
не приводят к удалению чужих данных. Для широкого пилота нужен согласованный
порядок обработки завершённых экономических записей.

Merge 05.3 сохраняет проверенную `privacy_aliases` связь, очищает старые delete
confirmations и сохраняет отдельные исторические UUID. Export/deletion охватывают
эти aliases. Старые merged profiles до 05.3 нельзя автоматически отождествить:
их наличие блокирует account deletion до подтверждённого ownership review.
Никаких догадок по имени, IP, тексту или LLM. Данные и неоднозначные профили
не переносятся автоматически.

Удалённая Telegram-привязка не создаёт участника сама при обычном сообщении.
Явный `/start` подтверждённого отправителя позволяет создать **новый UUID**,
без старых данных. После активации 05.3 старый JSON importer отключён, чтобы
он не вернул удалённые факты новому UUID. Перед активацией надо завершить
проверенный legacy import; файлы/копии требуют отдельного retention/erasure.

## TTL и минимальная аналитика

`cleanup` — явная проверяемая команда; она не запускается при старте/чате.
Удаляются истёкшие WebAuthn/delete/Telegram challenges, Telegram proofs только
после полного TTL, link codes, idle/absolute-expired и inactive-user sessions,
а также activity_events/security_signals по их expires_at. FK/cascades для
webauthn_pending сохраняются; повтор cleanup идемпотентен и возвращает счётчики.
Будущий cutoff отклоняется, клиент API не может передать время очистки.

Не удаляются update dedupe, диалоги, память, consent history и экономический
audit по произвольному technical TTL. Критический журнал не имеет unsafe TTL:
его tombstones нельзя удалять, пока существуют способные вернуть данные копии.
Архивация/compaction журнала потребует отдельного доказанного retirement
backup и подписанных checkpoints, а не просто «старше N дней».

«Активные участники» — distinct active-session principals плюс participants
с processed Telegram update за 24 часа; это аккаунты, не уникальные люди.
Idle/absolute deadlines используются как в runtime. Для себя показываются
сессии, виды текущего входа и число доступных password/Telegram/passkey средств.
Общие показатели и breakdown по видам входа подавляются при когорте <5.
Новых tracking records нет, текст диалога не читается. Нет IP, UA, MAC, IMEI,
fingerprinting или социальных оценок. Suppression не является differential
privacy; differencing/Sybil inference остаётся предметом review перед открытым
доступом. Никакой «анонимности» по одним агрегатам не заявляется.

## Воспроизведение на временной/offline копии

Сначала core/Web/05.1/05.2 должны быть инициализированы существующими средствами.
Создавать новый journal directory надо один раз, отдельно от app backup:

```sh
PYTHONPATH=src python -m xblago.core.privacy_maintenance init-journal \
  --journal /tmp/blago-independent-security
PYTHONPATH=src python -m xblago.core.privacy_maintenance migrate \
  --database /tmp/blago-offline-copy.sqlite3 --journal /tmp/blago-independent-security
```

Migration `foundation05_privacy_003` добавляет privacy_state/outbox/aliases/
delete_challenges/schema ledger, audit/ownership-guard triggers. Старые таблицы
не перестраиваются и данные не переносятся. Проверяются integrity/FK, точная
DDL/checksum и journal binding. Повтор — no-op, ошибки DDL откатываются.

Будущее окружение защищённого приложения должно получать:

```sh
export XBLAGO_SECURITY_JOURNAL_DIR=/tmp/blago-independent-security
export XBLAGO_REQUIRE_PRIVACY_JOURNAL=true
```

Пример cleanup копии:

```sh
PYTHONPATH=src python -m xblago.core.privacy_maintenance cleanup \
  --database /tmp/blago-offline-copy.sqlite3 --journal /tmp/blago-independent-security
```

Пример восстановления **на другой путь при остановленных writers**:

```sh
PYTHONPATH=src python -m xblago.core.privacy_maintenance restore \
  --database /tmp/restored-copy.sqlite3 --journal /tmp/blago-independent-security
```

Restore поддерживает и копию до 05.3 с Foundation 05.1/05.2. Привязывает исходный
независимый журнал, воспроизводит запреты, инвалидирует все bearer sessions,
pending challenges/link codes и сохраняет карантин Telegram 3661s из 05.2.
После snapshot некоторые обменянные proofs могли отсутствовать; это остаётся
необходимой границей replay. Повторное приложение intent идемпотентно.
Перед возвратом restore также запускает TTL cleanup просроченных технических
записей. Deleted tombstone нельзя реактивировать или удалить обычным writer.
SQLite integrity_check/foreign_key_check и поведение auth должны проверяться
до открытия сети. Чужой/потерянный/укороченный журнал не заменяется новым.

Используется [SQLite online backup](https://www.sqlite.org/backup.html), а не
копирование одного .sqlite3 при живом WAL. Backup приложения не включает
journal/key/head; их резервирование и свежая контрольная вершина должны иметь
отдельный проверенный lifecycle/fault domain. Нельзя одновременно открывать
live и restored копию одного identity namespace для обычной работы.

## Security review

Выполнен локальный review протокола и границ доступа с негативными тестами.
Ориентиры: [OWASP Logging](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
для разделения журналов/секретов и защиты доступа; SQLite WAL/backup для crash
и restore semantics. Это внутренний review; независимый аудит ещё необходим.

| Угроза / найденный дефект | Исправление и доказательство |
|---|---|
| Restore возвращает logout/revoked credentials | Внешние HMAC intents; тесты session/password/passkey replay |
| Memory/account deletion исчезает вместе с app DB | Tombstones, metadata-free targets, replay на старой копии |
| Сбой между двумя durable stores | Write-ahead protocol; simulated fsync failure и реальный `os._exit` после journal append |
| Усечённый/подменённый журнал или ключ | HMAC chain + signed head + cursor; негативные проверки, fail closed |
| Snapshot без privacy schema включает legacy auth | Directory/require flag запрещают fallback; явный restore migration |
| IDOR/CSRF/nonce другого token | Server-derived scope, fresh auth, session-bound confirmation, HTTP tests |
| In-flight LLM возвращает стёртые данные | SQL ownership guards запрещают запись deleted UUID |
| Merge оставляет исторический профиль | Записанные aliases, purge/export family; неоднозначный legacy merge блокируется |
| `/start` импортирует старый JSON в новый UUID | Legacy import отключён после privacy activation |
| TTL разрушает update idempotency/valid proof | Явный allowlist, реальные сроки, дедупликация сохраняется |
| Малые аналитические группы | Suppression <5, никаких идентификаторов; отсутствие tracking writes |

**Выявлено ограничение SQLite окружения.** Локальная sqlite3 сообщает 3.49.1.
Официальная [документация SQLite WAL-reset](https://www.sqlite.org/wal.html#walresetbug)
описывает rare corruption race в WAL до 3.51.2 включительно; исправление —
3.51.3+ либо подтверждённый backport (включая 3.44.6/3.50.7). Версию/наличие
vendor backport нужно доказать для deployment runtime, не выводить из зелёных
тестов. CI дополнительно печатает SQLite version. Production не обновлялся.
Для пилота требуется runtime с подтверждённым исправлением.

Оставшиеся границы:

1. Одновременный откат **app DB, журнала, ключа и всех независимых head copies**
   к одной старой точке локально не обнаруживается. Нужен независимый свежий
   checkpoint/immutable backup, key custody и проверенное disaster recovery.
2. Компрометация service/root/key не покрыта HMAC. Журнал — необходимые
   revoke/delete intents, не полная репликация нормальных post-snapshot writes,
   новых паролей, новых аккаунтов или экономических транзакций. Потерянные
   новые credentials не восстанавливаются через биографию/LLM.
3. Исторические backup/WAL/free pages, JSON, скачанные экспорты и внешние копии
   содержат старые байты до их отдельного retirement. Logical replay не равно
   физическому или криптографическому стиранию; нужны encryption at rest,
   retention schedule и проверенный порядок утилизации.
4. Economic/legacy ownership conflicts дают безопасный отказ и требуют
   отдельной политики. Никакой готовности полного self-service deletion для
   участников с такими связями не заявляется.
5. Full-chain verification и replay сканируют данные; большой журнал и большие
   экспорты требуют нагрузочных испытаний/checkpoint design. POSIX/local disk
   необходимы; multi-host/NFS не поддержаны. Два процесса tested, несколько
   независимых LLM workers пока не являются поддержанным пилотным режимом.
6. Real passkeys/WebView/visual acceptance, независимый security/dependency
   review и стабильный живой HF прогон из предыдущих Foundation остаются.

## Проверки и техническое решение

Сохранены все 166 прежних тестов. Добавлены 40 негативных/интеграционных тестов:
схема/idempotency/DDL rollback, journal binding/chain rollback/tamper/loss,
реальные отдельные процессы и abrupt exit, restore до/после 05.3,
credential/session/memory/history/account revocations, alias merge, stale-write
guard, export isolation/secret exclusion/limits, cross-user FK, retention
blockers, nonce/CSRF/Origin HTTP flow, TTL и минимальные агрегаты.
Локальный итог: **206/206**, 92.875s. Compileall, node --check, pip check и
git diff --check успешны. Новые тесты используют только синтетические данные.

CI расширен для stacked base PR #5, сохраняет прежние Docker smoke и добавляет
Privacy Recovery smoke на одноразовой SQLite/control plane. Ни одна проверка
не использует production данные или настоящие идентификаторы/секреты. Runtime
миграции не выполняются при обычном запуске Docker. JS проверяется node --check;
визуальные/device испытания не заявляются. Локально Docker отсутствует.
Точные результаты текущего SHA и Actions фиксируются в описании draft PR.

Техническое решение: подготовлено к review и закрытым испытаниям после CI.
**Пилотная готовность не подтверждена.** До пилота обязательны исправленный
SQLite runtime, независимый journal/anchor recovery drill, политика экономических
и legacy данных, real-device acceptance и независимый security review.
Deployment, merge, production SQLite и рабочие секреты не изменялись.
