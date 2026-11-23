# Living Deeds 02.1 — локальное согласие и три роли

База: exact PR #43 `6cce244f5b7cada33b00c08f7e6419acafc439a8`. Изолированная ветка `feat/living-deeds-02-1`. Стенд не подключён к production Web App, Telegram, Hugging Face, LLM или внешней доставке. На всех HTML-экранах плашка `LOCAL SANDBOX · SYNTHETIC DATA · NO EXTERNAL DELIVERY`.

Роли здесь: **A — автор HelpRequest; B — посредник и владелец частной памяти/NOTE; C — приглашённый получатель**. Это явное переименование ролей примера #43, а не новая идентичность.

## Что изменено

- Только явный CLI `tools.living_consent_lab serve --enable-local-sandbox` подключает отдельный HTTP handler. Bind — литерал `127.0.0.1`, проверяются peer, точный Host/Origin, session и CSRF; CORS нет. Существующие production routes не меняются. Импорт и `serve` не применяют миграции.
- `AgentLivingDeeds.view()` теперь возвращает нейтральный INVITATION `{id, kind, label}` без task/referrer/topic/status. Legacy `LivingDeeds` профиль #42 сам по себе не является новой границей доставки и этим HTTP-мостом не публикуется.
- C `/api/state` содержит только нейтральные delivery cards. Первое поле `task` передаётся только успешным `/api/action open` → `LocalPullAdapter.deliver()`. Отзыв до открытия даёт нейтральное «Приглашение недоступно», без текста.
- A видит собственную просьбу, своё разрешение и канонические предложения Help. Ни список получателей, ни состояние открытия/отказа не входят в его DTO. Кнопка отзыва зависит от собственного разрешения A, а не состояния C.
- B сам разрешает MATCH_PRIVATE и отдельной кнопкой запускает `candidates`. Поиск не выполняется из GET, polling или публикации просьбы. Только B получает результат с private marker. Превью INVITE_ONCE показывает адресата, точный согласованный текст, referrer и срок, явно исключая заметку. Смена адресата отзывает ephemeral preview nonce; новая версия просьбы инвалидирует его на сервере и экране.
- Одна видимая кнопка B подтверждает показанную последовательность BRIDGE → адресация → INVITE_ONCE → локальная очередь. Каждая существующая команда сохраняет собственный durable commit. При частичном сбое возможен приватный подготовительный BRIDGE без delivery; он не раскрывает task и не считается отправкой. Автоматического достраивания/ретрая нет, требуется новое человеческое превью. `queued=true` возвращается только после последнего commit. Journal/witness guards не ослаблялись ради искусственной общей транзакции.

Новых таблиц или миграций нет. Проверены VERSION/CHECKSUM/DDL 11 модулей: [MIGRATIONS.json](living-deeds021/MIGRATIONS.json). Help/Asset/Identity/UEL остаются каноническими; `HelpService` и его lifecycle не изменялись. Согласия и отказы проходят существующие доменные команды, а не отдельную UI базу.

## Проверяемый разрыв Help

В этой базе **положительная ветка останавливается после добровольного нажатия C «Предложить помощь»**:

1. `HelpService.prepare(..., action='offer')`, `src/xblago/domain/help_service.py:171`, сначала вызывает `_row(db, uid, rid)`.
2. `_row`, строки 104–110, требует owner либо `help_audience.member_id == uid` при не-DRAFT состоянии; специальное право previous_helper действует только для WITHDRAWN.
3. C не owner, не включён в audience и ещё не помощник. Поэтому `_row` возвращает 404 **до** `_check(..., 'offer')`.

Контактное согласие не меняет `help_audience`. Стенд намеренно не добавляет C, не перепубликует просьбу с C и не имитирует offer/accept/complete. На экране C и в JSON — `HELP_AUDIENCE_REQUIRED`. У A по-прежнему нет предложения и имени C. Принятие A и два подтверждения результата — **NOT EXECUTED / BLOCKED**, а не PASS.

