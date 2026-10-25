# Musicae Curo (`curo`)

*Universal music library custodian, playlist protector, and daily discovery mix archiver.*

Musicae Curo (*Latin: "I care for music"*) is a modular, provider-agnostic music curation and playlist preservation CLI. Tailored for automated Linux `cron` background jobs and interactive terminal administration, it keeps your playlists healthy, archives ephemeral daily discovery mixes, versions personal playlists with SHA-256 change detection, repairs retired/delisted tracks, and dispatches change digests via email.

📖 **[Full Documentation & Architecture Guide](DOCUMENTATION.md)** | 🤖 **[AI Agent Guide](AGENTS.md)**

> [!NOTE]
> ### Disclaimer & Project Origins
> This project was created to fulfill a personal music curation need. All Python code and architecture in this repository has been generated with the assistance of an AI coding agent. The author does not claim to be a coder and makes this project publicly available as-is, in case someone else has similar music curation and playlist maintenance needs.

---

## Key Features

- **Modular Multi-Provider Architecture**:
  - Unified Provider Service Provider Interface (`MusicProvider` SPI).
  - Dynamic adapter registry (`ProviderRegistry`) supporting runtime switching via `-p / --provider <name>`.
  - **Tidal**, **Spotify**, and **Deezer**: Fully functional and active out of the box.
  - **Qobuz** and **YouTube Music**: Scaffolded interface stubs ready for community contribution and future implementation.
- **Dynamic Mix Discovery**:
  - Automatically queries and discovers Daily Mixes, plus *Daily Discovery*.
- **Automated Daily Megamix & Archiving**:
  - Deduplicates tracks across all mixes using both Track IDs and ISRCs.
  - Generates dated snapshots (`Discovery Backup - YYYY-MM-DD`).
  - Shuffles and aggregates into a randomized `Daily Megamix` playlist (`replace` or `append` modes).
- **Playlist Versioning, SHA-256 Snapshots & Availability Alerting**:
  - Tracks content changes using cryptographic SHA-256 hashes.
  - Saves incremental versioned JSON backups only when modifications are detected.
  - Builds a historical healthy track registry (`track_registry.json`) to retain ISRCs, album UPCs, and release dates even after songs are delisted by streaming platforms.
  - Automatically records delisted tracks to `availability_alerts.log`.
- **Intelligent Playlist Repair & Track Replacement**:
  - Scans playlists for retired or unavailable tracks.
  - Multi-tiered candidate discovery across ISRC sound codes, Album UPC barcodes, and normalized fuzzy metadata.
  - Explainable 100-point scoring algorithm with detailed reasoning diagnostics.
  - Severe acoustic/live version penalties with manual approval safeguards.
  - Targeted track replacement by Track ID, 1-based position (`#4`), ISRC, or UPC.
- **Email Notifications & Change Digests**:
  - HTML & plain-text email digests sent whenever changes or unavailable tracks are detected.
  - Baseline "All Playlists Overview" report sent automatically on initial setup or on demand (`--all`).
  - Fully configurable SMTP settings including support for port 25, 587 (STARTTLS), or 465 (SSL).
- **Headless Server & Cron Automation**:
  - Browserless device code authorization flow (`curo auth login`).
  - POSIX file security permissions (`0600` for session files, `0700` for config directories).
  - Silent token refresh prevents auth expiration during unattended headless cron runs.
  - Declarative job pipeline (`jobs.yaml`) with built-in crontab generator (`curo job cron-help`).

---

## Installation

```bash
# Clone the repository
git clone https://github.com/odwulf/musicae-curo.git
cd musicae-curo

# Install dependencies and sync virtual environment
uv sync
```

---

## Quick Start

### 1. Authenticate Streaming Service
```bash
# Log in with Tidal (Device OAuth flow)
uv run curo auth login -p tidal

# Log in with Spotify (OAuth2 authorization code flow)
uv run curo auth login -p spotify

# Log in with Deezer (Direct access token or OAuth2 flow)
uv run curo auth login -p deezer

# Check authentication status
uv run curo auth status
```
*See the [Provider Authentication Guide](DOCUMENTATION.md#provider-authentication--membership-requirements) for details on membership requirements (free vs paid) and setup.*

### 2. Discover Your Dynamic Mixes
```bash
# List all active algorithmic mixes
uv run curo mix list

# Archive today's Daily Discovery mix
uv run curo mix backup-discovery

# Generate today's deduplicated, randomized Megamix (wiped clean daily)
uv run curo mix megamix

# Generate a Fresh Megamix (excluding tracks heard in the last 7 days)
uv run curo mix megamix --target "Daily Fresh Megamix" --exclude-recent-days 7
```

### 3. Backup & Snapshot Playlists (Versioning & Alerts)
```bash
# Create SHA-256 versioned snapshot of all playlists (saves only if changed)
uv run curo playlist snapshot

# Snapshot a specific playlist
uv run curo playlist snapshot "My Favorites"

# View logged unavailable / delisted track alerts
uv run curo playlist alerts
```

### 4. Backup & Snapshot Favorite Tracks
```bash
# List all liked/favorite tracks with seamless pagination (handles full catalog)
uv run curo favorites list

# Snapshot favorite tracks with cryptographic SHA-256 change detection & alerts
uv run curo favorites snapshot

# Mirror all favorite tracks into a regular streaming playlist
uv run curo favorites backup --target "Favorites Backup"

# Export favorites to JSON, M3U8, or CSV
uv run curo favorites export -f csv -o favorites.csv
```

### 5. Repair or Replace Unavailable Playlist Tracks
```bash
# Dry-run diagnostic repair report with candidate scoring
uv run curo playlist repair "My Favorites"

# Mutate and apply replacements live on the streaming service
uv run curo playlist repair "My Favorites" --live

# Replace a specific track by 1-based playlist index and ISRC
uv run curo playlist replace "My Favorites" --track "#4" --with USSM18200001 --live
```

### 6. Email Notifications (Daily Change Digest & Baseline)
```bash
# Configure SMTP credentials (e.g. port 25 with STARTTLS)
uv run curo notify config --host mail.myserver.org --port 25 --tls --user me@myserver.org --password secret --to-addr me@myserver.org

# Verify SMTP connectivity
uv run curo notify test

# Dispatch baseline all-playlists email overview
uv run curo notify send --all
```

### 7. Streaming Providers
```bash
# List available and active provider adapters
uv run curo provider list

# Switch default provider
uv run curo provider default tidal
```

### 8. Headless Linux Server Cron Setup
```bash
# Generate the exact crontab syntax for your server
uv run curo job cron-help

# Edit crontab
crontab -e
```

---

## Running Tests

Musicae Curo maintains an extensive test suite covering all providers, domain services, scoring logic, and CLI commands:

```bash
uv run pytest -v
```

See [DOCUMENTATION.md](DOCUMENTATION.md) for full architecture details, provider extension guides, and API documentation.
