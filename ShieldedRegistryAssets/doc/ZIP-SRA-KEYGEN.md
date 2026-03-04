# Shielded Registry Authority Key Ceremony

Part of Shielded Registry Assets v0.1

## Preamble

| Field | Value |
|-------|-------|
| ZIP | TBD |
| Title | Shielded Registry Authority Key Ceremony |
| Owners | Mark Henderson &lt;mark@shieldedlabs.net&gt;, Sebastian Rodriguez-Larrain &lt;serlarrain@gmail.com&gt; |
| Status | Draft |
| Category | Standards |
| Created | 2026-03-03 |
| License | CC0-1.0 |
| Credits | |
| Original-Authors | Mark Henderson &lt;mark@shieldedlabs.net&gt;, Sebastian Rodriguez-Larrain &lt;serlarrain@gmail.com&gt; |
| Discussions-To | [TODO Make Forum Post] |

## Terminology

The key words "RECOMMENDED", "MUST", "MUST NOT", "SHOULD", and "SHOULD NOT" in this document are to be interpreted as described in BCP 14 [^bcp14] when, and only when, they appear in all capitals.

The terms "Registry Authority" (RA), "Real World Asset" (RWA), "Shielded Registry Asset" (SRA), "Asset Registry", and "Chain of Custody" are defined as in ZIP-SRA-EVENTS (Shielded Registry Chain-of-Custody Logs).

The term "Participant" refers to a natural person or legal entity that holds a share of the RA signing authority under the threshold scheme defined in this ZIP.

The term "Genesis Declaration" refers to the inaugural on-chain event that constitutes the RA as a cryptographic identity, anchoring its public key, scope of authority, governance charter, and real-world bindings to the Zcash shielded pool.

The term "Key Rotation" refers to the protocol by which an RA replaces its active signing public key with a new one, while preserving the continuity of its event log.

The term "Emergency Revocation" refers to the protocol by which an RA declares a signing key compromised and establishes a block-height boundary after which signatures from the compromised key are invalid.

The term "Real-World Binding" refers to a verifiable linkage between the RA's on-chain cryptographic identity and a real-world institutional identity, such as a domain name, legal document, or well-known URI. Bindings are informational commitments that aid verifier confidence; they are not enforced by consensus.

## Abstract

This ZIP specifies the key generation, custody, and lifecycle protocol for Registry Authorities (RAs) operating within the Shielded Registry Assets framework. It defines how an RA constitutes itself as a cryptographic identity using FROST (Flexible Round-Optimized Schnorr Threshold) Ed25519 threshold signing, how it publishes a Genesis Declaration to anchor that identity on-chain, how it establishes verifiable linkages to real-world institutional identity, and how it manages key rotation and emergency revocation over the lifetime of the registry.

The protocol is designed to eliminate single-point-of-failure key custody, survive succession events without requiring unanimous cooperation among participants, and provide verifiable continuity of signing authority across generational timescales. It does not encode legal ownership or governance structure on-chain; it provides the cryptographic foundation upon which the Chain-of-Custody Logs ZIP (ZIP-SRA-EVENTS) operates.

## Motivation

The Chain-of-Custody Logs ZIP assumes the existence of an RA Ed25519 signing keypair (PK_R, SK_R) and a master seed Seed_RA from which the registry Orchard branch is derived. It does not specify how this key material is generated, who controls it, or what happens when control must change.

This omission is not merely a documentation gap. It is a structural vulnerability. If RA signing authority is held by a single individual, the registry inherits all of the fragilities that the SRA architecture is designed to resist: key-person risk, succession discontinuity, and single-point compromise. An estate manager who holds the sole signing key *is* the registry; if they are incapacitated, coerced, or deceased, the registry's ability to issue new events ceases.

The thesis motivating this work (see Chapters 4-5 of Rodriguez-Larrain, 2026) established a non-negotiable design principle: **continuity cannot depend on the uninterrupted virtue or competence of a specific individual.** This ZIP operationalizes that principle at the cryptographic layer.

Furthermore, a cryptographic identity that cannot be traced back to a real-world institution is insufficient for a system whose entire purpose is to strengthen trust in physical assets governed by human institutions. The SRA architecture deliberately places the physical asset and the off-chain registry entry first; the Zcash ledger is a cryptographic audit trail that supports those facts. A verifier who can validate PK_R's signatures but cannot determine *who controls PK_R* or *under what legal authority* has verified a chain of mathematical consistency, not a chain of institutional trust. This ZIP therefore specifies real-world binding mechanisms that create bidirectional links between the on-chain cryptographic identity and off-chain institutional identity.

### Why Threshold Signing

A naive multisig approach (independent keys, M-of-N signature aggregation) would require verifiers to understand the multisig structure and validate multiple signatures per event. This leaks governance metadata and complicates the verification protocol defined in ZIP-SRA-EVENTS.

FROST Ed25519 threshold signing produces a single, standard Ed25519 signature that is indistinguishable from a single-signer signature. Verifiers validate against a single public key PK_R using the same procedure defined in ZIP-SRA-EVENTS, with no knowledge of or dependency on the underlying threshold structure. The governance complexity is hidden behind the signing ceremony; the verification interface remains simple.

