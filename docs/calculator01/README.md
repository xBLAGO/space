# Calculator01 evidence

Exact base#26 `34ed8a6c47eb7c90f7eaa5a6807bb1eee78f97a8`; final source `cae6337157fa55d6a5cb4bb9e25fce9712f1975e`. Documentation HEAD/CI recorded externally in [Draft PR27](https://github.com/xBLAGO/space/pull/27) to avoid a self-referential hash.

| Artifact | Scope |
|---|---|
| source-identity.json | Exact final source/dependency/runtime/migration identity; no deployment attestation |
| compatibility.json | Existing app migration/schema/Privacy/G09/Web/deploy/dependency files byte-identical to base |
| lab-evidence.json | Eleven deterministic stock/consent/Help checks; synthetic quote/measurement example; exact in-memory SQL schema/checksum |
| test-results.json | Per-case expected/actual, final focused44; initial full local514; final full source CI516 PASS |
| ci-initial.json | Earlier e4d445b source:514 tests, Docker and original smokes PASS; not final clock/type source |
| ci-source.json | Exact final source CI jobs, test count, Docker image and artifact identity |
| gates.json | Inherited gates unchanged; new lab scope/remaining gaps only |
| evidence-index.json | SHA256 index; not independent attestation |

The initial focused run found four errors from using `id` instead of existing `help_events.event_id`; fixed before first source commit, without changing Help schema or expected golden results. Review also found missing updated_at on revoke; final source fixes monotonic timestamps/typed fields and adds two negative cases. No suppressed test failures or changed migration checksums.

Only synthetic non-personal values are included. No DB snapshots, session tokens, invite credentials, raw journal/witness, private keys or provider secrets. CI artifacts expire; expiry is not proof of secure deletion. Same-author testing is not an independent review. New stock registry is memory-only; actual shared-worker persistence, external sources, physical clients and real-user admission remain unavailable/unverified.
