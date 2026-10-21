"""Tests for MixService discovery, deduplication, shuffling, and megamix synthesis."""

import datetime
from unittest.mock import MagicMock
import pytest

from curo.core.models import CuratedMix, Track
from curo.providers.tidal import TidalProvider
from curo.services.mix_service import MixService


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


@pytest.fixture
def mock_provider(mock_session):
    return TidalProvider(session=mock_session)


@pytest.mark.parametrize("mix_count", [1, 3, 8, 12])
def test_categorize_dynamic_daily_mixes_arbitrary_counts(mock_session, mock_provider, mix_count):
    """Verify that any arbitrary count of Daily Mixes (1, 3, 8, 12, etc.) is discovered."""
    discovery_mix = MagicMock(id="mix-disc")
    discovery_mix.title = "My Daily Discovery"
    discovery_mix.mix_type = "discovery"

    daily_mixes = []
    for i in range(1, mix_count + 1):
        m = MagicMock(id=f"mix-daily-{i}")
        m.title = f"Daily Mix {i}"
        m.mix_type = "daily"
        daily_mixes.append(m)

    video_mix = MagicMock(id="mix-vid")
    video_mix.title = "Daily Video Mix 1"
    video_mix.mix_type = "video_daily"

    all_mixes = [discovery_mix, video_mix] + daily_mixes
    mock_session.mixes.return_value = all_mixes

    svc = MixService(mock_provider)
    disc, dailies = svc.get_categorized_daily_mixes()

    assert disc.title == "My Daily Discovery"
    assert len(dailies) == mix_count
    assert [m.title for m in dailies] == [f"Daily Mix {i}" for i in range(1, mix_count + 1)]


def test_megamix_deduplication_and_shuffle(mock_session, mock_provider):
    """Ensure tracks present across multiple daily mixes are deduplicated and shuffled."""
    t1 = make_mock_track(101, "Song A", isrc="ISRC1")
    t2 = make_mock_track(102, "Song B", isrc="ISRC2")
    t3 = make_mock_track(103, "Song C", isrc="ISRC3")
    t4 = make_mock_track(104, "Song D", isrc="ISRC4")
    t1_dupe_isrc = make_mock_track(999, "Song A (Remaster)", isrc="ISRC1")

    disc = MagicMock(id="m-disc", title="My Daily Discovery", mix_type="discovery")
    daily1 = MagicMock(id="m-d1", title="Daily Mix 1", mix_type="daily")
    daily2 = MagicMock(id="m-d2", title="Daily Mix 2", mix_type="daily")

    mock_session.mixes.return_value = [disc, daily1, daily2]

    m_disc_obj = MagicMock()
    m_disc_obj.tracks.return_value = [t1, t2]

    m_d1_obj = MagicMock()
    m_d1_obj.tracks.return_value = [t1, t3]

    m_d2_obj = MagicMock()
    m_d2_obj.tracks.return_value = [t1_dupe_isrc, t4]

    def _get_mix(mid):
        if mid == "m-disc":
            return m_disc_obj
        if mid == "m-d1":
            return m_d1_obj
        return m_d2_obj

    mock_session.mix.side_effect = _get_mix

    target_pl = MagicMock()
    target_pl.id = "megamix-pl"
    target_pl.name = "Daily Megamix"

    mock_provider.clear_playlist = MagicMock(return_value=True)

    svc = MixService(mock_provider)
    svc.playlist_service.get_or_create_playlist = MagicMock(return_value=target_pl)
    svc.playlist_service.add_tracks = MagicMock(return_value=4)

    res = svc.create_megamix(
        target_playlist="Daily Megamix",
        shuffle=True,
        mode="replace",
    )

    assert res["success"] is True
    assert res["total_unique_tracks"] == 4
    assert res["mixes_processed"] == 3
    mock_provider.clear_playlist.assert_called_once_with("megamix-pl")

    svc.playlist_service.add_tracks.assert_called_once()
    added_ids = svc.playlist_service.add_tracks.call_args[1]["track_ids"]
    assert set(added_ids) == {"101", "102", "103", "104"}


