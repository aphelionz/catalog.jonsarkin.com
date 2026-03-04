# Shielded Registry Chain-of-Custody Logs

Part of Shielded Registry Assets v0.1

## Preamble

| Field | Value |
|-------|-------|
| ZIP | TBD |
| Title | Shielded Registry Chain-of-Custody Logs |
| Owners | Mark Henderson &lt;mark@shieldedlabs.net&gt;, Sebastian Rodriguez-Larrain &lt;serlarrain@gmail.com&gt; |
| Status | Draft |
| Category | Standards |
| Created | 2025-11-16 |
| License | CC0-1.0 |
| Credits | |
| Original-Authors | Mark Henderson &lt;mark@shieldedlabs.net&gt;, Sebastian Rodriguez-Larrain &lt;serlarrain@gmail.com&gt; |
| Discussions-To | [TODO Make Forum Post] |

## Terminology

The key words "RECOMMENDED", "MUST", "MUST NOT", "SHOULD", and "SHOULD NOT" in this document are to be interpreted as described in BCP 14 [^bcp14] when, and only when, they appear in all capitals.

The term "Real World Asset" (RWA) refers to a physical, off-chain asset such as an artwork, collectible, or other tangible good whose legitimacy and provenance are determined by human institutions rather than by a blockchain protocol.

The term "Shielded Registry Asset" (SRA) refers to the on-chain record of a single registry-tracked artwork's RA-signed event log in the Zcash shielded pool.

