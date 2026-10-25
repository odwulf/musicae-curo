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
[TidalProvider]   [SpotifyProvider]   [DeezerProvider] [QobuzProvider]  [YTMusicProvider]
 (tidalapi SDK)     (spotipy SDK)        (Scaffold)       (Scaffold)        (Scaffold)
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
- **Favorites / Liked Songs**: `get_favorite_tracks()` (full pagination over thousands of saved tracks).
- **Playlists**: `get_user_playlists()`, `get_playlist()`, `get_playlist_tracks()`, `create_playlist()`, `add_tracks_to_playlist()`, `remove_track_from_playlist_by_index()`, `clear_playlist()`.
- **Discovery Mixes**: `get_daily_discovery_mix()`, `get_daily_mixes()`.
- **Search & Identifiers**: `get_track()`, `get_tracks_by_isrc()`, `get_album()`, `get_albums_by_barcode()`, `get_album_tracks()`, `search_tracks()`.

### Provider Registry (`curo.core.registry.ProviderRegistry`)
Adapters self-register into `ProviderRegistry`. At runtime, the active provider is chosen via:
1. Command line option: `-p / --provider <name>` (e.g., `curo mix list -p tidal`).
2. Local configuration default: `curo provider default <name>`.
3. Default fallback: `"tidal"`.

### Provider Implementation Status

| Provider | Identifier (`-p`) | Status | Implementation Details |
| :--- | :--- | :--- | :--- |
| **Tidal** | `tidal` | **Active (Fully Implemented)** | Device code OAuth, playlists, mixes, search, and live repair via `tidalapi`. |
| **Spotify** | `spotify` | **Active (Fully Implemented)** | OAuth2 authorization code / PKCE flow, playlists, algorithmic mixes, search, and catalog lookup via `spotipy`. |
| **Deezer** | `deezer` | **Planned (Scaffolded Stub)** | Interface defined in `curo.providers.deezer`; ready for community implementation. |
| **Qobuz** | `qobuz` | **Planned (Scaffolded Stub)** | Interface defined in `curo.providers.qobuz`; ready for community implementation. |
| **YouTube Music** | `ytmusic` / `youtube_music` | **Planned (Scaffolded Stub)** | Interface defined in `curo.providers.ytmusic`; ready for community implementation. |

> [!IMPORTANT]
> **Tidal and Spotify are currently active, fully operational providers.** The other providers (Deezer, Qobuz, YouTube Music) are scaffolded architectural stubs ready for future integration. Attempting to run library or playback operations against scaffolded stubs will clearly inform you that the adapter is not yet implemented.

### Provider Authentication & Membership Requirements

| Provider | Status | Membership Required for API? | Auth Flow | Credentials Needed |
| :--- | :--- | :--- | :--- | :--- |
| **Tidal** | **Active** | **Yes (Paid Subscription)**<br>Free tier was shut down in April 2024. | OAuth 2.0 Device Flow | None (built-in client, approve in browser/phone) |
| **Spotify** | **Active** | **Yes (Paid Premium for App Owner)**<br>Developer platform requires Premium for app creation. | OAuth 2.0 Authorization Code | Spotify Developer Client ID & Client Secret |
| **Deezer** | **Active** | **No (Free or Premium)**<br>Free accounts support playlist management & catalog search. | Direct Token / OAuth 2.0 | Access Token or Deezer App ID & Secret |
| **Qobuz** | *Scaffolded* | **Yes (Paid Account)**<br>Streaming & full library access require Studio/Sublime. | User Login + App ID | Qobuz App ID & App Secret |
| **YouTube Music** | *Scaffolded* | **No (Free Google Account)**<br>YouTube Premium is not required for playlist API. | Browser Cookie / OAuth | `oauth.json` or request header cookies |

#### Authenticating with Tidal

Tidal requires an active paid individual, family, or student subscription (or active trial) to access user playlists and streaming catalog endpoints.

