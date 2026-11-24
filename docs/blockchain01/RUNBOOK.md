# Чистый checkout и независимая сверка

Нужны Git, Node 24.14.0/npm, Python 3.12 с зависимостями requirements-integration.lock и проверенная SQLite по существующему tools/build_sqlite.py. Никаких production tokens или HF/Telegram credentials. Все пути выходов должны быть новыми. Exact source SHA зафиксирован в evidence.json и описании PR; не подменять его более поздним документационным HEAD.

Из чистого checkout source SHA:

```sh
git clone --no-checkout https://github.com/xBLAGO/space.git /tmp/blago-bc01-review-NEW
git -C /tmp/blago-bc01-review-NEW checkout --detach a031a45d4f04d39c8da035290558f37bb2966238
cd /tmp/blago-bc01-review-NEW
```

Этот source включает runtime и исправленный lockfile. Более поздний HEAD может добавлять документацию; не заменять source автоматически без сравнения.

```sh
python3 -m venv /tmp/blago-bc01-venv-NEW
/tmp/blago-bc01-venv-NEW/bin/python -m pip install -r requirements-integration.lock
/tmp/blago-bc01-venv-NEW/bin/python -m pip install --no-deps -e .
cd tools/blockchain01
npm ci --ignore-scripts --no-audit --no-fund
node compile.mjs --check
npm test
cd ../..
```

SQLite должна пройти `python -m xblago.core.sqlite_safety --require-safe`. На Linux существующий builder создаёт отдельную библиотеку:

```sh
/tmp/blago-bc01-venv-NEW/bin/python tools/build_sqlite.py --prefix /tmp/blago-bc01-sqlite-NEW
LD_LIBRARY_PATH=/tmp/blago-bc01-sqlite-NEW LD_PRELOAD=/tmp/blago-bc01-sqlite-NEW/libsqlite3.so.0 PYTHONPATH=.:src BLAGO_LAB_PYTHON=/tmp/blago-bc01-venv-NEW/bin/python node tools/blockchain01/lab.mjs --new-root /tmp/blago-bc01-run-NEW --enable-synthetic-local
```

На Mac использовались Python 3.12.10 x86_64 и отдельный driver SQLite 3.54.1. В воспроизведённой среде команда:

```sh
PYTHONPATH=/private/tmp/blago-pr41-live-sqlite:.:src BLAGO_LAB_PYTHON=/private/tmp/blago-pr41-live-venv/bin/python node tools/blockchain01/lab.mjs --new-root /tmp/blago-bc01-run-NEW --enable-synthetic-local
```

Эти два абсолютных пути относятся к проверенному Mac; на другой машине подготовить свою безопасную Python/SQLite-среду. Не подменять системную SQLite. Node x64 на Mac запускает producer через `/usr/bin/arch -x86_64`, сохраняя согласованность driver architecture. Producer получает только новую временную БД из существующего lab(), не путь к реальным данным.

В pinned package-lock восстановлено optional=true у bundled darwin-only fsevents2.3.2: родитель chokidar3.5.2 уже объявляет его optional dependency. Это исправляет EBADPLATFORM на Linux без --force и без изменения tarball integrity. Обычный npm ci использует закреплённую запись; npm install может вновь воспроизвести upstream metadata bug, поэтому не регенерировать lock без Linux CI.

Команда по умолчанию занимает 127.0.0.1:18545, отказывается подключаться к уже занятому порту, выполняет S1–S5, записывает отчёт, изменённую копию, standalone-verifier и evidence, затем останавливает только собственный дочерний процесс сети. Другой порт — `--port 18546`. При UNKNOWN/revert вне ожидаемого сценария runner завершается ненулевым кодом; это не PASS.

## DeepSeek / отдельный проверяющий

Сначала сверить source SHA и самостоятельно выполнить `npm ci`, `node compile.mjs --check`, `npm test`. Затем запустить такой же новый стенд, но оставить сеть включённой:

```sh
PYTHONPATH=.:src BLAGO_LAB_PYTHON=/path/to/test-venv/bin/python node tools/blockchain01/lab.mjs --new-root /tmp/blago-bc01-independent-NEW --enable-synthetic-local --serve
```

