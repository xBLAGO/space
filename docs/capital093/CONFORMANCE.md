# Foundation09.3 conformance / independence matrix

Expected and actual test outcomes are in test-results.json. All automated tests
use synthetic data and same-host stores. Local regression and CI are distinct.

| Requirement | Implemented evidence | Actual scope / remaining gap |
|---|---|---|
| R1 three nodes | A/B/C separate keys, SQLite/control paths, multiprocess tests | PASS local; independent operators NOT EXECUTED |
| R2 signed UEL propagation | Existing source proof,3/3 certificates, monotone prefix/counters, replay collision rejection | PASS local; private payload binding retains source trust |
| R3 independent anchoring/freshness | Quorum certificates, external floor, signed current nonce-bound statuses | PASS local assumptions; off-host custody NOT EXECUTED |
| R4 key rotation/loss | Live original GOV quorum+2 surviving old nodes+delay, custody epoch/config history | PASS custody only; fund owner/evidence/privacy rotation DESIGN/NOT IMPLEMENTED |
| R5 origin loss | Old signatures verified without origin key; B/C restore read-only projection, A2 import | PASS history custody; positive spending authority not restored |
| R6 preserve canonical domains | No src or golden-format changes, same existing Help/Asset/Capital exports | 821/821 local and twice in source CI PASS; distributed private deletion not qualified |
| R7 faults |48 new tests incl partition, minority, signature/fork, old snapshots,3-copy rollback, crash/replay, races | PASS focused local; physical storage fault/real network NOT EXECUTED |
| R8 threat/conformance/runbook | PROTOCOL, THREAT_MODEL, RUNBOOK, AUTHORITY_CUTOVER_DESIGN, machine evidence | Same-author review, no independent audit |

## Decision

- GO: offline verification and isolated local synthetic custody after passing checks.
- NO-GO: full Foundation09.3 fund authority failover; independent multi-operator
  fund/federation; real users/funds; production. No gate promotion.
- A single node cannot change a confirmed checkpoint without required additional
  signatures under the stated key/retention assumptions. This is not a promise
  against a compromised human quorum or loss of all independent knowledge.

Фактический полный локальный протокол: [full-regression.json](full-regression.json).
Он содержит821 исходов expected PASS / actual PASS,0 skipped. Ни один ранее
NOT EXECUTED межоператорский/аппаратный сценарий не превращён в PASS.
