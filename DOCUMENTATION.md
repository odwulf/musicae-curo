# TIDAL Schedulable CLI Client — Complete Documentation

A headless-ready, schedulable Command Line Interface for TIDAL, tailored for automated Linux `cron` tasks, dynamic algorithmic mix aggregation, and playlist management.

---

## 1. Overview & Core Architecture

`tidal-cli` is built with Python, [Typer](https://typer.tiangolo.com/), [Rich](https://rich.readthedocs.io/), and [`tidalapi`](https://github.com/EbbLabs/python-tidal). It is designed to run unattended on headless servers while also providing a rich terminal interface for interactive administration.

```
                    ┌─────────────────────────┐
                    │      Linux Cron Job     │
                    │   (e.g., 06:00 Daily)   │
                    └────────────┬────────────┘
                                 │
                                 ▼
                     tidal-cli job run <name>
                                 │
     ┌───────────────────────────┴───────────────────────────┐
     ▼                                                       ▼
[MixService]                                        [PlaylistService]
- Dynamic Mix Discovery (1..N)                      - Read / List user playlists
- Cross-Mix Deduplication (ID + ISRC)               - Create / Clear playlists
- Random Fisher-Yates Shuffling                     - Safe chunked track addition (50/req)
- Dated Daily Discovery Snapshot                    - Export (JSON, M3U8, CSV)
     │                                                       │
     └───────────────────────────┬───────────────────────────┘
                                 ▼
                         [SessionManager]
                 - Local session (~/.config/tidal-cli/session.json)
                 - Secure POSIX file permissions (0600)
                 - Silent token refresh for expired sessions
                                 │
                                 ▼
                        TIDAL API Service
```

---

## 2. Dynamic Algorithmic Mix Discovery

### Zero-Assumption Design
Tidal generates a dynamic number of algorithmic mixes for each user. Depending on an account's age, listening breadth, and genre variety, Tidal may provide anywhere from **1, 2, 4, 8, or more Daily Mixes**, in addition to **My Daily Discovery**.

`tidal-cli` makes **zero assumptions** about the number or names of daily mixes:
1. **Unconstrained Querying**: It queries Tidal's live mix feed (`session.mixes()`), user favorite mixes, and home categories.
2. **Dynamic Filtering**:
   - Matches `My Daily Discovery` via mix type (`DISCOVERY_MIX`) or discovery title keyword.
   - Matches all available `Daily Mix` instances (`DAILY_MIX` or title matching `Daily Mix`).
   - Filters out non-audio mixes (such as `Daily Video Mixes`).
3. **Natural Sorting**: Sorts daily mixes naturally by number so `Daily Mix 2` correctly precedes `Daily Mix 10`.
4. **Any Count $N$ Supported**: Whether an account currently has 1 daily mix, 8 daily mixes, or 15 daily mixes, all of them are automatically discovered and aggregated at runtime.

---

## 3. Daily Megamix & Deduplication Pipeline

The primary automated use case consists of two routines:

### A. Daily Discovery Backup
Permanent archiving of today's ephemeral discovery mix:
- Extracts track IDs from `My Daily Discovery`.
- Creates or retrieves a dated playlist: `Discovery Backup - YYYY-MM-DD` (e.g., `Discovery Backup - 2026-10-18`).
- Adds the tracks without duplicates.

### B. Randomized Daily Megamix
- **Harvesting**: Aggregates tracks from `My Daily Discovery` and **all** discovered `Daily Mixes`.
- **Dual-Layer Deduplication**:
  - Track ID check: Prevents duplicate track objects.
  - ISRC (International Standard Recording Code) check: Prevents duplicate recordings or remasters of the same song across different mixes.
- **Random Shuffling**: Shuffles the aggregated track list using cryptographic/system randomness (Fisher-Yates).
- **Safe Batch Ingestion**: Adds tracks in chunks of 50 per API call to adhere to Tidal's request payload constraints and prevent network timeouts.
- **Modes**:
  - `replace` (default): Clears yesterday's Megamix and populates today's fresh randomized selection.
  - `append`: Adds new tracks to the existing Megamix.

---

## 4. Headless Linux Server & Authentication

### Step 1: Initial Interactive Login
On headless servers (no browser display), run:
```bash
uv run tidal-cli auth login
```
The CLI displays an authorization prompt:
```text
To authorize TIDAL CLI, visit:
https://link.tidal.com/XXXXX

Code: ABCD (expires in 300s)
Waiting for authorization in your browser or phone...
```
Visit the link on any browser or mobile device connected to your Tidal account. Once authorized, the CLI detects completion and stores the credentials.

### Step 2: Credential Security
Session credentials are saved to:
```text
~/.config/tidal-cli/session.json
```
On Linux/macOS, the client automatically enforces strict POSIX permissions:
- Directory: `0700` (`rwx------`)
- Session file: `0600` (`rw-------`)

### Step 3: Unattended Silent Token Refresh
When Tidal access tokens expire (typically after 7 days), the CLI detects the expiration during `cron` execution and automatically uses the stored `refresh_token` to request a new access token from Tidal. The updated token is written back to `session.json`, ensuring the cron job never halts due to expired credentials.

---

## 5. Automated Linux Cron Setup

### Default Workflow Definition (`~/.config/tidal-cli/jobs.yaml`)
When first executed, `tidal-cli` creates `jobs.yaml`:
```yaml
jobs:
  daily-discovery-megamix:
    description: "Backup all daily mixes and build a randomized daily megamix"
    actions:
      - action: backup_daily_discovery
        params:
          playlist_name_format: "Discovery Backup - {date}"
          description: "Automated daily backup of Tidal Daily Discovery"
      - action: create_megamix
        params:
          target_playlist: "Daily Megamix"
          include_discovery: true
          include_daily_mixes: true
          shuffle: true
          mode: "replace"
          description: "All daily mix tunes in random order - updated on {date}"

  daily-playlist-backup:
    description: "Daily versioned snapshot of all personal playlists with availability alerts"
    actions:
      - action: snapshot_all_playlists
        params:
          alert_on_unavailable: true
```

### Inspect Available Jobs
```bash
uv run tidal-cli job list
```

### Ready-to-Copy Crontab Helper
Run:
```bash
uv run tidal-cli job cron-help
```
Output:
```cron
# Run Tidal Daily Discovery Backup & Megamix daily at 06:00 UTC
0 6 * * * /path/to/.venv/bin/python -m tidal_cli.cli job run daily-discovery-megamix --quiet >> ~/.config/tidal-cli/cron.log 2>&1

# Run Daily Personal Playlist Versioned Backup & Availability Check daily at 05:00 UTC
0 5 * * * /path/to/.venv/bin/python -m tidal_cli.cli job run daily-playlist-backup --quiet >> ~/.config/tidal-cli/cron.log 2>&1
```

To install:
```bash
crontab -e
```
Paste the line above, save, and exit.

---

## 6. Complete CLI Command Reference

### Global Options
| Flag | Description |
| :--- | :--- |
| `-c, --config PATH` | Custom config directory path (default: `~/.config/tidal-cli`). |
| `-q, --quiet` | Suppress decorative banners and progress output (recommended for cron). |
| `--json` | Output machine-readable JSON for scripting. |
| `-v, --verbose` | Enable debug logging. |

---

### `auth` Subcommands
```bash
# Authorize device with Tidal
tidal-cli auth login

# Check active session, profile name, and expiration
tidal-cli auth status

# Disconnect and wipe local credentials
tidal-cli auth logout
```

---

### `playlist` Subcommands
```bash
# List all user playlists (with track counts, duration, and privacy status)
tidal-cli playlist list

# Show tracks in a playlist by ID or name
tidal-cli playlist show "Daily Megamix"
tidal-cli playlist show "d6b5e00b-..."

# Create a new playlist
tidal-cli playlist create "Weekend Grooves" --desc "Hand-picked tracks" --public

# Export playlist with full container & track metadata to JSON, M3U8, or CSV
tidal-cli playlist export "Daily Megamix" --format json --output ~/megamix.json
tidal-cli playlist export "Daily Megamix" --format m3u8 --output ~/megamix.m3u8
tidal-cli playlist export "Daily Megamix" --format csv --output ~/megamix.csv

# Detect unavailable tracks and evaluate replacement candidates (default: 80% threshold & dry-run)
tidal-cli playlist repair "My Heavy Rotation"

# Apply replacements live on Tidal (threshold defaults to 80%)
tidal-cli playlist repair "My Heavy Rotation" --live

# Run repair analysis and export report to a custom JSON location
tidal-cli playlist repair "My Heavy Rotation" --export reports/custom_repair.json

# Check all tracks (including playable ones) for newer reissues or remasters
tidal-cli playlist repair "My Heavy Rotation" --all-tracks

# Snapshot all personal user playlists (versioned by SHA-256 content hash)
tidal-cli playlist snapshot

# Snapshot a specific playlist by ID or name
tidal-cli playlist snapshot "My Heavy Rotation"

# View recent track availability alerts logged when tracks become unavailable
tidal-cli playlist alerts
tidal-cli playlist alerts --limit 100
```

#### Daily Snapshots, Version History & Track Registry
To protect against catalog turnover, `tidal-cli` provides automated versioned playlist backups and an availability monitoring pipeline:
- **Change Detection via SHA-256**: Only creates a new snapshot file (`~/.config/tidal-cli/snapshots/<playlist_slug>/<hash>.json`) when track ordering or IDs actually change.
- **Playlist Manifest (`manifest.json`)**: Tracks complete revision history with timestamp, version number, track count, and content hash.
- **Historical Track Registry (`snapshots/index.json`)**: Continuously records healthy, playable tracks with their last-known `isrc`, `album_upc`, and `album_release_date`. When a track later becomes unavailable and Tidal zeroes out its identifiers, `playlist repair` queries this registry to recover the sound recording code and achieves **90%–100% repair accuracy**.
- **Delisted Track Alerts (`availability_alerts.log`)**: Appends structured JSON alert events whenever a track is detected as unavailable, making catalog regressions immediately observable.

#### Similarity Scoring Calibration
Because Tidal strips the `isrc` and album `upc` fields when de-listing tracks, the scoring formula is calibrated so that an exact metadata match achieves **80% (0.80)**:
- **Title Match (30 pts)**: Normalized title comparison (automatically cleans remaster notes like `(2011 Remaster)`, `(Remastered 2011)`, `- 2011 Remaster`).
- **Artist Match (25 pts)**: Primary artist ID, collaborating/contributing artist overlap (handles inverted credits on duets), or normalized name comparison.
- **Duration Match (15 pts)**: Exact runtime within 1s.
- **Album Name Match (10 pts)**: Normalized release title comparison.
- **Single-to-Album Matching (+8 pts)**: If the original was a self-titled single track (`album == title`), rewards full studio album versions.
- **Album Release Date Bonus (+5 pts exact date, +2 pts same year)**: Matches release date from album metadata.
- **ISRC Bonus (+10 pts)**: Verified exact sound recording code match (boosts score to 90%–100% when present or recovered from snapshot history).
- **Album UPC Bonus (+10 pts)**: Verified identical album release barcode.
- **Version Mismatches**: Severe penalties (capping score $\le 40\%$) for Live, Acoustic, Instrumental, or Remix cuts substituting studio tracks, requiring manual approval.

---

### `album` Subcommands
```bash
# Show album details and track list by Album ID or title
tidal-cli album show "Abbey Road"
tidal-cli album show "123456"

# Export album with full container & track metadata to JSON
tidal-cli album export "Abbey Road" --format json --output ~/abbey_road.json

# Export album to M3U8 or CSV
tidal-cli album export 123456 --format m3u8 --output ~/album.m3u8
tidal-cli album export 123456 --format csv --output ~/album.csv
```

#### Exported JSON Metadata Structure
When exporting a playlist or album to `json`, the output includes all high-level container fields and comprehensive metadata for every included track:

```json
{
  "type": "album", // or "playlist"
  "id": 123456,
  "name": "Album Title",
  "version": "Remastered",
  "duration_seconds": 2600,
  "num_tracks": 14,
  "release_date": "1975-11-21",
  "upc": "00602527718021",
  "audio_quality": "HI_RES_LOSSLESS",
  "audio_modes": ["STEREO", "DOLBY_ATMOS"],
  "artist": { "id": 1001, "name": "Artist Name" },
  "artists": [{ "id": 1001, "name": "Artist Name", "type": "MAIN" }],
  "listen_url": "https://tidal.com/browse/album/123456",
  "tracks": [
    {
      "id": 555666,
      "name": "Track Name",
      "full_name": "Track Name (2011 Remaster)",
      "duration_seconds": 354,
      "track_number": 1,
      "volume_number": 1,
      "isrc": "GBUM71029604",
      "explicit": false,
      "popularity": 95,
      "audio_quality": "LOSSLESS",
      "audio_modes": ["STEREO"],
      "is_lossless": true,
      "is_hi_res_lossless": false,
      "is_dolby_atmos": false,
      "bpm": 72,
      "key": "Bb",
      "replay_gain": -9.2,
      "peak": 0.99,
      "copyright": "(C) 2011 Record Label",
      "artist": { "id": 1001, "name": "Artist Name" },
      "album": { "id": 123456, "name": "Album Title", "release_date": "1975-11-21" },
      "listen_url": "https://tidal.com/browse/track/555666"
    }
  ]
}
```

---

### `mix` Subcommands
```bash
# List all dynamically discovered Daily Mixes (regardless of account mix count)
tidal-cli mix list

# Manually trigger today's Daily Discovery backup
tidal-cli mix daily-backup

# Manually generate/update the randomized Megamix
tidal-cli mix megamix --target "Daily Megamix" --shuffle --mode replace

# Generate Megamix with a track limit (e.g., top 100 random tracks)
tidal-cli mix megamix --target "Daily 100" --limit 100
```

---

### `job` Subcommands (Cron Entrypoints)
```bash
# List configured automation jobs from jobs.yaml
tidal-cli job list

# Run a job headlessly
tidal-cli job run daily-discovery-megamix

# Run silently in cron with JSON error reporting
tidal-cli --json --quiet job run daily-discovery-megamix

# Display recommended crontab entry for server setup
tidal-cli job cron-help
```

---

### `notify` Subcommands (Email Notifications)
```bash
# Check current email notification status and delivery history
tidal-cli notify status

# Configure SMTP parameters in ~/.config/tidal-cli/config.json (e.g. port 25 with STARTTLS)
tidal-cli notify config --host mail.myserver.org --port 25 --tls --user myuser --password mypass --to-addr listener@example.com --from-addr tidal-bot@example.com

# Send a test email to verify credentials and SMTP connectivity
tidal-cli notify test

# Manually trigger a notification (sends Changes Digest if changes/alerts exist)
tidal-cli notify send

# Manually trigger full All Playlists Baseline Overview email
tidal-cli notify send --all
```

#### Email Notification Behavior & Templates
When email settings (`smtp_host` and `to_address`) are configured in `~/.config/tidal-cli/config.json` or via environment variables (`TIDAL_SMTP_*`), `tidal-cli` automatically sends HTML + plain-text email reports:
1. **Very First Email Sent**: Dispatches the **All Playlists Baseline Overview** template, providing a complete inventory of all personal playlists, track counts, version numbers, and an initial delisted tracks audit.
2. **Subsequent Daily Cron Runs (`daily-playlist-backup`)**: Compares SHA-256 track sequence hashes. If one or more playlists were modified, or if new unavailable track alerts occurred, dispatches the **Playlist Changes Digest**:
   - Shows added tracks (`+N`) and removed tracks (`-M`) for each changed playlist.
   - Shows version transitions (`v1 -> v2`).
   - Highlights any unavailable tracks detected with historical recovery status.
   - If nothing changed and no alerts fired, no email is sent to avoid inbox clutter.
3. **Manual Triggering**: Run `tidal-cli notify send --all` at any time to receive the full catalog baseline overview.

---

## 7. Extending Automations

To add new automated routines, simply define additional jobs in `~/.config/tidal-cli/jobs.yaml`:
```yaml
jobs:
  weekly-archive:
    description: "Export full backup of all playlists every Sunday"
    actions:
      - action: export_playlist
        params:
          playlist: "Daily Megamix"
          format: "m3u8"
```
And add a corresponding schedule in your Linux crontab:
```cron
0 2 * * 0 /path/to/.venv/bin/python -m tidal_cli.cli job run weekly-archive --quiet
```