def test_megamix_append_mode_does_not_clear(mock_session, mock_provider):
    """Ensure append mode does not clear existing tracks."""
    t1 = make_mock_track(101, "Song A")
    disc = MagicMock(id="m-disc", title="My Daily Discovery", mix_type="discovery")
    mock_session.mixes.return_value = [disc]

    m_disc_obj = MagicMock()
    m_disc_obj.tracks.return_value = [t1]
    mock_session.mix.return_value = m_disc_obj

    target_pl = MagicMock(id="megamix-pl")
    mock_provider.clear_playlist = MagicMock(return_value=True)

    svc = MixService(mock_provider)
    svc.playlist_service.get_or_create_playlist = MagicMock(return_value=target_pl)
    svc.playlist_service.add_tracks = MagicMock(return_value=1)

    res = svc.create_megamix(mode="append")
    assert res["success"] is True
    mock_provider.clear_playlist.assert_not_called()


def test_megamix_exclude_recent_days_fresh_only(tmp_path, mock_session, mock_provider):
    """Ensure exclude_recent_days filters out tracks heard in recent days."""
    from curo.config import ConfigManager
    import json

    cfg = ConfigManager(custom_config_dir=tmp_path)
    svc = MixService(mock_provider, cfg=cfg)

    # Pre-populate history: yesterday saw track 101 and ISRC2
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    history = {
        mock_provider.name: {
            yesterday: {
                "track_ids": ["101"],
                "isrcs": ["ISRC2"],
            }
        }
    }
    with open(svc.history_file, "w", encoding="utf-8") as f:
        json.dump(history, f)

    # Today's mixes offer:
    # 101 (seen yesterday by ID) -> EXCLUDE
    # 102 with ISRC2 (seen yesterday by ISRC) -> EXCLUDE
    # 103 with ISRC3 (never seen) -> KEEP (FRESH)
    t1 = make_mock_track(101, "Old Song 1", isrc="ISRC1")
    t2 = make_mock_track(102, "Old Song 2", isrc="ISRC2")
    t3 = make_mock_track(103, "Fresh Song 3", isrc="ISRC3")

    disc = MagicMock(id="m-disc", title="My Daily Discovery", mix_type="discovery")
    mock_session.mixes.return_value = [disc]

    m_disc_obj = MagicMock()
    m_disc_obj.tracks.return_value = [t1, t2, t3]
    mock_session.mix.return_value = m_disc_obj

    target_pl = MagicMock(id="fresh-pl")
    mock_provider.clear_playlist = MagicMock(return_value=True)
    svc.playlist_service.get_or_create_playlist = MagicMock(return_value=target_pl)
    svc.playlist_service.add_tracks = MagicMock(return_value=1)

    res = svc.create_megamix(
        target_playlist="Daily Fresh Megamix",
        mode="replace",
        exclude_recent_days=7,
    )

    assert res["success"] is True
    assert res["total_unique_tracks"] == 1
    assert res["excluded_recent_repeats"] == 2
    mock_provider.clear_playlist.assert_called_once_with("fresh-pl")

    svc.playlist_service.add_tracks.assert_called_once()
    added_ids = svc.playlist_service.add_tracks.call_args[1]["track_ids"]
    assert added_ids == ["103"]


def test_backup_daily_discovery(mock_session, mock_provider):
    today = datetime.date.today().isoformat()
    t1 = make_mock_track(111, "Discovery 1")
    t2 = make_mock_track(222, "Discovery 2")

    disc = MagicMock(id="m-disc", title="My Daily Discovery", mix_type="discovery")
    mock_session.mixes.return_value = [disc]

    m_disc_obj = MagicMock()
    m_disc_obj.tracks.return_value = [t1, t2]
    mock_session.mix.return_value = m_disc_obj

    backup_pl = MagicMock(id="bk-pl", name=f"Discovery Backup - {today}")
    svc = MixService(mock_provider)
    svc.playlist_service.get_or_create_playlist = MagicMock(return_value=backup_pl)
    svc.playlist_service.add_tracks = MagicMock(return_value=2)

    res = svc.backup_daily_discovery()

    assert res["success"] is True
    assert res["playlist_name"] == f"Discovery Backup - {today}"
    assert res["tracks_added"] == 2


