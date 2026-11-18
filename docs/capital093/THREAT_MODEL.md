# Threat model delta

Assets: original signed capital history, UEL references/checkpoints, member rights,
private audit packages, custody keys/config epochs, independent retained floors.
Principals: existing Human GOV/approver/controller != Agent != source Node !=
three custody Node operators != independent offline verifier.

| Attack/fault | Enforced response | Residual limitation |
|---|---|---|
| One malicious operator rewrites history | Other honest prepares / all3 certificate reject; verifier detects prefix/signature/floor mismatch | All nodes controlled by same author in tests |
| Two-node minority colludes for normal checkpoint | No3/3 certificate | Availability unavailable under partition |
| Fewer than governance threshold human approvals | No custody rotation | Signatures do not establish independence of humans |
| Unilateral key reset / rotation | Requires human quorum +2 old nodes, exact parent and delay | Fully colluding quorum/compromised custody keys can authorize bad changes |
| Valid signed but stale checkpoint | External floor + fresh signed node statuses | All evidence in same rollback domain is not independent |
| All local copies rolled back | Independent floor rejects | Loss of ALL external knowledge cannot be cryptographically detected; verification must stop |
| Hidden newer prepared event | Pending status blocks fresh assertion / recovery | A permanently unavailable preparer may prevent completion; no unsafe auto-abort |
| Replay/receipt substitution | Exact package/certificate/hash/config/nonce bindings | Challenge generation/clock and floor custody must be operated correctly |
| Crash/fsync/disk failure | No ACK before DB+control; mismatch UNKNOWN | Injected EIO/ENOSPC are not physical power-loss proof |
| Compromised origin HMAC/evidence signer | Original human signatures still checked; confirmed proof hash cannot change alone | Old payload mapping and evidence source truth remain trust assumptions |
| Loss of origin source/private keys | Verify retained old signatures without key; custody recovery on B/C | No positive CapitalLedger authority restoration |
| Real personal info inserted into audit proof | Not permitted; no public endpoints/logged payloads; synthetic inputs only | Metadata/reidentification risk; distributed deletion NOT implemented/qualified |
| Sybil hosts / shared volume / shared backups | Cannot count as independent operators | Independent operators/hosts and custody review NOT EXECUTED |

Safety assumes at least one honest non-equivocating normal checkpoint node and
live honest retained state or an independently pinned minimum floor. Recovery
also assumes honest quorum-governor signing practices and that the required
threshold is not compromised. Multiple node faults combined with governance
quorum compromise are outside the guarantee. An operator may always censor or
withhold service; no availability promise under arbitrary partitions.

This experiment does not solve global double-spend. A source can execute an
unreplicated synthetic command locally, but cannot label it a confirmed distributed
checkpoint without the configured signatures. Replicated read-only projections
are not writable fund replicas and never imply authority to spend after failover.

Pending recovery has no automatic timeout reset: expire => hold + reviewed future
protocol. Online compromised keys can exfiltrate private proof content accessible
to their host. Local file permissions/metadata minimization are not encryption at
rest. Production use is forbidden. No change to old G04/G09/security release gates.

Authority freshness boundary: live GOV membership is replayed from the latest
custody-certified source snapshot, not queried from a possibly newer source DB.
A canonical revocation not yet replicated is not claimed to be globally enforced.
This is another reason custody rotation cannot grant fund execution or real
private-data dissemination rights. Full negative-first authority synchronization
belongs to the future canonical cutover contract, not this shadow lab.
