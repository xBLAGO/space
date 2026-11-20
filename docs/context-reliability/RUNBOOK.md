# Изолированный стенд и восстановление

Ни одна команда ниже не предназначена для production. Использовать только новый каталог синтетического стенда. Не указывать путь действующей SQLite, HF volume, webhook или реальные credentials. Данные и ключи создаются только временно.

## Воспроизводимые проверки (Linux / CI)

```sh
python3.12 -m venv /tmp/blago-context-venv
/tmp/blago-context-venv/bin/python -m pip install -r requirements-integration.lock
/tmp/blago-context-venv/bin/python -m pip install playwright==1.64.1
python tools/build_sqlite.py --prefix /tmp/blago-context-sqlite
export LD_LIBRARY_PATH=/tmp/blago-context-sqlite
export LD_PRELOAD=/tmp/blago-context-sqlite/libsqlite3.so.0
export PYTHONPATH=.:src
/tmp/blago-context-venv/bin/python -m xblago.core.sqlite_safety --require-safe
/tmp/blago-context-venv/bin/python -m unittest discover -s tests -v
/tmp/blago-context-venv/bin/python -m tools.freeze_integration
/tmp/blago-context-venv/bin/python -m tools.agent_context_check --output /tmp/agent-context.json
/tmp/blago-context-venv/bin/python -m tools.agent_context_browser_check --chrome /usr/bin/google-chrome --output /tmp/agent-context-browser
```

В macOS локальные доказательства получены с уже изолированным драйвером `/tmp/blago-sqlite-driver-3541` и Python `/tmp/blago-uel03-venv/bin/python`; фактические команды и результаты записаны в evidence. Это не замена системной SQLite. Browser запускается через установленный Chrome, весь нелокальный трафик перехватывается / блокируется. Физический Telegram WebView не эмулируется как доказанный результат.

## Сравнение exact baseline

На отдельном detached worktree exact `34ed8a6…` запускается копия `tools/agent_context_check.py` из этого PR с `--legacy`. Код базового worktree не изменяется. `PYTHONPATH=.:src` должен указывать на базовую версию, не новую ветку. Legacy probe не импортирует новый runtime-модуль и не применяет новую миграцию. В evidence отмечается actual `git rev-parse HEAD`.

Один и тот же сценарий включает profile → explicit name/city → 35 ходов → запрос факта → Bot link → Mini App → correction → restart → delete → чужой участник. Baseline и новая версия обе проходят собственные существующие identity / integrity проверки; исправление оценивается по различию контекста, а не по одинаковым scripted фразам ответа.

## Интерактивный synthetic-only стенд

Готовый стенд:

```sh
python -m tools.agent_context_lab --new-root /tmp/blago-context-NEW
python -m tools.help_readiness_lab inspect --root /tmp/blago-context-NEW
python -m tools.help_readiness_lab serve --root /tmp/blago-context-NEW --port 17860
```

Открыть `http://127.0.0.1:17860/app`, использовать только синтетические аккаунты и приглашение `synthetic-help011-only`. Этот явный lab initializer использует существующий Help-стенд, выполняет отдельную offline-миграцию и включает новый флаг только в конфигурации нового root. Команда отказывает для существующего root. Внешних LLM-вызовов и работающего Bot webhook нет.

Для отдельной проверки миграции исходный немигрированный стенд создаётся через `tools.integration_fixture` с `--new-synthetic-root /tmp/<НОВЫЙ-КАТАЛОГ> --help-enabled --context-fencing --lifecycle`. Сохранить выданный `public-config.json` и загрузить его переменные в отдельный shell через безопасный JSON-reader, не через `eval`. Флаг нового контекста в этой начальной конфигурации — `false`.

После проверки этой синтетической копии, при остановленных workers:

1. Проверить pinned SQLite и целостность текущих DB/journal/witness. Сохранить отдельную проверяемую копию всех требуемых control-plane данных. Same-volume каталоги лаборатории не являются независимыми disaster-recovery копиями.
2. При `XBLAGO_INTEGRATION_MODE=synthetic` выполнить `python -m xblago.core.agent_context_schema /tmp/<НОВЫЙ-КАТАЛОГ>/app/blago.sqlite3`. Миграция не запускается при импорте / старте.
3. Повторить команду: ожидается `unchanged`. Проверить `PRAGMA integrity_check`, `foreign_key_check` и сохранение старых migration checksums.
4. Только теперь установить `XBLAGO_AGENT_CONTEXT_ENABLED=true`. Остальные обязательные privacy, secure identity, fencing, lifecycle и synthetic controls оставить включёнными.
5. Для интерактивного запуска использовать описанный выше lab initializer и его private marker. Произвольный root от `integration_fixture` не выдаётся за валидный marker существующего Help-сервера. Для интерфейса с синтетическим SDK использовать автоматизированный browser tool, а не работающий Telegram bot.

Начальная миграция исключает всю прежнюю raw history из LLM-контекста. Legacy memory сохраняется, но её источник остаётся UNKNOWN. Это намеренное консервативное поведение, не автоматический backfill биографии.

## Восстановление / неопределённый commit

1. Остановить writers лаборатории. Не переинициализировать journal, witness или их ключи. Проверить, что контрольные копии актуальны и принадлежат исходной lineage.
2. Работать с новой копией backup; не перезаписывать исходник. Использовать существующую `privacy_maintenance.restore`, сохраняющую retained denials и frontier. Сессии после restore требуют нового входа по существующим правилам.
3. Если backup старше новой схемы, startup должен отказать. Явно применить совместимые fencing/lifecycle и agent-context миграции offline в установленном порядке. Новая миграция не создаёт новую доверенную lineage и не сбрасывает retained address denial.
4. Проверить, что `city` / `name` после удаления не возвращаются в память, source quotes, профильное обращение или модельную историю. Новые dialogue IDs должны быть выше независимо сохранённой границы, включая backup до первой реплики.
5. PREPARED ticket старого snapshot не является правом записи. При конфликте не повторять старый LLM-вызов; участник при желании отправляет новое сообщение. Bot update replay использует существующую receipt, без новой генерации.
6. При неизвестном результате SQL commit допустим лишний retained запрет; недопустимо считать ответ сохранённым без доказательства. Полный откат журнала и witness вместе с БД не покрывается этой лабораторией.

## Rollback / остановка

Остановить лабораторию при недоступности control plane, дрейфе DDL/checksum, несовпадении источника или возврате удалённого. Не удалять новые триггеры / journal events и не выставлять flag=false на уже мигрированной базе как способ обойти защиту: защищённый чат должен отказать. Для возврата к старому runtime допустимо создать **новый синтетический стенд без пользовательских данных**; не открывать мигрированную lineage старым бинарником.

Down-migration, удаляющая retained denials, не предоставляется. В production ничего из этого PR не активируется. Владельцу ещё предстоят независимый review, допуск тестового LLM, физические устройства, эксплуатационный DR и отдельное решение по общим release gates.
