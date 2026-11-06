# Help 01.1 — воспроизводимые изолированные проверки

Только новая synthetic SQLite и тестовые ключи. Не выполнять команды на production paths. Source candidate: `d2294c1b1c7b082c6dfcfe1eb788bb6e8d12285b`; старые source pins и evidence в `../help01/` не заменяются.

## Local

Python 3.12, dependencies из `requirements-integration.lock`, verified patched SQLite по существующему `sqlite_safety`. В этой сессии использованы `/tmp/blago08-venv/bin/python` и `PYTHONPATH=/tmp/blago-sqlite-driver-3541:.:src`; это локальный driver, не замена system/production SQLite. На Linux использовать `tools/build_sqlite.py` и отдельный `LD_LIBRARY_PATH`/`LD_PRELOAD` как в CI.

```sh
PYTHONPATH=.:src python -m xblago.core.sqlite_safety --require-safe
PYTHONPATH=.:src python -m tools.freeze_integration
PYTHONPATH=.:src python -m unittest discover -s tests -v
PYTHONPATH=.:src python -m unittest tests.test_help_readiness -v
PYTHONPATH=.:src python -m tools.help_browser_check --chrome /path/to/chrome --output /tmp/help011-desktop
PYTHONPATH=.:src python -m tools.help_mobile_check --chrome /path/to/chrome --output /tmp/help011-mobile
PYTHONPATH=.:src python -m tools.help_worker_probe --output /tmp/help011-worker.json
```

Worker probe выполняет настоящий двухпроцессный эксперимент. Поле `status: FAIL` — обнаруженный blocker, не ошибка оформления evidence. Не превращать его в PASS на основании exit code 0. Chrome mobile использует синтетический SDK/HMAC и не является physical Telegram testing. В headless CI может понадобиться sandbox configuration среды; не запускать такие команды на реальном браузерном профиле.

Новый постоянный local lab:

```sh
PYTHONPATH=.:src python -m tools.help_readiness_lab init --new-root /tmp/blago-help011-new
PYTHONPATH=.:src python -m tools.help_readiness_lab inspect --root /tmp/blago-help011-new
PYTHONPATH=.:src python -m tools.help_readiness_lab serve --root /tmp/blago-help011-new
```

Каждый init требует отсутствующего root. Web URL `http://localhost:17860/app`. Приглашение **только synthetic**: `synthetic-help011-only`. Создавать только искусственные accounts. Команды не запускают bot и не подключают HF. Сервер и браузер работают с прежними auth/CSRF/fresh-auth controls. После окончания stop process; synthetic volumes хранить приватно до удаления владельцем стенда. Не помещать journal keys/credential exports в Git.

## Docker parity lab, не HF deployment

Тот же pinned Dockerfile и dependencies, patched SQLite и port 7860. Recipe [compose.yaml](../../lab/help01_1/compose.yaml) задаёт три logical volumes и internal network; host port привязан только к loopback. Три volumes не являются тремя независимыми failure domains.

```sh
docker compose -f lab/help01_1/compose.yaml build
docker compose -f lab/help01_1/compose.yaml run --rm lab init --new-root /lab/instance --journal-dir /control/original --witness-dir /witness/original
docker compose -f lab/help01_1/compose.yaml run --rm lab inspect --root /lab/instance
docker compose -f lab/help01_1/compose.yaml up lab
```

Для нового эксперимента использовать новый Compose project name, не переинициализировать существующие control stores. `up` не выполняет migrations. Не монтировать реальные DB/.env/HF caches/secrets, не передавать production token. Локальный Docker в текущем окружении отсутствовал; новый smoke проверяется в CI, Compose invocation отдельно не аттестован.

HF Docker Spaces используют Docker entrypoint и настраиваемый app port; данные/credentials хоста не следуют автоматически из локального теста. [Официальная документация](https://huggingface.co/docs/hub/spaces-sdks-docker). В стенде намеренно Web-only harness вместо production Telegram launcher; UID, storage/fsync, proxy/HTTPS/cookies, sleep/restart, offsite custody и реальные devices требуют отдельной квалификации. SQLite WAL не следует размещать на неквалифицированном сетевом FS: [SQLite WAL](https://www.sqlite.org/wal.html).

## DR / crash

`tests/test_help_readiness.py` создаёт новые app/control stores; выполняет exit 74 до journal, exit 75 после SQL commit, main-file copy без живого WAL, encrypted custody export/recover в новую директорию, restore старой app copy с актуальными retained controls, missing key/witness и ENOSPC injection. Старый `tests/test_help01.py` проверяет промежуток journal→SQL, multiprocess accepts и account deletion.

Принцип восстановления: остановить writers; иметь consistent app backup через SQLite backup API, отдельно encrypted control bundle, отдельно wrapping key и актуальный retained witness. Сначала authenticate/recover journal в новый destination против retained witness, затем offline `privacy_maintenance.restore` app copy. Не инициализировать новые control keys вместо потерянных. При неизвестной вершине не обслуживать пользователей. Воспроизводимый DR в тесте — same-host simulation, не независимый offsite drill.

## Разрешённый test LLM, если появится

Только явные env `BLAGO_TEST_HF_TOKEN`, `BLAGO_TEST_HF_MODEL`, `BLAGO_TEST_HF_APPROVED=synthetic-only`. Передать token безопасным способом вне Git/shell history; не использовать production credential, `HF_TOKEN`, Hub cache или `.env`. Harness не читает их и не меняет Integration launcher policy.

```sh
PYTHONPATH=.:src python -m tools.help_test_provider --output /tmp/help011-provider.json
```

Без test configuration результат `NOT EXECUTED`, 0 requests. С approval — 10 synthetic turns, HTTP call budget 24, ограниченные timeout/token budget, timing, no-action/integrity checks и ответы для ручной оценки связности/естественности. Никаких реальных identifiers/private memory. Timing запроса до headers и полная turn latency различаются. Автоматические invariants не заменяют manual quality review. До фактического выполнения G06 остаётся UNKNOWN.
