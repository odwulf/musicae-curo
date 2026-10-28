# Playlist Versioning, Historical Snapshotting & Availability Alerting Plan

## 1. Feature Overview

TIDAL frequently delists older releases, stripping their ISRC and album UPC codes upon retirement. To prevent permanent loss of these vital identification codes:
1. **Automated Daily Export**: Periodically archives full metadata snapshots of all user personal playlists.
2. **Version History**: Saves a new snapshot version whenever a playlist is modified (`last_updated` changed or tracks added/removed).
3. **Availability Alerts**: Flags an immediate alert whenever a track transitions from playable to unavailable.
4. **Historical Metadata Recovery**: Stores the last-known healthy ISRC and UPC for every track. When `tidal-cli playlist repair` runs, it recovers these original identifiers from history, enabling exact Tier 1 matching (90% - 100% score) even for de-listed tracks!

---

## 2. Architecture & Data Flow

```mermaid
flowchart TD
    Cron["Daily Cron Job\n(tidal-cli job run daily-playlist-snapshot)"] --> ScanPlaylists["Iterate all user playlists\n(session.user.playlists)"]
    ScanPlaylists --> CompareHash{"Playlist modified since\nlast snapshot?"}
    
    CompareHash -->|No Changes| CheckHealth["Check track availability\n(is_track_available)"]
    CompareHash -->|Modified / New| SaveSnapshot["Save Snapshot JSON\nsnapshots/<playlist_slug>/<timestamp>.json"]
    SaveSnapshot --> UpdateIndex["Update Track History Store\n(maps track_id -> {isrc, upc, title, artist, date})"]
    UpdateIndex --> CheckHealth

    CheckHealth --> FindDead{"Any track newly unavailable?"}
    FindDead -->|Yes| RaiseAlert["Generate Availability Alert\n- Log to alert.log\n- Include track title, artist, and historical ISRC\n- Surface in CLI output"]
    FindDead -->|No| Done["Snapshot run complete"]

    subgraph RepairIntegration ["Integration with Playlist Repair"]
        RepairCmd["tidal-cli playlist repair <id>"] --> CheckLocalISRC{"Unavailable track\nhas ISRC in Tidal API?"}
        CheckLocalISRC -->|No (Stripped)| LookupHistory["Query History Store\n(Lookup last healthy ISRC & UPC)"]
        LookupHistory --> RecoveredISRC["Historical ISRC & UPC Recovered!"]
        RecoveredISRC --> RunTier1["Run Tier 1 ISRC + UPC Search\n(Scores: 90% - 100%)"]
    end
```

---

## 3. Storage & Snapshot Structure

Snapshots are stored in `~/.config/tidal-cli/snapshots/`:

```text
~/.config/tidal-cli/
├── session.json
├── jobs.yaml
├── snapshots/
│   ├── index.json                  # Global track registry: track_id -> last_known_healthy_metadata
│   ├── brol_2022/
│   │   ├── manifest.json           # Version list, timestamps, checksums
│   │   ├── 2026-10-01T060000Z.json # Full metadata snapshot
│   │   └── 2026-10-18T060000Z.json # Snapshot taken after track added
│   └── heavy_rotation/
│       ├── manifest.json
│       └── 2026-10-18T060000Z.json
└── logs/
    └── availability_alerts.log     # Append-only alert log
```

### 3.1 Global Track Registry Schema (`snapshots/index.json`)
```json
{
  "133142519": {
    "title": "Losing My Religion",
    "artist": "Applewood Road",
    "album": "Applewood Road",
    "duration_seconds": 275,
    "isrc": "GBYG51531015",
    "album_upc": "88697233442",
    "last_available_at": "2026-10-01T06:00:00Z",
    "found_in_playlists": ["brol_2022"]
  }
}
```

---

## 4. Implementation Todo List

### Phase 1: Snapshot Engine & Storage
- [ ] Create `tidal_cli.services.snapshot_service`:
  - `save_playlist_snapshot(playlist: UserPlaylist) -> Optional[Path]`
  - Compute checksum of track IDs to detect content changes.
  - Write formatted snapshot JSON with all container and tune metadata.
  - Update `snapshots/<slug>/manifest.json` with version history.
- [ ] Implement `update_track_registry(tracks: List[Track], playlist_slug: str)`:
  - Cache healthy `isrc` and `album_upc` for every active track into `snapshots/index.json`.

### Phase 2: Availability Monitoring & Alerting
- [ ] Implement availability difference detector:
  - Compare current playable status of each track against previous snapshot.
  - Detect tracks that transitioned from `available: True` to `available: False`.
- [ ] Alerting mechanisms:
  - Console summary during scheduled/interactive runs.
  - Append alert event to `~/.config/tidal-cli/logs/availability_alerts.log`.
  - Machine-readable alert output in JSON mode (`--json`).

### Phase 3: Integration with Playlist Repair
- [ ] Connect `CandidateFinder` and `PlaylistRepairService` to the `snapshot_service`:
  - When an unavailable track has `isrc is None`:
    - Check `snapshot_service.get_historical_metadata(track_id)`.
    - If found, hydrate `original_track["isrc"]` and `original_track["album_upc"]`.
  - Trigger Tier 1 ISRC search using the recovered code.
  - Display recovery note in diagnostic reasoning:
    `"Recovered original ISRC (GBYG51531015) from snapshot 2026-10-01. Exact ISRC match confirmed (+10 pts)."`

### Phase 4: CLI Commands & Cron Job
- [ ] Add `tidal-cli playlist snapshot [all|<identifier>]`:
  - Archives playlists and checks health.
- [ ] Add declarative job to default `jobs.yaml`:
  ```yaml
  daily-playlist-backup:
    description: "Daily snapshot of all user playlists with availability alerts"
    actions:
      - action: snapshot_all_playlists
        params:
          alert_on_unavailable: true
  ```
- [ ] Add `tidal-cli playlist alerts` to view recent delisted tracks across all playlists.

### Phase 5: Testing & Calibration
- [ ] Unit tests for snapshot creation, change detection, and history indexing.
- [ ] Unit tests for ISRC/UPC recovery and repair using historical snapshots.
- [ ] Verification on live user playlists.
