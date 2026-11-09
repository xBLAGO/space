# Evidence — durable asset registry09.1

Source `34fb475455678dc024cf39f8fa27d2cd0c5af7dd`; exact base#27 `00c665b369acb6465f9afa795a6046c43f4107ce`. [Draft PR29](https://github.com/xBLAGO/space/pull/29). Final documentation HEAD/CI recorded in PR to avoid self-referential hashes.

| Artifact | Scope |
|---|---|
| lab-evidence.json |23-check same-app DB/users memory/history/Help/registry/restore demonstration; synthetic data and exact new DDL |
| source-identity.json | Exact source, new optional migration checksum, unchanged old migrations/deps, actual SQLite; no runtime secrets |
| channel_check.py / channel-evidence.json |6 supplemental local checks with installed registry, Bot handler and synthetic Mini session; not CI/physical proof |
| compatibility.json | Existing ledger/schema/dependency/deployment invariants and scope of reviewed code hooks |
| test-results.json |49 new test cases, full regression565, distinct local/focused/CI scopes and unexecuted scenarios |
| ci-source.json | Source Actions jobs/steps/counts/image/artifact; no private raw logs |
| gates.json | Inherited G00–G11 status scopes, new synthetic evidence only |
| evidence-index.json | SHA256 artifact hashes, not independent attestation |

Local source rehearsal initially compared raw main DB bytes before/after migration and failed: WAL can keep changes outside the main file. Replaced that incorrect assertion with the stronger logical scenario: pre-migration old backup → new record erase → restore refused → explicit additive migration/replay → old key denied. No expected security behavior/checksum was relaxed. Full tests must inspect SQLite including WAL or use SQLite backup API, never infer data state from main-file bytes alone.

No published DB snapshots, journal keys, witness contents, session tokens, secret env values or real identities. Artifacts are synthetic; expiration of an Actions artifact isn't proof of secure erasure. Same-author tests aren't independent review; independent-host recovery, live physical devices, real users/LLM and federation remain unverified.

Supplemental harness first incorrectly expected byte-identical `/link` reply; existing G09 intentionally returns SAFE_CONFLICT for command cache without a committed generation binding. Final assertion explicitly expects that safe response and also verifies one processed event and unchanged canonical statement; runtime protection was not changed.
