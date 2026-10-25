# Musicae Curo (`curo`) -- Complete Technical Documentation

*Universal music library custodian, playlist protector, and daily discovery mix archiver.*

Musicae Curo is a modular, provider-agnostic music library custodian CLI application designed for headless automation, playlist preservation, and algorithmic mix archival across music streaming platforms.

> [!NOTE]
> **Disclaimer & Project Origins**: This project was conceived to address personal music curation and playlist preservation workflows. All Python code and architectural designs were implemented with the aid of an AI coding agent. The author is not a software engineer and provides this repository openly as-is for the benefit of the community.

---

## 1. Architecture & Design Principles

Musicae Curo adheres to strict Clean Architecture and Hexagonal / Port-and-Adapter principles:

```
                            +--------------------------+
                            |      Linux Cron Job      |
                            |   (e.g., 06:00 Daily)    |
                            +------------+-------------+
                                         |
                                         v
                                curo job run <name>
                                         |
       +---------------------------------+---------------------------------+
       |                                 |                                 |
       v                                 v                                 v
 [MixService]                   [PlaylistService]               [SnapshotService]
 - Dynamic discovery (1..N)     - Playlist CRUD                 - SHA-256 versioning
 - Dual deduplication (ID+ISRC) - Track chunking (50/req)       - Track history registry
 - Fisher-Yates shuffle         - JSON/M3U8/CSV export          - Availability alerts log
       |                                 |                                 |
       +---------------------------------+---------------------------------+
                                         |
                                         v
                           [PlaylistRepairService]
                           - Scans unavailable tracks
                           - Multi-tiered candidate discovery
                           - 100-pt explainable scoring engine
                           - Manual approval safeguards
                                         |
                                         v
                         +-------------------------------+
                         |   MusicProvider SPI (Core)    |
                         +---------------+---------------+
                                         |
      +------------------+---------------+---------------+------------------+
      |                  |               |               |                  |
      v                  v               v               v                  v
[TidalProvider]   [DeezerProvider][SpotifyProvider][QobuzProvider]  [YTMusicProvider]
 (tidalapi SDK)      (Scaffold)      (Scaffold)      (Scaffold)        (Scaffold)
```

### Core Domain Models (`curo.core.models`)
All domain services operate exclusively on pure, provider-agnostic domain models:
- **`Track`**: Normalized track representation (`id`, `title`, `duration_seconds`, `isrc`, `available`, `allow_streaming`, `stream_ready`, `artist`, `collaborating_artists`, `album`, `provider`).
- **`Album`**: Normalized album container (`id`, `title`, `upc`, `release_date`, `artists`, `provider`).
- **`Artist`**: Normalized artist profile (`id`, `name`, `provider`).
- **`Playlist`**: Container for user or curated playlists (`id`, `title`, `description`, `is_public`, `track_count`, `duration_seconds`, `last_updated`, `provider`).
- **`CuratedMix`**: Ephemeral algorithmic mix representation (`id`, `title`, `sub_title`, `mix_type`, `provider`).

### Provider SPI (`curo.core.provider.MusicProvider`)
Every music streaming platform implements `MusicProvider`:
- **Authentication**: `login()`, `logout()`, `get_auth_info()`, `is_authenticated()`.
- **Playlists**: `get_user_playlists()`, `get_playlist()`, `get_playlist_tracks()`, `create_playlist()`, `add_tracks_to_playlist()`, `remove_track_from_playlist_by_index()`, `clear_playlist()`.
- **Discovery Mixes**: `get_daily_discovery_mix()`, `get_daily_mixes()`.
- **Search & Identifiers**: `get_track()`, `get_tracks_by_isrc()`, `get_album()`, `get_albums_by_barcode()`, `get_album_tracks()`, `search_tracks()`.

### Provider Registry (`curo.core.registry.ProviderRegistry`)
Adapters self-register into `ProviderRegistry`. At runtime, the active provider is chosen via:
1. Command line option: `-p / --provider <name>` (e.g., `curo mix list -p tidal`).
2. Local configuration default: `curo provider default <name>`.
3. Default fallback: `"tidal"`.