The term "Registry Authority" (RA) refers to a legal or de-facto authority that maintains the canonical record for a given set of RWAs (for example, an artist's estate, a foundation, or a catalogue raisonne committee). The RA controls the registry keys defined in ZIP-SRA-KEYGEN and is the sole oracle for asset events.

The term "Asset Registry" refers to an off-chain database, catalog, or collection of records maintained by the RA that describes each RWA under its authority. Each entry in the asset registry has a unique registry identifier ID_Registry.

The term "Canonical Artwork ID" (CAID) refers to the stable, non-semantic reference identifier assigned to a specific RWA within the SRA protocol. The CAID is derived deterministically from ID_Registry and functions as a persistent referential anchor across institutional, scholarly, and market contexts. The CAID is not a token of ownership; it is a semantic anchor that ensures referential continuity across time.

The term "Chain of Custody" refers to the RA's recorded history of significant events for a given RWA, such as initial registration, changes in holder, revisions to documentation, status changes (e.g. stolen, destroyed, deaccessioned), and dispute flags.

The term "Dossier Snapshot" refers to a structured bundle of the best available documentary evidence for an RWA at a specific point in time -- photographic documentation, catalog entries, invoices, exhibition histories, conservation reports, and any other supporting material. The Dossier Snapshot itself is never placed on-chain; only its cryptographic digest is anchored.

The term "Snapshot Digest" refers to Blake2b-256(Dossier Snapshot), the cryptographic fingerprint of a Dossier Snapshot. The Snapshot Digest is included in event payloads to commit to specific documentary evidence without exposing it.

The terms "receiver" and "unified address" are defined as in ZIP-316 [^zip316].

## Abstract

This ZIP specifies a protocol for Registry Authorities (RAs) of Shielded Registry Assets (SRAs) to maintain per-asset, append-only event logs anchored in the Zcash Orchard shielded pool.

For each off-chain asset registry entry, the RA derives a dedicated Orchard viewing key and unified address, then records RA-signed registration, transfer, revision, status, and dispute events as minimum-value shielded transactions containing signed payloads in memos. Verifiers who receive the per-asset viewing key can independently reconstruct and authenticate that asset's full chain of custody, without learning anything about unrelated assets, balances, or counterparties.

The protocol encodes a non-binary evidentiary taxonomy (Registered, Provisional, Disputed, Unverified) and treats revision as a first-class structural feature -- correction occurs through addition, not deletion, preserving the chronological trace of knowledge growth.

The protocol does not create on-chain tokens and does not encode legal ownership or settlement. It is fully expressible using NU6 Zcash features (ZIP-32, ZIP-316, and ZIP-317) and is intended as an immediately deployable, privacy-preserving integrity layer for existing Real-World Asset registry workflows.

## Motivation

Registries for Real-World Assets (RWAs) still depend on human institutions for legitimacy. These registries typically live in opaque databases and paper files that are hard to audit, easy to fragment, and trivial to quietly edit. Relying parties either trust screenshots and PDFs, or they hire intermediaries to repeat the same manual checks.

The cost of this opacity is structural. Each time a work changes hands -- from estate to dealer, dealer to auction house, auction house to collector, collector to museum -- trust must be re-purchased from scratch. Prior verification work leaves no reusable trace. This "reset dynamic" imposes a coordination tax that compounds over time, producing uncertainty discounts, institutional withdrawal, and the gradual devaluation of works whose documentary infrastructure cannot keep pace with their circulation.

SRAs use Zcash's unique capabilities of shielding and selective disclosure to transform verification from a repeated private expense into a cumulative infrastructure investment. For each registry identifier (ID_Registry), the RA maintains a private, per-asset Chain of Custody: an append-only sequence of RA-signed transactions anchored in Orchard's commitment tree. Software can reconstruct the Asset Registry from these per-asset logs, while per-asset viewing keys let the RA disclose one asset's Chain of Custody to a verifier without revealing unrelated holdings or balances.

The design is intentionally modest and immediately deployable on NU6 Zcash: there is no on-chain asset, no atomic barter, and no requirement for ZSAs or TZE.

## Requirements

- All functionality MUST be expressible using NU6 features, specifically standard Orchard shielded transactions with 512-byte memos.
- The protocol MUST tolerate registry authorities omitting events; it only guarantees integrity and auditability of events that are written.
- The protocol MUST NOT assume or depend on owner or counterparty signatures in v0.1.
- The protocol MUST NOT attempt to enforce legal ownership, custody, or payment settlement on-chain.
- The protocol MUST support revision of prior claims through append-only correction events, preserving the full chronological trace of knowledge growth.
- The protocol MUST support a non-binary evidentiary taxonomy that distinguishes levels of documentary confidence.
- The protocol MUST support dispute flagging as a visible, non-terminal response to contested claims.
- Each event payload MUST include a Lamport clock and hash-chain reference to ensure chronological integrity and tamper detection.
- All identifiers MUST be non-semantic to minimize information disclosure in the event of an unwanted viewer obtaining VK_Asset.

## Privacy Implications

This ZIP introduces no changes to consensus rules, transaction formats, or address/key types. All on-chain activity consists of standard Orchard shielded transactions and memos [^protocol], so the protocol inherits the existing privacy properties and risks of the current network upgrade.

All higher-level processes around creation, transport, sale, or destruction of physical assets remain off-chain and governed by existing social, legal, and contractual norms. This ZIP does not attempt to encode those processes on-chain and therefore does not introduce new surveillance or linkage vectors beyond standard shielded usage.

Per-asset viewing keys (VK_Asset) provide granular disclosure control. The RA can share a single asset's full event history with a buyer without revealing any information about other assets in the registry. However, the RA and all holders of a given VK_Asset can see the complete event log for that asset. RAs SHOULD treat VK_Asset disclosure as a deliberate act and SHOULD maintain records of which parties have received which viewing keys.

The use of non-semantic identifiers for ID_Registry is RECOMMENDED to prevent information leakage. If ID_Registry is human-readable (e.g. "ERL-painting-042"), an unwanted viewer who obtains VK_Asset gains not only the event log but also contextual information about the asset. Non-semantic identifiers (e.g. UUIDs) limit leakage to the event contents themselves.

## Adversary Model

- We assume the RA can censor or omit events but cannot undetectably reorder or edit included events once published (enforced by the Lamport clock and hash chain).
- We assume an adversary who obtains VK_Asset for a specific artwork can read that artwork's full event history, but learns nothing about other assets in the registry.
- We assume an adversary cannot forge RA signatures without compromising the RA's signing key (see ZIP-SRA-KEYGEN for key custody and compromise response).
- We assume the RA may issue incorrect or fraudulent events. The protocol provides auditability and tamper detection, not truth enforcement. Institutional and legal remedies address RA misbehavior.
- We assume the underlying Zcash network provides consensus-level ordering (block height and timestamp) that the RA cannot manipulate.

## Specification

### Key Material

The RA's signing keypair (PK_R, SK_R), master seed (Seed_RA), and registry full viewing key (FVK_Registry) are generated and maintained as specified in ZIP-SRA-KEYGEN (Shielded Registry Authority Key Ceremony).

This ZIP assumes these exist and are valid. Specifically:

- PK_R is the RA's Ed25519 public key, established by a valid Genesis Declaration
- SK_R is the corresponding secret (held as FROST threshold shares)
- FVK_Registry is the Orchard full viewing key derived from Seed_RA

### Asset Definition

#### Canonical Artwork ID (CAID)

Let ID_Registry be the canonical key of the asset in the off-chain Asset Registry. It is RECOMMENDED that ID_Registry be a non-semantic identifier (e.g. UUID v4) to reduce information disclosure in the event of an unwanted viewer.

Let Hash be the Blake2b-256 hash function.

For each ID_Registry in the registry, derive the Canonical Artwork ID and per-asset viewing key as per ZIP-32 [^zip32]:

    ID_Asset = Blake2b-256(ID_Registry) mod 2^31
    VK_Asset = ChildFVK(FVK_Registry, ID_Asset)

Then derive the per-asset unified address:

    UA_Asset = OrchardReceiver(VK_Asset)

Each ID_Registry MUST map to exactly one VK_Asset and its derived UA_Asset. ID_Registry MUST NOT be reused for any other asset.

The CAID is the tuple (ID_Registry, ID_Asset). In practice, ID_Asset (the 31-bit derived index) serves as the on-chain identifier, while ID_Registry is the off-chain lookup key maintained by the RA. The deterministic derivation ensures that any party with knowledge of ID_Registry and FVK_Registry can independently derive VK_Asset and verify the event log.

### Event Types

The following event types are defined for per-asset Chain of Custody logs. All events are sent to UA_Asset as minimum-value Orchard shielded transactions with signed payloads in memos.

| Code | Event Type | Purpose |
|------|-----------|---------|
| 0x10 | REGISTER | Initial registration of an artwork in the SRA |
| 0x11 | TRANSFER | Recognition of a change in holder/custodian |
| 0x12 | STATUS | Change in the asset's operational status |
| 0x13 | REVISION | Correction or update to prior claims about the asset |
| 0x14 | DISPUTE | Flag indicating contested claims about the asset |
| 0x15 | DOSSIER_UPDATE | Commitment to an updated documentary evidence bundle |

[Note: Event type codes 0x10-0x1F are reserved for per-asset events. Codes 0x01-0x0F are reserved for RA lifecycle events defined in ZIP-SRA-KEYGEN.]

### Common Payload Structure

All event payloads share a common header:

    EventPayload = {
      version:        u8,           // Payload format version (1 for this ZIP)
      event_type:     u8,           // Event type code (see table above)
      id_registry:    [u8; 16],     // Non-semantic registry identifier (UUID)
      clock:          u64,          // Lamport clock, monotonically increasing per ID_Asset
      prev_hash:      [u8; 32],     // Blake2b-256 of previous EventPayload for this ID_Asset
      event_data:     EventData,    // Type-specific event data (see below)
    }

Let Signature = Ed25519.Sign(SK_R, Blake2b-256(EventPayload)) with ZIP-215 [^zip215] semantics.

Upon off-chain confirmation of an asset event, the Registry Authority constructs a minimum-value transaction (including ZIP-317 [^zip317] fees) to UA_Asset containing a memo bundle:

    { EventPayload, Signature }

The total size of EventPayload + Signature (64 bytes) MUST NOT exceed the NU6 Orchard memo maximum of 512 bytes. This leaves a maximum of 448 bytes for the EventPayload.

#### Lamport Clock

The `clock` field is a Lamport-style monotonically increasing integer, scoped to a specific ID_Asset. It starts at 0 for the REGISTER event and increments by 1 for each subsequent event.

The clock provides a logical ordering that is independent of block timestamps. Combined with the hash chain (`prev_hash`), it ensures that events form a tamper-evident, totally ordered sequence per asset.

#### Hash Chain

The `prev_hash` field contains Blake2b-256 of the immediately preceding EventPayload for this ID_Asset. For the first event (REGISTER, clock = 0), `prev_hash` MUST be set to 32 zero bytes (0x00...00).

The combination of clock + prev_hash creates a dual integrity mechanism: the clock enforces monotonicity, and the hash chain enforces content integrity. A verifier who detects a gap in the clock sequence or a hash mismatch knows the event log has been tampered with or is incomplete.

### Event Type: REGISTER (0x10)

#### Purpose

The REGISTER event anchors a new artwork into the SRA. It is the first event in any asset's chain of custody and establishes the CAID as a persistent referential anchor. Registration does not claim completeness of documentation; it claims that the RA has begun formal tracking of this asset.

#### Event Data

    RegisterData = {
      holder_label:     [u8; var],  // Non-identifying label for current holder (max 64 bytes)
      description:      [u8; var],  // Brief asset description (max 128 bytes)
      evidence_status:  u8,         // Evidentiary confidence level (see taxonomy below)
      snapshot_digest:  [u8; 32],   // Blake2b-256 of the initial Dossier Snapshot (or 32 zero bytes if none)
      policy:           [u8; var],  // Human-readable contact/policy instructions (max 64 bytes)
    }

The `holder_label` field is a non-identifying label for the current holder or custodian of the physical asset. It is RECOMMENDED that this be a pseudonymous reference (e.g. "Holder-A", "Estate Collection") rather than a legal name, to preserve privacy within the shielded memo. The mapping between labels and legal identities is maintained off-chain by the RA.

The `description` field contains a brief, non-sensitive description of the asset. It is RECOMMENDED that this be minimal (e.g. "Oil on canvas, 1962, 120x80cm") to limit information exposure.

The `evidence_status` field classifies the asset's current evidentiary confidence level according to the non-binary taxonomy defined below.

The `snapshot_digest` field commits to the documentary evidence supporting this registration. If the RA has assembled a Dossier Snapshot, this field contains Blake2b-256(dossier). If no dossier is yet available (e.g. for a work known from historical records but not yet physically inspected), this field MUST be set to 32 zero bytes, and `evidence_status` SHOULD be set to UNVERIFIED or PROVISIONAL.

#### Evidentiary Status Taxonomy

The SRA rejects the simplistic dichotomy of "authentic" versus "fake." Instead, it employs a non-binary taxonomy that reflects the epistemic humility required by a fallibilist framework:

| Code | Status | Meaning |
|------|--------|---------|
| 0x01 | REGISTERED | Fully verified and documented. The RA has high confidence in attribution, provenance, and physical condition based on direct inspection and robust archival evidence. |
| 0x02 | PROVISIONAL | Anchored with strong historical evidence but pending updated inspection. The RA has reasonable confidence based on archival records, prior catalog entries, or credible secondary sources, but has not yet performed fresh physical verification. |
| 0x03 | DISPUTED | Actively contested. There exist credible competing claims regarding attribution, authenticity, provenance, or ownership that the RA has not yet resolved. |
| 0x04 | UNVERIFIED | Referenced in historical records but lacking current documentation. The RA acknowledges the asset's existence in the historical record but cannot yet make confident claims about its current state. |

This taxonomy is applied at registration time and can be updated via STATUS events as the evidentiary situation evolves. It is RECOMMENDED that the RA begin with the most conservative applicable status and upgrade as evidence strengthens, rather than registering at REGISTERED and later downgrading.

The taxonomy renders epistemic states visible and structured, allowing scholars, institutions, and market actors to calibrate risk accordingly. It replaces informal whisper networks with auditable procedural signals.

### Event Type: TRANSFER (0x11)

#### Purpose

The TRANSFER event records the RA's recognition that custodianship or stewardship of the physical asset has changed. It does not enforce or execute the transfer; it records the RA's acknowledgment of a change that has occurred off-chain through legal, contractual, or social processes.

#### Event Data

    TransferData = {
      to_holder_label:  [u8; var],  // Non-identifying label for the new holder (max 64 bytes)
      snapshot_digest:  [u8; 32],   // Blake2b-256 of Dossier Snapshot at time of transfer (or zeros)
      transfer_note:    [u8; var],  // Optional human-readable note (max 64 bytes)
    }

It is RECOMMENDED that the RA assemble and commit a fresh Dossier Snapshot at the time of each transfer, documenting the asset's condition and supporting evidence at the moment of transition. This practice converts circulation into cumulative memory: each movement of the work strengthens rather than weakens its integrity chain.

The `transfer_note` field MAY contain brief contextual information (e.g. "Consignment to Gallery X", "Inheritance"). It SHOULD NOT contain legally sensitive information.

### Event Type: STATUS (0x12)

#### Purpose

The STATUS event records a change in the asset's operational or evidentiary status. This includes changes to the evidentiary confidence level, as well as operational status changes that affect the asset's availability or legal standing.

#### Event Data

    StatusData = {
      status_code:      u8,         // Status code (see below)
      evidence_status:  u8,         // Updated evidentiary confidence level (taxonomy above)
      snapshot_digest:  [u8; 32],   // Blake2b-256 of supporting Dossier Snapshot (or zeros)
      status_note:      [u8; var],  // Human-readable explanation (max 96 bytes)
    }

#### Operational Status Codes

| Code | Status | Meaning |
|------|--------|---------|
| 0x01 | ACTIVE | The asset is in normal status; no special conditions |
| 0x02 | ON_LOAN | The asset is on institutional loan |
| 0x03 | IN_CONSERVATION | The asset is undergoing conservation or restoration |
| 0x04 | STOLEN | The asset has been reported stolen |
| 0x05 | LOST | The asset's location is unknown |
| 0x06 | DAMAGED | The asset has sustained significant damage |
| 0x07 | DESTROYED | The asset has been destroyed |
| 0x08 | DEACCESSIONED | The asset has been formally deaccessioned from a collection |
| 0xFF | OTHER | Unspecified status change |

A STATUS event MAY update the `evidence_status` field simultaneously with the operational status. For example, a work that was PROVISIONAL may be upgraded to REGISTERED after a conservation examination reveals new evidence, with status_code = IN_CONSERVATION.

### Event Type: REVISION (0x13)

#### Purpose

The REVISION event is the protocol's mechanism for corrigibility -- correction through addition, not deletion. When new evidence emerges that changes the RA's understanding of an asset (a newly discovered letter clarifying a date, a conservation analysis revealing overpainting, a comparative study refining attribution), the RA does not modify prior registry entries. Instead, it issues a REVISION event that references the prior claim and anchors the updated understanding.

The result is not duplication but transparency. The registry shows what was believed at time T1, what evidence justified that belief, what was later discovered at time T2, and how the understanding of the work evolved. The canon becomes auditable in its growth.

#### Event Data

    RevisionData = {
      revised_clock:    u64,        // Clock value of the event being revised
      revised_hash:     [u8; 32],   // Blake2b-256 of the EventPayload being revised
      revision_type:    u8,         // What aspect is being revised (see below)
      snapshot_digest:  [u8; 32],   // Blake2b-256 of Dossier Snapshot containing updated evidence
      revision_note:    [u8; var],  // Human-readable explanation (max 96 bytes)
    }

#### Revision Type Codes

| Code | Type | Meaning |
|------|------|---------|
| 0x01 | ATTRIBUTION | Revised attribution (artist, date, period, technique) |
| 0x02 | PROVENANCE | Revised provenance chain (prior ownership, exhibition history) |
| 0x03 | DESCRIPTION | Revised physical description (dimensions, materials, condition) |
| 0x04 | EVIDENCE_UPGRADE | Evidentiary status upgrade based on new documentation |
| 0x05 | EVIDENCE_DOWNGRADE | Evidentiary status downgrade based on new information |
| 0x06 | CORRECTION | Correction of an error in a prior event (e.g. typographic, factual) |
| 0xFF | OTHER | Unspecified revision |

#### Semantics

A REVISION event does not invalidate the event it revises. Both the original event and the revision remain in the append-only log. Verifiers reconstruct the current understanding by replaying the full event sequence: the most recent REVISION for any given aspect supersedes earlier claims, but the historical trace is preserved.

This accomplishes three structural objectives. First, it preserves epistemic humility -- the RA's authority is stabilized but not absolutized. The estate can change its position without erasing its past stance. Second, it increases institutional trust -- museums and scholars can see the trajectory of interpretation rather than receiving opaque updates. Third, it transforms revision from reputational risk into procedural normality -- because correction is built into the architecture, acknowledging error becomes a sign of epistemic strength rather than weakness.

### Event Type: DISPUTE (0x14)

#### Purpose

The DISPUTE event is a visible, non-terminal response to contested claims about an asset. Not all epistemic challenges arise from improved evidence; some arise from conflict. Works may circulate with incomplete provenance, competing claims of authorship, or suspected forgery. In conventional estate practice, such situations often produce silence: ambiguous works are ignored, denied, or discussed informally without structured documentation. This silence accelerates entropy.

The DISPUTE event renders uncertainty visible without asserting final judgment.

#### Event Data

    DisputeData = {
      dispute_action:   u8,         // OPEN = 0x01, RESOLVE = 0x02
      dispute_type:     u8,         // What is contested (see below)
      snapshot_digest:  [u8; 32],   // Blake2b-256 of Dossier Snapshot documenting the dispute
      dispute_note:     [u8; var],  // Human-readable explanation (max 96 bytes)
    }

#### Dispute Type Codes

| Code | Type | Meaning |
|------|------|---------|
| 0x01 | ATTRIBUTION | Contested attribution (authorship, dating, authenticity) |
| 0x02 | PROVENANCE | Contested provenance (ownership history, title claims) |
| 0x03 | CONDITION | Contested condition assessment (conservation disputes) |
| 0x04 | FORGERY | Suspected forgery or unauthorized reproduction |
| 0xFF | OTHER | Unspecified dispute |

#### Semantics

**OPEN (0x01):** Marks the asset as under dispute. The RA SHOULD simultaneously issue a STATUS event setting `evidence_status` to DISPUTED (0x03) if the dispute is substantive enough to affect evidentiary confidence.

**RESOLVE (0x02):** Marks a previously opened dispute as resolved. The RA SHOULD include a `snapshot_digest` committing to the resolution documentation and SHOULD issue a REVISION event and/or STATUS event reflecting the outcome of the dispute.

A dispute MAY be opened and resolved multiple times for the same asset. Each opening and resolution is a separate event in the append-only log.

The DISPUTE event does not assert final judgment. It marks the work as under review and preserves the reasoning behind the uncertainty. The registry therefore communicates structured ambiguity rather than false certainty.

### Event Type: DOSSIER_UPDATE (0x15)

#### Purpose

The DOSSIER_UPDATE event commits to an updated Dossier Snapshot without changing any other aspect of the asset's chain of custody. It is used when the RA has assembled significant new documentation (e.g. digitized archival material, new photography, conservation reports) that strengthens the evidentiary basis without constituting a revision of prior claims.

#### Event Data

    DossierUpdateData = {
      snapshot_digest:  [u8; 32],   // Blake2b-256 of the new Dossier Snapshot
      update_note:      [u8; var],  // Human-readable description of what changed (max 128 bytes)
    }

This event is intentionally lightweight. Its primary purpose is to timestamp the availability of new documentary evidence so that verifiers can request updated dossiers from the RA. The actual evidence is never placed on-chain.

### Dossier Snapshots

#### Definition

A Dossier Snapshot is an off-chain bundle of the best available documentary evidence for an RWA at a specific point in time. The RA assembles and maintains dossiers; the SRA protocol only anchors their cryptographic fingerprints.

A Dossier Snapshot MAY include any combination of:

- Photographic documentation (multiple angles, raking light, UV, infrared)
- Catalog entries and catalog raisonne references
- Invoices, receipts, and transaction records
- Exhibition histories and loan records
- Conservation and condition reports
- Correspondence and archival references
- Expert opinions and authentication reports
- Prior certificates of authenticity
- Insurance valuations and appraisals

#### Canonical Serialization

To ensure that the Snapshot Digest is deterministic and reproducible, the Dossier Snapshot MUST be serialized in a canonical format before hashing.

[TODO: Define the canonical serialization format for Dossier Snapshots. Options include a deterministic archive format (e.g. sorted tar), a manifest-based approach (hash each file individually, then hash the sorted list of hashes), or a structured metadata format (JSON-LD, CBOR). The choice affects tooling requirements and interoperability. A manifest-based approach is likely simplest: the Snapshot Digest = Blake2b-256(sorted concatenation of Blake2b-256(file) for each file in the dossier).]

#### Disclosure

Dossier Snapshots are disclosed out-of-band by the RA to verifiers who request them. The SRA protocol does not specify the transport mechanism for dossier disclosure; this is an application-layer concern. The protocol guarantees only that a verifier who receives a dossier can check its integrity against the on-chain Snapshot Digest.

RAs MAY provide tiered disclosure: a summary dossier for routine verification, and a full dossier (with high-resolution images and confidential documents) for institutional due diligence. The Snapshot Digest commits to the full dossier; partial disclosures are at the RA's discretion and do not affect on-chain integrity.

### Disclosure and Verification

#### Selective Disclosure

The RA selectively discloses VK_Asset (and PK_R) to buyers, insurers, scholars, institutions, and other parties who require verification of a specific asset's chain of custody.

Disclosure of VK_Asset for one asset reveals nothing about any other asset in the registry. This is the fundamental privacy property of the per-asset key derivation scheme: each asset has its own shielded account, and viewing keys are independent.

#### Verification Procedure

Given VK_Asset and PK_R, a verifier:

1. Scans the Zcash chain and recovers all notes addressed to UA_Asset (derived from VK_Asset)
2. Decrypts each memo with VK_Asset
3. Parses each memo to extract EventPayload and Signature
4. Verifies the Signature in each memo against PK_R
5. Orders events by the `clock` field
6. Validates the hash chain: for each event with clock > 0, confirms that `prev_hash` equals Blake2b-256 of the immediately preceding EventPayload
7. Confirms that the first event (clock = 0) is a REGISTER event with `prev_hash` = 32 zero bytes

Clients MUST reject any event where:

- The Signature does not verify against PK_R
- The `clock` is non-monotonic (equal to or less than a previously accepted event's clock)
- The `prev_hash` does not match Blake2b-256 of the prior accepted EventPayload
- The first event is not a REGISTER event

#### Reconstructing Current State

A verifier who has validated the full event sequence can reconstruct the asset's current state by replaying events in clock order:

- The most recent REGISTER or TRANSFER event determines the current holder_label
- The most recent STATUS event determines the current operational status and evidentiary confidence
- The most recent REVISION event for each revision_type supersedes prior claims of that type
- Any open (unresolved) DISPUTE events flag active contestation
- The most recent DOSSIER_UPDATE (or the most recent event with a non-zero snapshot_digest) identifies the current documentary evidence commitment

This replay model means that the "current state" of an asset is always a computed view over the full event history, never a mutable record. The history is the source of truth.

#### Verifying RA Authority

Before trusting any event signatures, a verifier SHOULD also verify the RA's authority by checking the Genesis Declaration and any real-world bindings as specified in ZIP-SRA-KEYGEN. This establishes that PK_R belongs to a legitimate Registry Authority with a defined scope.

If the RA has undergone key rotation (also specified in ZIP-SRA-KEYGEN), the verifier MUST use the appropriate PK_R for each event based on the effective block height of each rotation.

### Progressive Anchoring

#### Integrity Before Completeness

The SRA does not require complete archival consolidation before initialization. A conventional centralized catalogue raisonne often postpones publication until exhaustive documentation is achieved, which in resource-constrained estates can take decades. The SRA reverses this order: it prioritizes chronological integrity over immediate completeness.

The RA begins by registering works whose documentation is already robust -- those in the estate's possession or those supported by high-confidence archival records. These are registered with `evidence_status` = REGISTERED. Works known from historical records but not yet physically inspected are registered as PROVISIONAL or UNVERIFIED. Works under active contestation are registered as DISPUTED.

This progressive model ensures that the registry does not prematurely confer legitimacy, nor does it erase uncertainty. Instead, it renders epistemic states visible and structured from the first moment of registration.

#### Market Invitation

In estates where a significant portion of the oeuvre resides outside the direct physical control of the estate, progressive anchoring has a strategic dimension: it functions as a formal invitation to the market. Collectors holding works are invited to participate in the stabilization of the legacy by submitting documentation for review and inclusion. The registry becomes not merely an archival exercise but a professionalization initiative. The estate does not claim ownership over dispersed works; it offers verifiable integration into a durable integrity chain.

## Rationale

### Why Zcash

Zcash is uniquely suited for real-world asset registries. Orchard shielded transfers and viewing keys provide selective disclosure capabilities that public chains cannot. Each asset gets its own shielded account; giving a buyer the per-asset viewing key lets them independently reconstruct and verify that asset's full event history, and nothing else.

Public blockchains (Ethereum, Bitcoin, Solana) record all transaction data in plaintext on a globally readable ledger. For art market participants -- who operate under strong norms of confidentiality around pricing, ownership, and collection strategy -- this transparency is not a feature but a structural hazard. The SRA architecture requires privacy by default with proof on demand, which is precisely the design philosophy of Zcash's shielded pool.

### Why Not Tokens

The design deliberately inverts the NFT model. The physical asset and the off-chain registry entry come first; the Zcash ledger is a cryptographic audit trail that supports those facts, not a tradeable "representation" that pretends to be the asset.

This boundary is not merely aesthetic. Tokenization would: (a) conflate the integrity layer with a financial instrument, inviting speculative dynamics that distort stewardship incentives; (b) require on-chain settlement logic that contradicts the SRA principle of leaving ownership in traditional legal frameworks; (c) alienate precisely the institutional actors (museums, auction houses, insurers) whose trust the protocol is designed to restore.

The SRA is an integrity notary, not a financial instrument. It records stewardship recognition and documentary integrity. Ownership remains a World 1 legal status governed by contract law and jurisdictional institutions.

### Why Append-Only with Revision Events

A traditional database corrects error by overwriting the prior record. The previous claim disappears, replaced by the updated entry. While administratively efficient, this method destroys the visible trace of intellectual evolution. It collapses the historical path of inquiry into a single present-tense assertion, eroding accountability and scholarly trust.

The SRA adopts the opposite logic: correction occurs through addition, not deletion. The REVISION event preserves the chronological trace of knowledge growth, allowing verifiers to see not only what the RA currently believes but how that understanding evolved. In Popperian terms, the registry does not enshrine truth; it records conjectures and their subsequent refutations or refinements.

### Why a Non-Binary Evidentiary Taxonomy

The binary "authentic / fake" dichotomy is incompatible with the epistemic reality of art historical practice. Attribution exists on a spectrum of confidence. Provenance can be strong for some periods and weak for others. Physical condition assessments change with new conservation techniques. A registry that forces binary classification either produces false certainty (registering ambiguous works as fully verified) or false exclusion (refusing to register anything that isn't exhaustively documented).

The four-level taxonomy (Registered, Provisional, Disputed, Unverified) reflects the epistemic humility required by a fallibilist framework. It preserves transparency regarding evidentiary strength and allows scholars, institutions, and market actors to calibrate risk accordingly.

### Why Dossier Snapshots Are Off-Chain

Dossier Snapshots can be large (high-resolution photography alone may be gigabytes) and contain sensitive information (valuations, insurance details, private correspondence). Placing them on-chain would be technically impractical and privacy-hostile. Instead, the protocol anchors only the Snapshot Digest -- a 32-byte cryptographic fingerprint -- which is sufficient to prove that a specific evidence bundle existed at a specific time, without exposing its contents.

This separation also decouples the integrity layer from storage concerns. Dossier Snapshots can be stored on any infrastructure the RA chooses (local archive, institutional repository, IPFS, cloud storage) without affecting the on-chain integrity chain. The chain commits to *what* was documented; the RA decides *where* the documentation lives.

## Event Type Summary

| Code | Event Type | Section |
|------|-----------|---------|
| 0x10 | REGISTER | Registration |
| 0x11 | TRANSFER | Transfer |
| 0x12 | STATUS | Status |
| 0x13 | REVISION | Revision |
| 0x14 | DISPUTE | Dispute |
| 0x15 | DOSSIER_UPDATE | Dossier Update |

[Note: Codes 0x01-0x0F are reserved for RA lifecycle events (ZIP-SRA-KEYGEN). Codes 0x16-0x1F are reserved for future per-asset event types.]

## Open Questions

1. **Dossier Snapshot canonical serialization**: What format should be used for deterministic serialization of Dossier Snapshots? A manifest-based approach (sorted list of per-file hashes) is simplest but may be insufficient for complex multi-format dossiers. A structured metadata format (JSON-LD, CBOR) is richer but adds tooling dependencies.

2. **Payload size budget**: The 512-byte memo limit creates tension between expressiveness and compactness. The current design uses variable-length fields with maximum sizes, but a binary encoding scheme (e.g. CBOR, protobuf) would be more space-efficient than the conceptual JSON-like notation used in this specification. A concrete binary wire format needs to be defined.

3. **Multi-memo events**: For events that require more data than a single 512-byte memo can accommodate (e.g. a REGISTER with a long description and multiple fields), should the protocol support multi-memo transactions? This would require a fragmentation/reassembly scheme and a way for verifiers to identify related memos.

4. **Counterparty participation in v0.2**: The current design is RA-centric; only the RA signs events. A future version could allow counterparty co-signatures on TRANSFER events (the buyer confirms receipt) or third-party signatures on DISPUTE events (an expert submits a signed opinion). This would strengthen the protocol but significantly complicates the signing and verification model.

5. **Disclosure scoping**: The current model discloses VK_Asset, which reveals the *entire* event history for an asset. Should the protocol support time-bounded or event-type-filtered disclosure? For example, a buyer might need to see REGISTER and TRANSFER events but not internal REVISION or DISPUTE events. This may require additional key derivation layers.

6. **Cross-RA interoperability**: When an asset moves from one RA's jurisdiction to another (e.g. a work attributed to a different artist), how is the transition recorded? This likely requires a cross-reference event type and mutual recognition between RAs, potentially standardized through ALMA.

7. **Backward compatibility with existing ZIPs**: The per-asset key derivation scheme uses ZIP-32 child key derivation. Should the derivation path be formalized as a registered ZIP-32 purpose to avoid collision with other applications using the same derivation tree?

## References

[^bcp14]: Information on BCP 14 -- "RFC 2119: Key words for use in RFCs to Indicate Requirement Levels" and "RFC 8174: Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words"

[^zip32]: ZIP 32: Shielded Hierarchical Deterministic Wallets. https://zips.z.cash/zip-0032

[^zip215]: ZIP 215: Explicitly Defining and Modifying Ed25519 Validation Rules. https://zips.z.cash/zip-0215

[^zip316]: ZIP 316: Unified Addresses and Unified Viewing Keys. https://zips.z.cash/zip-0316

[^zip317]: ZIP 317: Proportional Transfer Fee Mechanism. https://zips.z.cash/zip-0317

[^protocol]: Electric Coin Company, Zcash Protocol Specification. https://zips.z.cash/protocol/protocol.pdf

## Reference Implementation

https://sras.io
