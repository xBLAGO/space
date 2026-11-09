# Unified MVP readiness matrix

Observed main `3c218dc5757879c26b3aec520af3636b2d702715`; base#25 `0772e5bc5919aefac2c209f4e40fe5ea790d6792`; candidate source `395ecf10f9187663fddb64795597e7efe0f014fa`. Implemented означает только проверенный synthetic scope. Production deployment/operating evidence не выводятся из CI.

| Компонент | Main / candidate | Evidence и код | Реальный scope / приоритет |
|---|---|---|---|
| Telegram + SQLite | Main: basic implemented; candidate: extended | [storage](../../src/xblago/core/storage.py#L28), Telegram tests, journey38 | Production bot version UNKNOWN; не вызывался. P0 provenance |
| Web/Mini App | Main missing; candidate implemented synthetic | [Web handler](../../src/xblago/integrations/web.py#L1319), browser17/mobile14, journey | Public-domain /app404; physical WebViews NOT EXECUTED. P0 release / P1 UX |
| Invitation | Main no Web lifecycle; candidate shared code | [register](../../src/xblago/integrations/web.py#L292), bad/valid invite checks | Individual invitation expiry/revoke/issuer tracking missing. P1 owner scope |
| Identity / linking | Candidate implemented account authority | [session guard](../../src/xblago/integrations/identity.py#L78), signed initData, Bot link, reauth | Not unique human, passport or Sybil resistance. Physical passkeys not qualified. P0 |
| Personal history | Both versions; candidate atomic/fenced | [append_exchange](../../src/xblago/core/storage.py#L415), channel/restart/G09 tests | Help events separate; no automatic chat status cards. P1 |
| Semantic memory | Main basic key/value; candidate versioned | PAA/semantic_memory/memory_commands, tests.unified, old memory suites | Regex-only offline cannot infer arbitrary interests/skills; live extractor quality UNKNOWN. P0 model / P1 coverage |
| Conversation Engine06.1 | Main absent; candidate integrated | [understand](../../src/xblago/core/conversation.py#L85), 40 scenarios/50turns, generation failures | Hints are tentative; local fallback weak. Real naturalness NOT EXECUTED. P0 |
| Help consent lifecycle | Main absent; candidate implemented | [Help](../../src/xblago/domain/help_service.py#L165), HTTP journey38, browser17 | Single selected helper in Help01; multi-helper future. Mutual confirmation is user testimony, not external proof. P0 acceptance |
| Conversational Help companion | Candidate PARTIAL | [suggest_draft](../../src/xblago/domain/help_service.py#L51), chat payload | Needs panel/API for every action; no verified read projection into agent. P1 or P0 if chat-only admission criterion |
| NEED / RESOURCE views | Candidate legacy tables + separate Help | Web create_item/analytics + Help feed | Different objects; dashboard doesn't reflect Help request as legacy NEED. Unification missing. P1 |
| Collective fulfillment / well-being | Candidate MISSING | [feed personal count](../../src/xblago/domain/help_service.py#L122), Web analytics | No validated community completed counter/well-being metric. Never infer human welfare from counts. P1 |
| Privacy export/delete/retention | Candidate integrated, G04 UNKNOWN | PrivacyService, lifecycle sweep, ops Runner, inherited/new tests | Approved policy, copies/provider/logging and target-host SLA not qualified. P0 |
| G09 concurrency | Candidate PASS scoped | context_fencing/schema, old multiprocess tests + new one DB journey | Only installed context migration + original controls; no workload/device qualification. P0 retain |
| Recovery / custody | Candidate local tests PASS | journal/witness/custody, old backup denial, crash86/replay | Independent failure domains/key custody NOT EXECUTED. P0 |
| Migrations | Ordered main synthetic upgrade PASS; overall PARTIAL | main_upgrade_check.py + main-upgrade.json | Legacy Help reapply refuses after newer schemas; ledger-aware dispatcher needed. Real legacy inventory UNKNOWN. P0 before real upgrade |
| Docker / release controls | Candidate verified disposable image | CI tests/docker, sqlite_safety, [preflight](../../src/xblago/core/integration.py#L50) | Default launcher synthetic-only. Main auto-deploy workflow still old. P0 |
| Economic source | Present legacy methods/tables | [route deny](../../src/xblago/core/integration.py#L27), tests.test_integration_candidate | Financial routes blocked only in approved integration surface; no new financial operations here. P0 scope |
| Sovereign Identity / Genesis / federation | Main/candidate excluded | PR12/13/14/17 files exist only on separate branches | Experimental flags/schemas rejected; not needed for first nonfinancial MVP. P2 separate lab |
| Public site | Separate observed marketing surface | public-site.json + linked live pages | Token/deposit/NFT/asset-management claims conflict with scope; source repo UNKNOWN. P0 communication |

## PR inventory and disposition

Full files/base/head/checks: `pr-inventory.json`; Git facts: `repository-audit.json`.

| PR | HEAD prefix | Current candidate relation | Disposition |
|---|---|---|---|
|10|d11a2a55|Ancestor, natural conversation source present|Already integrated; don't reimplement|
|11|2d6d6591|Sibling architecture doc|Optional reference, not runtime prerequisite|
|12|b79cb603|Separate Identity Core,11 conflict markers|Exclude; future FK/merge/delete/recovery adaptation|
|13|d96ade93|Separate recovery,11 markers|Exclude, independent infrastructure blocked|
|14|483475fd|Separate Genesis,18 markers|Exclude; new public-consent semantics outside MVP|
|15|03cdbf82|Separate federation audit|Reference only|
|16|0a6acd91|Separate conformance|Offline lab only|
|17|44a5d52a|Separate durable federation lab|No production import; independent operators not proved|
|18|6f83b66d|Docs-only sibling|Use as historical audit, Integration02 rechecks facts|
|19|837b2bfd|Only runtime d7f619a is ancestor; later four files docs|Do not replace tested runtime with docs HEAD|
|20|417e12be|Ancestor|Help included|
|21|e65a696c|Docs-only sibling, no code files|Review UI concept separately, no release authority|
|22|77c7f7f0|Ancestor|Lab/gates included|
|23|7807f5aa|Ancestor|G09 included, preserve|
|24|80e60b47|Ancestor|Lifecycle included, G04 still UNKNOWN|
|25|0772e5bc|Exact base|Ops scheduler included, default OFF|

All16 observed PRs are open Draft, none merged; own-base CI green for code PRs, no checks for some docs-only PRs. This is provenance, not merged MVP readiness. Zero text conflicts for docs/federation branches do not authorize their inclusion.

## Security/release gates

| Gate | Candidate / real pilot decision | Remaining evidence |
|---|---|---|
|G00|PASS synthetic exact-source scope|Image promotion/production approval UNKNOWN|
|G01|PASS candidate workflow code only|Main still push-deploy; environment protection/admin settings UNKNOWN|
|G02|UNKNOWN|Actual target storage/SQLite WAL/fsync/flock and host qualification|
|G03|UNKNOWN|Independent off-host custody/witness/key recovery drill|
|G04|UNKNOWN|Owner policy, all copies/logs/providers, SLA/on-call, legacy dispositions|
|G05|UNKNOWN|Physical Telegram iOS/Android/passkeys, real onboarding|
|G06|UNKNOWN|Approved live provider quality, factuality, latency/cost and outage review|
|G07|PASS only synthetic Help scope|Human acceptance; guided-panel vs chat-only decision|
|G08|UNKNOWN|Independent review, logging redaction/canary, metadata re-identification|
|G09|PASS only activated synthetic fencing|Preserve controls; capacity/device/real-host qualification not added|
|G10|UNKNOWN|Operators perform admission/stop/rollback/delete incident drill|
|G11|FAIL|No approved real-user runtime/manifest, synthetic-only guards intentionally retained|
