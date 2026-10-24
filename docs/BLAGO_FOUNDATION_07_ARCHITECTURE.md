# BLAGO Foundation 07 — Sovereign Human Identity (Architecture v0.1)

Status: architecture proposal only. **NO-GO for pilot, production keys, biometric storage and on-chain deployment.**
Base: Foundation 06.1, draft PR #10. Identity work must not be used to bypass Foundation 05.4 security gates.

## Mission and invariant

A human is not a wallet, a biometric template, or an AI-generated profile. An individual controls their digital identity. BLAGO acts as an accountable helper, not a sole custodian. The web and Telegram Mini App remain conversation-first.

Integrate three logically linked yet security-separated layers:
1. **Crypto**: verifiable control over keys, credentials and explicitly authorized operations.
2. **Bio**: optional local authenticator user verification using WebAuthn/passkeys; never collect face/fingerprint templates on the BLAGO server.
3. **Mind**: encrypted user-specific semantic memory with provenance, confidence, correction and deletion; never an authentication factor or proof of unique personhood.

**Never hash together biometric images, a semantic profile, and a public wallet address to create a permanent universal fingerprint.** Hashing public or guessable identity data does not confer anonymity. Avoid global public correlators.

## Identity model

- `blago_user_id`: current internal account reference (private mapping).
- `identity_id`: random, versioned internal identifier (not on-chain).
- `controller_keys`: public keys, credential IDs, purpose, status, registration and revocation timestamps; store no signing secrets.
- `identity_credentials`: issuer, issuance, validity, revocation status, schema, minimal claims; no plaintext personally identifying claims in the ledger.
- `wallet_links`: chain, network, public address, role, proof of ownership, status, key rotation history. One identity MAY link multiple wallets.
- `identity_commitments`: domain-separated, privacy-reviewed commitments; do not put a stable cross-context identity handle on-chain.
- `identity_events`: minimal, append-only local audit records, idempotency keys, versions, no secrets.
- `semantic_identity_refs`: references to encrypted memory records; never replicate personal memory into identity attestations by default.

L1 = proven key control. L2 = verified membership. L3 = separately evaluated person uniqueness. L1 and L2 **do not prove** L3. Invites do not prove unique personhood. Lack of L3 must not automatically block ordinary human assistance.

## Authentication, biometric verification, and wallets

Use existing Foundation 05 IdentityRuntime as authentication authority for web/passkeys, without interpreting WebAuthn success as a biometric sample or proof of unique personhood. Platform authenticators can use biometric or PIN depending on their configuration. Verify origin, RP ID, challenge, user verification, sign counters/clone indicators where applicable and session binding.

Passkeys (often P-256) are not interchangeable with Solana Ed25519 account private keys. Foundation 07.1 must **not generate server-custodied Solana private keys**, create live wallets, or transfer assets. Subsequent wallet work must select and security-review an explicit design:
- external self-custody wallet with challenge signature; OR
- audited programmable account authorization with narrow, revocable passkey-controlled permissions.

AI cannot authorize privileged writes, asset transfers, delegated spending or publication based on inferred conversational intent. High-impact actions require dedicated structured confirmation, fresh authentication where necessary, explicit scope/expiry and verifiable audit event.

## Consent and memory

User can inspect, edit and delete semantic memory; extraction must record confidence and source and preserve distinction between fact, desire, hypothesis and inferred attribute. Sensitive memories remain subject to existing restrictions and explicit policies. No semantic embedding, summary, biography or its global hash is published on-chain. Sharing uses purpose-bound opt-in and minimized disclosure.

Account erasure must remain compatible with Foundation 05.4 privacy/recovery policies. Verify effect of backups, immutable audit trails, wallet-link revocations and eventually public commitments before enabling production. Never claim deletion of immutable blockchain information.

## Key loss and recovery

Recovery cannot rely solely on a BLAGO administrator, an AI decision or a single shared secret. Threat-model separate device loss, credential theft, insider compromise, malicious recovery attempt, compromised backup, lost passkey sync and account collision. Design recovery as stateful, delayed and notifiable, with high-risk revocation, dispute and audit, subject to privacy and accessibility review. No production recovery credentials are issued in 07.1.

## Proposed Foundation 07.1 deliverables (offline only)

1. Formal threat model, trust boundaries, data-flow diagrams, linkage/privacy review, and choice of DID/VC representation justified against current deployment.
2. Additive SQLite schema and reversible migration for identity IDs, public controller metadata, wallet **link metadata only**, minimal credential status, identity events and explicit consent. Keep existing `blago_user_id` and Telegram/Web identity mapping intact.
3. Typed domain interfaces for identity lifecycle and key ownership challenge/verification with **mock/test public keys only**; avoid writing signing-key material.
4. Idempotent create/link/revoke/rotate event semantics; permissions and authenticated access boundaries; do not expose public user search.
5. Feature flag default OFF; no external chain writes, no network operations, no actual biometric data acquisition.
6. Unit, migration, concurrency, rollback, security, authorization and isolation tests, including duplicate registration, replay, mismatched account, revoked keys, recovery collision, deleted accounts and legacy backup restoration.
7. Documentation: migration, compatibility, threat model, failures and deployment safeguards.

## Foundation 07.2+ (not authorized by 07.1)

- Wallet design / Solana devnet prototype after a reviewed signing and custody model.
- VC issuance/revocation, privacy-preserving presentation, pairwise identifiers and unlinkability tests.
- Optional L3 uniqueness verification with independent recourse; no mandatory centralized biometric database.
- Encryption / per-user key lifecycle and memory capability sharing; separate security review.
- Independent audit of contracts, recovery, and real-device Telegram WebView/passkey behavior.

## Acceptance gates

- 07.1 is limited to architecture, offline interfaces and migrations; zero production credentials, chain transactions, or biometric collection.
- CI passes including Foundation 05.4 and 06.1 regression suites. Verify against real branch baseline and document environmental limits.
- Identity changes do not mutate existing auth/session/CSRF, privacy deletion, memory isolation and backup/restore behavior.
- Static and integration tests confirm cross-user isolation, authorization/replay defenses and absence of sensitive/public stable correlators.
- A draft PR with a clear GO/NO-GO decision for *further isolated testing*, not pilot readiness.
- Pilot remains NO-GO while HF timeouts/template quality, HF storage/custody, retention, WebView/passkeys and backup/legacy gates are unresolved.

## Design rationale

Identity must be provable, revocable, recoverable and user-controlled. Biometric authentication is local device verification rather than immutable personhood. A changing memory model must never become a permanent public identity fingerprint.
