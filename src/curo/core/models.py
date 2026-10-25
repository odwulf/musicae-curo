"""Core domain models for Musicae Curo.

Pure domain representation of music entities (Track, Album, Artist, Playlist, CuratedMix)
independent of any streaming service provider.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Artist(BaseModel):
    """Domain model representing a musical artist or band."""

    id: str
    name: str
    provider: str = "unknown"

    def __hash__(self) -> int:
        return hash((self.provider, self.id))


class Album(BaseModel):
    """Domain model representing a music album, EP, or single release."""

    id: str
    title: str
    provider: str = "unknown"
    upc: Optional[str] = None
    release_date: Optional[str] = None
    artists: List[Artist] = Field(default_factory=list)

    @property
    def artist_name(self) -> str:
        if self.artists:
            return self.artists[0].name
        return "Unknown Artist"


class Track(BaseModel):
    """Domain model representing a sound recording / playable track."""

    id: str
    title: str
    provider: str = "unknown"
    duration_seconds: int = 0
    artist: Artist
    collaborating_artists: List[Artist] = Field(default_factory=list)
    album: Optional[Album] = None
    isrc: Optional[str] = None
    available: bool = True
    allow_streaming: bool = True
    stream_ready: bool = True
    provider_replacement_id: Optional[str] = None

    # Helper property for compatibility with existing string normalization
    @property
    def name(self) -> str:
        return self.title

    @property
    def duration(self) -> int:
        return self.duration_seconds

    @property
    def is_playable(self) -> bool:
        return bool(self.available and self.allow_streaming and self.stream_ready)


class Playlist(BaseModel):
    """Domain model representing a curated or user playlist container."""

    id: str
    title: str
    provider: str = "unknown"
    description: Optional[str] = ""
    is_public: bool = False
    is_owner: bool = True
    track_count: int = 0
    duration_seconds: int = 0
    updated_at: Optional[datetime] = None

    @property
    def name(self) -> str:
        return self.title


class CuratedMix(BaseModel):
    """Domain model representing an algorithmic or editorially curated discovery mix."""

    id: str
    title: str
    sub_title: Optional[str] = None
    provider: str = "unknown"
    mix_type: str = "curated_mix"  # e.g., 'daily_discovery', 'new_arrivals', 'curated_mix'
    track_count: int = 0

    @property
    def name(self) -> str:
        return self.title