## Requirements

- The protocol MUST produce standard Ed25519 signatures verifiable against a single public key, as required by ZIP-SRA-EVENTS.
- The protocol MUST support threshold signing such that any M of N participants can produce a valid signature, where M > N/2.
- The protocol MUST NOT require all participants to be available for routine signing operations.
- The protocol MUST define a Genesis Declaration format that anchors the RA's public key, scope, governance charter, and initial real-world bindings on-chain.
- The protocol MUST define at least one mechanism for bidirectional binding between on-chain identity and real-world institutional identity.
- The protocol MUST define key rotation and emergency revocation procedures that preserve event log continuity.
- The protocol MUST define a mechanism for updating real-world bindings without requiring key rotation.
- The protocol MUST tolerate the permanent loss of up to (N - M) participant shares without loss of signing capability.
- The protocol MUST NOT encode governance rules, legal ownership, or participant identities on-chain.
- The protocol MUST NOT make on-chain verification dependent on the availability of any off-chain resource (bindings are advisory, not consensus-critical).
- All on-chain artifacts MUST be expressible using NU6 Orchard shielded transactions with 512-byte memos.

## Specification

### Key Generation via FROST

#### Overview

The RA signing keypair is generated using the FROST (Flexible Round-Optimized Schnorr Threshold) protocol for Ed25519, as specified in draft-irtf-cfrg-frost [^frost-spec] and implemented in the Zcash ecosystem [^frost-zcash]. FROST produces a shared group public key PK_R and distributes secret key shares among N participants, such that any subset of M participants (where M > N/2) can cooperatively produce a valid Ed25519 signature against PK_R.

#### Parameters

Let N be the total number of participants.

Let M be the signing threshold, where M > N/2 and M <= N.

It is RECOMMENDED that small RAs (e.g. a single estate with 1-3 stewards) use a 2-of-3 configuration:

- Share 1: Primary steward's operational device
- Share 2: Primary steward's cold storage, held at a separate physical location
- Share 3: Designated recovery holder (legal counsel, ALMA delegate, or escrow service)

It is RECOMMENDED that larger RAs (e.g. a foundation committee) use an M-of-N configuration where M = ceil((N+1)/2) (simple majority).

#### Distributed Key Generation (DKG)

Participants execute the FROST Distributed Key Generation protocol [^frost-spec, section 5] to produce:

- A group public key PK_R (a standard Ed25519 public key)
- For each participant i in {1, ..., N}: a secret key share sk_i
- For each participant i: a public verification share PK_i

Each participant MUST store their secret key share sk_i in a manner consistent with the security requirements of their role (see Share Custody below).

The group public key PK_R becomes the RA's signing identity for all operations defined in ZIP-SRA-EVENTS.

#### Share Custody

Each participant MUST maintain physical control of their secret key share. Shares MUST NOT be stored on shared infrastructure, cloud services, or any system accessible to other participants.

It is RECOMMENDED that at least one share designated for cold storage be maintained on an air-gapped device or hardware security module, stored in a physically secure location (e.g. safe deposit box, fireproof safe) at a site geographically separate from operational shares.

The recovery share (in a 2-of-3 configuration) SHOULD be held by a party whose interests are aligned with the long-term continuity of the registry but who is institutionally independent of the estate's day-to-day operations.

### Seed Derivation

#### Registry Seed

In addition to the FROST group keypair, the RA requires a deterministic seed Seed_RA from which the Orchard registry branch FVK_Registry is derived (as specified in ZIP-SRA-EVENTS for per-asset key derivation).

Seed_RA MUST be a 256-bit value generated from a cryptographically secure random source during the key ceremony.

Seed_RA is NOT part of the FROST threshold scheme. It is a shared secret that must be known to the operational signing quorum in order to derive per-asset addresses.

It is RECOMMENDED that Seed_RA be encrypted under a symmetric key that is itself secret-shared among participants using Shamir's Secret Sharing with the same (M, N) threshold as the FROST signing scheme. This ensures that Seed_RA can be reconstructed by any signing quorum but is not held in plaintext by any single participant.

[TODO: Evaluate whether Seed_RA should be derived deterministically from the FROST group secret, eliminating the need for a separate shared secret. This simplifies the protocol but couples seed lifecycle to key rotation. Trade-offs need further analysis.]

#### Derivation Path

From Seed_RA, derive the registry Orchard full viewing key as per ZIP-32 [^zip32]:

    FVK_Registry = OrchardMasterFVK(Seed_RA) / purpose' / registry_id'

Where `purpose` and `registry_id` are defined constants. [TODO: Assign specific derivation path indices.]

Per-asset key derivation from FVK_Registry proceeds as specified in ZIP-SRA-EVENTS.

### Genesis Declaration

#### Purpose

The Genesis Declaration is the constitutional event of the RA. It transitions the registry from a legal/social entity to a cryptographic identity anchored on the Zcash blockchain. Until a Genesis Declaration is published, no valid SRA events can exist for this RA.

The Genesis Declaration also establishes the RA's initial set of real-world bindings -- verifiable linkages between the on-chain cryptographic identity and off-chain institutional identity. These bindings allow verifiers to answer the question that a signature alone cannot: *who controls this key, and under what authority?*

