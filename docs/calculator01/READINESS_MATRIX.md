# Readiness matrix

| Capability | Status | Evidence / remaining work |
|---|---|---|
| Exact #26 base + existing checksums | PASS isolated | source ancestry/unchanged app migrations; current manifest regenerated |
| Offline multi-source Decimal BTC engine | PASS isolated | ValueTests; deterministic demo;7 supported currencies |
| Historical/revaluation separation | PASS isolated | frozen value objects, separate rows and supersedes; no UPDATE API |
| Asset/resource canonical count | PASS isolated | one row; available portion; test equal-price different lots |
| Registration vs community consent | PASS isolated | draft exclusion, exact preview and actor-bound challenge |
| Read-only Help accounting | PASS synthetic integration | real existing Help auth/journal/witness/audience; no new lifecycle/cache |
| Counts/fulfillment share | PASS scoped | completed/disputed/withdrawn live reads; no global census |
| Partial measurement arithmetic | PASS pure function | `quantity_coverage`, overfulfillment rejected; not Help completion proof |
| Quantified Help partial fulfillment / BTC flow | NOT IMPLEMENTED | needs consented measurement model in existing Help |
| Identity-backed join stock + Help | NOT IMPLEMENTED | lab synthetic actors не production BLAGO participants; no permission migration |
| Durable stock/consent registry | NOT IMPLEMENTED | explicitly memory only, no file/restore support; future reviewed PR |
| Shared-worker stock concurrency | NOT EXECUTED / unavailable | no durable shared backend; sequential stale-version tests only |
| Migration/recovery for new app tables | NOT APPLICABLE | no new app tables/migrations. Future ledger-aware dispatch required |
| Independently verified resources/jury | NOT IMPLEMENTED | levels reserved, no self-upgrade from declaration |
| Physical asset dedup/title verification | UNKNOWN | distinct IDs/claims cannot establish physical uniqueness |
| Current market valuation | NOT EXECUTED | only synthetic pinned fixtures; no exchange/provider requests |
| Real source independence/signatures | UNKNOWN | fixture pins are configuration, not external proof |
| Production API / natural chat action cards | NOT IMPLEMENTED | Python API only; no routes registered; no model mutation rights |
| Whole-community privacy-safe analytics | NO-GO | current actor-view only; no public census/differencing review |
| Existing G04 | UNKNOWN | no policy/operator/external deletion approvals |
| Existing G09 | PASS inherited scope | no changes to memory/atomic history/recovery; complete regression required |
| Existing G11 | FAIL | deliberately synthetic-only; no pilot admission |

GO only for additional isolated Calculator design and testing. Real financial operations prohibited at every stage. Any real-user path needs owner decisions on scope/policy/custody, durable integration controls, independent evidence and existing release gates. BTC is arithmetic, never backing or an exchangeable BLAGO balance.