---

## 2. Directory Structure & File Permissions

Musicae Curo manages its configuration and state in a secure dedicated directory:

| Path | Description | Permissions |
| :--- | :--- | :--- |
| `~/.config/curo/` | Root configuration directory | `0700` (`rwx------`) |
| `~/.config/curo/config.json` | General config (default provider, SMTP setup, paths) | `0600` (`rw-------`) |
| `~/.config/curo/jobs.yaml` | Declarative scheduled job definitions | `0600` (`rw-------`) |
| `~/.config/curo/tidal_session.json` | OAuth token cache for Tidal adapter | `0600` (`rw-------`) |
| `~/.config/curo/snapshots/<slug>/` | Incremental versioned SHA-256 JSON backups | `0700` (`rwx------`) |
| `~/.config/curo/track_registry.json` | Historical healthy track metadata registry | `0600` (`rw-------`) |
| `~/.config/curo/availability_alerts.log` | Append-only log of delisted tracks | `0600` (`rw-------`) |
| `~/.config/curo/curo.log` | Persistent debug and cron operation logs | `0600` (`rw-------`) |
| `./reports/` | Diagnostic JSON reports generated during playlist repairs | Project root (git-ignored) |

---

## 3. Dynamic Algorithmic Mix Archiving

### Zero-Assumption Mix Discovery
Tidal and other streaming providers dynamically generate daily mixes depending on listening history. Accounts may have anywhere from 1 to 15 mixes.
`MixService` queries mixes without hardcoded count assumptions:
- Matches `My Daily Discovery` by mix type (`DISCOVERY_MIX`) or keyword.
- Identifies all `Daily Mix N` instances, naturally sorting numbers (`Daily Mix 2` before `Daily Mix 10`).
- Discards non-audio mixes (e.g. video mixes).

### Daily Megamix & Deduplication Pipeline
1. **Harvesting**: Gathers all tracks from `My Daily Discovery` and all `Daily Mixes`.
2. **Dual-Layer Deduplication**:
   - Track ID check: Filters exact duplicate objects.
   - ISRC check: Filters identical sound recordings across different albums or remasters.
3. **Fisher-Yates Shuffle**: Randomizes track order.
4. **Chunked Addition**: Ingests tracks in batches of 50 to prevent request timeouts.
5. **Modes**:
   - `replace` (default): Clears the Megamix and injects fresh tracks.
   - `append`: Appends new tracks without duplicates.

---

## 4. Playlist Versioning & Historical Recovery

### SHA-256 Versioned Snapshots
Running `curo playlist snapshot` executes incremental versioning:
1. Fetches playlist tracks and calculates a canonical content hash over `id`, `isrc`, and `duration_seconds`.
2. Reads the playlist's local `manifest.json`.
3. If the hash matches the last snapshot, no file is written (`status: unchanged`).
4. If modified, a new version file `YYYY-MM-DD_<version>.json` is saved, computing exact additions, removals, and reorderings.

### Historical Track Metadata Registry (`track_registry.json`)
When streaming services delist or retire a track, its metadata (such as ISRC or album UPC) is often erased from active API responses.
`SnapshotService` automatically registers every healthy track encountered into `track_registry.json`. When a track becomes delisted:
- `CandidateFinder` queries `track_registry.json` using the retired track's ID.
- Recovers the original ISRC, Album UPC, and release date.
- Queries the streaming provider by ISRC to locate an active reissue or replacement with high confidence.

---

## 5. Intelligent Playlist Repair & Scoring Engine

`PlaylistRepairService` identifies unavailable tracks (`available: false` or `allow_streaming: false`) and scores potential replacements using a 100-point explainable algorithm:

