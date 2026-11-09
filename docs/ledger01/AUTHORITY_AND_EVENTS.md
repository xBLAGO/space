# Detached authority and event contracts — design-only

These required field sets are **proposals**, not additional accepted wire kinds in the executable inspector. Unknown kinds must fail closed. First lab wire example accepts only inert NODE `source.observation`; it cannot validate grants, endorsements or delivery receipts.

## Shared artifact framing

Future artifact profile `uel-authority-design-0.1` must be reviewed/frozen before use. Each signed object has exactly alg/body/signature; body includes profile, kind, network, policy_ref, issuer, issuer_key and schema_ref. Ed25519 domain is reconstructed from the **validated artifact kind/profile**, never supplied by sender. Proposed suffixes: ENDORSEMENT, MANDATE, AUTHORITY-EVENT, AUTHORITY-STATUS, DELIVERY-RECEIPT, REVOCATION-RECEIPT, CHECKPOINT, PUBLIC-RELEASE, PUBLIC-ANCHOR. Artifact refs bind complete signed object in its own OBJECT domain. Existing BOP and07 proofs retain their original domains/encodings and do not cross-validate as these artifacts.

Policy/document/schema commitments need independent out-of-band pinning and role enrollment. A field called `issuer_key` isn't a trust anchor. No new cryptographic algorithm, password recovery or universal admin signer is proposed.

## Human endorsement / disclosure authorization

Beyond shared fields, exactly: endorsement_id, identity_ref, authority_epoch, authority_frontier_ref, event_ref, object_ref, object_version, purpose, operation, recipient_nodes, payload_ref, not_before, expires_at, approval_nonce, decision.

- Human controller independently authorized for this operation signs. Node/session/Agent cannot sign in Human role.
- decision is endorse or withdraw. Endorse binds exact event/object/version/payload and sorted recipient set/purpose. A new text, broader audience, higher limit or different action needs new approval.
- An assertion endorsement and permission to disclose/execute are different purposes. `SERVICE_OBSERVED_CONFIRMATION` remains a Node statement, not a forged Human endorsement.
- not_before/expires_at are conservative bounded intervals, never inferred from created_at of an old assertion. approval_nonce and endorsement_id consumed once per defined action; duplicate gets historical receipt or denial, never another effect.
- WITHDRAW is terminal for that reference. A future separate endorsement may authorize a new scope; old bytes never reactivate the withdrawn one.

## Agent mandate

Beyond shared fields: mandate_id, owner_identity, owner_epoch, owner_frontier_ref, agent_ref, agent_key, operation_set, object_ref, purpose, recipient_nodes, not_before, expires_at, allow_delegation, parent_ref, limits.

Initial proposal: allow_delegation=false,parent_ref=null, max_uses=1, max_payload_bytes≤2048, lifetime≤3600s. Operation set must be exact approved catalogue operations, not arbitrary tools, role names, wildcard assets or all recipients. Any quantity bound includes decimal quantity/unit and source revision; no implicit spending/financial capability. Current inert observation profile grants **no** transfer/payment/Help mutation at a receiver.

Mandate verification requires owner-controlled issuance plus current signed status/log, not possession of Agent key or local session. Human key stays independent of Agent. A Node may relay owner status, not originate it with its receipt key. Delegated status signers/delegation chains are NO-GO until separately specified.

## Authority log/status

Authoritative identity/controller changes remain Foundation07's reviewed mechanism, not a new UEL identity registry. Mandate issue/revoke retains08-style monotonic owner log; UEL `identity.authority.observed` is only a mirror. An epoch transition needs full independently verified key/recovery evidence.07 possession alone does not prove current membership, uniqueness or valid recovery. Unknown epoch/key history is UNKNOWN_KEY_HISTORY.

Proposed authority-event fields beyond shared: identity_ref, epoch, sequence, previous, operation, target_ref, transition_proof_ref. sequence increments exactly1, previous binds prior signed event/genesis; same sequence/different hash is a fork. A receiver's retained prefix cannot be replaced by an earlier snapshot or competing root. Mandate revoke is terminal for the reference; same ref cannot be issued again.

Proposed authority-status fields beyond shared: identity_ref, epoch, sequence, head, mandate_ref, mandate_state, event_ref, recipient_node, receiver_nonce, issued_at, expires_at. Owner/current authorized status role signs, exact event/recipient/receiver-owned unpredictable nonce bound, lifetime≤30s. Full verified history agrees with head/status, contains retained floor, current key and grant scope. At clock estimate t and qualified error s≤5: start≤t−s and t+s<end. Unknown clock/frontier/connectivity is not positive authority.

