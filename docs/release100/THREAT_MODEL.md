# Deployment threat model

Scope: release preparation control plane. Not Agent Context, Identity, Capital or
Federation. Repository/GitHub control-plane administrators remain trusted actors;
this code does not defend against the owner replacing the reviewed main policy.

| Threat | Code/control | Residual/manual condition |
|---|---|---|
| main push / docs-only merge deploys | HF upload removed; manual-only preparation | Disable old workflow before first merge and cancel old runs |
| PR/fork executes with production token | Safe CI read-only, no secrets/environment | Repo/org tokens must be removed from branch access by owner |
| Branch edits workflow to bypass if | Guard checks exact event/ref/workflow source; no publisher | YAML is not a boundary; environment/main/credential restrictions mandatory |
| Wrong source/config/image claim | Exact current main SHA, config/baseline/Git blob validation | Only source bundle; runtime image/dependencies/storage UNKNOWN |
| Artifact from failed/fork/old run | Successful push/main CI and repo/path/head binding | GitHub API and Actions storage trusted; no independent SLSA attestation claimed |
| Zip/tar injection or payload execution | Strict members, bounded reads, hashes, no extraction/execution | Trusted Python/stdlib/parser supply chain |
| Token leaked on redirect | Strip Authorization across hosts, HTTPS/host restrictions | Public signed artifact URLs not logged |
| Insufficient permissions | admin/maintain API check, UNKNOWN denies | Environment approval/owner settings require separate proof |
| Duplicate/conflicting/concurrent prepare | Atomic create-only metadata ref, no force/delete; fixed concurrency | Tag deletion/admin bypass defeats history; immutable rules required |
| Lost network response after reservation | Ambiguous error, retry rejected if ref exists | Owner inspects immutable intent; never silently resets |
| Implicit storage migration | Release helper never imports app or opens SQLite | Baseline app constructor has startup DDL; deployment remains NO-GO |
| Green tests taken as deployment consent | production_authorized=false, publisher absent | Future publisher must not trust tags or CI alone |
| Emergency variable changed mid-run | Stops later starts only | Cancel active jobs; variable is captured context, not live kill switch |

Preparation writes only future explicit Git metadata tags, never a GitHub Release,
HF Space or DB. Current tests use fake API: no real tag/dispatch/settings write.
HF metadata is read-only. Public actor/run IDs and source hashes are audit metadata,
not user conversation or secrets. GitHub platform protections are NOT ATTESTED.

A write-capable unrelated workflow could squat a preparation tag. That is a denial
of service, not production authorization: existing refs are always rejected and
no publisher consumes these tags. Future publication requires independently
verified main workflow/environment provenance, never trust tag claims alone.