В первом терминале остаётся **только локальная сеть и runner**, producer BLAGO уже завершился. Во втором терминале прочитать evidence.json, взять `network.contract`, `network.genesis`, `network.endpoint`, `publication.tx_hash`. Не принимать чужой адрес без проверки контекста. Выполнить, подставив реальные значения из собственного evidence:

```sh
node /tmp/blago-bc01-independent-NEW/standalone-verifier/verify.mjs /tmp/blago-bc01-independent-NEW/report.json CONTRACT_ADDRESS 1 --rpc http://127.0.0.1:18545/ --genesis GENESIS_HASH
node /tmp/blago-bc01-independent-NEW/standalone-verifier/verify.mjs /tmp/blago-bc01-independent-NEW/changed-report.json CONTRACT_ADDRESS 1 --rpc http://127.0.0.1:18545/ --genesis GENESIS_HASH
node /tmp/blago-bc01-independent-NEW/standalone-verifier/verify.mjs /tmp/blago-bc01-independent-NEW/report.json CONTRACT_ADDRESS 999 --rpc http://127.0.0.1:18545/ --genesis GENESIS_HASH
```

Ожидания: MATCH/0, MISMATCH/1, UNKNOWN/2. Команды с 1/2 закономерно возвращают ненулевой exit; не выдавать его за сбой стенда или успешную проверку. После Ctrl-C в первом терминале повтор первой команды должен дать UNKNOWN/2.

Проверяющий может скопировать только report.json и standalone-verifier (verify.mjs, core.mjs, artifact.json) на другую рабочую директорию. Для этой проверки установка BLAGO/npm не требуется, нужен Node и доступ к той же loopback-сети. На другом host loopback обозначает другую сеть: данная задача не создаёт remote RPC/P2P.

Отдельная проверка операций исходного отчёта, не заменяющая on-chain сверку:

```sh
python -c 'import json; from pathlib import Path; from tools.independent_capital_verifier import verify_bundle; r=json.loads(Path("/tmp/blago-bc01-independent-NEW/report.json").read_text()); checked=verify_bundle(r["proof"],r["producer_pins"]); assert checked==r["verified"], "Report projection mismatch"; print(checked)'
```

Здесь pins synthetic producer-owned. Для реального аудита pins и человеческие полномочия требовали бы независимого закрепления — его нет.

Независимый пересчёт byte fingerprint без Node и без BLAGO (путь к разрешённой копии заменить своим):

```sh
python -c 'from pathlib import Path; import hashlib; b=Path("/tmp/blago-bc01-independent-NEW/report.json").read_bytes(); print("0x"+hashlib.sha256(b"BLAGO/FUND-REPORT-CHECKPOINT/1\0"+len(b).to_bytes(8,"big")+b).hexdigest())'
```

Сравнить с publication.digest собственного evidence и записью сети. Сам по себе локальный evidence не заменяет сетевую сверку.

Для независимой проверки rejected transactions прочитать S3/S4 tx_hash и вызвать обычный read-only JSON-RPC `eth_getTransactionReceipt` на том же localhost; status должен быть `0x0`. `eth_getTransactionByHash` покажет from/to/input, а `get(1)` и `last()` — неизменность точки. Это не отправляет новых транзакций. Пример с собственным TX_HASH:

```sh
curl --fail --silent http://127.0.0.1:18545/ -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","id":1,"method":"eth_getTransactionReceipt","params":["TX_HASH"]}'
```

Публичный SHA-256 вектора можно пересчитать без Node/Solidity:

```sh
python -m unittest tests.test_blockchain01_export -v
```

Самостоятельный вердикт DeepSeek фиксируется им после собственной проверки. Этот same-author стенд его не выдает.

## Отказ и восстановление

Данный chain одноразовый/in-memory: остановка уничтожает его. Старый report при отключённом RPC или без своего контракта — UNKNOWN. Повторный запуск создаёт новую сеть; использовать новые адрес/генезис/evidence, не переписывать прежний отчёт и не объявлять восстановленной старую цепь. У нас нет публичной finality, долговечного backup blockchain или multi-operator consensus.

При ошибке компиляции, source verification или установки runner не должен публиковать точку. Сохранить stdout/error и исходные файлы, исправлять в отдельной итерации; не подгонять hash-vector/ожидания. При прерывании остановить только свой foreground runner Ctrl-C; production-процессы не трогать. Никаких миграций/rollback реальных БД, ключей и webhook.