| Factor | Points | Evaluation Details |
| :--- | :--- | :--- |
| **ISRC Match** | +30 | Exact sound recording match (authoritative cross-album identifier). |
| **Album UPC** | +15 | Exact album barcode match (tracks from the exact same physical/digital release). |
| **Title Match** | +30 | Normalized string similarity (strips remaster dates, version tags, audio markers). |
| **Artist Match** | +25 | Exact primary artist ID (+25) or contributing artist ID match (+22 to +24). |
| **Duration Delta** | +15 | Exact duration (+15), scaling down to 0 at $\ge 15$ seconds difference. |
| **Release Year** | +5 | Exact date (+5), same release year (+3). |
| **Live Penalty** | -40 | Penalty if candidate is "Live" while original was studio (requires manual approval). |
| **Acoustic Penalty** | -35 | Penalty if candidate is "Acoustic" while original was standard (requires manual approval). |

### Replacement Safety Safeguards
- **100% Threshold Default**: Default CLI runs require an exact match ($\ge 0.80$ with `--threshold 0.8`).
- **Dry-Run by Default**: Generates diagnostic reports in `./reports/repair_<slug>_<date>.json` without modifying streaming playlists. Pass `--live` to mutate playlists.
- **Atomic Two-Step Swap**: When replacing a track live:
  1. Calls `provider.add_tracks_to_playlist(pl_id, [new_id], position=idx)`.
  2. If and only if the provider confirms addition, calls `provider.remove_track_from_playlist_by_index(pl_id, idx + 1)`.
  3. If addition fails or is skipped by the platform, the original track is **never** removed.

---

## 6. Email Notifications & Change Digests

Musicae Curo includes a built-in notification engine (`curo notify`) for headless server alerts:

- **Daily Change Digest**: Dispatched when playlists are modified or delisted tracks are discovered. Includes itemized track additions, removals, and availability warnings.
- **All Playlists Baseline Overview**: Dispatched on first run or on demand (`curo notify send --all`). Provides an executive summary of all personal playlists, track counts, and availability health.
- **Supported SMTP Configurations**:
  - Direct TLS / SSL (Port 465).
  - STARTTLS (Port 587 or Port 25 for internal mail relays).
  - Unauthenticated local SMTP relays or authenticated remote servers.

---

## 7. CLI Command Reference

### Global Options
- `-c, --config <path>`: Custom configuration directory (default: `~/.config/curo`).
- `-p, --provider <name>`: Target streaming provider (e.g. `tidal`, `deezer`, `spotify`).
- `-q, --quiet`: Suppress banners and progress bars (ideal for cron).
- `--json`: Output machine-readable JSON.
- `-v, --verbose`: Enable debug logging.

### Authentication (`curo auth`)
```bash
curo auth login [-p tidal]      # Interactive OAuth device flow
curo auth status [-p tidal]     # Check authentication status
curo auth logout [-p tidal]     # Clear saved credentials
```

### Providers (`curo provider`)
```bash
curo provider list              # List all registered providers and active status
curo provider default <name>    # Set default provider (e.g. tidal)
```

### Playlists (`curo playlist`)
```bash
curo playlist list
curo playlist show <id|title>
curo playlist create <title> [--description <desc>] [--public]
curo playlist export <id> [--format json|m3u8|csv] [--output <path>]
curo playlist snapshot [id] [--no-alert] [--notify] [--all-mail]
curo playlist alerts [--limit 50]
curo playlist repair <id> [--threshold 0.8] [--live]
curo playlist replace <id> --track <id|#pos> --with <id|isrc|upc> [--live]
```

### Albums (`curo album`)
```bash
curo album show <id>
curo album export <id> [--format json|m3u8|csv] [--output <path>]
```

### Mixes (`curo mix`)
```bash
curo mix list
curo mix backup-discovery [--playlist-name "Custom Name"]
curo mix megamix [--mode replace|append] [--limit 100]
```

### Automated Jobs (`curo job`)
```bash
curo job list                   # List defined jobs in jobs.yaml
curo job run <job_name>         # Execute a job headless
curo job cron-help              # Generate Linux crontab syntax
```

### Notifications (`curo notify`)
```bash
curo notify config --host <smtp> --port <port> [--tls/--ssl] --user <user> --password <pass> --to-addr <email>
curo notify test                # Verify SMTP delivery
curo notify send [--all]        # Dispatch notification manually
```
