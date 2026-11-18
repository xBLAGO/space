# Synthetic proof fixture

proof-fixture.json и producer-pins.fixture.json содержат только новые синтетические публичные ключи, подписи и условные события. Приватные keys/seeds, UID, sessions и реальные сведения отсутствуют.

```sh
python tools/independent_capital_verifier.py docs/governance092/proof-fixture.json --pins docs/governance092/producer-pins.fixture.json
```

Ожидается PASS, 17 событий, policy version2, 703000000 spent minor и 297000000 available minor. Это producer-generated test pins, не доказательство независимого закрепления. Для настоящей независимой проверки pins и актуальный checkpoint tip должны поступать из отдельного доверенного канала.

48 сценариев tests/test_governance.py покрывают положительные и отрицательные replay states. Негативные переходы проверяются двумя алгоритмами. Mutated bundle tests меняют поля событий, подписи/хэши/UEL inclusion/binding и требуют отказа. Frozen 72 UEL примера не изменялись.

Новый CLI --proof-output/--pins-output создаёт отдельные файлы с O_EXCL и режимом0600. Оба пути обязательны вместе. В CI сохраняются заново сгенерированные proof, producer pins, их hashes и результат запуска второго verifier. Каждое выполнение создаёт новые ключи, поэтому crypto hashes различаются между запусками, а баланс воспроизводим при одинаковых событиях.
