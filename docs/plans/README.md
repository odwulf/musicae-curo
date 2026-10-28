# Musicae Curo Architecture & Feature Plans

This directory contains the foundational specifications, architectural blueprints, and feature designs developed during the evolution of Musicae Curo (`curo`).

These documents serve as Architectural Decision Records (ADRs) explaining the mathematical, structural, and operational design choices of the project.

---

## Plan Directory

| Document | Focus & Scope |
| :--- | :--- |
| **[01. Foundational Schedulable CLI Client Plan](01_foundational_cli_plan.md)** | Initial headless CLI architecture, Tidal session management, daily discovery archiving, and megamix generation. |
| **[02. Modular Architecture & Rebranding Plan](02_modular_architecture_rebranding.md)** | Transition from single-vendor CLI to clean Hexagonal Architecture (`MusicProvider` SPI, ProviderRegistry, and domain models). |
| **[03. Playlist Versioning & Availability Alerting Plan](03_playlist_versioning_and_alerts.md)** | Cryptographic SHA-256 change detection, deduplicated snapshots, historical track registry (`track_registry.json`), and delisted track alerts. |
| **[04. Playlist Track Replacement & Scoring Algorithm](04_playlist_track_replacement_scoring.md)** | 100-point explainable similarity scoring algorithm, tiered heuristic candidate discovery, acoustic/live mismatch penalties, and repair engine. |
| **[05. Cross-Provider Playlist Comparison & Sync Plan](05_cross_provider_sync_and_compare.md)** | Cross-platform playlist comparison, one-way mirror and append synchronization, ISRC and UPC barcode resolution, and dry-run reports. |

---

## Architectural Principles

All implementations in Musicae Curo follow strict boundaries:
1. **Hexagonal Architecture**: Core services (`curo.services`) and domain models (`curo.core`) depend strictly on the abstract `MusicProvider` interface and never import platform SDKs (`tidalapi`, `spotipy`).
2. **Explainable Diagnostics**: Operations that repair or modify music libraries produce transparent, auditable scores and dry-run reports before live mutations.
3. **Data Preservation**: Historical metadata (ISRCs, album barcodes, release years, and proactive provider replacements) are cached permanently to survive catalog delistings.