Старый положительный тест #43 с ручным расширением audience остаётся regression, но не является доказательством нового acceptance flow. Для продолжения нужен отдельный проверяемый контракт добровольного предложения по приглашению; нельзя превращать возможность прочесть forwarding text в общий доступ ко всем данным HelpRequest.

## Воспроизведение на Mac

Новая копия: `/tmp/blago-living021`. Python — `/private/tmp/blago-pr41-live-venv/bin/python` (3.12.10 x86_64), SQLite — 3.54.1, безопасный драйвер в `/private/tmp/blago-pr41-live-sqlite`. Playwright 1.64.1 находится в `/tmp/blago-context-browser-deps`. Эти пути относятся к данному Mac, а не к переносимой установке.

```sh
cd /tmp/blago-living021
export PYTHONPATH=/private/tmp/blago-pr41-live-sqlite:/tmp/blago-context-browser-deps:.:src
/usr/bin/arch -x86_64 /private/tmp/blago-pr41-live-venv/bin/python -m xblago.core.sqlite_safety --require-safe
/usr/bin/arch -x86_64 /private/tmp/blago-pr41-live-venv/bin/python -m tools.living_consent_lab init --new-root /tmp/ld021-manual-new
/usr/bin/arch -x86_64 /private/tmp/blago-pr41-live-venv/bin/python -m tools.living_consent_lab serve --root /tmp/ld021-manual-new --bind 127.0.0.1 --port 17861 --enable-local-sandbox
```

Открыть `http://127.0.0.1:17861` в трёх независимых browser profiles/contexts. Логины: `ld021_a`, `ld021_b`, `ld021_c`; общий **публичный синтетический** пароль `synthetic-living021-only`. Это не модель допуска к реальным данным. Приватная заметка B содержит контрольный маркер `LD021_PRIVATE_B_MARKER_Q7M4`. До старта seed создаёт только аккаунты, память/NOTE B и три исходные опубликованные просьбы A с audience B. Ни одного пользовательского мандата, forwarding permission, BRIDGE или delivery до браузерных действий нет.

Автоматизированное воспроизведение теми же обычными кнопками:

```sh
PYTHONPATH=/private/tmp/blago-pr41-live-sqlite:/tmp/blago-context-browser-deps:.:src /usr/bin/arch -x86_64 /private/tmp/blago-pr41-live-venv/bin/python -m tools.living_consent_browser_check --new-root /tmp/ld021-check-new --output /tmp/ld021-check-evidence-new
```

Каждый `--new-root` и `--output` должен быть новым. Init не принимает существующее хранилище. Для restart использовать только `serve` с прежним `--root`, **без** повторного init. `Ctrl+C` останавливает HTTP и его локальный custodian. Сессия требует повторного входа после истечения штатного fresh-access срока; стенд этот срок не расширяет.

## Evidence и ограничения

Полная таблица с пятью колонками, дампы ответов A, network indexes, снимки трёх экранов и restart evidence — в отчёте `docs/living-deeds021/E2E_REPORT.md`, создаваемом после проверенного прогона. Machine-readable результаты — `RESULTS.json` и artifacts CI. Точный runtime SHA, patch от базы и его SHA256 записывает browser runner; исходный код и API payload не подменяются во время прогона.

Браузерные действия не используют API writes, injected cookies, `page.evaluate` с бизнес-операциями или route fulfillment. Вне браузера выполняются только начальный seed, запуск/restart сервера, уничтожение browser contexts для очистки и read-only storage inspection. Сетевые JSON сканируются до редактирования CSRF/nonce для публикации; B marker запрещён в ответах/экранах A/C. В A сравниваются исходные SHA256 тел state/view, а не только очищенные JSON.

Loopback не защищает от полностью скомпрометированного Mac или оператора с ключами. Локальные SQL/journal/custodian не доказывают независимость операторов. SQLite at-rest encryption, внешняя доставка, реальные устройства, живой LLM и общий допуск к пилоту не проверены. Старые release gates не повышаются.

**GO — только локальная синтетическая проверка интерфейса и privacy boundary после фактического PASS evidence. NO-GO — полный целевой цикл Help до исправления контракта, реальные люди, внешняя доставка и production.** Merge/deploy не выполняются.
