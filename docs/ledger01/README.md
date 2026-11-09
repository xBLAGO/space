# UEL0.1 design artifacts

Base exact #29 `c3b4c5b4fd37a5e16e09b19cdbcf494183963bf5`; source boundaries and pinned #27/07/08/Help code: baseline.json. No production implementation/migration is included.

- [Main Draft](../BLAGO_UNIVERSAL_EVENT_LEDGER_0_1.md), [authority/event contracts](AUTHORITY_AND_EVENTS.md), [catalogue](event-catalog.json), [inert wire schema](event-envelope.schema.json).
- [Projection](PROJECTION.md), [privacy/anchors](PRIVACY_AND_ANCHORS.md), [threat model](THREAT_MODEL.md), [migration plan](MIGRATION.md).
- [Conformance](CONFORMANCE.md):72 offline examples PASS,22 future protocol scenarios NOT EXECUTED.
- policy/test_keys/golden/wire/projection/valuation/merkle JSON and vectors-manifest freeze synthetic bytes/results; no production keys or real-user data.
- fixture_codec/fixture_projection are single same-author documentation examples, not imported by src. generate_vectors --check never changes corpus; check_vectors returns WIRE_OK_NOT_AUTHORIZED, not delivery ACK.
- results.json records local scope; ci.json records Actions for the design checks; evidence-index.json hashes documents/examples, not an independent attestation.

No new runtime collection or public root publication. No chain transaction. No irreversible migration. No merge/deploy. All existing gates remain scoped; real-user/production decision NO-GO.
