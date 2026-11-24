# Threat model / границы доказательства

| Угроза | Контроль стенда | Остаток |
|---|---|---|
| Изменение копии отчёта | Versioned SHA-256 над точными байтами, длина и фиксированный prefix | Не доказывает смысл/достоверность исходного файла |
| Неоднозначная JSON canonicalization | Verifier не парсит/не пересериализует JSON; whitespace значим | Копию нужно передавать без форматирования |
| Публикация другим EVM address | msg.sender==publisher; реальная mined reverted tx account1 | Локальные development keys известны всем, RPC unlocked; это не реальные участники |
| Перезапись/старый номер | number>last; mapping не имеет setter/delete; отсутствуют proxy/upgrade | Оператор может удалить/пересоздать всю локальную сеть |
| Substitution контракта/chain | chain31337, точный runtime bytecode, publisher, optional pinned genesis | Адрес/genesis/code должны быть сверены вне BLAGO; зловредный RPC может лгать |
| Недоступность/отсутствие точки | UNKNOWN/2, никаких cached MATCH | Нет резервирования chain, light client или distributed consensus |
| Ложный/неполный отчёт фонда | Существующий proof_export + независимая реализация verify_bundle до публикации | Публикующий адрес может зафиксировать любую другую строку; human consent/source truth проверяются отдельно |
| Подмена checkpoint freshness | Вывод номера, chain/genesis/contract; явная семантика конкретной точки | Клиент не обещает latest/freshness и не доказывает глобальное отсутствие новых событий |
| Утечка в chain | Только number + bytes32; ни файла, ни чатов, ни имён | Хеши и timing могут раскрывать сведения; реальные отчёты запрещены |
| SSRF / redirect | Только literal loopback HTTP, read-only RPC allowlist, no redirects, bounded responses/timeouts | Другие локальные сервисы считаются недоверенными и дают UNKNOWN |
| Смешивание с production | Новые temporary fixtures, обязательные synthetic flags, отдельные dev dependencies | Никакого разрешения на реальных участников/средства |

Один контракт — одна synthetic fund scope. Разрешённый адрес не является новым governor CapitalLedger и не получает прав на деньги. Single-publisher anchoring здесь не отменяет существующие кворумы Foundation09.2/09.3 и не решает single-operator trust.

Неизменяемость проверяется для транзакций контракта в честно исполняемой EVM. Административные dev-RPC Ganache способны подменить storage/состояние вне этих функций; скомпрометированный узел также может лгать клиенту. Стенд не доказывает защиту от оператора самой сети или финальность публичного блокчейна.

Контракт не принимает обычные payable вызовы и не выполняет переводов; на EVM возможна принудительная доставка тестового ETH извне. Наличие байтового balance адреса не является коллективным капиталом и не учитывается BLAGO. Газ и faucet balances только лабораторные.

Прежние migration VERSION/CHECKSUM, privacy deletion/retention, G09, CAS, journal/witness/checkpoint и recovery holds не изменяются. Это цепь фиксации отчёта, не второй CapitalLedger или Help lifecycle. Удаление приватного payload и отзыв полномочий должны продолжать исполняться исходными доменами; этот стенд не одобряет публичное хранение их hashes.

При утрате сети вывод UNKNOWN, а не «история восстановлена». При неправильном source proof публикация не начинается. При недостаточных данных результата нет; результаты пяти сценариев записываются только после проверки фактических ответов/receipts. Same-author run — не независимый аудит; DeepSeek вердикт NOT EXECUTED до его собственной сверки.
