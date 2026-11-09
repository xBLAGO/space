# 09.1 threat model / review delta

Same-author review, not independent security audit. Assumptions: authenticated current identity runtime; patched SQLite/WAL/FULL; honest protected process; retained journal/key/head/witness not jointly rolled back. Only fresh synthetic DBs and keys used.

| Threat | Control / evidence | Residual risk |
|---|---|---|
| Arbitrary uid / foreign object ID | Existing fresh session; owner derived in transaction; same404 for unavailable object | Compromised trusted process/session outside proof |
| LLM turns mention into grant | Pure tentative suggestion; private DRAFT; exact version/body/audience preview and separate confirmation | No production action bridge yet; don't expose tokens to model |
| Replay / lost ACK | Unique(owner,key), immutable initial hash, sealed challenge and stored receipt; committed version checked before returning receipt | Retention may remove old receipt and deny retry; never repeat action |
| Challenge/body/session substitution | HMAC binds nonce hash, owner, asset, version, session, body, expiry | Host/HMAC-key compromise not solved |
| Receipt set without commit | Lookup committed DECLARE/REVISE body and revision, validate global retained fingerprint | Signed public receipt not implemented |
| Concurrent grant/revoke | BEGIN IMMEDIATE version check + effect + receipt; multiprocess race tests | No multi-primary/multi-host proof |
| Corrupt/delete history or detached legacy edits | Fingerprint canonical+metadata+all versions+operation provenance checked before and after every guarded write | Direct DB/host attacker can cause denial of service |
| App rollback / lost SQL commit after journal | Conservative erase + retained hold; no ordinary positive write replay | Availability/history loss possible; never pretend successful recovery |
| Entire old schema / downgraded binary | asset_state requires migration even flag OFF; new installer explicit; old binary not safe against new schema | Pre-Context/Lifecycle coordinated restore still unqualified |
| User/account erase / retention resurrection | Canonical DELETE trigger journal intent + cascade + holds; old snapshots replay erase; new context revision | Physical pages/backups/client/provider copies not certified erased |
| Stale generated context | Additive triggers invalidate same owner revision/epoch; multiprocess held-LLM test | Asset reads not yet in actual model context; cross-user cached agent context must never be added without recipient fencing |
| Improper merge | Existing unknown-ownership FK guard rejects source with claims and rolls back | Full asset-aware merge not implemented; supported linking needs further UX proof |
| Public/private disclosure | Owner private history; other active members only ACTIVE LAB_COMMUNITY current body; no internal uid in view | Community audience includes future admitted members; requires explicit policy before real users |
| False evidence / wealth inflation | Constant SELF_DECLARED; no verified flag accepted; known/unknown separated, no physical double-count guarantee claimed | Dishonest claims, title uncertainty, Sybils, physical duplicates remain |
| Stock/flow/need confusion | Resource is availability slice, Help lifecycle unchanged, BTC/flow unknown | No durable valuation or matching/fulfillment allocation yet |
| Aggregate inference | Actor-qualified view only; no public endpoints/new activity collection | Small group/differencing review not complete |
| Old secrets / financial activation | Existing routing/release guards/dependency pins; local provider only; no payment paths | Prior legacy internal methods remain outside approved surface |

There is no public cryptographic ownership claim, blockchain deployment, jury, automatic allocation or administrative property transfer. Independent federation cannot verify an HMAC it does not own; don't export the key or treat local session as mandate.

## Atomicity argument

Registry actions, account purge and lifecycle canonical DELETE share the same SQLite write serialization. Auth/version/hold checks precede mutation inside BEGIN IMMEDIATE; global post-check covers pending asset_state outbox; original PrivacyControl.flush writes external controls before SQL commit. Concurrent writers see either committed new version or old pre-transaction state; a stale generation's CAS cannot commit through a completed root change. WAL crash before external intent rolls back ordinary writes; after durable intent, mismatch purges/holds; after SQL commit retry returns original receipt. Tested points are explicit crash hooks, not proof of every filesystem/hardware failure.

Before including someone else's shared asset in an agent's LLM context, fence **recipient** context on audience/availability change.09.1 does not add shared asset data to any LLM context, so only owner context is invalidated here. A future cross-user context cache without new recipient fencing is NO-GO.
