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

# Export playlist to file
tidal-cli playlist export "Daily Megamix" --format m3u8 --output ~/megamix.m3u8
tidal-cli playlist export "Daily Megamix" --format json --output ~/megamix.json
tidal-cli playlist export "Daily Megamix" --format csv --output ~/megamix.csv
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

