# Deterministic collective balance — design contract

Draft `uel-projection-design-1`. The executable helper tests **final arithmetic over already eligible synthetic current source snapshots**, not the complete event/authority reducer below. There is no new Help lifecycle implementation.

## Inputs and reproducibility identity

A projection request fixes: audience/purpose, network/policy version, canonical source mapping version, source coverage floors and authenticated cut/frontiers, privacy/consent/authority frontiers, valuation-as-of and knowledge-as-of times, unit catalogue, valuation algorithm, projector version. Output includes those commitments, completeness/unknown/conflict counts and scope. Hashing a private result is not permission to publish that hash.

Do not compare differently scoped results as if they were the same society-wide balance. Source snapshots are private evidence; newly omitted/erased data reduces coverage. A lower known subtotal is not proof that a community became poorer.

## Normative reduction order

1. Bounded parse/schema/canonical/signature check. Valid signature alone does not qualify an event.
2. Verify pinned identity/key history, actor role, operation scope, current authority/revocation and disclosure. Receiver context is local trusted state, never supplied as a peer's Boolean. Unknown → no positive execution/disclosure/accounting promotion.
3. Apply authenticated privacy/retirement denials and erase derivative payloads. An old included/anchored positive event cannot override denial. Missing denial/replay frontier after restore → UNKNOWN_RECOVERY, not a fresh zero baseline.
4. Resolve **reviewed** canonical source identities. Dedup by `(source_namespace,canonical_source_event_ref)`, not transport event/ref, signature, delivery, receipt or anchor. Same key/different source bytes → conflict. Unknown cross-node object equivalence does not justify automatic identity/property merging.
5. Check object predecessor/revision chain and complete eligible cut. Missing coverage → unknown. Same revision/non-equivalent branch → quarantine. Never choose winner by arrival time, timestamp, lowest hash or AI confidence.
6. Read current canonical source state. For Help, validate/observe existing Help committed transition and revision; do not run a competing FSM. Import can't publish/offer/accept/complete local Help. Replica findings can request a source review, not create a second operation.
7. Apply evidence predicate policy. SELF_DECLARED does not become confirmed via replay/number of mirrors. Counterparty evidence must match exact occurrence/measurement; independent verification requires separately qualified independence. Dispute/retraction changes evidential eligibility while retaining lawful history; it does not forge a new source transition.
8. Select valuation evidence for the exact canonical lot/basis quantity/revision and requested knowledge/effective cut. Prefer policy-approved applicable revaluation lineage, not hash tie-breaking. Conflicting/latest-retracted candidates or incomplete provenance → UNKNOWN. Keep original assessment unchanged; don't silently reuse a price for a different lot.
9. Calculate exact quantities and values, then sort canonical group keys for stable serialization. Only this final step is represented by `fixture_projection.py`; its input is the result of1–8, not an untrusted event dump.

## Canonical domain rules

- **ASSET:** one canonical object/rights claim. ASSET and a RESOURCE view of its available fraction count once in registered stock. Unknown physical duplicate/title → flagged self-declared claims, not verified unique global property.
- **RESOURCE:** available quantity ≤ registered quantity with current explicit share consent and permitted purpose/audience. Registration alone gives zero granted availability if consent is absent, **not** ownership transfer or an obligation. Quantity unknown is not numeric0; report unknown counts/totals separately.
- **NEED:** current existing Help request or reviewed independent domain source; no new NEED alongside the same Help request. Missing unit/required quantity from Help01 remains UNKNOWN.
- **OBLIGATION:** existing accepted Help promise (one selected helper per Help01 cycle), not every published need or resource. Withdrawal/dispute follows source semantics. Asset availability withdrawal does not automatically cancel or fulfill Help; contradictory links become a review condition.
- **FULFILLMENT:** at most one current eligible occurrence per canonical Help completion/selected offer in Help01. Confirmation events from A and B are evidence for **one** occurrence, not two flows. A source COMPLETED→DISPUTED change removes it from confirmed flow; don't append a second negative financial transaction.
- **Partial measurements:** future paired attestations bind exact Help completion/occurrence, qty/unit/measurement revision and both-party scope. They supplement evidence; they do not introduce a partial-completion FSM. Current Help lacks this model; no inferred hours/items/value. Multiple measurements must identify disjoint contributions or explicitly supersede prior measurement.
- **VALUATION:** original local price/currency/time/provider/rate/algorithm retained; current revaluation separate. BTC only an internal measurement. Use existing `blago-value-1-half-even-satoshi` and reviewed source/currency policy, no floats. Round whole-lot assessment HALF_EVEN to8 BTC decimals; sum integer satoshis, do not round each duplicate/slice again.

## Separate outputs

`registered_claims_stock`, `verified_available_resources`, `self_declared_available`, `unmet_needs`, `confirmed_help_flow`, `satisfied_need_share`, `category_deficit_or_surplus`, `valuation_coverage`, `evidence/uncertainty/coverage` are separate views. There is **no** scalar `wealth = assets - needs + historic_help` and no ranking of human worth.

Stocks and cumulative flows are never added. Known BTC subtotal is accompanied by unvalued count, not presented as total. Flow uses valued, consented, currently confirmed occurrences once; disputed/erased occurrences follow policy/source state. No value for unknown Help measurement, no price from LLM. A fractional-share ratio requires known denominator; empty/unknown denominator → null, not100%.

Deficit/surplus requires compatible category, units, availability times/location/rights/conditions and measured need. Computers cannot mechanically satisfy food or inaccessible private equipment count as shared. Incompatible or unmeasured requests contribute unknown coverage; a simplistic stock minus all need titles is forbidden. Physical matching/allocation is a future voluntary recommendation, not an accounting side effect.

## Example invariants and executable scope

P01–P20 cover one asset plus its resource slice, identical observations in either order, consent removal, unknown quantity/missing value, basis quantity mismatch, one paired Help completion, duplicate completion, unqualified self-certification, later dispute/withdrawal, unknown flow, revoked statement, same-version fork quarantine, changed bytes under same event ID, consent bypass, float rejection, historical valuation cut and exact0.1+0.2 quantities.

The non-null flow operands in P08/P09 are hypothetical future measured Help evidence, not existing Help01 stored amounts. The helper assumes source IDs/scope/current snapshots are **already verified**. It does not prove full history, select arbitrary predecessor chains, enforce Human/mandate signatures, detect physical duplicate objects, compute global truth or implement authorization. `complete` refers only to supplied arithmetic operands, not completeness of community data or authority. Negative design scenarios for missing proof/current state are separate NOT EXECUTED cases.

Before runtime integration require an independently implemented reducer to agree on the same golden cut, all event arrival permutations/duplicates, corrections, erased payloads and old backup recovery. Sampling a few Python fixtures is insufficient for that gate.