This reuses08's **semantics**, not its frozen note schema. New economic/source schemas, status services and stronger quorum claims require new vectors and tests. A byzantine owner can equivocate to uninformed nodes; prevention/detection requires retained evidence and independent cross-check, not an invented global sequence.

## Durable receipts

Proposed delivery receipt beyond shared fields: receipt_id, receipt_schema_ref, event_id, event_ref, mandate_ref, receiver_nonce, authority_epoch, authority_sequence, authority_head, decision, inbox_commit_index, retained_checkpoint_ref. decision is recorded-inert-observation, not executed-command or owner-consent. Bind receiver issuer/key through independent Node pin.

Revocation receipt additionally/replacement binds revoke_event_ref and revoke_sequence/head, exact grant and caller challenge; decision=revocation-recorded-at-this-node. Delivery/revoke share the persistence serialization lock. A Node cannot sign a receipt before successful durable checkpoint/witness/SQL commit. Blueprint reserved in committed state excludes its own final signature/hash to avoid circular hashing, then deterministic signature may be regenerated on retry. Duplicate returns identical historical receipt; it isn't fresh authority and cannot rewind source/authority fronts.

Missing receipt after network loss is pending, not proof of failure or success. Receiver may already have committed; retransmit same ID/bytes. Reusing the economic source event under a fresh transport ID does not create another stock/flow. A signed deletion receipt records an honest-node operation report, not proof every disk/client copy was physically erased.

## Evidence qualification

Attestation required design fields: attestation_id, predicate_schema_ref, assertion_ref, exact predicate/measurement, attester_ref/key/role, evidence_refs, independence_basis_ref, scope/recipient/purpose, issued_at/expires_at, withdrawal_ref where applicable. Sign ATTESTATION domain (must be added only in a reviewed future profile).

SELF_DECLARED identifies provenance. COUNTERPARTY_CONFIRMED needs the intended counterpart and exact occurrence/measurement; a Node report of two sessions must be labeled that way. INDEPENDENTLY_VERIFIED needs a valid predicate/observer role and reviewed independence basis, not raw key count. DISPUTED/REVOKED status is orthogonal, not a higher confidence score. Arbitrary third-party counterclaims do not rewrite source Help or authorize erasure of someone else's records.

Correction/retraction references exact assertion/attestation lineage, preserves necessary lawful history and follows personal-content deletion policy. If evidence for a source Help completion is withdrawn, project evidence eligibility transparently; don't silently invent a second Help state machine or financial reversal transaction.

## Full source observation payload (design-only semantic schema)

Required fields: format=`blago.source-observation.v1`, topic, source_namespace_ref, source_schema_ref, source_object_ref, source_event_ref, source_revision, source_action, source_state_ref, coverage_ref, evidence_basis, subject_mapping_ref, protected_claim_ref, consent_ref. All refs are independently scoped/authenticated hashes or opaque handles, not raw uid or a new person registry; unknown mapping is not an equivalence proof.

`source_namespace_ref` pins the authoritative domain adapter/source, `source_event_ref` identifies the one occurrence irrespective of observer signatures/transport IDs. `source_revision`/state/coverage identify which existing committed transition is observed. `evidence_basis` explicitly distinguishes NODE_OBSERVED_SESSIONS / HUMAN_ENDORSED / PROVIDER_REPORTED, never upgrades one to another. `protected_claim_ref` is usable only with existing source access/disclosure authority; a content reference isn't a bearer token. `consent_ref` pins exact purpose/audience/version, not a universal permission.

Per-topic schema must add the minimal permitted source fields and actor/evidence predicate, with unknown keys rejected. Help topic binds the **existing** Help event/request/selected-offer revision and source state; it cannot synthesize actions or mutually confirmed quantities. Asset topic binds canonical lot/version/rights and availability consent, not another RESOURCE identity. Memory/chat topics remain LOCAL and should expose no body or personal hash by default. Unknown payload/type/version stays in quarantine, never eligible arithmetic input.

This full semantic payload is **not** accepted/implemented by the inert lab inspector, whose payload is `text.utf8.v1` under `blago.inert-observation-frame.v1`. A later semantic profile needs its own schema hashes, independent validators, authority and source-coverage vectors before activation; do not relabel a passing wire example as a typed event validator.
