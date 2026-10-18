"""Business logic services for Musicae Curo."""

from curo.services.album_service import AlbumService
from curo.services.candidate_finder import CandidateFinder
from curo.services.email_service import EmailService
from curo.services.job_runner import JobExecutionError, JobRunner
from curo.services.metadata_serializer import (
    serialize_album,
    serialize_playlist,
    serialize_track,
)
from curo.services.mix_service import MixService
from curo.services.playlist_repair_service import PlaylistRepairService
from curo.services.playlist_service import PlaylistService
from curo.services.similarity_scorer import CandidateEvaluation, evaluate_candidate
from curo.services.snapshot_service import SnapshotService
from curo.services.track_matcher import is_track_available, normalize_title

__all__ = [
    "AlbumService",
    "CandidateEvaluation",
    "CandidateFinder",
    "EmailService",
    "JobExecutionError",
    "JobRunner",
    "MixService",
    "PlaylistRepairService",
    "PlaylistService",
    "SnapshotService",
    "evaluate_candidate",
    "is_track_available",
    "normalize_title",
    "serialize_album",
    "serialize_playlist",
    "serialize_track",
]