#### Payload Format

The Genesis Declaration is recorded as a shielded memo in a minimum-value Orchard transaction sent to a well-known "genesis address" derived from FVK_Registry.

Let GenesisPayload be:

    {
      version:          u8,           // Payload format version (1 for this ZIP)
      event_type:       u8,           // GENESIS = 0x01
      pk_r:             [u8; 32],     // The RA group public key (Ed25519)
      scope:            [u8; var],    // UTF-8 encoded scope declaration (see below)
      charter_hash:     [u8; 32],     // Blake2b-256 hash of the governance charter document
      bindings:         [Binding],    // Array of real-world bindings (see Real-World Bindings)
      frost_params:     {             // Threshold parameters (informational, not enforced on-chain)
        n:              u8,           // Total participants
        m:              u8,           // Signing threshold
      },
      timestamp:        u64,          // Unix timestamp of the ceremony
    }

The `scope` field contains a human-readable declaration of the RA's domain of authority, for example: "Official registry for the oeuvre of Emilio Rodriguez-Larrain". The scope field MUST NOT exceed 128 bytes.

The `bindings` array contains zero or more real-world binding records as defined in the Real-World Bindings section below. It is RECOMMENDED that the Genesis Declaration include at least one binding.

Let GenesisSignature = Ed25519.Sign(SK_R, Blake2b-256(GenesisPayload)) with ZIP-215 [^zip215] semantics, where the signature is produced cooperatively by the FROST signing quorum.

The memo bundle is: { GenesisPayload, GenesisSignature }

The total size of GenesisPayload + GenesisSignature (64 bytes) MUST NOT exceed the NU6 Orchard memo maximum of 512 bytes. RAs with many bindings MAY defer additional bindings to subsequent BINDING_UPDATE events.

#### Genesis Address Derivation

The Genesis Declaration is sent to a deterministic address derived from FVK_Registry:

    ID_Genesis = Blake2b-256("SRA-GENESIS") mod 2^31
    VK_Genesis = ChildFVK(FVK_Registry, ID_Genesis)
    UA_Genesis = OrchardReceiver(VK_Genesis)

This address is not associated with any specific artwork. It serves as the anchor point for RA-level lifecycle events (genesis, key rotation, revocation, and binding updates).

#### Verification

A verifier who possesses FVK_Registry and PK_R can:

1. Derive VK_Genesis and scan for notes at UA_Genesis
2. Decrypt the memo to recover GenesisPayload and GenesisSignature
3. Verify GenesisSignature against PK_R
4. Confirm that pk_r in the payload matches the PK_R used for verification
5. Optionally, check each binding in the `bindings` array against its off-chain counterpart (see Real-World Bindings: Verification)

A valid Genesis Declaration establishes PK_R as the authoritative signing key for all subsequent events under this FVK_Registry.

### Real-World Bindings

#### Motivation

A cryptographic key pair, by itself, carries no institutional meaning. PK_R proves that a holder of the corresponding secret key produced a signature; it does not prove that this holder is the legitimate steward of an artist's estate or the authorized operator of a catalogue raisonne. In the SRA architecture, legitimacy originates in the real world -- in legal mandates, institutional recognition, and professional reputation -- and the chain merely stabilizes and makes that legitimacy auditable.

Real-world bindings bridge this gap by creating verifiable, bidirectional linkages between the RA's on-chain identity and its off-chain institutional identity. The chain commits to a binding (pointing outward), and the off-chain resource commits to the chain (pointing inward). A verifier who checks both directions gains confidence not from either link alone, but from their mutual consistency.

Critically, bindings are *advisory*. They are not enforced by the Zcash consensus protocol. A transaction is valid or invalid based on its cryptographic properties; a binding is credible or not based on a verifier's judgment. This preserves the SRA principle that human institutions -- not blockchain protocols -- determine legitimacy.

#### Binding Record Format

Each binding is a typed record:

    Binding = {
      binding_type:     u8,           // Enumerated type (see below)
      value:            [u8; var],    // Type-specific value (see below)
    }

#### Binding Types

| Code | Type | Value Field | Off-chain Counterpart |
|------|------|-------------|----------------------|
| 0x01 | DNS | UTF-8 hostname (e.g. `_sra-authority.rodriguezlarrain.art`) | DNS TXT record containing hex-encoded PK_R or Blake2b-256(GenesisPayload) |
| 0x02 | LEGAL | Blake2b-256 hash of legal attestation document | The document itself (notarized affidavit, apostilled declaration, or equivalent) |
| 0x03 | URI | UTF-8 URL (e.g. `https://rodriguezlarrain.art/.well-known/sra-authority.json`) | Machine-readable JSON file at that URL containing PK_R and scope |
| 0x04 | ATTESTATION | Blake2b-256 hash of a third-party attestation payload | Signed attestation from a recognized third party (e.g. ALMA, a notary, an institutional verifier) |
| 0xFF | OTHER | Opaque bytes, interpreted by application-layer convention | Unspecified |

