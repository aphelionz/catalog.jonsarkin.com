# Shielded Registry Selective Disclosure Workflows

Part of Shielded Registry Assets v0.1

## Preamble

| Field | Value |
|-------|-------|
| ZIP | TBD |
| Title | Shielded Registry Selective Disclosure Workflows |
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

The terms "Registry Authority" (RA), "Real World Asset" (RWA), "Shielded Registry Asset" (SRA), "Canonical Artwork ID" (CAID), "Chain of Custody", "Dossier Snapshot", "Snapshot Digest", and all event types are defined as in ZIP-SRA-EVENTS (Shielded Registry Chain-of-Custody Logs).

The terms "Genesis Declaration", "Key Rotation", and "Real-World Binding" are defined as in ZIP-SRA-KEYGEN (Shielded Registry Authority Key Ceremony).

The term "Disclosure Package" refers to a structured bundle of cryptographic material and contextual metadata that the RA transmits to a verifier to grant them the ability to reconstruct and authenticate a specific asset's chain of custody.

The term "Verifier" refers to any party that receives a Disclosure Package and uses it to independently validate an asset's event log. Verifiers include but are not limited to: prospective buyers, auction houses, insurers, museum registrars, lending institutions, scholars, and law enforcement.

The term "Disclosure Grant" refers to the RA's deliberate act of transmitting a Disclosure Package to a specific verifier for a stated purpose.

The term "Disclosure Ledger" refers to the RA's off-chain record of all Disclosure Grants issued, including the identity of the recipient, the purpose, the date, and the scope of material disclosed.

## Abstract

This ZIP specifies the protocols by which Registry Authorities (RAs) selectively disclose per-asset viewing keys and supporting documentation to verifiers within the Shielded Registry Assets framework.

ZIP-SRA-EVENTS defines how events are recorded on-chain. ZIP-SRA-KEYGEN defines how the RA constitutes and maintains its cryptographic identity. This ZIP completes the triad by defining how the RA *opens windows* into the shielded record -- packaging viewing keys with contextual metadata, calibrating disclosure to verifier needs, managing tiered dossier access, and maintaining an auditable record of who has been granted visibility into which assets.