To authenticate:
```bash
curo auth login -p tidal
```
1. Musicae Curo requests an OAuth 2.0 Device Code and displays a verification link in the terminal:
   ```text
   To authorize Tidal with Musicae Curo, visit:
   https://link.tidal.com/ABCDE
   Code: ABCDE (expires in 300s)
   ```
2. Open the link on any browser (desktop or mobile), log into your Tidal account, and approve the authorization.
3. Once approved, session tokens are securely saved to `~/.config/curo/tidal_session.json` (`0600` permissions). Musicae Curo automatically performs headless silent token refresh during cron runs.

#### Authenticating with Spotify

> [!NOTE]
> **Spotify Premium Required for Developer Apps**
> Spotify requires the account that registers the app on the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) to have an active Spotify Premium plan (Individual, Duo, or Family). Free accounts cannot create functional API applications.

To authenticate:
1. **Create a Developer App**:
   - Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and log in with your **Spotify Premium account**.
   - Click **Create App**.
   - Set **App Name** (e.g. `Musicae Curo`) and **App Description**.
   - Under **Redirect URIs**, add:
     ```text
     http://127.0.0.1:8888/callback
     ```
   - Select **Web API** under "Which APIs are you using?" and save the app.
   - Go to app **Settings** and copy your **Client ID** and **Client Secret**.
2. **Log In via CLI**:
   ```bash
   curo auth login -p spotify
   ```
   Musicae Curo will prompt you for your Client ID and Client Secret on first run, saving them to `~/.config/curo/config.json`.
   
   *(Alternatively, export them as environment variables `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` before running the command).*
3. A browser window will open automatically asking you to authorize Musicae Curo. If running on a headless server, copy the callback URL from your browser address bar back into the CLI prompt.
4. Tokens are saved to `~/.config/curo/spotify_session.json` (`0600` permissions) and refreshed silently and automatically.

#### Authenticating with Deezer

Deezer allows free user accounts to inspect libraries, manage playlists, search tracks, and access Deezer Flow discovery mixes without requiring a paid subscription.

To authenticate:
```bash
curo auth login -p deezer
```

Musicae Curo provides two authentication options:
1. **Direct Access Token (Fastest)**:
   - Paste an access token directly at the prompt (or export `DEEZER_ACCESS_TOKEN`).
