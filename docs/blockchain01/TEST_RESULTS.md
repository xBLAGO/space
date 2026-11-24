# Результаты Blockchain 01

База `f9d7353033f21b75887e0f3d1f61a9fbb740d95f`; точный source SHA и clean status финального прогона записываются в evidence.json.

| Проверка | Результат |
|---|---|
| Solidity compile / artifact --check | PASS, 0.8.30+commit.73712a01.Emscripten.clang, EVM Paris |
| Node groups | 4/4 PASS: byte vector, loopback/URL bounds, redirect, actual EVM + standalone verifier |
| Python tests | 3/3 PASS: independent hash reference, reuse/no-overwrite, rejection before output |
| Первый actual CapitalLedger→chain lab | S1 MATCH, S2 MISMATCH, S3/S4 mined revert, S5a/S5b UNKNOWN |
| npm ci Linux target в пустом каталоге | PASS; это проверка установки lockfile, не Linux runtime |
| Clean checkout acceptance | Будет приложен после source commit; прежний dirty checkout не объявляется clean |
| Полный regression / Docker / Linux EVM | Фактические CI ссылки и статусы публикуются в Draft PR; не наследуются от #39 |
| Независимый DeepSeek / operator audit | NOT EXECUTED |

Локально: Node24.14.0, npm11.9.0, Ganache7.9.2, Python3.12.10 x86_64, SQLite3.54.1 (изолированный driver). Node output в source check: 4 tests / 0 fail / 0 skipped. Python: 3 tests / 0 errors/failures. Реальные EVM assertions включают strict growth, zero digest refusal, skipped point, wrong genesis, missing contract code, stopped network, и CLI exit codes0/1.

Golden hash-vector.json не изменялся под результаты прогона. Все новые зависимости находятся в tools/blockchain01; requirements-integration.lock/pyproject.toml и production runtime не менялись. Новых миграций нет. Полный локальный baseline regression не выполнялся повторно из-за объёма; существующий CI запускает все тесты дважды. Это не замена фактическому итоговому статусу CI.