Each binding type creates a different kind of trust signal with different durability, cost, and evidentiary weight. They are designed to be composable: an RA that begins with only a DNS binding can later add a legal attestation and eventually receive an institutional counter-signature, each strengthening the aggregate confidence without invalidating prior bindings.

#### Type 0x01: DNS Binding

The RA publishes a DNS TXT record at the hostname specified in the binding's `value` field. The TXT record MUST contain one of:

- `sra-authority=<hex-encoded PK_R>`, or
- `sra-genesis=<hex-encoded Blake2b-256(GenesisPayload)>`

The DNS hostname SHOULD use the `_sra-authority` subdomain prefix (e.g. `_sra-authority.example.art`) to avoid collision with other TXT records, following the convention established by DKIM [^rfc6376] and DMARC [^rfc7489].

DNS bindings provide a lightweight, continuously verifiable signal. They are inexpensive to establish and can be checked programmatically by any verifier with DNS access. Their trust derives from the domain registration system: an attacker would need to compromise the RA's domain registrar account to forge this binding.

DNS bindings are inherently mutable (the RA controls the DNS zone) and therefore serve as a *liveness* signal rather than a *historical* commitment. A verifier who checks the DNS record at verification time confirms that the RA currently endorses this PK_R. The on-chain commitment to the hostname provides the historical anchor: the chain records *which domain* the RA claimed at genesis time, while the DNS record confirms the claim is still active.

#### Type 0x02: Legal Attestation Binding

The RA prepares a legal document -- such as a notarized affidavit, a declaration before a notary public, or an apostilled statement (for cross-jurisdictional validity under the Hague Convention [^hague]) -- that explicitly states:

1. The legal identity of the RA (registered name, jurisdiction, registration number if applicable)
2. The scope of authority claimed (which artist, which oeuvre)
3. The public key PK_R (hex-encoded) generated during the key ceremony
4. The date and circumstances of the key ceremony

The `value` field in the binding record contains Blake2b-256(document), where `document` is the canonical byte representation of the legal attestation.

The RA MUST retain the original document and make it available to verifiers upon request. The document itself is never placed on-chain.

Legal attestation bindings are heavyweight: they require professional services (notarization, apostille) and carry legal consequences for false statements. This weight is their strength. A legal attestation provides the strongest single-document evidence that a specific PK_R is controlled by a specific legal entity with a specific mandate. It is particularly important for RAs operating across jurisdictions, where a buyer in one country must evaluate the legitimacy of an estate governed by the laws of another.

#### Type 0x03: URI Binding

The RA hosts a machine-readable file at the URL specified in the binding's `value` field. The file MUST be served over HTTPS and SHOULD be placed at a well-known path:

    https://<domain>/.well-known/sra-authority.json

The file MUST contain a JSON object with at minimum:

    {
      "sra_version": 1,
      "pk_r": "<hex-encoded PK_R>",
      "scope": "<scope string matching the Genesis Declaration>",
      "genesis_hash": "<hex-encoded Blake2b-256(GenesisPayload)>",
      "contact": "<contact URI for the RA>"
    }

URI bindings serve a dual purpose: they provide a machine-readable discovery mechanism for automated verifiers, and they provide a human-readable web presence that contextualizes the RA's identity. They are complementary to DNS bindings -- the DNS record provides a minimal, infrastructure-level signal, while the URI endpoint provides richer metadata.

Like DNS bindings, URI bindings are mutable and serve as a liveness signal. They are also dependent on the continued availability of the hosting infrastructure. RAs SHOULD NOT rely solely on URI bindings for long-term verifiability.

#### Type 0x04: Attestation Binding

A recognized third party -- such as ALMA, a notary service, an institutional verifier, or another RA -- produces a signed attestation that the Genesis Declaration corresponds to a legitimate RA. The attestation is a structured document (format to be specified in a companion ZIP or ALMA governance standard) containing at minimum:

1. The attester's identity and public key
2. The PK_R being attested
3. The scope being attested
4. The basis for the attestation (e.g. "verified legal documentation", "in-person key ceremony witness")
5. The attester's signature over the above

The `value` field in the binding record contains Blake2b-256(attestation), where `attestation` is the canonical byte representation of the third-party attestation document.

Attestation bindings provide *social* trust rather than *institutional* trust. Their weight depends on the reputation and verification standards of the attester. An attestation from ALMA carries different evidentiary weight than an attestation from an unknown entity. This is by design: the SRA architecture does not attempt to flatten trust into a single boolean; it provides structured signals that verifiers evaluate according to their own risk models.

[TODO: Define the attestation payload format, or defer to a separate ALMA-focused ZIP.]

#### Verification

Binding verification is RECOMMENDED but not consensus-critical. A verifier MAY check any subset of bindings according to its own risk model. The following verification procedures are defined:

**DNS (0x01):** Query the DNS TXT record at the specified hostname. Confirm that the record contains a value matching PK_R or Blake2b-256(GenesisPayload). If the DNS record is absent or does not match, the binding is *stale* (not necessarily fraudulent -- the RA may have lost control of the domain or may not have published the record yet).

