"""Tests for MixService discovery, deduplication, shuffling, and megamix synthesis."""

import datetime
from unittest.mock import MagicMock, patch
import pytest

from tidalapi.mix import MixType
from tidal_cli.services.mix_service import MixService


def make_mock_mix(mix_id: str, title: str, mix_type: MixType, tracks: list):
    m = MagicMock()
    m.id = mix_id
    m.title = title
    m.mix_type = mix_type
    m.items.return_value = tracks
    return m


def make_mock_track(track_id: int, name: str, isrc: str = ""):
    t = MagicMock()
    t.id = track_id
    t.name = name
    t.isrc = isrc
    return t


@pytest.fixture
def mock_session():
    s = MagicMock()
    s.user = MagicMock()
    return s


@pytest.mark.parametrize("mix_count", [1, 3, 8, 12])
def test_categorize_dynamic_daily_mixes_arbitrary_counts(mock_session, mix_count):
    """Verify that any arbitrary count of Daily Mixes (1, 3, 8, 12, etc.) is discovered."""
    discovery_mix = make_mock_mix("mix-disc", "My Daily Discovery", MixType.discovery, [])

    daily_mixes = [
        make_mock_mix(f"mix-daily-{i}", f"Daily Mix {i}", MixType.daily, [])
        for i in range(1, mix_count + 1)
    ]

    # Include a video daily mix that should be ignored
    video_mix = make_mock_mix("mix-vid", "Daily Video Mix 1", getattr(MixType, "video_daily", MixType.daily), [])
    video_mix.title = "Daily Video Mix 1"

    all_mixes = [discovery_mix, video_mix] + daily_mixes

    cat = MagicMock()
    cat.items = all_mixes
    mock_session.mixes.return_value = MagicMock(categories=[cat])
    mock_session.user.favorites.mixes.return_value = []
    mock_session.home.return_value = None

    svc = MixService(mock_session)
    disc, dailies = svc.get_categorized_daily_mixes()

    assert disc == discovery_mix
    assert len(dailies) == mix_count
    assert [m.title for m in dailies] == [f"Daily Mix {i}" for i in range(1, mix_count + 1)]


def test_megamix_deduplication_and_shuffle(mock_session):
    """Ensure tracks present across multiple daily mixes are deduplicated and shuffled."""
    # Track 1 appears in both Discovery and Daily Mix 1
    t1 = make_mock_track(101, "Song A", isrc="ISRC1")
    t2 = make_mock_track(102, "Song B", isrc="ISRC2")
    t3 = make_mock_track(103, "Song C", isrc="ISRC3")
    t4 = make_mock_track(104, "Song D", isrc="ISRC4")
    # Duplicate of t1 with different ID but same ISRC
    t1_dupe_isrc = make_mock_track(999, "Song A (Remaster)", isrc="ISRC1")

    disc = make_mock_mix("m-disc", "My Daily Discovery", MixType.discovery, [t1, t2])
    daily1 = make_mock_mix("m-d1", "Daily Mix 1", MixType.daily, [t1, t3])
    daily2 = make_mock_mix("m-d2", "Daily Mix 2", MixType.daily, [t1_dupe_isrc, t4])

    cat = MagicMock()
    cat.items = [disc, daily1, daily2]
    mock_page = MagicMock(categories=[cat])
    mock_session.mixes.return_value = mock_page
    mock_session.user.favorites.mixes.return_value = []
    mock_session.home.return_value = None

    # Target playlist mock
    target_pl = MagicMock()
    target_pl.id = "megamix-pl"
    target_pl.name = "Daily Megamix"
    target_pl.clear.return_value = True

    svc = MixService(mock_session)
    svc.playlist_service.get_or_create_playlist = MagicMock(return_value=target_pl)
    svc.playlist_service.add_tracks = MagicMock(return_value=4)

    res = svc.create_megamix(
        target_playlist="Daily Megamix",
        shuffle=True,
        mode="replace",
    )

    assert res["success"] is True
    # Out of [t1, t2] + [t1, t3] + [t1_dupe_isrc, t4], unique tracks are 4: 101, 102, 103, 104
    assert res["total_unique_tracks"] == 4
    assert res["mixes_processed"] == 3

    # Check playlist was cleared in replace mode
    target_pl.clear.assert_called_once()
    # Check add_tracks was called with 4 unique track IDs
    svc.playlist_service.add_tracks.assert_called_once()
    added_ids = svc.playlist_service.add_tracks.call_args[1]["track_ids"]
    assert set(added_ids) == {101, 102, 103, 104}


def test_backup_daily_discovery(mock_session):
    today = datetime.date.today().isoformat()
    t1 = make_mock_track(111, "Discovery 1")
    t2 = make_mock_track(222, "Discovery 2")
    disc = make_mock_mix("m-disc", "My Daily Discovery", MixType.discovery, [t1, t2])

    cat = MagicMock(items=[disc])
    mock_session.mixes.return_value = MagicMock(categories=[cat])
    mock_session.user.favorites.mixes.return_value = []
    mock_session.home.return_value = None

    backup_pl = MagicMock(id="bk-pl", name=f"Discovery Backup - {today}")
    svc = MixService(mock_session)
    svc.playlist_service.get_or_create_playlist = MagicMock(return_value=backup_pl)
    svc.playlist_service.add_tracks = MagicMock(return_value=2)

    res = svc.backup_daily_discovery()

    assert res["success"] is True
    assert res["playlist_name"] == f"Discovery Backup - {today}"
    assert res["tracks_added"] == 2

