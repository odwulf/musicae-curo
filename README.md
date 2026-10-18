# Tidal CLI

A schedulable command-line interface for TIDAL, tailored for automated Linux `cron` jobs and interactive playlist management.

## Features
- **OAuth2 Device Login**: Easy authorization on headless servers via Tidal device code.
- **Local File Session Storage**: Stores credentials in `~/.config/tidal-cli/session.json` with strict `0600` permissions.
- **Automatic Token Refresh**: Transparently refreshes expired tokens in unattended cron executions.
- **Playlist Management**: List, inspect, export, and create playlists.
- **Automated Daily Discovery & Megamix**:
  - Dynamically extracts tracks from *My Daily Discovery* and any number of *Daily Mixes* (handles dynamic counts from 1 to 8+ mixes).
  - Deduplicates tracks across all daily mixes.
  - Creates dated snapshots (`Discovery Backup - YYYY-MM-DD`).
  - Shuffles and aggregates into a single randomized `Daily Megamix` playlist.
- **Declarative Job Engine**: Define recurring cron tasks in `jobs.yaml`.

## Installation

```bash
uv sync
```

## Quick Start

### 1. Authenticate
```bash
uv run tidal-cli auth login
```
Follow the URL provided to authorize the device in your browser.

Check your session:
```bash
uv run tidal-cli auth status
```

### 2. Manual Megamix Execution
```bash
uv run tidal-cli mix megamix
```

### 3. Schedulable Cron Execution
To see ready-to-paste crontab syntax:
```bash
uv run tidal-cli job cron-help
```

