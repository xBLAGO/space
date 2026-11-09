# Threat model delta — Calculator01

Assets: точная оценка/источник, отсутствие ложных полномочий/исполнений, приватность Help audience, retained denials. Adversaries: malicious participant, replaying caller, wrong provider fixture, stale backup, ошибочная LLM; compromised trusted process/operator не решён этим lab.

| Атака | Control / test / предел |
|---|---|
| Float/NaN/overflow/неверная валюта | Decimal-string bounded inputs, currency minor-unit precision, no floats; ValueTests |
| Подмена/старый/конфликтующий курс | pinned source fixture, unique source IDs, max age/spread; errors fail closed. Pins не подписанный независимый feed |
| Двойной asset/resource счёт | canonical good + available portion; same object valuation ID bound to lot. Physical duplicates remain unverified |
| Подмена confirmed evidence | Inventory всегда SELF_DECLARED; Help COMPLETED только после двух существующих confirmations. Независимый verifier не реализован |
| Publication или LLM создаёт obligation | Help state projection только accepted states; агент не подключён к mutation API |
| Чужой Help/aggregate IDOR | existing fresh session/Help `_row` audience; outsider aggregate empty; user ID не token |
| Witness потерян/old backup | existing Help transaction reconciliation/holds; test unavailable witness and offline old backup after withdrawal |
| Replay/ACK потерян | same committed lab challenge returns original receipt; unique event(target,version); same Help state read не копирует событие |
| Конкурентные версии | transactional version compare in one in-memory store; no shared-process/file-backed guarantee claimed |
| Crash до commit | synthetic injected OSError → rollback body/event/receipt. Process loss discards all lab memory |
| Crash/restore resurrect consent | Lab restore/import всегда UNKNOWN_RECOVERY отказ; новый объект пуст. Это потеря доступности, не durable recovery |
| Логи/экспорт/retention leakage | no free text/location, no persistent lab DB, no LLM logs; Help text/owners не экспортируются. Synthetic reports contain fixture values only |
| Small-cohort re-identification | only actor-visible scope; no new public endpoint. Even aggregate counts can reveal change; external release needs threshold/differencing review |
| Stock/flow/need смешение | separate counters/nullable flow; no overall wealth score; missing quantities/values UNKNOWN |

No independent security review claimed. In-memory SQL is not a cryptographic event log. Untrusted code can mutate Python objects/connection; its authority must not be deployed to agents. Synthetic principal is an internal test capability, not Web authentication. Timestamps/clock supplied by trusted lab caller; no production clock assurance.

Lab omission of durable data intentionally prevents a second deletion/recovery perimeter. A durable implementation must first specify atomic app+journal/witness revocation, outbox/crash transitions, account merge/deletion/retention, backup frontier and cross-worker fencing. Do not attach these tables to app SQLite, bypass Help.tx, catch-and-ignore journal errors, or re-init controls to make an old backup pass.