The protocol is designed to make verification cumulative rather than episodic: each disclosure event produces reusable informational residue that lowers the coordination cost of future transactions. It does not enforce access control on-chain (which would contradict the SRA's privacy architecture); it provides a structured framework for the RA's deliberate, off-chain disclosure decisions.

## Motivation

### The Problem: Privacy Without Disclosure Is Useless

The SRA architecture achieves privacy by default through Zcash's shielded pool. Per-asset viewing keys ensure that no party can observe an asset's chain of custody without the RA's explicit cooperation. This privacy is essential -- it protects collectors from exposure, shields commercial relationships, and respects the confidentiality norms of the art market.

But privacy without a structured path to disclosure is a vault without a door. The entire value proposition of the SRA depends on the RA's ability to *selectively reveal* an asset's integrity chain to parties who need it: a buyer conducting pre-acquisition due diligence, an insurer pricing coverage, a museum registrar evaluating a loan, a scholar reconstructing exhibition history. If disclosure is ad hoc -- communicated through emails, PDF attachments, verbal assurances -- then the SRA reproduces the very reset dynamic it was designed to eliminate.

### The Problem: Repeated Verification Resets

In the status quo model, when a collector seeks to sell a work, verification is re-performed from scratch. Auction houses commission independent research. Insurers demand fresh provenance documentation. Museums conduct months of archival reconciliation before accepting a loan. Each actor incurs its own private coordination cost because trust is not reusable. The estate's prior verification work does not automatically compound into lower friction for future transactions.

This repeated reset dynamic imposes a structural discount on works whose documentation is fragmented and disincentivizes estates from investing in documentation because the benefits dissipate across the market rather than accruing to the estate.

The SRA reconfigures this by making verification cumulative -- but only if disclosure itself is structured. A viewing key that is transmitted without context, without stated purpose, and without an auditable record of who received it is only marginally better than a PDF certificate. This ZIP provides the structure.

### The Problem: Disclosure Without Accountability

A viewing key, once transmitted, cannot be technically revoked (the Zcash protocol does not support retroactive revocation of viewing key access). This means that disclosure is a one-way door: once a verifier has VK_Asset, they can read the asset's full event history in perpetuity, including events anchored after the disclosure grant.

This irrevocability makes disciplined disclosure essential. The RA must know what it has disclosed, to whom, for what purpose, and when. Without this discipline, viewing keys proliferate without oversight, and the privacy properties of the shielded pool erode in practice even though they hold in theory.

## Requirements

- The protocol MUST define a standard format for Disclosure Packages that bundles cryptographic material with contextual metadata.
- The protocol MUST support disclosure of per-asset chain-of-custody logs to individual verifiers without revealing information about other assets.
- The protocol MUST define a mechanism for the RA to maintain an auditable record of all Disclosure Grants.
- The protocol MUST support tiered dossier disclosure, allowing the RA to calibrate the depth of off-chain evidence shared with different verifier classes.
- The protocol MUST NOT require on-chain transactions for disclosure operations (disclosure is an off-chain process).
- The protocol MUST NOT introduce mechanisms for technical revocation of viewing key access (which is impossible in the Zcash protocol) but MUST provide guidance for managing the consequences of irrevocable disclosure.
- The protocol SHOULD define standard verifier roles with recommended disclosure tiers to promote interoperability across RAs.
- All disclosure metadata formats MUST be expressible in standard, machine-readable formats (JSON).

## Specification

### Disclosure Package

#### Purpose

A Disclosure Package is the atomic unit of selective disclosure. It contains everything a verifier needs to independently reconstruct and authenticate a specific asset's chain of custody, packaged with contextual metadata that identifies the disclosure's scope and purpose.

#### Format

A Disclosure Package is a JSON object with the following structure:

    {
      "sra_version":        1,
      "package_id":         "<UUID v4>",
      "issued_at":          "<ISO 8601 timestamp>",
      "expires_at":         "<ISO 8601 timestamp or null>",

      "registry_authority": {
        "pk_r":             "<hex-encoded RA public key>",
        "scope":            "<scope string from Genesis Declaration>",
        "genesis_hash":     "<hex-encoded Blake2b-256(GenesisPayload)>",
        "bindings":         [
          {
            "type":         "DNS",
            "value":        "_sra-authority.example.art"
          }
        ]
      },

      "asset": {
        "id_registry":      "<hex-encoded ID_Registry>",
        "vk_asset":         "<hex-encoded per-asset viewing key>",
        "description":      "<brief asset description>",
        "evidence_status":  "<REGISTERED | PROVISIONAL | DISPUTED | UNVERIFIED>",
        "event_count":      <integer>,
        "latest_clock":     <integer>,
        "latest_hash":      "<hex-encoded Blake2b-256 of most recent EventPayload>"
      },

      "disclosure": {
        "purpose":          "<stated purpose of this disclosure>",
        "verifier_role":    "<role code (see Verifier Roles)>",
        "dossier_tier":     "<tier code (see Dossier Tiers)>",
        "conditions":       "<human-readable conditions or restrictions, if any>",
        "contact":          "<RA contact URI>"
      }
    }

#### Field Descriptions

The `registry_authority` block provides the verifier with the information needed to validate the RA's identity. The verifier can check the RA's real-world bindings as specified in ZIP-SRA-KEYGEN to establish confidence that PK_R belongs to a legitimate authority.

The `asset` block contains the per-asset viewing key (VK_Asset) -- the core cryptographic material that enables chain-of-custody verification -- plus summary metadata that lets the verifier know what to expect before scanning the chain. The `event_count` and `latest_clock` fields are informational snapshots at the time of package creation; the verifier's own chain scan is authoritative.

The `disclosure` block records the RA's intent: why this package was created, what role the verifier occupies, what tier of dossier access is being granted, and any conditions the RA attaches to the disclosure.

The `expires_at` field is advisory, not technically enforceable. It signals the RA's expectation of when the verifier's need for access ends. Since viewing keys cannot be revoked, expiry is a social contract rather than a technical control. See Disclosure Lifecycle below.

#### Signing

The Disclosure Package SHOULD be signed by the RA to prevent tampering in transit:

    {
      "package":    <the JSON object above>,
      "signature":  "<hex-encoded Ed25519 signature over Blake2b-256(canonical JSON of package)>"
    }

The signature is produced using the RA's current signing key (SK_R, via FROST threshold signing). A verifier who receives a signed Disclosure Package can confirm that it was produced by the same authority whose events appear on-chain.

[TODO: Define canonical JSON serialization for deterministic signing. Options include RFC 8785 (JSON Canonicalization Scheme) or a simpler sorted-keys approach.]

#### Transport

Disclosure Packages are transmitted out-of-band. This ZIP does not mandate a specific transport mechanism. Common transports include:

- Encrypted email (PGP/S-MIME)
- Secure file transfer
- In-person delivery (USB device, printed QR code)
- Authenticated API endpoint hosted by the RA

The RA SHOULD use an encrypted transport to protect VK_Asset in transit. A Disclosure Package transmitted in plaintext exposes the viewing key to any intermediary.

For high-value transactions or institutional due diligence, in-person delivery with identity verification is RECOMMENDED. The Disclosure Package can be encoded as a QR code for scanning into verification software.

### Verifier Roles

#### Purpose

Different parties interact with the SRA for different reasons and require different depths of information. A prospective buyer needs to verify provenance and authenticity. An insurer needs condition and valuation documentation. A scholar needs exhibition history and attribution evidence. A law enforcement agency investigating theft needs ownership chain and status records.

Standardized verifier roles allow RAs to calibrate disclosure consistently and allow verifiers to communicate their needs in a common vocabulary. They also enable ALMA (when operational) to define recommended disclosure standards per role.

#### Role Definitions

| Code | Role | Typical Verifiers | Primary Interest |
|------|------|-------------------|-----------------|
| BUYER | Prospective Acquirer | Collectors, dealers, galleries | Provenance continuity, authenticity, title clarity |
| CONSIGNOR | Consignment Partner | Auction houses, dealers accepting consignment | Provenance, title, legal encumbrances, condition |
| INSURER | Insurance Underwriter | Insurance companies, risk assessors | Condition, valuation history, loss/theft status, custody chain |
| LENDER | Lending Institution | Banks, collateral-based lenders | Title verification, valuation, encumbrance status |
| MUSEUM | Institutional Registrar | Museums, exhibition organizers | Provenance, attribution, condition, loan history |
| SCHOLAR | Academic Researcher | Art historians, cataloguers, critics | Attribution, dating, exhibition history, revision trail |
| CONSERVATOR | Conservation Professional | Restorers, conservation labs | Condition history, prior interventions, materials documentation |
| LEGAL | Legal/Regulatory Authority | Law enforcement, customs, regulatory bodies | Ownership chain, stolen/lost status, title disputes |
| ALMA | Network Peer | Other ALMA-member RAs, ALMA governance body | Cross-registry verification, attestation, standard compliance |
| OTHER | Unspecified | Any party not fitting above categories | As stated in disclosure purpose |

These roles are advisory. An RA MAY assign any role to any verifier based on its own judgment. The roles exist to promote consistency across RAs and to enable automated disclosure policies in future tooling.

### Dossier Tiers

#### Purpose

The Dossier Snapshot committed on-chain via Snapshot Digest may contain highly sensitive material: insurance valuations, private correspondence, confidential expert opinions, high-resolution photography. Not every verifier needs -- or should receive -- the full dossier.

Dossier tiers define graduated levels of off-chain evidence disclosure that the RA provides alongside the Disclosure Package. The on-chain integrity chain (accessible via VK_Asset) is the same for all tiers; what varies is the depth of supporting documentation disclosed out-of-band.

#### Tier Definitions

| Code | Tier | Contents | Typical Use |
|------|------|----------|-------------|
| CHAIN_ONLY | Chain Verification | Disclosure Package only. No off-chain dossier material. The verifier can scan the chain and verify the event log but receives no supporting documentation. | Preliminary inquiry, initial interest, automated verification |
| SUMMARY | Summary Dossier | Disclosure Package + a summary report prepared by the RA. Includes: basic provenance outline, current attribution, key exhibition dates, current condition grade, and thumbnail photography. Does NOT include valuations, detailed correspondence, or full expert reports. | Pre-acquisition research, loan inquiry, scholarly overview |
| STANDARD | Standard Dossier | Disclosure Package + structured documentary package. Includes everything in SUMMARY plus: full provenance chain with dates and transaction types, complete exhibition history, full-resolution photography (standard views), conservation summary, and relevant catalog raisonne entries. Does NOT include financial valuations, confidential correspondence, or internal RA deliberations. | Active due diligence, consignment evaluation, museum loan processing |
| FULL | Full Dossier | Disclosure Package + the complete Dossier Snapshot as committed on-chain. Includes everything in STANDARD plus: insurance valuations, detailed conservation reports, expert opinion letters, relevant correspondence, forensic analysis results, and any other material included in the Snapshot Digest. | Major acquisition, institutional accession, legal proceeding, insurance claim |

#### Tier Selection

The RA selects the dossier tier based on the verifier's role, the stated purpose of disclosure, the sensitivity of the material, and any applicable legal or contractual obligations. The following matrix provides RECOMMENDED defaults:

| Verifier Role | Recommended Default Tier |
|--------------|-------------------------|
| BUYER | SUMMARY (upgrade to STANDARD upon serious interest) |
| CONSIGNOR | STANDARD |
| INSURER | FULL |
| LENDER | FULL |
| MUSEUM | STANDARD (upgrade to FULL for accession) |
| SCHOLAR | SUMMARY (upgrade to STANDARD upon institutional affiliation) |
| CONSERVATOR | STANDARD |
| LEGAL | FULL |
| ALMA | STANDARD |

These defaults are RECOMMENDED, not mandatory. An RA may deviate based on circumstances, relationship, contractual requirements, or risk assessment.

#### Tier Upgrades

A verifier who has received a lower-tier disclosure MAY request an upgrade. The RA evaluates the request and, if approved, issues a new Disclosure Package at the higher tier. The original Disclosure Package remains valid; the upgrade supplements but does not replace it.

Tier upgrades are recorded in the Disclosure Ledger (see below) as separate Disclosure Grants.

### Disclosure Lifecycle

#### Disclosure Grant

A Disclosure Grant is the RA's deliberate act of transmitting a Disclosure Package to a specific verifier. Each grant is a discrete, auditable event in the RA's off-chain Disclosure Ledger.

The RA MUST record the following for each Disclosure Grant:

    DisclosureGrant = {
      package_id:       "<UUID matching the Disclosure Package>",
      granted_at:       "<ISO 8601 timestamp>",
      verifier_id:      "<RA's internal identifier for the verifier>",
      verifier_role:    "<role code>",
      verifier_name:    "<legal or organizational name, if known>",
      asset_id:         "<ID_Registry of the disclosed asset>",
      dossier_tier:     "<tier code>",
      purpose:          "<stated purpose>",
      expires_at:       "<advisory expiry, or null>",
      conditions:       "<any conditions attached>",
      transport:        "<how the package was transmitted>",
      notes:            "<optional RA notes>"
    }

The Disclosure Ledger is maintained off-chain by the RA. It is NOT anchored on the Zcash blockchain (doing so would create a linkage between disclosure events and asset addresses, degrading privacy).

#### Irrevocability and Its Consequences

Viewing keys in Zcash cannot be retroactively revoked. Once a verifier possesses VK_Asset, they can read the asset's full event history -- including events anchored after the disclosure grant -- in perpetuity. This is a fundamental property of the cryptographic design, not a limitation that can be engineered away.

The SRA addresses this irrevocability through *social and institutional* mechanisms rather than technical ones:

**Advisory Expiry:** The Disclosure Package includes an `expires_at` field. After this date, the RA considers the disclosure morally (though not technically) expired. A verifier who continues to use the viewing key after expiry is operating outside the stated terms of disclosure. This has no cryptographic effect but may have contractual, reputational, or legal consequences depending on the terms agreed between the RA and verifier.

**Contractual Controls:** The RA MAY require verifiers to sign a Disclosure Agreement before receiving a Disclosure Package. This agreement may include: non-redistribution clauses (the verifier agrees not to share VK_Asset with third parties), purpose limitation (the verifier agrees to use the viewing key only for the stated purpose), and notification obligations (the verifier agrees to notify the RA if they become aware of unauthorized access).

**Key Rotation as a Hard Reset:** If the RA determines that disclosure of a particular VK_Asset has become a liability (e.g. a viewing key has been leaked or redistributed beyond its intended audience), the RA can rotate the underlying key material. This requires a Seed_RA rotation (see ZIP-SRA-KEYGEN), which re-derives all per-asset keys. Previously issued viewing keys become invalid. This is an extreme measure -- it invalidates *all* outstanding Disclosure Packages for *all* assets -- and SHOULD be reserved for cases of systemic compromise.

[TODO: Evaluate whether per-asset key rotation (without rotating Seed_RA) is feasible. This would require re-anchoring the asset's event log under a new VK_Asset, which is costly but less disruptive than a full seed rotation.]

**Disclosure Audit:** The Disclosure Ledger allows the RA to audit who has access to what. If a leak is suspected, the RA can review the ledger to identify potential sources. While this does not prevent leaks, it provides forensic traceability.

#### Disclosure to Multiple Verifiers

An RA may disclose the same asset to multiple verifiers simultaneously. Each disclosure is an independent Grant with its own package_id, purpose, tier, and conditions. The Disclosure Ledger records each grant separately.

Verifiers do not learn about each other's existence through the protocol. Two verifiers holding VK_Asset for the same artwork can both scan the chain independently, but neither knows the other has access. This is a privacy property: disclosure to a buyer does not reveal that an insurer has also been granted access.

### Verification Reports

#### Purpose

After receiving a Disclosure Package and scanning the chain, a verifier may wish to produce a structured report summarizing their findings. Verification Reports are not part of the SRA protocol itself -- they are application-layer artifacts -- but standardizing their format promotes interoperability and enables verification labor to compound.

A Verification Report produced by one verifier can be presented to another party as evidence that independent verification was performed. This is the mechanism by which due diligence becomes reusable rather than episodic.

#### Recommended Format

    VerificationReport = {
      "sra_version":        1,
      "report_id":          "<UUID v4>",
      "report_date":        "<ISO 8601 timestamp>",

      "verifier": {
        "name":             "<verifier's legal or organizational name>",
        "role":             "<role code>",
        "contact":          "<contact URI>"
      },

      "asset": {
        "id_registry":      "<hex-encoded ID_Registry>",
        "description":      "<brief asset description>",
        "evidence_status":  "<current evidentiary status from chain>"
      },

      "chain_summary": {
        "events_verified":  <integer>,
        "first_event":      "<ISO 8601 timestamp of REGISTER block>",
        "latest_event":     "<ISO 8601 timestamp of most recent event block>",
        "hash_chain_valid": <boolean>,
        "signatures_valid": <boolean>,
        "open_disputes":    <integer>,
        "revisions_count":  <integer>
      },

      "ra_authority": {
        "pk_r":             "<hex-encoded PK_R>",
        "genesis_verified": <boolean>,
        "bindings_checked": [
          {
            "type":         "DNS",
            "status":       "ACTIVE | STALE | NOT_CHECKED"
          }
        ]
      },

      "dossier_review": {
        "tier_received":    "<tier code>",
        "snapshot_digest_match": <boolean>,
        "notes":            "<verifier's assessment of documentary evidence>"
      },

      "conclusion": {
        "integrity_assessment": "<CONSISTENT | ANOMALOUS | INCOMPLETE>",
        "notes":            "<verifier's overall assessment>"
      }
    }

The `integrity_assessment` field reflects the verifier's judgment on the chain of custody as a whole:

| Assessment | Meaning |
|-----------|---------|
| CONSISTENT | The event log is internally consistent: hash chain valid, signatures valid, clock monotonic, no unexplained gaps. The RA's real-world bindings are active. Documentary evidence (if reviewed) is consistent with on-chain commitments. |
| ANOMALOUS | The event log contains anomalies: gaps in the clock sequence (suggesting omitted events), stale or missing RA bindings, or discrepancies between on-chain commitments and off-chain documentation. |
| INCOMPLETE | Verification could not be completed: chain scan failed, viewing key invalid, RA authority could not be verified, or insufficient evidence to assess. |

A Verification Report SHOULD be signed by the verifier. A signed report can be presented to third parties as evidence of independent verification, reducing the need for those parties to repeat the full verification process. This is the mechanism by which the SRA converts episodic due diligence into compounding informational residue.

### Interoperability with Due Diligence Frameworks

#### Responsible Art Market (RAM) Alignment

The Responsible Art Market (RAM) initiative articulates principles and practical guidance for responsible conduct in art market transactions, including provenance research, anti-money-laundering compliance, and risk assessment. RAM presupposes that the outputs of due diligence are reusable -- that once provenance is verified, that verification persists and compounds. In practice, this assumption frequently fails: due diligence is conducted in the form of internal memoranda, private PDFs, or transaction-specific reports that do not survive the transaction.

The SRA disclosure protocol renders RAM principles executable by providing the persistence layer RAM implicitly requires. Specifically:

**Cumulative Verification:** Each Disclosure Grant and subsequent Verification Report contributes to a growing body of independent attestations about an asset's integrity. When a RAM-compliant actor receives a Disclosure Package, their verification labor becomes a reusable artifact that lowers the marginal cost of diligence for the next actor in the chain.

**Standardized Output:** The Verification Report format provides a structured, machine-readable summary that can be shared with other RAM-compliant actors. An auction house that verifies an asset's chain of custody and produces a signed Verification Report can present that report to a buyer, who can independently confirm its claims by scanning the chain themselves.

**Compliance Premium:** Works with deep chain-of-custody logs, active RA bindings, and a history of independent Verification Reports become easier and cheaper to transact. The reduced friction translates into measurable economic advantages: lower insurance premiums, faster consignment acceptance, stronger collateral potential, and greater institutional willingness to lend or exhibit. This compliance premium makes disciplined participation in the SRA framework economically rational.

#### Institutional Due Diligence Pathways

For institutional actors (museums, banks, insurers), the disclosure framework maps directly onto existing due diligence workflows:

**Museum Loan Processing:** A museum registrar receives a Disclosure Package at STANDARD tier. They scan the chain, verify the integrity of the event log, review the provided documentation, and produce a Verification Report. This report becomes part of the museum's internal loan file. If the work is later considered for acquisition, the registrar requests a tier upgrade to FULL and conducts deeper review.

**Insurance Underwriting:** An insurer receives a FULL-tier Disclosure Package. They verify the chain, review condition reports and valuation history, and assess risk based on the event log (including any STATUS events indicating prior damage, loss, or theft). The structured event history replaces the manual provenance research that currently consumes underwriting time.

**Collateral-Based Lending:** A lender receives a FULL-tier Disclosure Package. They verify title through the chain of TRANSFER events, assess encumbrance risk through STATUS events, and evaluate documentary strength through the evidentiary status taxonomy. The viewing key provides ongoing monitoring capability: the lender can detect new events (including STATUS changes like STOLEN or DISPUTED) in real-time by rescanning the chain.

## Privacy Implications

This ZIP does not introduce on-chain transactions or modify any consensus rules. All disclosure operations occur off-chain.

The primary privacy consideration is the irrevocability of viewing key disclosure. Once VK_Asset is transmitted, the recipient has permanent read access to the asset's event log. RAs must treat each Disclosure Grant as a deliberate act with irreversible consequences.

The Disclosure Ledger itself is a sensitive document that maps verifier identities to specific assets. RAs MUST protect the Disclosure Ledger with the same rigor applied to other confidential estate records. Compromise of the Disclosure Ledger would reveal the RA's network of commercial and institutional relationships.

Disclosure Packages, when transmitted, contain VK_Asset in cleartext (within the JSON structure). Interception of a Disclosure Package in transit grants the interceptor the same access as the intended verifier. RAs MUST use encrypted transport mechanisms.

Verification Reports, if shared among parties, can reveal that independent verification was performed on a specific asset. This may indirectly signal market activity (e.g. a buyer is conducting due diligence, suggesting an impending transaction). Verifiers SHOULD be aware of this signal when deciding whether and how to share reports.

## Adversary Model

- An adversary who intercepts a Disclosure Package in transit gains VK_Asset and can read the asset's full event history. Encrypted transport mitigates this.
- An adversary who compromises the RA's Disclosure Ledger learns which parties have been granted access to which assets, revealing commercial relationships. Physical and digital security of the ledger mitigate this.
- An adversary who receives a legitimate Disclosure Grant and redistributes VK_Asset to unauthorized parties cannot be technically prevented from doing so. Contractual controls and disclosure audit provide forensic accountability.
- An adversary cannot forge a signed Disclosure Package without compromising the RA's signing key.
- An adversary cannot forge a signed Verification Report without compromising the verifier's signing key.
- The protocol does not protect against a verifier who misrepresents the contents of a Verification Report. Third parties who rely on a report SHOULD independently verify critical claims by scanning the chain themselves.

## Rationale

### Why Disclosure Is Off-Chain

On-chain disclosure events would create linkages between the RA's addresses and verifier identities, degrading the privacy properties of the shielded pool. If every disclosure were recorded as a transaction, an observer could correlate disclosure patterns with market activity, identifying which assets are being actively marketed or evaluated. Keeping disclosure entirely off-chain preserves the unlinkability of on-chain events.

### Why Dossier Tiers Rather Than All-or-Nothing

The art market operates on graduated trust. A preliminary inquiry does not warrant full financial disclosure. A scholarly inquiry does not require insurance valuations. An insurer assessing risk, however, needs everything. All-or-nothing disclosure would either over-expose sensitive material to casual inquirers or under-serve serious institutional actors. Tiered disclosure calibrates information flow to the verifier's actual need.

### Why the Disclosure Ledger Is RA-Managed, Not Shared

A shared or decentralized disclosure ledger would create a surveillance tool. If any party could see who had been granted access to which assets, the disclosure infrastructure would become a competitive intelligence resource rather than a trust mechanism. The RA manages its own ledger because the RA is the party that bears the consequences of disclosure decisions.

### Why Verification Reports Are Standardized But Optional

Standardization enables composability: a report produced by one verifier can be interpreted by another without bespoke translation. But mandating reports would impose an obligation on verifiers that the SRA has no authority to enforce. Reports are recommended because they amplify the value of verification labor; they are optional because the SRA respects the sovereignty of verifiers.

### Why Advisory Expiry Rather Than Technical Revocation

Zcash viewing keys cannot be revoked by protocol design -- this is a feature, not a bug. The shielded pool's privacy guarantees depend on the impossibility of selective surveillance. Advisory expiry is the honest design choice: it communicates the RA's expectations without pretending to enforce them technically. Contractual and reputational mechanisms provide the enforcement layer that cryptography cannot.

## Open Questions

1. **Canonical JSON for signing**: Which JSON canonicalization scheme should be used for deterministic signing of Disclosure Packages and Verification Reports? RFC 8785 (JCS) is the IETF standard but adds a dependency. A simpler sorted-keys-with-no-whitespace approach may suffice.

2. **Per-asset key rotation**: Is it feasible to rotate VK_Asset for a single asset without rotating Seed_RA? This would require re-anchoring the asset's event log under a new viewing key (a migration event), which is costly but would allow targeted revocation of disclosure access. This is the most impactful open design question in the disclosure framework.

3. **Disclosure Package encryption**: Should the Disclosure Package format include a standard encryption envelope (e.g. NaCl sealed box using the verifier's public key) rather than relying on transport-layer encryption? This would provide end-to-end confidentiality regardless of transport mechanism.

4. **Machine-readable disclosure agreements**: Should the protocol define a standard format for Disclosure Agreements (the contractual terms a verifier accepts before receiving a package)? This would enable automated disclosure workflows but veers into legal territory that may be better addressed by ALMA governance standards.

5. **Verification Report registry**: Should signed Verification Reports be submittable back to the RA for inclusion in the Disclosure Ledger? This would create a growing record of independent attestations, but raises questions about who curates the report collection and how conflicting reports are handled.

6. **Bulk disclosure for estate-wide due diligence**: Some verifiers (e.g. ALMA auditors, estate-wide insurance underwriters) may need access to the full registry, not a single asset. Should the protocol define an "estate-level" Disclosure Package that discloses FVK_Registry rather than individual VK_Assets? This is a significant privacy step and should be carefully bounded.

7. **Disclosure to legal authorities**: Law enforcement requests may require disclosure without the RA's cooperation (e.g. under court order). Should the protocol define a mechanism for third-party custodians (legal counsel, ALMA) to hold escrow copies of viewing keys for this purpose? This intersects with the recovery key design in ZIP-SRA-KEYGEN.

## References

[^bcp14]: Information on BCP 14 -- "RFC 2119: Key words for use in RFCs to Indicate Requirement Levels" and "RFC 8174: Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words"

[^zip316]: ZIP 316: Unified Addresses and Unified Viewing Keys. https://zips.z.cash/zip-0316

[^rfc8785]: Rundgren, A., Jordan, B., and S. Erdtman, "JSON Canonicalization Scheme (JCS)", RFC 8785. https://datatracker.ietf.org/doc/html/rfc8785

## Reference Implementation

https://sras.io
