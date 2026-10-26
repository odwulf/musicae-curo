"""Serializers for exporting comprehensive metadata of playlists, albums, and tracks to JSON."""

from __future__ import annotations

import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


def _is_mock(val: Any) -> bool:
    """Detect if an object is an unconfigured unittest MagicMock."""
    return hasattr(val, "_mock_return_value") or hasattr(val, "_mock_wraps") or "Mock" in type(val).__name__


def _clean_str(val: Any) -> Optional[str]:
    """Convert value to string, handling datetime/date objects and ignoring mocks."""
    if val is None or _is_mock(val):
        return None
    if isinstance(val, (datetime.datetime, datetime.date)):
        return val.isoformat()
    if isinstance(val, Enum):
        return str(val.value)
    return str(val)


def _clean_int(val: Any, default: Optional[int] = None) -> Optional[int]:
    """Safely convert value to integer, ignoring mocks."""
    if val is None or _is_mock(val):
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _clean_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    """Safely convert value to float, ignoring mocks."""
    if val is None or _is_mock(val):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _clean_bool(val: Any, default: bool = False) -> bool:
    """Safely convert value to boolean, ignoring mocks."""
    if val is None or _is_mock(val):
        return default
    return bool(val)


def _clean_list(val: Any) -> List[Any]:
    """Safely return a list of clean string/primitive values."""
    if val is None or _is_mock(val):
        return []
    if not isinstance(val, (list, tuple, set)):
        return []
    cleaned = []
    for item in val:
        if _is_mock(item):
            continue
        if isinstance(item, Enum):
            cleaned.append(item.value)
        elif isinstance(item, (int, float, bool, str)):
            cleaned.append(item)
        else:
            cleaned.append(str(item))
    return cleaned


def _serialize_artist(artist: Any) -> Optional[Dict[str, Any]]:
    """Serialize an Artist object."""
    if not artist:
        return None
    a_id = _clean_int(getattr(artist, "id", None)) or _clean_str(getattr(artist, "id", None))
    a_name = _clean_str(getattr(artist, "name", None))
    if not a_id and not a_name:
        return None

    return {
        "id": a_id,
        "name": a_name or "Unknown Artist",
        "provider": _clean_str(getattr(artist, "provider", None)),
        "type": _clean_str(getattr(artist, "type", None)),
        "picture": _clean_str(getattr(artist, "picture", None)),
        "url": _clean_str(getattr(artist, "url", None)),
    }


def _serialize_album_summary(album: Any) -> Optional[Dict[str, Any]]:
    """Serialize a summary of an album attached to a track."""
    if not album:
        return None
    alb_id = _clean_int(getattr(album, "id", None)) or _clean_str(getattr(album, "id", None))
    alb_name = _clean_str(getattr(album, "name", None)) or _clean_str(getattr(album, "title", None))
    if not alb_id and not alb_name:
        return None

    return {
        "id": alb_id,
        "name": alb_name or "Unknown Album",
        "title": alb_name or "Unknown Album",
        "upc": _clean_str(getattr(album, "upc", None)),
        "release_date": _clean_str(getattr(album, "release_date", None)),
        "cover": _clean_str(getattr(album, "cover", None)),
        "url": _clean_str(getattr(album, "listen_url", None) or getattr(album, "share_url", None)),
    }