**Legal (0x02):** Obtain the legal attestation document from the RA (out-of-band). Compute Blake2b-256(document) and confirm it matches the binding value. Evaluate the document's legal validity according to the verifier's jurisdictional standards.

**URI (0x03):** Fetch the JSON file at the specified URL over HTTPS. Confirm that `pk_r` and `scope` match the Genesis Declaration. If the URL is unreachable or the content does not match, the binding is *stale*.

**Attestation (0x04):** Obtain the attestation document from the RA or the attester (out-of-band). Compute Blake2b-256(attestation) and confirm it matches the binding value. Verify the attester's signature. Evaluate the attester's credibility according to the verifier's own trust model.

A binding is in one of three states:

| State | Meaning |
|-------|---------|
| **Active** | The on-chain commitment and the off-chain counterpart are mutually consistent |
| **Stale** | The on-chain commitment exists but the off-chain counterpart is absent, unreachable, or inconsistent |
| **Revoked** | The RA has explicitly revoked the binding via a BINDING_UPDATE event |

Stale bindings are not proof of fraud. Domain names expire, web servers go offline, and legal documents may be superseded. A stale binding reduces confidence but does not invalidate the Genesis Declaration or any signed events. Verifiers SHOULD weight active bindings more heavily than stale ones and SHOULD consider the age and diversity of bindings when assessing overall confidence.

### Binding Updates

#### Purpose

Real-world bindings evolve over the lifetime of a registry. An RA may acquire a new domain, obtain an ALMA attestation, update a legal document after governance changes, or need to revoke a binding whose off-chain counterpart has been compromised (e.g. domain hijacking).

Binding updates allow the RA to add, replace, or revoke bindings without requiring key rotation. This decouples the lifecycle of institutional identity from the lifecycle of cryptographic identity.

#### Payload Format

A BINDING_UPDATE event is sent to UA_Genesis:

    {
      version:          u8,
      event_type:       u8,           // BINDING_UPDATE = 0x06
      action:           u8,           // ADD = 0x01, REVOKE = 0x02
      binding:          Binding,      // The binding record being added or revoked
      reason:           [u8; var],    // UTF-8 human-readable reason (optional, max 64 bytes)
      clock:            u64,          // Lamport clock for RA lifecycle events at UA_Genesis
      prev_hash:        [u8; 32],     // Blake2b-256 of previous lifecycle event payload
    }

Let BindingSignature = Ed25519.Sign(SK_R, Blake2b-256(BindingUpdatePayload))

The memo bundle is: { BindingUpdatePayload, BindingSignature }

#### Semantics

**ADD (0x01):** Appends a new binding to the RA's binding set. Verifiers who scan UA_Genesis reconstruct the current binding set by replaying all Genesis and BINDING_UPDATE events in clock order.

**REVOKE (0x02):** Marks a previously added binding as revoked. The binding record in the REVOKE event MUST exactly match the binding record in the prior ADD (or Genesis). Revoked bindings remain visible in the event history (the chain is append-only) but verifiers MUST treat them as inactive.

A binding MAY be re-added after revocation (e.g. if a domain is recovered after hijacking). The re-addition is a new ADD event; the prior revocation remains in the historical record.

#### Replacing a Binding

To replace a binding (e.g. updating a legal attestation after a governance change), the RA issues a REVOKE for the old binding followed by an ADD for the new binding. These MAY be anchored in the same transaction (as two events in the memo bundle if space permits) or in consecutive transactions.

### Key Rotation

#### Motivation

Key rotation is required when:

- A participant permanently leaves the RA (death, resignation, removal)
- A participant's share is suspected of exposure but not confirmed compromised
- The RA's governance structure changes (e.g. new committee members)
- Proactive cryptographic hygiene (periodic rotation)

Key rotation is a *planned* transition. For suspected active compromise, see Emergency Revocation below.

#### Procedure

1. The current signing quorum (M of N holders of the *old* key) and the incoming participants execute a new FROST DKG, producing a new group public key PK_R' and new shares.

2. If Seed_RA is being retained (typical case), the symmetric key protecting it is re-shared among the new participant set.

3. The RA constructs a Key Rotation Event and sends it to UA_Genesis:

        {
          version:          u8,
          event_type:       u8,           // KEY_ROTATION = 0x02
          old_pk_r:         [u8; 32],     // The outgoing public key
          new_pk_r:         [u8; 32],     // The incoming public key
          effective_height: u64,          // Block height after which new_pk_r is authoritative
          rotation_reason:  u8,           // Enumerated reason code (see below)
          frost_params:     {             // New threshold parameters
            n:              u8,
            m:              u8,
          },
          clock:            u64,          // Lamport clock for RA lifecycle events at UA_Genesis
          prev_hash:        [u8; 32],     // Blake2b-256 of previous lifecycle event payload
        }

4. This payload is signed by BOTH the old key and the new key. The memo bundle contains:

    { RotationPayload, OldSignature, NewSignature }

