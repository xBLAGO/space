# C01–C14: до / после

Исходная спецификация: audit #38 `d62b6bb…`, а baseline поведения проверен на exact `34ed8a6…`, не на HEAD поздних экспериментальных веток. PASS ниже означает только указанную локальную проверку, не живую модель.

| ID | До | После и доказательство | Предел |
|---|---|---|---|
| C01 | В ChatContext нет профиля участника | Отдельный scoped participant profile; `test_C01_profile_is_participant_not_agent`; HTTP stage `profile` | Реальное обращение модели NOT EXECUTED |
| C02 | Только 24 последних реплики; source turn0 отсутствует в prompt | Активный факт и его настоящий source ID после 35 промежуточных ходов и restart; `test_C02_old_assertion_selected_after_long_dialogue_and_restart` | PARTIAL: произвольные несохранённые темы не извлекаются из архива |
| C03 | `relevant_memory({name:…}, 'Продолжим') == {}` | Небольшой name/style core, `test_C03_name_core_on_generic_continuation` | Конфликт выбранных имён → нейтральное обращение |
| C04 | Прямая self assertion допустима | Совместимость сохранена; проверка канонической записи и source span | Не независимое подтверждение города |
| C05 | Смешанная декларация + вопрос отвергается whole-message gate | Извлекается только прямая часть; отрицательные тесты на гипотезу, отрицание и значение из вопроса | Только узкая грамматика, не общий NLP-parser |
| C06 | Чужой город не записывается владельцу | Прежние guards + новые отрицательные сценарии | Live extraction NOT EXECUTED |
| C07 | CAS исправляет память, но старое значение может оставаться в LLM history | Единственная актуальная запись; старый history исключён, source quote только нового значения | История владельца сохраняется; часть сырого контекста исключается |
| C08 | Storage veto сохраняет беседу | Поведение сохранено; `test_C08_veto_keeps_dialogue_without_fact` | История диалога подчиняется прежней отдельной retention-политике |
| C09 | Прежний G09 проходит | Повтор всего `ContextFencingTests` в `ReliableContextFencingTests`, 2–3 процесса | Гарантия в точке commit, не отзыв уже отправленного ответа |
| C10 | Crash/replay/restore защищены fencing | Повтор прежних испытаний, новый retained floor и source/pair rollback, backup до первой реплики | Все доверенные копии в одном rollback domain не независимы |
| C11 | Общий UID и базовая изоляция реализованы | Web → Bot linking → synthetic Mini App, реальные localhost HTTP cookies/CSRF; чужие source IDs не читаются | Реальный Telegram и физические WebView NOT EXECUTED |
| C12 | Нет адресуемой исходной реплики у семантического факта | Новые записи: atomic `context_turn:id`, owner/role/value/span checks; legacy/deleted/foreign → UNKNOWN | PARTIAL для legacy; автоматического backfill по похожему тексту нет |
| C13 | Детерминированные проверки 503/empty/truncation | Прежние тесты провайдера сохраняются; новые сбои не создают факт или source | Реальный HF / factual quality NOT EXECUTED |
| C14 | Нет переносимого context bundle | NOT IMPLEMENTED / NOT EXECUTED; ничего не имитируется | Отдельный federated protocol / owner proof / fresh revocations |

## Пример происхождения

В синтетическом стенде реплика «Я живу в Москве» создаёт `city=Москве`. После 35 ходов запрос «Где я живу?» выбирает активный ключ по `query_relevance`. Источник — точный ID этой пользовательской реплики и диапазон символов, содержащий только «Москве».

После «Я больше не живу в Москве, теперь живу в Казани» выбирается `city=Казани`, с ID новой реплики и фрагментом «Казани». Старое значение не передаётся из исходной цитаты или истории. После DELETE memory `city` ключ и его source отсутствуют; прежние цитаты ниже retained floor не попадают в модель. Неизвестное место жительства не заменяется догадкой.

Ссылка на источник объясняет происхождение заявления, а не доказывает его истинность. Источник может быть удалён раньше самого факта: тогда quote становится UNKNOWN. Имена, гипотезы и сведения о третьих лицах не используются для определения аутентифицированного UID.

## Приёмка живых ответов и физических устройств

Обновление после живого прогона HEAD `9fe62d6`: C08 — вето сработало,
есть замечание к связности; C12 UNKNOWN — FAIL ответа; городский C12 —
SETUP FAILED / NOT EXECUTED. Подробности и локальное исправление:
[C12_CORRECTION.md](C12_CORRECTION.md). Таблица ниже сохраняет исходную
матрицу квалификации; повтор исправленной версии и устройства не квалифицированы.

Следующие статусы отделены от локальных контрактов. **NE = NOT EXECUTED**: отсутствует фактическое доказательство, не PASS и не наблюдаемый FAIL. Порядок и рубрика: [LIVE_AND_DEVICE_ACCEPTANCE.md](LIVE_AND_DEVICE_ACCEPTANCE.md).

| ID | Локальный контракт | Живые ответы | Физический Android | Физический iPhone | Граница |
|---|---|---|---|---|---|
| C01 | PASS: scoped profile / agent persona | NE | NE | NE | Правильное обращение требует оценки ответа |
| C02 | PARTIAL: сохранённый факт после 35 ходов | NE | NE | NE | Произвольная старая тема недоступна |
| C03 | PASS: устойчивое обращение | NE | NE | NE | Конфликт имён → нейтральное обращение |
| C04 | PASS: direct self assertion | NE | NE | NE | Самозаявление не равно независимой проверке |
| C05 | PASS: ограниченная смешанная конструкция | NE | NE | NE | Нет универсального NLP-parser |
| C06 | PASS: other/hypothesis guards | NE | NE | NE | Проверять сам ответ, не только отсутствие записи |
| C07 | PASS: CAS, актуальная запись и history barrier | NE | NE | NE | Во всех трёх каналах после исправления |
| C08 | PASS: memory veto, разговор продолжается | NE | NE | NE | Вето semantic memory не стирает историю автоматически |
| C09 | PASS: 2–3 процесса / G09 | NE | NE | NE | Commit linearization; не отзыв уже отправленного |
| C10 | PASS: crash/replay/backup restore | NE | NE | NE | Потеря control plane не даёт успешного recovery |
| C11 | PASS: synthetic cross-channel identity/isolation | NE | NE | NE | Физический Telegram не равен synthetic SDK |
| C12 | PARTIAL: новые source pointers | NE | NE | NE | Legacy / удалённый source = UNKNOWN |
| C13 | PASS: mocked 503/empty/truncation | NE | NE | NE | Реальное качество / сбои провайдера не квалифицированы |
| C14 | NOT IMPLEMENTED | NE | NE | NE | Отдельная федерация; устройства ≠ независимые узлы |

Успешные тесты лабораторного live harness доказывают его safeguards и wiring с мок-транспортом, но не повышают ни одну ячейку «Живые ответы» или физических устройств. Для обновления нужны реальные ответы / артефакты, exact SHA и review.
