# BLAGO — Agent Context Audit

Дата проверки: 18 ноября 2026. Статус: Draft, audit / architecture / planning only.
База этой документации: `3c218dc5757879c26b3aec520af3636b2d702715` (`main`). Проверенный исследовательский runtime: PR #37 `4706a60e7ec675ed1ae16b9c9cd551cde291cfd3`. Данные — только синтетические. Код, схемы, production и release gates не изменены.

Доказательства различаются: **CODE** — прочитанный код, **LOCAL** — исполненный в этом аудите тест, **CI** — проверенный существующий запуск Actions, **DESIGN** — предложение, **UNKNOWN / NOT EXECUTED** — доказательства отсутствуют. Проверка выполнена повторным анализом исходников и GitHub; независимый внешний аудитор и независимые операторы не участвовали.

## Проверенный путь сообщения

| Шаг | Реализация на exact #37 | Вывод |
|---|---|---|
| Web/Mini App auth | [integrations/web.py](https://github.com/xBLAGO/space/blob/4706a60e7ec675ed1ae16b9c9cd551cde291cfd3/src/xblago/integrations/web.py#L554), IdentityRuntime в `integrations/identity.py` | HTTP middleware проверяет session/CSRF; server-derived uid. Внутренний `chat(uid, ..., token=None)` сам по себе не является публичным доказательством авторизации |
| Telegram identity | `integrations/telegram.py`: `_telegram_identity_from_message`, channel_links, private `/link`, update receipt | Общая личность появляется через подтверждённую привязку, не из имени/username. Новый несвязанный вход — другой локальный участник |
| Snapshot | [core/context_fencing.py](https://github.com/xBLAGO/space/blob/4706a60e7ec675ed1ae16b9c9cd551cde291cfd3/src/xblago/core/context_fencing.py#L51) | Авторизация, revision/epoch, PREPARED generation, memory snapshot и recent_history(uid,24) |
| Agent construction | `context_fencing.py:90`, `web.py:554`, Telegram fallback | Объект агента может пересоздаваться, SQLite сохраняет данные; process-local object не является памятью между workers |
| Prompt | [personal_assistant.py:276](https://github.com/xBLAGO/space/blob/4706a60e7ec675ed1ae16b9c9cd551cde291cfd3/src/xblago/agents/personal_assistant.py#L276), [llm.py:174](https://github.com/xBLAGO/space/blob/4706a60e7ec675ed1ae16b9c9cd551cde291cfd3/src/xblago/core/llm.py#L174) | agent profile, текущий текст, history, отобранная память; модели не даётся полный профиль человека |
| Generation | `core/llm.py`: OpenAICompatibleProvider | Основной запрос; максимум одно ограниченное продолжение незаконченного текста. Для части фактов ещё один extraction request. HTTP503/timeout не запускает бесконечный retry |
| Extraction | [semantic_memory.py:24](https://github.com/xBLAGO/space/blob/4706a60e7ec675ed1ae16b9c9cd551cde291cfd3/src/xblago/core/semantic_memory.py#L24), memory_policy | Только grounded self assertions; extraction получает текущую реплику, пустую history/memory, хотя вызывающий объект содержит их |
| Commit | `context_fencing.py:95`, [context_schema.py:24](https://github.com/xBLAGO/space/blob/4706a60e7ec675ed1ae16b9c9cd551cde291cfd3/src/xblago/core/context_schema.py#L24) | Атомарная сверка версии/полномочий и запись буферизованных фактов, пары реплик, Bot receipt; при конфликте safe rejection без автоповтора |

Во время LLM нет SQLite write transaction. Все INSERT/UPDATE/DELETE dialogue_turns, memory, profiles, релевантных связок и полномочий инвалидируют in-flight поколение. Поэтому две параллельные генерации не имеют права обе записать ответы на один старый контекст. Это желаемая сериализация, а не причина убирать G09.

## Фактические причины ошибок и классификация

| ID / priority | Наблюдение и доказательство | Вероятное проявление | Безопасное исправление, пока DESIGN |
|---|---|---|---|
| CTX-01 P1 profile split | Web хранит display_name/language/timezone (`web.py:518`), но ChatContext не содержит participant profile; agent.profile — профиль самого агента | Неверное обращение, повторный вопрос о имени даже после заполнения профиля | Версионированный минимальный participant-context с происхождением и пользовательским preferred address; отличать display_name от legal identity |
| CTX-02 P1 bounded history | `conversation.model_history`:24 turns,24000 chars; Web/G09 загружают24; history API показывает100 | Старый разговор сохранён на диске, но отсутствует в prompt | Privacy-scoped retrieval или исправляемое summary с ссылками на источники и delete/fencing dependencies; budget заранее измерять |
| CTX-03 P1 relevance | `memory.relevant_memory` positive lexical score, limit12 | «Продолжим» не извлекает name; косвенные отсылки теряют релевантные факты | Детерминированный небольшой core-context и bounded retrieval; не передавать всю базу модели |
| CTX-04 P1 provenance | SemanticMemoryRecord содержит source/confidence/status, но не turn_id/evidence span; grounding проверяется и теряется при преобразовании | Нельзя надёжно объяснить «откуда ты это знаешь?» и отозвать производные утверждения по источнику | Additive provenance контракт отдельно: source turn, asserted/derived/confirmed_by, timestamps; privacy-safe объяснение только своему участнику |
| CTX-05 P1 truth semantics | `semantic_memory.py`: need candidate, другие допустимые self assertions confirmed; confidence — оценка extraction | confirmed ошибочно понимают как независимую проверку | Разделить self-asserted, extraction confidence, owner approval, independent attestation; не повышать доверие из числа повторений |
| CTX-06 P2 conservative extraction | memory_policy отвергает вопросы/цитаты/гипотезы/ряд отрицаний в целой фразе; простая local correction отдельна | Полезный факт в сложной реплике не запоминается, повторный вопрос позднее | Только после тестов sentence-level assertion scope и отрицаний; сохранить veto/third-party/sensitive guards |
| CTX-07 P1 biography | Нет отдельного подтверждённого autobiography source в ChatContext | Впечатление персонального знания без доступного источника | Биография — управляемая приватная проекция разрешённых фактов, без автоматической публикации в ISTINA |
| CTX-08 P0 federation | Переносимая история key/identity/context отсутствует в интеграционной линии | Копия локального uid/backup принимается за прежнюю authority | До Phase4 запрет переносимой positive authority; новый import только с owner proof и свежими retained revocations |
| CTX-09 P1 provider | Естественность и factual quality зависят от фактической модели, prompt+short window | Повторные вопросы, ложная память, неоднозначность | Отдельный разрешённый test-token/model, фиксированный набор диалогов и human rubric; не считать scripted provider тестом реальной модели |

Грамматические пол/обращение не следует выводить из одного сообщения или имени. Контекстное резюме не должно становиться authority или personality score. Отсутствие факта в prompt должно означать «не знаю», а не право реконструировать биографию.

## Подтверждённый факт, мнение и сведения о третьих лицах

Текущее ограничение — grounded self assertion, допустимая category и confidence0.8–1.0, sensitive filter. Это хорошая защита от части hallucinated extraction; она не доказывает реальность факта. Намерение NEED сохраняется только как candidate и не создаёт HelpRequest. Третьи лица/цитаты/гипотезы в проверенных негативных примерах отклоняются. Универсальной модели мнений, нескольких субъектов, независимых подтверждений и оспаривания в личной памяти нет.

Коррекция preference/city использует family keys, CAS и историю; natural forget удаляет подходящие версии и при неоднозначности уточняет. Доступные локальные команды полезны, но тестовые русские шаблоны не доказывают понимание всех формулировок реальной моделью. Защита от prompt injection в основном состоит из разграничения данных/инструкций и серверных правил; никакой модельный вывод не должен разрешать публикацию или выполнение.

## Воспроизводимые сценарии

Текущий probe [reproduce_context.py](master_audit/reproduce_context.py) читает модули exact #37, ничего не пишет в runtime. Результат [context_probe.json](master_audit/context_probe.json). Для запуска нужен отдельный checkout `4706a60e7ec675ed1ae16b9c9cd551cde291cfd3`, затем `PYTHONPATH=/path/to/checkout/src python docs/master_audit/reproduce_context.py` из этого документационного checkout.

| Scenario | Проверка / expected | Actual / доказательство |
|---|---|---|
| C01 | Web profile name доступно как participant context | GAP: поле отсутствует; CODE web.py:518/554 и ChatContext fields |
| C02 | Старый turn0 после30 turns доступен без retrieval | GAP: только24, первый synthetic turn6; LOCAL probe |
| C03 | «Продолжим» извлекает устойчивое обращение | GAP: name selector returns empty; LOCAL probe |
| C04 | «Я живу в Казани» разрешён для extraction | PASS eligibility; probe. Это не live extraction |
| C05 | «Я живу в Казани, что здесь посмотреть?» сохраняет декларативную часть | GAP: whole-message eligibility false; probe |
| C06 | «Мой друг живёт в Казани» не присваивается владельцу | PASS eligibility rejection; probe + memory negative tests |
| C07 | Исправление/противоречие не создаёт два активных факта | Existing memory_pilot correction/CAS tests; повторный запуск в local_checks |
| C08 | Вето запоминания сохраняет разговор | Existing memory_pilot veto/provider tests; no live model claim |
| C09 | Два процесса: delete во времяLLM не коммитит stale pair | Existing context_fencing multiprocess tests; локально повторены |
| C10 | Crash/replay/old backup не возвращает ticket | context_fencing restart/restore/Telegram receipt tests |
| C11 | Web/Bot linked identity изолирована от чужой памяти | memory_pilot linking/isolation + fencing channels tests |
| C12 | Источник каждого semantic fact можно адресовать исходной репликой | GAP: source_turn_id отсутствует; probe |
| C13 | HF503/empty/truncation не пишет новые выдуманные факты | deterministic provider unit tests/CI; live provider NOT EXECUTED |
| C14 | Перенос на независимый узел сохраняет контекст и отзыв | NOT EXECUTED / реализации переносимого context bundle нет |

Повторяемые команды (synthetic only, в checkout #37):

```sh
PYTHONPATH=/tmp/blago-sqlite-driver-3541:.:src /tmp/blago-uel03-venv/bin/python -m unittest tests.test_context_fencing tests.test_memory_pilot tests.test_semantic_memory -v
PYTHONPATH=/tmp/blago-sqlite-driver-3541:.:src /tmp/blago-uel03-venv/bin/python -m unittest discover -s tests -v
```

Пути выше описывают фактическую локальную среду, не требование использовать неизвестный бинарник. На чистом host собрать patched SQLite согласно исходной CI-конфигурации, установить declared package dependencies в disposable venv и записать фактическую SQLite/version/checksum. Для HTTP-тестов требуется разрешённый localhost bind. Первичный sandbox-run получил EPERM, затем выполнен разрешённый повтор; результаты не скрыты. Разрешённый повтор:60/60 PASS,44.514s; полный runtime regression773/773 PASS,1283.643s.

## План исправлений и приёмка

1. Context Contract PR: participant display preference + ссылки происхождения, правила unknown/self-asserted, без новых прав агента; replay/redaction/migration tests.
2. Scoped Context Retrieval PR: ограниченный отбор, summary из разрешённых актуальных источников, инвалидация delete/retention и G09 version; отложенная обработка conflict только по новому действию пользователя.
3. Multichannel continuity PR: link/unlink/merge, два устройства/workers, реальный restart и backup fixture, запрет profile of another account.
4. Provider qualification: синтетические многошаговые диалоги и живой approved provider, token/latency/cost budget, human evaluation связности/естественности/завершённости; NOT EXECUTED в этом аудите.

Критерии: ноль cross-user disclosure/false save claims; каждый существенный personal claim имеет проверяемый приватный источник или явный UNKNOWN; delete не возвращается через summary/retrieval; конфликты не пишут половину пары. ISTINA получает только отдельно подтверждённые владельцем утверждения с конкретной аудиторией и жизненным циклом; из памяти автоматического публичного экспорта нет.