Where OldSignature = Ed25519.Sign(SK_R, Blake2b-256(RotationPayload)) and NewSignature = Ed25519.Sign(SK_R', Blake2b-256(RotationPayload)).

The dual signature proves that the old authority endorsed the transition and that the new authority is capable of signing. Together, they establish continuity.

5. After `effective_height`, verifiers MUST accept only signatures from PK_R' for new events. Signatures from PK_R on events anchored *before* `effective_height` remain valid.

6. After key rotation, existing real-world bindings that reference PK_R become stale. The RA SHOULD issue BINDING_UPDATE events to add updated bindings referencing PK_R' (e.g. updating the DNS TXT record, issuing a new legal attestation, refreshing the well-known URI). The RA SHOULD revoke bindings whose off-chain counterparts have been updated so that the on-chain record reflects the current state.

#### Rotation Reason Codes

| Code | Name | Description |
|------|------|-------------|
| 0x01 | SUCCESSION | Participant change due to death or incapacity |
| 0x02 | GOVERNANCE | Governance restructuring (new committee composition) |
| 0x03 | SECURITY | Proactive rotation due to suspected (not confirmed) exposure |
| 0x04 | SCHEDULED | Routine periodic rotation |
| 0xFF | OTHER | Unspecified reason |

### Emergency Revocation

#### Motivation

Emergency revocation is the response to confirmed or strongly suspected key compromise. Unlike key rotation, it is adversarial: the RA must assume the compromised key is actively being used to forge events.

#### Procedure

Emergency revocation requires a pre-committed recovery path. At the time of the Genesis Declaration, the RA SHOULD also anchor a Recovery Commitment:

    {
      version:          u8,
      event_type:       u8,           // RECOVERY_COMMITMENT = 0x03
      recovery_pk:      [u8; 32],     // Public key of the cold recovery keypair
      activation_delay: u64,          // Minimum block height gap between revocation and reactivation
    }

The recovery keypair is a separate Ed25519 keypair generated during the initial key ceremony. It is RECOMMENDED that the recovery secret key be:

- Generated on an air-gapped device
- Split via Shamir's Secret Sharing with a *different* participant set than the operational FROST shares (e.g. including external legal counsel, ALMA representatives)
- Stored in cold custody with no network-connected copies

Upon confirmed or suspected compromise of the operational FROST key:

1. The recovery key holders reconstruct the recovery secret key.

2. They anchor an Emergency Revocation Event to UA_Genesis:

        {
          version:          u8,
          event_type:       u8,           // EMERGENCY_REVOCATION = 0x04
          revoked_pk:       [u8; 32],     // The compromised public key
          revocation_height: u64,         // Block height after which revoked_pk is invalid
          clock:            u64,
          prev_hash:        [u8; 32],
        }

Signed by the recovery key: RecoverySignature = Ed25519.Sign(SK_Recovery, Blake2b-256(RevocationPayload))

3. After anchoring the revocation, the RA executes a new FROST DKG to generate PK_R' and then issues a standard Key Rotation Event (co-signed by the recovery key standing in for the old key).

4. Verifiers who encounter the Emergency Revocation Event MUST reject any event signed by `revoked_pk` at or after `revocation_height`, even if the event's Lamport clock and hash chain appear valid.

5. Upon key rotation following emergency revocation, all existing real-world bindings are implicitly stale (they reference a revoked key). The RA MUST issue BINDING_UPDATE events to establish fresh bindings under the new key. This is especially critical for DNS and URI bindings, which automated verifiers may check programmatically.

#### Dispute Window

Between `revocation_height` and the anchoring of a new Key Rotation Event, the registry is in a *suspended* state for new events. Existing events anchored before `revocation_height` remain valid. This window is bounded by `activation_delay` from the Recovery Commitment.

This suspension is an intentional design choice. A brief interruption in the ability to issue new events is preferable to the silent acceptance of forged events from a compromised key.

### Cryptographic Agility

#### Migration Hook

This ZIP specifies Ed25519 and Blake2b-256 as the signature and hash primitives. These choices are appropriate for current threat models and are consistent with the Zcash protocol's existing primitives.

However, a registry designed for multi-generational timescales MUST anticipate the eventual obsolescence of any specific cryptographic scheme.

The `version` field in all payload formats defined in this ZIP enables forward-compatible upgrades. A future version of this ZIP MAY specify:

- Post-quantum signature schemes (e.g. SPHINCS+, Dilithium) as alternatives to Ed25519
- Updated hash functions as alternatives to Blake2b-256
- Hybrid signing (classical + post-quantum) during a transition period

When migrating to a new signature scheme, the RA issues a Key Rotation Event under the *current* scheme that commits to the new public key under the *new* scheme. This provides a cryptographic bridge: the historical chain remains verifiable under the old scheme, while new events are protected by the new scheme.

#### Ledger Migration

If the underlying Zcash blockchain undergoes a network upgrade that changes transaction semantics, or if the RA elects to migrate to a different anchoring substrate, the RA SHOULD issue a final event under the current scheme containing the digest of the most recent validated event, then anchor that digest on the new substrate. This creates a verifiable reference chain across substrates without "moving" the historical record.

## Privacy Implications

This ZIP introduces no changes to consensus rules, transaction formats, or address/key types. All on-chain activity consists of standard Orchard shielded transactions and memos, inheriting the existing privacy properties of the Zcash shielded pool.

The `frost_params` (N, M) in the Genesis Declaration and Key Rotation events reveal the *size* of the signing quorum but not the identities of participants. This metadata is encrypted within shielded memos and visible only to holders of VK_Genesis. It is RECOMMENDED that RAs treat frost_params as operational metadata and limit disclosure of VK_Genesis accordingly.

The `scope` field in the Genesis Declaration contains a human-readable description of the RA's domain. Since this is encrypted in shielded memos, it is visible only to holders of VK_Genesis, not to the public chain.

Participant identities are never recorded on-chain. The mapping between FROST share indices and natural persons is maintained off-chain by the RA and is outside the scope of this protocol.

Real-world bindings introduce a deliberate, controlled exception to the privacy-by-default posture of the SRA architecture. While binding records themselves are encrypted within shielded memos (visible only to VK_Genesis holders), the *off-chain counterparts* of bindings are inherently public or semi-public: DNS TXT records are publicly queryable, well-known URIs are publicly accessible, and legal attestations may be disclosed to verifiers. This is by design. The purpose of bindings is precisely to create a verifiable bridge between the shielded on-chain identity and a public real-world identity. An RA that wishes to maximize privacy MAY choose to include only a LEGAL binding (whose document is disclosed selectively to specific verifiers) rather than DNS or URI bindings (which are publicly discoverable). The choice of binding types reflects the RA's own trade-off between discoverability and privacy.

RAs SHOULD be aware that DNS and URI bindings, once published, create a publicly observable association between a domain name and the existence of an SRA registry. While the contents of the registry remain shielded, the *existence* of a registry for a given domain becomes discoverable. This may be acceptable or even desirable for most RAs (who benefit from discoverability) but should be considered by RAs operating under heightened privacy constraints.

## Adversary Model

This ZIP assumes the following adversary capabilities:

- An adversary may compromise up to (M - 1) participant shares without being able to forge signatures.
- An adversary who compromises M or more shares can forge signatures. The Emergency Revocation protocol limits the damage window.
- An adversary who controls the RA's network connectivity can delay but not forge events (events are ordered by the Zcash consensus clock, not by the RA).
- An adversary cannot rewrite events already anchored in the Orchard commitment tree without a 51% attack on the Zcash network.
- An adversary who compromises the RA's domain registrar can forge or remove DNS bindings. Verifiers SHOULD NOT rely on DNS bindings alone; DNS bindings are strengthened by corroboration with LEGAL or ATTESTATION bindings.
- An adversary who compromises the RA's web hosting can forge or remove URI bindings. The same corroboration principle applies.
- An adversary cannot forge a LEGAL binding without producing a fraudulent legal document, which carries legal consequences independent of the SRA protocol.
- An adversary cannot forge an ATTESTATION binding without compromising the attester's signing key.

The protocol does NOT protect against:

- A corrupt quorum of M or more participants who collude to issue false events. This is a governance failure, not a cryptographic one, and is addressed at the institutional layer (ALMA).
- Total loss of all N shares (the registry becomes unable to issue new events but historical events remain verifiable).

## Rationale

### Why FROST Rather Than On-Chain Multisig

Zcash does not currently support on-chain multisig for Orchard shielded transactions. Even if it did, on-chain multisig would expose governance structure (number of signers, signing patterns) on the public chain. FROST produces a single standard Ed25519 signature, preserving the privacy properties of the shielded pool and maintaining compatibility with the simple verification protocol defined in ZIP-SRA-EVENTS.

### Why a Separate Recovery Key

The FROST threshold key and the recovery key serve different threat models. The FROST key is optimized for operational availability (M-of-N, with M chosen for daily usability). The recovery key is optimized for adversarial resilience (cold storage, different custodians, higher activation barriers). Combining these into a single mechanism would force a trade-off between operational convenience and emergency robustness.

### Why the Genesis Declaration Includes Scope

Without an on-chain scope declaration, an RA's key could be ambiguously applied to assets outside its legitimate domain. The scope field provides a human-readable boundary that can be checked by verifiers and referenced in dispute resolution. It is informational rather than enforced by consensus, consistent with the SRA principle that human institutions (not blockchain protocols) determine legitimacy.

### Why Real-World Bindings Are Advisory, Not Consensus-Critical

A binding that is enforced by consensus would require the Zcash network to evaluate off-chain state (DNS records, legal documents, web endpoints). This is architecturally impossible in a decentralized system and would introduce oracle dependencies that contradict the SRA's design philosophy. Instead, bindings are structured *signals* that verifiers evaluate according to their own risk models -- just as a buyer evaluates a certificate of authenticity, a provenance document, or an institutional endorsement. The chain preserves the *commitment*; the verifier evaluates the *credibility*.

This design also ensures graceful degradation. If a DNS record expires or a web server goes offline, the registry continues to function. Events can still be signed, anchored, and verified. Only the *confidence* in the RA's real-world identity is affected, not the *integrity* of the event chain. This separation of concerns -- integrity from identity, cryptographic validity from institutional legitimacy -- is fundamental to the SRA architecture.

### Why Bindings Are Composable and Incremental

Estates operate under resource constraints. Requiring a full suite of bindings at genesis time would create an adoption barrier. A small estate might launch with only a DNS binding, later add a legal attestation when it engages counsel for a sale, and later still receive an ALMA attestation as the network matures. The incremental model allows confidence to accrue over time without requiring upfront completeness -- mirroring the Progressive Anchoring Model for artworks defined in the thesis (section 5.2.3).

### Why Bindings Are Bidirectional

A unidirectional binding (chain points to domain, but domain doesn't point back) proves only that the RA *claims* a domain. It does not prove the domain holder *endorses* the claim. Bidirectionality -- the chain commits to a hostname, and the DNS record commits to PK_R -- closes this gap. An attacker who compromises only the chain cannot forge the DNS record; an attacker who compromises only the domain cannot forge the on-chain commitment. Both must be consistent for the binding to be active.

This is the same verification pattern used by DKIM for email authentication and by Keybase for identity proofs. It is well-understood, widely deployed, and does not require novel trust assumptions.

## Event Type Summary

| Code | Event Type | Section |
|------|-----------|---------|
| 0x01 | GENESIS | Genesis Declaration |
| 0x02 | KEY_ROTATION | Key Rotation |
| 0x03 | RECOVERY_COMMITMENT | Emergency Revocation |
| 0x04 | EMERGENCY_REVOCATION | Emergency Revocation |
| 0x06 | BINDING_UPDATE | Binding Updates |

[Note: 0x05 is reserved for future use.]

## Open Questions

1. **Seed_RA coupling**: Should Seed_RA be derived deterministically from the FROST group secret? This would eliminate a separate shared secret but would couple seed lifecycle to key rotation, requiring re-derivation of all per-asset keys on rotation. Trade-offs need further analysis.

2. **Recovery commitment timing**: Must the Recovery Commitment be anchored simultaneously with the Genesis Declaration, or can it be added later? Early commitment is safer but may be impractical for RAs that bootstrap incrementally.

3. **Cross-RA key ceremonies**: When multiple RAs coordinate under ALMA, should there be a standardized ceremony format for mutual verification of Genesis Declarations? This may belong in a separate ALMA-focused ZIP.

4. **Offline signing UX**: The FROST signing protocol requires at least two rounds of communication among participants. For air-gapped shares, this implies a physical ceremony. Practical guidance on ceremony logistics (which is out of scope for this ZIP) would be valuable as a companion document.

5. **Share refresh without rotation**: FROST supports proactive share refresh (re-randomizing shares without changing the group public key). Should this be specified as a separate event type, or is it purely an off-chain operational matter?

6. **Attestation payload format**: Should the format for Type 0x04 ATTESTATION bindings be defined in this ZIP, in a companion ZIP, or in an ALMA governance standard? The answer depends on whether attestation interoperability is needed before ALMA is operational.

7. **Binding expiry**: Should bindings carry an explicit expiry timestamp, or is staleness (failure of off-chain verification) sufficient? Explicit expiry would allow automated verifiers to flag aging bindings proactively, but adds payload size and complexity.

8. **DNS record format standardization**: Should the DNS TXT record format be registered with IANA as an underscore-prefixed service name per RFC 8552 [^rfc8552]? This would formalize the `_sra-authority` prefix but requires an IANA registration process.

## References

[^bcp14]: Information on BCP 14 -- "RFC 2119: Key words for use in RFCs to Indicate Requirement Levels" and "RFC 8174: Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words"

[^frost-spec]: Komlo, C. and Goldberg, I., "FROST: Flexible Round-Optimized Schnorr Threshold Signatures", draft-irtf-cfrg-frost. https://datatracker.ietf.org/doc/draft-irtf-cfrg-frost/

[^frost-zcash]: Zcash Foundation, "FROST for Zcash". https://frost.zfnd.org/

[^zip32]: ZIP 32: Shielded Hierarchical Deterministic Wallets. https://zips.z.cash/zip-0032

[^zip215]: ZIP 215: Explicitly Defining and Modifying Ed25519 Validation Rules. https://zips.z.cash/zip-0215

[^zip316]: ZIP 316: Unified Addresses and Unified Viewing Keys. https://zips.z.cash/zip-0316

[^zip317]: ZIP 317: Proportional Transfer Fee Mechanism. https://zips.z.cash/zip-0317

[^rfc6376]: Crocker, D., Hansen, T., and M. Kucherawy, "DomainKeys Identified Mail (DKIM) Signatures", RFC 6376. https://datatracker.ietf.org/doc/html/rfc6376

[^rfc7489]: Kucherawy, M. and E. Zwicky, "Domain-based Message Authentication, Reporting, and Conformance (DMARC)", RFC 7489. https://datatracker.ietf.org/doc/html/rfc7489

[^rfc8552]: Crocker, D., "Scoped Interpretation of DNS Resource Records through 'Underscored' Naming of Attribute Leaves", RFC 8552. https://datatracker.ietf.org/doc/html/rfc8552

[^hague]: Hague Conference on Private International Law, "Convention Abolishing the Requirement of Legalisation for Foreign Public Documents" (Apostille Convention), 1961. https://www.hcch.net/en/instruments/conventions/full-text/?cid=41

## Reference Implementation

https://sras.io
