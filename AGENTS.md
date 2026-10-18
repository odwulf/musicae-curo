# AGENTS.md -- AI Agent Guidelines for Musicae Curo (`curo`)

Welcome, AI Agent! This document contains essential technical context, architectural rules, environment quirks, and development conventions for pair-programming and extending **Musicae Curo**.

---

## 1. Project Background & Disclaimer

- **Project Name**: Musicae Curo (executable: `curo`, Python package: `curo`).
- **Purpose**: Universal music library custodian, playlist protector, and daily discovery mix archiver.
- **Origins**: This project was created to fulfill a personal music curation need. All Python code and architecture in this repository has been created with the assistance of an AI coding agent. The repository owner does not claim to be a professional coder and makes this project available as-is for anyone with similar playlist curation needs.

---

## 2. Technology Stack & Environment

- **Language**: Python 3.10+ (tested through Python 3.14).
- **Package & Environment Manager**: [`uv`](https://github.com/astral-sh/uv).
- **Core Libraries**:
  - `typer`: CLI command dispatch and option parsing.
  - `rich`: Terminal tables, panels, and JSON rendering.
  - `pydantic`: Schema validation for domain models and configuration.
  - `tidalapi`: Unofficial TIDAL API client.
  - `pyyaml`: Parsing declarative jobs (`jobs.yaml`).
  - `pytest`: Comprehensive unit and integration test suite.

### Critical Environment Note: Cloud-Synced Filesystems (e.g. OneDrive)
When the repository resides within a cloud-synced folder (such as Microsoft OneDrive or Google Drive on Windows), `uv` hardlinks can fail with `os error 396 (incompatible hardlinks)`.
**Always pass `--link-mode=copy` or set `$env:UV_LINK_MODE="copy"` in PowerShell**:
```powershell
# PowerShell (Windows)
$env:UV_LINK_MODE="copy"; uv run pytest -v
$env:UV_LINK_MODE="copy"; uv run curo --help

# Bash / Linux
UV_LINK_MODE=copy uv run pytest -v
```

---

## 3. Essential Developer Commands

```powershell
# Run the entire test suite
$env:UV_LINK_MODE="copy"; uv run pytest -v

# Run a specific test file
$env:UV_LINK_MODE="copy"; uv run pytest tests/test_snapshot_service.py -v

# Run a specific test function
$env:UV_LINK_MODE="copy"; uv run pytest tests/test_playlist_repair_service.py -k "test_resolve_replacement" -v

# Run the CLI tool
$env:UV_LINK_MODE="copy"; uv run curo --help
$env:UV_LINK_MODE="copy"; uv run curo provider list
```

---

## 4. Architectural Boundaries & Conventions

Musicae Curo follows strict Hexagonal / Clean Architecture:

```
src/curo/
├── core/                  # Pure Domain Core (NEVER import vendor SDKs here)
│   ├── models.py          # Domain entities: Track, Album, Artist, Playlist, CuratedMix
│   ├── provider.py        # Abstract Base Class: MusicProvider SPI
│   └── registry.py        # ProviderRegistry for dynamic adapter resolution
├── providers/             # Platform-Specific Adapter Implementations
│   ├── base.py            # ProviderStub raising clear NotImplementedError
│   ├── tidal/             # Tidal adapter (tidalapi + TidalSessionManager)
│   ├── deezer/            # Scaffolded stub adapter
│   ├── spotify/           # Scaffolded stub adapter
│   ├── qobuz/             # Scaffolded stub adapter
│   └── ytmusic/           # Scaffolded stub adapter
├── services/              # Domain Business Logic (ONLY depends on MusicProvider)
│   ├── album_service.py
│   ├── candidate_finder.py
│   ├── email_service.py
│   ├── job_runner.py
│   ├── metadata_serializer.py
│   ├── mix_service.py
│   ├── playlist_repair_service.py
│   ├── playlist_service.py
│   ├── similarity_scorer.py
│   ├── snapshot_service.py
│   └── track_matcher.py
├── cli.py                 # Typer application with global -p/--provider option
└── config.py              # Configuration manager (~/.config/curo/)
```

### Golden Architectural Rules
1. **No Vendor Leaks in Core or Services**:
   - `curo.core` and `curo.services` must **never** import `tidalapi`, `spotipy`, or any third-party streaming SDK.
   - All interactions with music services **must** go through the `MusicProvider` abstract interface.
2. **Domain Models Are Sovereign**:
   - Services consume and return `Track`, `Album`, `Artist`, `Playlist`, and `CuratedMix` from `curo.core.models`.
   - Providers are responsible for bidirectional mapping between vendor API objects and domain models.
3. **Rich Console & JSON Mode**:
   - When outputting JSON in CLI commands, **always use `console.print_json(data=...)`**.
   - **Never** use `console.print(json.dumps(...))` because Rich interprets brackets and Windows file paths (`\U`, `\u`) as markup or control sequences, causing `JSONDecodeError`.
4. **Mock Handling in Unit Tests**:
   - In unit tests with `MagicMock`, unconfigured attributes create dynamic mock objects that are truthy in Python.
   - Helper functions `_is_mock(val)` and `_clean_str(val)` protect serialization and scoring logic from treating mock children as real data.

---

## 5. Adding a New Music Provider

To implement a new streaming provider (e.g. Deezer or Spotify):
1. Navigate to `src/curo/providers/<name>/`.
2. Subclass `MusicProvider` from `curo.core.provider`.
3. Implement required methods:
   - Authentication: `login`, `logout`, `get_auth_info`, `is_authenticated`.
   - Playlists: `get_user_playlists`, `get_playlist`, `get_playlist_tracks`, `create_playlist`, `add_tracks_to_playlist`, `remove_track_from_playlist_by_index`.
   - Search & Lookup: `get_track`, `get_tracks_by_isrc`, `get_album`, `get_albums_by_barcode`, `get_album_tracks`, `search_tracks`.
   - Algorithmic Mixes: `get_daily_discovery_mix`, `get_daily_mixes`.
4. Register the provider at the bottom of the adapter module:
   ```python
   ProviderRegistry.register("<provider_id>", MyProviderClass)
   ```
5. Add unit tests in `tests/test_provider_<name>.py`.

---

## 6. Git Commit Guidelines

When preparing or proposing git commits in this repository, **you must adhere to the Seven Golden Rules**:

1. **Separate subject from body with a blank line.**
2. **Limit the subject line to 50 characters** (maximum 72 characters).
3. **Capitalize the subject line** (unless using lowercase Conventional Commit prefixes like `feat:` or `fix:`).
4. **Do not end the subject line with a period.**
5. **Use the imperative mood in the subject line** (e.g., `Add feature`, `Fix bug`, not `Added` or `Fixes`).
   - Rule of thumb: Complete the sentence *"If applied, this commit will `<subject>`"*.
6. **Wrap body lines at 72 characters.**
7. **Use the body to explain *what* and *why*, not *how*.**
   - Format detailed points with bulleted lists (`- `).

### Mandatory Validation Rule
- **Always propose the commit message to the user for validation before executing `git commit`.**

