# 08.2 threat model delta — Draft

The 08.1 profile and its 73 golden results are unchanged. This implementation is
**same-author local integration**, not independent audit or production authority.

| Threat | Implemented boundary | Residual risk / gate |
|---|---|---|
| ACK before write | Immutable complete record fsync, directory fsync, retained CAS, witness fsync, then Ed25519 receipt | Honest POSIX fsync/atomic rename/flock required; no physical power-loss qualification |
| Replay / single-use | Receiver flock covers load, validation and commit; inbox hash and mandate count in same record | Append snapshots are intentionally small-lab only; no scalable compaction |
| Crash between two checkpoint writes | Different heads cause UNKNOWN_RECOVERY | Availability sacrificed; no automatic reconciliation or destructive reset |
| App rollback | Both retained heads reference missing/corrupt record: UNKNOWN_RECOVERY | All app/control/witness copies rolled back together is undetectable without an external anchor |
| One control copy lost/rolled back | Missing/mismatched head locks out | Same-host directories are not independently retained infrastructure |
| Revocation race | Shared receiver lock, verified full owner prefix and retained frontier | No instantaneous global revoke; previously accepted exact duplicate returns historical receipt, no new effect |
| Authority fork | Root signatures, contiguous history, retained prefix agreement | Epoch 1 only; owner rotation/import remain unsupported |
| Receipt substitution | Frozen schema, exact expected binding, node pin, signature domain, checkpoint membership | Membership is a trusted local filesystem capability, not a remotely authenticated proof |
| Node signature mistaken for mandate | Distinct kind/domain; owner key separately pinned | Caller must not grant authority from receipt; no economic executor exists |
| Clock uncertainty | Missing supplied clock fails UNKNOWN_CLOCK | Tests use a synthetic qualified clock with uncertainty zero; real clock qualification remains open |
| Agent steals Human key | Spawned signer generates key in its own memory, only signs role-specific kinds | Same UID/admin/debugger/host compromise is outside model; no sandbox or HSM claim |
| Prompt or HTML injection | Payload is stored text, HTML escaped; no LLM, shell, tools, memory access | No production recipient integration is enabled |
| HTTP abuse | Loopback binding only, body/wire/history quotas, timeout, duplicate-key rejection | Thread-per-request is not public-server DoS protection; TLS/authenticated remote transport is absent |
| Key loss | No inferred owner key or admin recovery; no reset on startup | Human/Agent keys deliberately ephemeral; cannot continue signing after their process dies |

## Same-author security review

Reviewed commit/sign ordering, receipt hash circularity, lock scope, exact schema,
boolean/integer confusion, caller context injection and recovery bootstrap. Fixed:
receipt counter type checks; no implicit trusted wall clock; second retained
witness prevents undetected rollback of one control copy; staged orphan files do
not become inbox records. No production imports, SQLite, LLM or deploy hooks.

**Independent review: NOT EXECUTED.** Need separate implementer review, signed
remote checkpoint service with fresh nonce/anti-rollback anchor, independent
operator custody, qualified storage/clock and cross-host fault evidence before
independent A↔B GO. A compromised node host can forge node receipts or roll back
all local copies; this lab does not claim Byzantine-host resistance.
