# Результаты Blockchain 01

База `f9d7353033f21b75887e0f3d1f61a9fbb740d95f`; точный source SHA и clean status финального прогона записываются в evidence.json.

| Проверка | Результат |
|---|---|
| Solidity compile / artifact --check | PASS, 0.8.30+commit.73712a01.Emscripten.clang, EVM Paris |
| Node groups | 4/4 PASS: byte vector, loopback/URL bounds, redirect, actual EVM + standalone verifier |
| Python tests | 3/3 PASS: independent hash reference, reuse/no-overwrite, rejection before output |
| Первый actual CapitalLedger→chain lab | S1 MATCH, S2 MISMATCH, S3/S4 mined revert, S5a/S5b UNKNOWN |
| npm ci Linux target в пустом каталоге | PASS; это проверка установки lockfile, не Linux runtime |
| Clean checkout acceptance | PASS, source 140ab2912b3cc5008219e78875ebf337f269ae72, source_clean=true; clean-run/evidence.json |
| Полный regression / Docker / Linux EVM | Фактические CI ссылки и статусы публикуются в Draft PR; не наследуются от #39 |
| Независимый DeepSeek / operator audit | NOT EXECUTED |

Локально: Node24.14.0, npm11.9.0, Ganache7.9.2, Python3.12.10 x86_64, SQLite3.54.1 (изолированный driver). Node output в source check: 4 tests / 0 fail / 0 skipped. Python: 3 tests / 0 errors/failures. Реальные EVM assertions включают strict growth, zero digest refusal, skipped point, wrong genesis, missing contract code, stopped network, и CLI exit codes0/1.

Golden hash-vector.json не изменялся под результаты прогона. Все новые зависимости находятся в tools/blockchain01; requirements-integration.lock/pyproject.toml и production runtime не менялись. Новых миграций нет. Полный локальный baseline regression не выполнялся повторно из-за объёма; существующий CI запускает все тесты дважды. Это не замена фактическому итоговому статусу CI.

## Проверенный clean checkout

```sh
git worktree add --detach /tmp/blago-bc01-clean 140ab2912b3cc5008219e78875ebf337f269ae72
cd /tmp/blago-bc01-clean/tools/blockchain01
npm ci --ignore-scripts --no-audit --no-fund
node compile.mjs --check
cd ../..
PYTHONPATH=/private/tmp/blago-pr41-live-sqlite:.:src BLAGO_LAB_PYTHON=/private/tmp/blago-pr41-live-venv/bin/python node tools/blockchain01/lab.mjs --new-root /tmp/blago-bc01-clean-run --enable-synthetic-local
```

Producer PID20221, exit0, закончил 2026-11-24T05:49:18.519Z до старта chain. Сеть: chainId31337, Ganache7.9.2, genesis `0xe7366627da1a0d5b3296726fbe579c1a14ac791ad75756f8cb87929fe4f58e3a`.

Contract `0x5FbDB2315678afecb367f032d93F642f64180aa3`; deployment tx `0xa418c91555b94f7bf08ff4ca7a5e7ff3f15df5bd1bf05b0c4aa65f14c57a9661`.

Point1 digest `0x9e8d3aa41318fd046830bfbd06968db458409e942a9edbe2d2d4ed9ea17fe6e0`; publication tx `0xcdfaf17d69460917fb23b7006d738ab04f6e158cfb96af350b3fb9f3857b0ca1`, block2, receipt1.

| Сценарий | Фактический результат |
|---|---|
| S1 / BLAGO OFF | MATCH/0 |
| S2 / verified.balance.available_minor +1 | MISMATCH/1 |
| S3 / account1 | REVERT, mined receipt0, tx `0xd41e710e2a60a27b581b0e4135926d1299c18ddf3c2f3ea8494b59a092d6e8f8` |
| S4 / publisher overwrite point1 | REVERT, mined receipt0, tx `0x3f474eb5a67bb2415c7a8ab0be0f364d2dd8fa4568a7dd1533b2be051fd7c61d` |
| S5a / point999 | UNKNOWN/2, POINT_ABSENT |
| S5b / unavailable RPC | UNKNOWN/2, NETWORK_UNAVAILABLE |

Файлы clean-run содержат оригинальную и изменённую синтетические копии, их SHA-256 и контрольные суммы standalone verifier. Историческая сеть по окончании остановлена; эти адреса/tx проверяются только в том прогоне. При новом запуске genesis/ключи proof/digest/tx будут другими, исходы должны совпасть. Не объявлять старую цепь восстановленной по этим JSON.

Source CI: https://github.com/xBLAGO/space/actions/runs/45335921791 (PR), https://github.com/xBLAGO/space/actions/runs/45335843230 (push). Текущие статусы фиксируются в описании PR #46 после выполнения, без переписывания исторического evidence под новый HEAD.

## Обнаруженный Linux installation failure

На source140ab291 job blockchain01 в run45335921791 остановился при npm ci: EBADPLATFORM fsevents2.3.2 (darwin-only). Локальный Linux-target install не доказал Linux runtime/проверку реальной process.platform. Этот failed run не объявляется PASS.

Причина — bundled extraneous metadata Ganache: chokidar3.5.2 объявляет fsevents~2.3.2 в optionalDependencies, но bundled lock entry потерял optional=true. В новом lockfile восстановлен только optional=true для этой записи; version/resolved/integrity не менялись. --force, отключение platform checks, изменение приложения и тестовых ожиданий не используются. Новый Linux CI должен фактически подтвердить исправление. Старый clean-run отражает Mac source140ab291 и сохраняется как исторический; его PASS не переносится на иной SHA.
