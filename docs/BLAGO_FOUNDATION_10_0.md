# Foundation 10.0 — Release Guard & Baseline Provenance

Статус: Draft. Exact base `main`: `3c218dc5757879c26b3aec520af3636b2d702715`.
Основание аудита: PR #38 `d62b6bb24dc5604ccfa6b3da453879c264040d6a`.
Ветка: `feat/foundation-10-release-guard`. Ни merge, ни deployment не выполнялись.

## Что изменено

1. Старый `.github/workflows/huggingface-space.yml` заменён ручной **подготовкой
   source candidate**, без HF client, upload, production secrets или publisher.
   Push, PR, merge, schedule, workflow_run и repository_dispatch её не запускают.
2. Новый `ci.yml`: read-only token, pinned action SHA, отсутствие environment и
   секретов, исходные main-тесты и release negatives, детерминированный source
   artifact. Артефакт PR считается недоверенным и непригоден для подготовки выпуска.
3. `tools/release_guard.py`: SHA/artifact/config/provenance validation и атомарное
   резервирование metadata tag `release-preparations/<exact-source-SHA>`.
   Это **не публикация**, не release approval и не применение миграций.
4. `release/baseline.json` фиксирует runtime files SHA256, dependency constraints,
   SQL source fingerprint/table names и известные storage риски. `config.json`
   фиксирует разрешённый workflow/ref/environment и отсутствие publisher.
5. `release/hf-observed.json` отдельно фиксирует публичный HF repo/running SHA
   `b95c630eb69ec4a505e2008a15a4417b12a74553`. Это metadata, не аттестация образа,
   production SQLite, данных или полномочий.

Файлы workflows записаны в JSON-синтаксисе, являющемся подмножеством YAML.
Это позволяет проверять точную структуру stdlib JSON без нового runtime dependency.
Все action references закреплены SHA; checkout не сохраняет credentials.

## Точная область допуска

**GO:** изолированные тесты, построение source artifact, review этого Draft PR.
**NO-GO deployment:** publisher технически отсутствует. Ни зелёный CI, ни metadata
reservation tag, ни переменная enabled не дают разрешения публиковать приложение.
**Merge: NO-GO до ручной bootstrap-процедуры владельца**; после её документального
подтверждения возможен отдельный review для merge без deployment.
**Следующая интеграция: условный GO** после безопасного включения guard и нового
review baseline/schema/rollback; никакого автоматического переноса Foundation.

## Почему runtime не назван безопасным

Сохранён каждый байт `src/`, Dockerfile, pyproject и существующего runtime README.
Функциональность main не менялась. Но `SQLiteBlagoStore.__init__` вызывает
`_ensure_schema`; Bot startup создаёт store и может импортировать legacy JSON.
Обнаружен **startup DDL**, а не доказано его отсутствие. Обычные module imports
проверены без DB connections. Prepare/build вообще не импортируют приложение и
не открывают SQLite. Схемы не создаются на production или в release job.

Baseline не содержит полноценного versioned schema ledger: таблица `migrations`
учитывает legacy imports. Зависимости ограничены диапазонами, Docker base не
закреплён digest, реальная библиотека SQLite неизвестна. Source bundle воспроизводим;
runtime image **не аттестован**. Изменение runtime-файлов требует явного нового
baseline, а безопасный запуск/миграция — отдельного PR. Guard не переписывает
контрольные суммы старых Foundation и не отключает их гарантии.

## Ручная подготовка: условия

Workflow работает только при workflow_dispatch из refs/heads/main и явном
`BLAGO_RELEASE_PREPARATION_ENABLED=true` (по умолчанию выключен). Нужны exact SHA
текущего main, успешный push/main CI run, artifact ID, точный ZIP digest и config
SHA256. Пользователь вводит PREPARE_SOURCE_ONLY. Actor должен иметь admin/maintain;
неизвестные права, API errors и повтор run_attempt>1 блокируют подготовку.

Проверяются repository, workflow ref/path, run source/ref/conclusion, artifact
name/run/expiry/digest, весь скачанный ZIP и каждый source blob против GitHub Git
object tree. Архив не исполняется и не извлекается на файловую систему. Запрещены
неожиданные файлы, symlinks, path traversal, большой unpack, ложная runtime
аттестация и подмена config/baseline. Token удаляется из cross-host redirect.

Tag-ref создаётся атомарным remote create, без force/update/delete. Повтор и
конфликт завершаются отказом. Потеря ответа после создания — UNKNOWN, а не
повторный успех. Аннотированный tag содержит SHA, source tree, artifact/config
hashes, actor/run и явный `production_authorized:false`. Он резервирует именно
source candidate; новая commit-версия с теми же runtime bytes остаётся отдельной
явно запрошенной подготовкой, не доказательством нового/повторного deployment.
Никаких таких tags этим заданием не создавалось: remote writes тестируются fake API.

## Проверка и пределы доказательств

[Evidence](release100/evidence.json) и [test results](release100/test-results.json)
разделяют CODE, local synthetic tests, CI и UNKNOWN. Исходные 27 main tests
сохранены; добавлены 49 release tests. Нет проверки реального deployment,
GitHub Environment approval, отказа реальной защищённой ветке или live tag CAS:
они NOT EXECUTED. Негативные ответы GitHub смоделированы локально.

Публичный read rulesets вернул пустой список; classic branch protection API —403.
Это не доказательство отсутствия classic protection. Секреты не читались и не
изменялись. Отсутствие production token на repo/org уровне **не подтверждено**.
Пока владелец не выполнит checklist, нельзя обещать, что произвольно изменённый
workflow в другой ветке не сможет обратиться к старому repository-wide secret.

[Bootstrap и ручные настройки](release100/BOOTSTRAP.md),
[threat model](release100/THREAT_MODEL.md), [операции/rollback](release100/RUNBOOK.md).

## Локальный результат

**76/76 PASS**:27 исходных тестов main и49 release tests, без пропусков. Source/package determinism, negative provenance, ref/role/CAS и storage import checks выполнены на синтетических fixtures. Проверка реальных environment restrictions и реального remote tag creation не выполнялась.
