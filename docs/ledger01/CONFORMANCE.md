# UEL conformance design and observed results

Draft. Executed checks verify examples, **not a working universal ledger**.

| Layer | Executable cases | Expected / actual scope |
|---|---:|---|
| W01–W38 wire/signature |38 | Strict bounded ASCII encoding, duplicate keys, UTF-8 bytes, key/role/domain pin, signature mutation, no session/HMAC/Public substitution. Valid result **WIRE_OK_NOT_AUTHORIZED** |
| P01–P20 arithmetic |20 | Already-eligible current source snapshots only; one canonical asset/resource, one Help occurrence, disputes/withdrawals, unknowns, forks, exact quantities |
| V01–V08 valuation |8 | Calls existing Calculator01 ValueEngine on synthetic quotes: RUB/USD/JPY, HALF_EVEN, stale/conflicting rates, float rejection |
| M01–M06 public tree examples |6 | Empty/1/2/3-leaf tree hashes, private/public leaf distinction and forbidden extra personal field. Not complete proof/approval verification |
| Authority/privacy/recovery design scenarios |22 | **NOT EXECUTED**; normative pre/postconditions in design-scenarios.json, require future stateful implementation |

72 executable examples PASS locally; exact per-case expected/actual in [results.json](results.json). Frozen corpus hashes: [vectors-manifest.json](vectors-manifest.json). [golden.json](golden.json) contains exact signing bytes, canonical wire and object ref. [test_keys.json](test_keys.json) is an explicitly PUBLIC TEST ONLY seed/public key, not a wallet or operational authority; never reuse it in real enrollment or08 labs.

## Reproduction

Use existing locked development environment. No dependency, DB, environment/secrets, production route or branch merge required.

```sh
PYTHONPATH=src python docs/ledger01/generate_vectors.py --check
PYTHONPATH=src python docs/ledger01/check_design.py
PYTHONPATH=src python docs/ledger01/check_vectors.py --output /tmp/uel-design-results.json
```

`generate_vectors.py --check` regenerates **in memory** and compares checked-in bytes; it never overwrites golden results. Without --check it is an authoring tool and changes require explicit review/version rationale, not a way to hide a failed case. Expected W/P/V outcomes are fixed literals, not computed by the checker from its own result. Merkle expected roots are deterministic sample outputs from the same author; independent implementation is still required.

Local interpreter uses existing `/tmp/blago-life013-venv`; no real rates/provider/LLM/Telegram/chain requests. The inspector imports standard reviewed cryptography for known pinned synthetic Ed25519 key, but is **not** a full enrollment/key-point/authority verifier. It intentionally cannot return ACCEPT_DELIVERY or execute an operation.

P08/P09 include a hypothetical already-approved measured-flow operand for future Help measurement design; current Help01 does not store that amount or prove such measurement. Current09.1 actual flow remains UNKNOWN.

Projection input is not an untrusted packet. Eligibility, full predecessor/authority/source coverage, privacy cut, domain state and valuation selection are preconditions; current source states are fixture inputs. It does not reconstruct Help transitions; snapshots use realistic source revisions but omit already-validated intermediate events. Same-author encoder/checker sharing is explicit; no two-independent-validator claim.

## Mandatory future stateful matrix

| Requirement | Design scenario examples | Promotion evidence needed |
|---|---|---|
| Human/Agent/Node authority separation | A01–A04 | Distinct independently controlled keys, endorsement/mandate signatures, no session substitution |
| Fresh status / nonce / revoke | A05–A09 | Event/recipient-bound nonce, current retained history, both serial orders and partitions |
| Durable ACK/replay | A10/R01/R03 | Actual fsync/commit/crash-before-and-after-ACK; no effect duplication or positive reconstruction |
| Independent restore / missing controls | R01–R05 | Separate hosts/volumes/custody, checkpoint loss/rollback/key loss, explicit UNKNOWN_RECOVERY |
| Single Help lifecycle / evidence quantities | H01–H03 | Exact source transition mapping, one canonical occurrence, partial measurements with both parties, no remote Help mutation |
| Public privacy and historical inclusion | P01–P03 | Real release barrier + reviewed differencing policy + independent inclusion/consistency checks |
| Independent operators | F01 |08 S12 remains NOT EXECUTED until actual independent operator evidence |

Before interoperability GO two **separately implemented** validators/reducers must match on exact frozen bytes and agreed cuts, including all malformed, reordered, duplicate, revoked, erased, forked and stale cases. Reuse of72 examples does not replace new semantic/authority schemas or tests. Do not change08 golden files or declare its note-only profile production-ready.

## CI boundaries

New workflow `Universal Ledger specification` checks no runtime/source drift, frozen generation, design consistency and72 examples; read-only permissions, no deployment/secrets or DB. Existing #29 CI565 tests/Docker are **baseline evidence only** because this PR doesn't alter those paths; they are not rerun nor relabeled UEL runtime tests. CI URL and exact source/documentation SHA recorded in PR and [CI evidence](ci.json).

GO design discussion/new isolated implementation; NO-GO runtime, independent federation, public blockchain release, real users or production.
