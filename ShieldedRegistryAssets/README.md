# Shielded Registry Assets

An [Omeka S](https://omeka.org/s/) module for managing privacy-preserving provenance records anchored on the [Zcash](https://z.cash) shielded pool.

> **Status:** v0.1.0-dev — under active development, not yet suitable for production use.

## What it does

Shielded Registry Assets enables a **Registry Authority** (RA) to maintain tamper-evident, privacy-preserving chain-of-custody records for artworks and cultural objects. Each asset gets a unique shielded address on Zcash; custody events are recorded as shielded transactions with signed memo payloads. Selective disclosure allows the RA to share per-asset viewing keys with verifiers without exposing unrelated holdings.

The module provides an admin interface for:

- **Keygen ceremony orchestration** — configure FROST threshold parameters (M-of-N), record the resulting public key, and track ceremony status
- **Registry operations** — register assets, record transfers, update status *(planned)*
- **Selective disclosure** — generate disclosure packages for verifiers *(planned)*

Cryptographic operations (FROST DKG, Zcash transactions) are delegated to external tools. This module handles orchestration, data management, and the admin UI.

## Requirements

- Omeka S 4.0+
- PHP 8.0+

## Installation

Copy or symlink the `ShieldedRegistryAssets` directory into your Omeka S `modules/` folder, then activate it in **Admin > Modules**.

## Specifications

The `doc/` directory contains the draft ZIP standards that define the protocol:

| Document | Scope |
|----------|-------|
| [ZIP-SRA-KEYGEN](doc/ZIP-SRA-KEYGEN.md) | Key ceremony, FROST threshold signing, genesis declaration |
| [ZIP-SRA-EVENTS](doc/ZIP-SRA-EVENTS.md) | Chain-of-custody event logs on Zcash Orchard |
| [ZIP-SRA-DISCLOSURE](doc/ZIP-SRA-DISCLOSURE.md) | Selective disclosure workflows and verifier roles |
| [Art market thesis](doc/Art%20market%20thesis.md) | Conceptual foundation: cultural entropy and regenerative stewardship |

Specification documents are licensed under [CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/).

## License

Module code is released under the [MIT License](LICENSE).