def serialize_track(track: Any) -> Dict[str, Any]:
    """Extract all available metadata from a Track object."""
    artists_list = []
    collab = getattr(track, "collaborating_artists", None)
    raw_artists = collab if (collab is not None and not _is_mock(collab)) else getattr(track, "artists", [])
    if isinstance(raw_artists, (list, tuple)):
        for a in raw_artists:
            serialized_a = _serialize_artist(a)
            if serialized_a:
                artists_list.append(serialized_a)

    track_id = getattr(track, "id", None)
    clean_id = _clean_int(track_id) or _clean_str(track_id)
    name = _clean_str(getattr(track, "name", None)) or _clean_str(getattr(track, "title", "")) or ""
    full_name = _clean_str(getattr(track, "full_name", None)) or name

    dur = _clean_int(getattr(track, "duration_seconds", None))
    if dur is None:
        dur = _clean_int(getattr(track, "duration", None))

    return {
        "id": clean_id,
        "name": name,
        "title": name,
        "full_name": full_name,
        "provider": _clean_str(getattr(track, "provider", None)),
        "duration_seconds": _clean_int(dur),
        "track_number": _clean_int(getattr(track, "track_num", None)),
        "volume_number": _clean_int(getattr(track, "volume_num", None)),
        "index": _clean_int(getattr(track, "index", None)),
        "isrc": _clean_str(getattr(track, "isrc", None)),
        "explicit": _clean_bool(getattr(track, "explicit", False)),
        "popularity": _clean_int(getattr(track, "popularity", None)),
        "audio_quality": _clean_str(getattr(track, "audio_quality", None)),
        "audio_modes": _clean_list(getattr(track, "audio_modes", [])),
        "is_lossless": _clean_bool(getattr(track, "is_lossless", None)),
        "is_hi_res_lossless": _clean_bool(getattr(track, "is_hi_res_lossless", None)),
        "is_dolby_atmos": _clean_bool(getattr(track, "is_dolby_atmos", None)),
        "media_metadata_tags": _clean_list(getattr(track, "media_metadata_tags", [])),
        "bpm": _clean_int(getattr(track, "bpm", None)),
        "key": _clean_str(getattr(track, "key", None)),
        "key_scale": _clean_str(getattr(track, "key_scale", None)),
        "replay_gain": _clean_float(getattr(track, "replay_gain", None)),
        "peak": _clean_float(getattr(track, "peak", None)),
        "copyright": _clean_str(getattr(track, "copyright", None)),
        "allow_streaming": _clean_bool(getattr(track, "allow_streaming", True)),
        "stream_ready": _clean_bool(getattr(track, "stream_ready", True)),
        "stream_start_date": _clean_str(getattr(track, "stream_start_date", None)),
        "tidal_release_date": _clean_str(getattr(track, "tidal_release_date", None)),
        "date_added": _clean_str(getattr(track, "date_added", None)),
        "available": _clean_bool(getattr(track, "available", True)),
        "version": _clean_str(getattr(track, "version", None)),
        "provider_replacement_id": _clean_str(getattr(track, "provider_replacement_id", None)) or (track.get("provider_replacement_id") if isinstance(track, dict) else None),
        "has_provider_replacement": bool(_clean_str(getattr(track, "provider_replacement_id", None)) or (track.get("provider_replacement_id") if isinstance(track, dict) else None)),
        "listen_url": _clean_str(getattr(track, "listen_url", None)),
        "share_url": _clean_str(getattr(track, "share_url", None)),
        "artist": _serialize_artist(getattr(track, "artist", None)),
        "artists": artists_list,
        "album": _serialize_album_summary(getattr(track, "album", None)),
    }


def serialize_playlist(playlist: Any, tracks: List[Any]) -> Dict[str, Any]:
    """Serialize full playlist metadata and all included tracks."""
    creator_obj = getattr(playlist, "creator", None)
    creator_info = None
    if creator_obj:
        c_name = _clean_str(getattr(creator_obj, "name", None))
        c_id = _clean_int(getattr(creator_obj, "id", None)) or _clean_str(getattr(creator_obj, "id", None))
        if c_name or c_id:
            creator_info = {
                "id": c_id,
                "name": c_name,
            }

    pl_id = getattr(playlist, "id", None)
    clean_id = _clean_str(pl_id) or str(pl_id)
    pl_name = _clean_str(getattr(playlist, "name", None)) or _clean_str(getattr(playlist, "title", "")) or ""

    dur = getattr(playlist, "duration_seconds", None) or getattr(playlist, "duration", None)

    return {
        "type": "playlist",
        "id": clean_id,
        "name": pl_name,
        "title": pl_name,
        "provider": _clean_str(getattr(playlist, "provider", None)),
        "description": _clean_str(getattr(playlist, "description", "")) or "",
        "created": _clean_str(getattr(playlist, "created", None)),
        "last_updated": _clean_str(getattr(playlist, "last_updated", None) or getattr(playlist, "updated_at", None)),
        "duration_seconds": _clean_int(dur),
        "num_tracks": len(tracks),
        "track_count": len(tracks),
        "num_followers": _clean_int(getattr(playlist, "followers_count", None)),
        "followers_count": _clean_int(getattr(playlist, "followers_count", None)),
        "public": _clean_bool(getattr(playlist, "is_public", False) or getattr(playlist, "public", False)),
        "creator": creator_info,
        "tracks": [serialize_track(t) for t in tracks],
    }


def serialize_album(album: Any, tracks: List[Any]) -> Dict[str, Any]:
    """Serialize full album metadata and all included tracks."""
    artists_list = []
    raw_artists = getattr(album, "artists", [])
    if isinstance(raw_artists, (list, tuple)):
        for a in raw_artists:
            serialized_a = _serialize_artist(a)
            if serialized_a:
                artists_list.append(serialized_a)

    alb_id = getattr(album, "id", None)
    clean_id = _clean_int(alb_id) or _clean_str(alb_id)
    alb_name = _clean_str(getattr(album, "name", None)) or _clean_str(getattr(album, "title", "")) or ""

    dur = getattr(album, "duration_seconds", None) or getattr(album, "duration", None)

    return {
        "type": "album",
        "id": clean_id,
        "name": alb_name,
        "title": alb_name,
        "provider": _clean_str(getattr(album, "provider", None)),
        "version": _clean_str(getattr(album, "version", None)),
        "duration_seconds": _clean_int(dur),
        "num_tracks": len(tracks),
        "track_count": len(tracks),
        "release_date": _clean_str(getattr(album, "release_date", None)),
        "upc": _clean_str(getattr(album, "upc", None)),
        "artist": _serialize_artist(getattr(album, "artist", None)),
        "artists": artists_list,
        "tracks": [serialize_track(t) for t in tracks],
    }
