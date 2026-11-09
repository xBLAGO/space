# Migration / adoption plan — no applied migration

This PR changes docs and one documentation-only CI workflow. Existing src/tools/tests/manifests/dependencies/Docker/deployment stay byte-identical to base#29 `c3b4c5b4fd37a5e16e09b19cdbcf494183963bf5`. No backfill, SQL, branch merge or production release is executed.

| Stage | Base/dependencies | Work and mandatory evidence | GO/stop/rollback |
|---|---|---|---|
|0 Design review | exact #29 + inspected #27/#11–17 | Review this Draft, event catalogue, privacy/evidence/Help boundaries; reconcile with roadmap #28 | GO offline only; reject ambiguous authority semantics |
|1 Shadow local adapters | reviewed runtime source34fb475,565 base tests | New small PR: source namespace/coverage and unique canonical source refs; event index/outbox in same existing domain tx; no dispatch | Require Help/asset state and count parity, consent/G09/deletion/retention/crash tests. Flag OFF; drop shadow read model only after preserving current denial controls |
|2 Explicit additive schema | stage1 approved | Proposed tables: `uel_source_bindings`, `uel_event_index`, `uel_payload_refs`, `uel_export_outbox`, `uel_import_inbox`, `uel_effect_keys`, `uel_frontiers`. Final DDL/checksums require review, not frozen by this document | Exact predecessor validation, repeat migration, unknown legacy/quarantine, account delete/merge, full old-backup test. No old checksum edits or arbitrary ALTER loop |
|3 Owner authority/disclosure |07 architecture/core and separately qualified recovery; current09.1 has session observations only | Distinct owner/Agent/Node keys, reviewed portable endorsements/mandates and egress consent, revocation/epoch/current-status schemas | No inheritance from local session or LAB_COMMUNITY. Unknown owner continuity → stop. No admin recovery fallback |
|4 Two-node inert exchange | stage3 +08 verified concepts, **new** profile | Implement receiver-owned nonce, durable inbox/use/frontier and exact receipt schema; import read-only source observations, never Help commands | Repeat new conformance and all crash/fork/replay/restore/revoke tests. Two local processes = local integration only |
|5 Independent A↔B | stage4 plus independent operators/hosts/custody | Separate keys/volumes/transport/out-of-band pins, independent implementations and cut/reducer parity; sender-receiver deletion uncertainty | S12 evidence mandatory. Loss/correlated rollback → UNKNOWN_RECOVERY, no new action. No production federation GO from old08 tests |
|6 Public release/anchor lab | independent privacy/audience/retention/governance approval | Separate release schema/barrier, no private roots; review chain/network/finality/fee/upgrade and contracts; synthetic offline first | No mainnet/deploy/transactions without separate approval. No private payload migration on-chain; absence of safe release policy = NO-GO |

## No automatic historical re-signing

#29 actual history is session-confirmed/node-observed. Do not backfill it as Human-signed events. Original source event IDs/revisions and approved coverage can be referenced via new opaque scoped mappings; ambiguous sources/old merges stay quarantined.

A bootstrap can create `source.snapshot.observed` at an explicit cut with `history_before_cut=UNKNOWN`. It must not manufacture missing Help transitions, confirmations, quantities, ownership or private publication consent. Existing #14 Genesis asset/need declarations cannot be counted alongside #29/Help until one-to-one source equivalence is reviewed. Prototype fixture keys/identity bundles are not importable production keys.

## Local atomic adapter contract

Inside existing BEGIN IMMEDIATE validate source authority/current version, mutate source once, insert uniquely mapped local audit header and protected payload ref, enqueue export **only if permitted**, then original PrivacyControl.flush and SQL commit. Do not sign/send on an uncommitted preview. Postcommit exporter reads committed rows and uses stable message/source IDs across retries; receiving ACK commits delivery metadata only, not another economic occurrence.

A source mutation remains valid without successful network delivery; no optimistic remote success. A shadow index is not a second Help write store. Positive audit/event copies cannot bypass source holds, recipient context fencing, retention or G09 CAS.

## Recovery and rollback gates

Before any runtime PR: seed exact old baseline with identity/history/memory/Help/assets, explicitly migrate test copy, repeat migration, compare unchanged canonical IDs and source counts; interrupt every write/journal/commit/export/ACK boundary; replay an old app backup with current controls; drop/rollback each independent copy; reject forked/unqualified frontiers. Rerun all existing565 tests plus source-specific new regressions/Docker/browser where affected. Synthetic proof remains scoped.

Keep user payload, minimal denial journal, replay/effect consumption and authority fronts in documented custody domains. A future vault key rotation/deletion needs its own backup/restore review; don't assume crypto-shredding of arbitrary copies.

Rollback is stop/disable new export/adapter flag, preserve authoritative source and current controls, recover with a forward-compatible reader. Never restore old app+old journal to erase revokes or deploy old binary that cannot understand new critical intents. There is no implicit down migration and no automatic merge of PRs #11–17/#27/#29.

## Real-user conditions unchanged

Owner-approved policy/retention/audience, qualified target-host SQLite/fsync/storage, independent custody/DR and security/privacy review, physical WebAuthn/WebView/Telegram, live provider quality, secure UI confirmations, legacy/merge disposition and all release gates. G04 UNKNOWN/G11 FAIL remain. Next GO is only further isolated implementation/review, never pilot/deployment from these documents.