def test_backup_daily_discovery_with_folder(mock_session, mock_provider):
    today = datetime.date.today().isoformat()
    t1 = make_mock_track(555, "Discovery Folder 1")
    disc = MagicMock(id="m-disc-f", title="Daily Discovery", mix_type="discovery")
    mock_session.mixes.return_value = [disc]

    m_disc_obj = MagicMock()
    m_disc_obj.tracks.return_value = [t1]
    mock_session.mix.return_value = m_disc_obj

    backup_pl = MagicMock(id="bk-pl-f", name=f"Discovery Backup - {today}")
    svc = MixService(mock_provider)
    svc.playlist_service.get_or_create_playlist = MagicMock(return_value=backup_pl)
    svc.playlist_service.add_tracks = MagicMock(return_value=1)

    res = svc.backup_daily_discovery(folder="Discovery Backups")

    assert res["success"] is True
    assert res["folder"] == "Discovery Backups"
    svc.playlist_service.get_or_create_playlist.assert_called_once()
    assert svc.playlist_service.get_or_create_playlist.call_args[1]["folder"] == "Discovery Backups"


def test_create_megamix_with_folder(mock_session, mock_provider):
    t1 = make_mock_track(666, "Mega Track 1")
    disc = MagicMock(id="m-disc-m", title="Daily Discovery", mix_type="discovery")
    mock_session.mixes.return_value = [disc]

    m_disc_obj = MagicMock()
    m_disc_obj.tracks.return_value = [t1]
    mock_session.mix.return_value = m_disc_obj

    target_pl = MagicMock(id="pl-mega-f", name="Daily Megamix")
    svc = MixService(mock_provider)
    svc.playlist_service.get_or_create_playlist = MagicMock(return_value=target_pl)
    svc.playlist_service.add_tracks = MagicMock(return_value=1)

    res = svc.create_megamix(folder="My Megamixes")

    assert res["success"] is True
    assert res["folder"] == "My Megamixes"
    svc.playlist_service.get_or_create_playlist.assert_called_once()
    assert svc.playlist_service.get_or_create_playlist.call_args[1]["folder"] == "My Megamixes"


def test_backup_daily_discovery_with_mix_items_attribute(mock_session, mock_provider):
    """Ensure Mix objects having .items() (as in real tidalapi.mix.Mix) work without error."""
    today = datetime.date.today().isoformat()
    t1 = make_mock_track(333, "Discovery Real 1")
    t2 = make_mock_track(444, "Discovery Real 2")

    disc = MagicMock(id="m-disc-real", title="Daily Discovery", mix_type="discovery")
    mock_session.mixes.return_value = [disc]

    # Simulate real tidalapi.mix.Mix: has .items(), but NOT .tracks()
    class RealTidalMix:
        def __init__(self, items):
            self._items = items

        def items(self):
            return self._items

    mock_session.mix.return_value = RealTidalMix([t1, t2])

    backup_pl = MagicMock(id="bk-pl-real", name=f"Discovery Backup - {today}")
    svc = MixService(mock_provider)
    svc.playlist_service.get_or_create_playlist = MagicMock(return_value=backup_pl)
    svc.playlist_service.add_tracks = MagicMock(return_value=2)

    res = svc.backup_daily_discovery()

    assert res["success"] is True
    assert res["playlist_name"] == f"Discovery Backup - {today}"
    assert res["tracks_added"] == 2
    tracks = mock_provider.get_mix_tracks("m-disc-real")
    assert len(tracks) == 2
    assert tracks[0].id == "333"
    assert tracks[1].id == "444"