2. **OAuth 2.0 Web Flow**:
   - Create a free developer application at [https://developers.deezer.com/myapps](https://developers.deezer.com/myapps).
   - Set the Redirect URL domain to `http://127.0.0.1:8888/callback`.
   - Provide your Application ID and Secret Key at the prompt (or export `DEEZER_APP_ID` and `DEEZER_APP_SECRET`).
   - The CLI opens a browser window for authorization and captures the callback automatically via a local temporary server.

Tokens and user details are securely cached at `~/.config/curo/deezer_session.json` (`0600` permissions).

#### Scaffolded Providers (Qobuz, YouTube Music)

- **Qobuz**: Qobuz requires developer API keys (App ID and App Secret). Full catalog matching works best with an active Studio/Sublime subscription.
- **YouTube Music**: YouTube Music does not require a YouTube Premium subscription to manage personal playlists or search tracks. Authentication relies on Google account cookie tokens or local OAuth tokens via `ytmusicapi`.

---

## 2. Directory Structure & File Permissions

Musicae Curo manages its configuration and state in a secure dedicated directory:

| Path | Description | Permissions |
| :--- | :--- | :--- |
| `~/.config/curo/` | Root configuration directory | `0700` (`rwx------`) |
| `~/.config/curo/config.json` | General config (default provider, SMTP setup, paths) | `0600` (`rw-------`) |
| `~/.config/curo/jobs.yaml` | Declarative scheduled job definitions | `0600` (`rw-------`) |
| `~/.config/curo/tidal_session.json` | OAuth token cache for Tidal adapter | `0600` (`rw-------`) |
| `~/.config/curo/spotify_session.json` | OAuth token cache for Spotify adapter | `0600` (`rw-------`) |
| `~/.config/curo/deezer_session.json` | OAuth/Access token cache for Deezer adapter | `0600` (`rw-------`) |
| `~/.config/curo/megamix_history.json` | Rolling daily mix track history for fresh-only filtering | `0600` (`rw-------`) |
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
   - `replace` (default): Clears all existing tracks from the Megamix and injects fresh tracks.
   - `append`: Appends new tracks without wiping previous entries.
6. **Rolling History & Fresh-Only Filtering (`--exclude-recent-days / -e`)**:
   - When `exclude_recent_days > 0` (e.g. `7`), tracks observed in the preceding *N* days are automatically excluded.
   - Only truly **fresh tracks** that haven't appeared in recent daily mixes are selected.
   - Historical entries are securely recorded in `~/.config/curo/megamix_history.json` and pruned automatically after 60 days.

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
- **Threshold Default**: Default similarity threshold requires $\ge 80\%$ match (`--threshold 80`, accepting integers, percentages like `80%`, or legacy decimals like `0.8`).
- **Dry-Run by Default**: Generates diagnostic reports in `./reports/repair_<slug>_<date>.json` without modifying streaming playlists. Pass `--live` to mutate playlists.
- **Atomic Two-Step Swap**: When replacing a track live:
  1. Calls `provider.add_tracks_to_playlist(pl_id, [new_id], position=idx)`.
  2. If and only if the provider confirms addition, calls `provider.remove_track_from_playlist_by_index(pl_id, idx + 1)`.
  3. If addition fails or is skipped by the platform, the original track is **never** removed.

---

## 6. Email Notifications & Change Digests

Musicae Curo includes a built-in notification engine (`curo notify`) for headless server alerts:

- **Daily Change Digest**: Dispatched when playlists are modified or delisted tracks are discovered. Includes itemized track additions, removals, and availability warnings.
- **Daily Mix & Megamix Summarization**: Ephemeral daily mix and megamix playlists are automatically summarized rather than exhaustively listing hundreds of rotating additions and removals, keeping digests clean and readable.
- **All Playlists Baseline Overview**: Dispatched on first run or on demand (`curo notify send --all`). Provides an executive summary of all personal playlists, track counts, and availability health.
- **Supported SMTP Configurations**:
  - Direct TLS / SSL (Port 465).
  - STARTTLS (Port 587 or Port 25 for internal mail relays).
  - Unauthenticated local SMTP relays or authenticated remote servers.

---

## 7. Declarative Jobs & Automated Scheduling (`jobs.yaml`)

Musicae Curo provides a headless job runner (`JobRunner`) designed for unattended batch routines, such as nightly backups, discovery mix archiving, and playlist health audits.

### How Jobs Work
Jobs are defined declaratively in a YAML file located at `~/.config/curo/jobs.yaml`. 
- When `curo job run <name>` is executed, `JobRunner` loads the job definition, resolves the target music provider, and executes each action step sequentially.
- If any action fails, the runner halts and logs the error, ensuring partially executed jobs do not leave silent failures.
- If `~/.config/curo/jobs.yaml` does not exist, `curo` automatically generates a default file with standard production routines on first run.

### Structure of `jobs.yaml`

```yaml
jobs:
  <job-name>:
    description: "Human-readable summary of what the job does"
    folder: "Default Folder" # Optional: default playlist folder for all actions in this job
    actions:
      - action: <action_type>
        params:
          <param_name>: <param_value>
          folder: "Custom Folder" # Optional: overrides job-level folder
```

### Supported Action Types & Parameters

| Action Type | Description | Available Parameters |
| :--- | :--- | :--- |
| `backup_daily_discovery` | Archives today's algorithmic Daily Discovery mix into a dated playlist. | - `playlist_name_format` (default: `"Discovery Backup - {date}"`)<br>- `description` (optional custom playlist description)<br>- `folder` (optional: destination folder name or ID) |
| `create_megamix` | Merges Daily Discovery and all active Daily Mixes into a single deduplicated, shuffled playlist. | - `target_playlist` (default: `"Daily Megamix"`)<br>- `include_discovery` (bool, default: `true`)<br>- `include_daily_mixes` (bool, default: `true`)<br>- `shuffle` (bool, default: `true`)<br>- `mode` (`replace` or `append`, default: `replace`)<br>- `description` (template string)<br>- `limit` (optional integer limit)<br>- `exclude_recent_days` (integer, default: 0)<br>- `folder` (optional: destination folder name or ID) |
| `snapshot_all_playlists` | Runs SHA-256 versioned snapshots across all personal playlists, updates track history registry, checks track availability, and sends email notifications. | - `alert_on_unavailable` (bool, default: `true`)<br>- `notify` (bool, default: `true`)<br>- `force_all_mail` (bool, default: `false`) |
| `snapshot_playlist` | Takes a versioned snapshot of a single designated playlist. | - `playlist` (required: playlist title or ID)<br>- `alert_on_unavailable` (bool, default: `true`) |
| `snapshot_favorite_tracks` | Takes a versioned SHA-256 snapshot of all favorite/liked tracks, updates registry, and logs availability alerts. | - `alert_on_unavailable` (bool, default: `true`) |
| `backup_favorites_to_playlist` | Mirrors all favorite/liked tracks into a designated regular playlist with full pagination. | - `target_playlist` (default: `"Favorites Backup"`)<br>- `mode` (`replace` or `append`, default: `replace`)<br>- `description` (optional description)<br>- `folder` (optional: destination folder name or ID) |
| `export_playlist` | Exports a designated playlist to disk in the specified format. | - `playlist` (required: playlist title or ID)<br>- `format` (`json`, `m3u8`, or `csv`, default: `json`) |
| `sync_playlist` | Synchronizes playlist tracks between providers or within the same provider (`mirror` or `append` modes). | - `source_playlist` (required: title or ID)<br>- `target_playlist` (optional: target title)<br>- `source_provider` (optional: provider name)<br>- `target_provider` (optional: target provider name)<br>- `mode` (`mirror` or `append`, default: `mirror`)<br>- `threshold` (percentage float, default: `80`)<br>- `dry_run` (bool, default: `false`)<br>- `create_if_missing` (bool, default: `true`)<br>- `folder` (optional: destination folder name or ID) |

### Default Shipped Jobs

When first initialized, `curo` configures two standard jobs:

```yaml
jobs:
  daily-discovery-megamix:
    description: "Backup all daily mixes and build a randomized daily megamix (wiped fresh daily)"
    actions:
      - action: backup_daily_discovery
        params:
          playlist_name_format: "Discovery Backup - {date}"
          description: "Automated daily backup of Daily Discovery"
      - action: create_megamix
        params:
          target_playlist: "Daily Megamix"
          include_discovery: true
          include_daily_mixes: true
          shuffle: true
          mode: "replace"
          exclude_recent_days: 0
          description: "All daily mix tunes in random order - updated on {date}"

  daily-fresh-megamix:
    description: "Build a daily megamix containing only fresh tracks not seen in the last 7 days"
    actions:
      - action: create_megamix
        params:
          target_playlist: "Daily Fresh Megamix"
          include_discovery: true
          include_daily_mixes: true
          shuffle: true
          mode: "replace"
          exclude_recent_days: 7
          description: "Fresh daily mix tunes not heard in the last 7 days - updated on {date}"

  daily-playlist-backup:
    description: "Daily versioned snapshot of all personal playlists with availability alerts"
    actions:
      - action: snapshot_all_playlists
        params:
          alert_on_unavailable: true
          notify: true

  daily-favorites-backup:
    description: "Daily versioned snapshot and playlist mirror of all favorite/liked tracks"
    actions:
      - action: snapshot_favorite_tracks
        params:
          alert_on_unavailable: true
      - action: backup_favorites_to_playlist
        params:
          target_playlist: "Favorites Backup"
          mode: "replace"
          description: "Automated daily backup of favorite/liked tracks"
```

### Scheduling with Linux `cron` or Task Scheduler

Musicae Curo can automatically output the correct crontab entry for any declared job:

```bash
# Print ready-to-copy crontab line using current Python interpreter
curo job cron-help --job daily-discovery-megamix
```

Example crontab entry:
```crontab
# Run daily at 06:00 UTC with progress bars suppressed and logs piped to cron.log
0 6 * * * /usr/bin/python3 -m curo.cli job run daily-discovery-megamix --quiet >> ~/.config/curo/cron.log 2>&1
```

---

## 8. CLI Command Reference

### Global Options
- `-c, --config <path>`: Custom configuration directory (default: `~/.config/curo`).
- `-p, --provider <name>`: Target streaming provider (e.g. `tidal`, `deezer`, `spotify`).
- `-q, --quiet`: Suppress banners, tables, and progress bars (ideal for cron). Can be placed globally (e.g. `curo --quiet ...`) or directly on subcommands (e.g. `curo job run ... --quiet`).
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

### Favorite Tracks (`curo favorites`)
```bash
curo favorites list [-p <prov>] [--limit <num>] [--json]
curo favorites snapshot [-p <prov>] [--no-alerts] [--json]
curo favorites backup [-p <prov>] [--target <name>] [--mode replace|append] [--desc <text>]
curo favorites export [-p <prov>] [--format json|m3u8|csv] [--output <path>]
```

### Playlists (`curo playlist`)
```bash
curo playlist list
curo playlist show <id|title>
curo playlist create <title> [--desc <desc>] [--public] [--folder <name|id>]
curo playlist folders
curo playlist move <id|title> <folder_name|id>
curo playlist export <id> [--format json|m3u8|csv] [--output <path>]
curo playlist snapshot [id] [--no-alert] [--notify] [--all-mail]
curo playlist alerts [--limit 50]
curo playlist repair <id> [--threshold 80] [--live]
curo playlist replace <id> --track <id|#pos> --with <id|isrc|upc> [--live]
curo playlist compare <source_id> <target_id> [--from <prov>] [--to <prov>] [--threshold 80]
curo playlist sync <source_id> [--target <name>] [--from <prov>] [--to <prov>] [--mode mirror|append] [--threshold 80] [--live] [--folder <name|id>]
```

### Albums (`curo album`)
```bash
curo album show <id>
curo album export <id> [--format json|m3u8|csv] [--output <path>]
```

### Mixes (`curo mix`)
```bash
curo mix list
curo mix daily [--format <template>] [--folder <name|id>]
curo mix megamix [--target <title>] [--mode replace|append] [--exclude-recent-days <N>] [--folder <name|id>]
```

### Favorites (`curo favorites`)
```bash
curo favorites list [--limit <N>]
curo favorites snapshot [--no-alert]
curo favorites backup [--target <title>] [--mode replace|append] [--folder <name|id>]
curo favorites export [--format json|m3u8|csv] [--output <path>]
```

### Automated Jobs (`curo job`)
```bash
curo job list                                   # List defined jobs in jobs.yaml
curo job run <job_name> [-q, --quiet] [--json]  # Execute a job headless (quiet for cron)
curo job cron-help                              # Generate Linux crontab syntax
```

### Notifications (`curo notify`)
```bash
curo notify config --host <smtp> --port <port> [--tls/--ssl] --user <user> --password <pass> --to-addr <email> [--exclude-megamix-diff/--include-megamix-diff]
curo notify test                # Verify SMTP delivery
curo notify send [--all]        # Dispatch notification manually
```
