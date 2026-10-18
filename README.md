# Tidal CLI

A schedulable command-line interface for TIDAL, tailored for automated Linux `cron` jobs, unconstrained dynamic mix discovery, and interactive playlist management.

📖 **[Full Documentation & Architecture Guide](DOCUMENTATION.md)**

## Key Features
- **Dynamic Mix Discovery (Zero Assumptions)**: Automatically queries Tidal and discovers **any arbitrary count** of Daily Mixes (whether an account has 1, 3, 8, 12, or more) plus *My Daily Discovery*.
- **Automated Daily Megamix & Archiving**:
  - Deduplicates tracks across all discovered mixes using both Track IDs and ISRCs.
  - Generates dated snapshots (`Discovery Backup - YYYY-MM-DD`).
  - Shuffles and aggregates into a single randomized `Daily Megamix` playlist.
- **Headless Linux Server & Cron Optimization**:
  - Device code authorization flow (`tidal-cli auth login`) for browserless servers.
  - Strict POSIX security permissions (`0600` for `session.json`, `0700` for config directory).
  - Silent token refresh transparently prevents expired authentication during unattended runs.
- **Declarative Job Engine**: Define recurring cron tasks in `jobs.yaml` with built-in `job cron-help` syntax generator.
- **Playlist & Album Export with Full Metadata**: Export any playlist or album with comprehensive container and track metadata (ISRC, duration, audio quality, BPM, key, gain, artists, release dates) to formatted JSON, M3U8, or CSV.

## Installation

```bash
uv sync
```

## Quick Start

### 1. Authenticate Once
```bash
uv run tidal-cli auth login
```
Visit the link provided in your browser or phone to approve the device. Check status with:
```bash
uv run tidal-cli auth status
```

### 2. Discover Your Dynamic Mixes
```bash
uv run tidal-cli mix list
```

### 3. Generate Megamix Manually
```bash
uv run tidal-cli mix megamix
```

### 4. Setup Linux Server Cron Job
Get the exact crontab line for your server:
```bash
uv run tidal-cli job cron-help
```
Add it to your crontab:
```bash
crontab -e
```

## Running Tests

```bash
uv run pytest -v
```

See [DOCUMENTATION.md](DOCUMENTATION.md) for detailed CLI commands, cron configuration, and extension guides.
