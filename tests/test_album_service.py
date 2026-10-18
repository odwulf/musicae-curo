"""Tests for AlbumService inspection and export."""

import json
from unittest.mock import MagicMock
import pytest

from curo.providers.tidal import TidalProvider
from curo.services.album_service import AlbumService


@pytest.fixture
def mock_session():
    return MagicMock()


@pytest.fixture
def mock_provider(mock_session):
    return TidalProvider(session=mock_session)


def test_get_album_by_id(mock_session, mock_provider):
    alb = MagicMock(id=987654)
    alb.name = "Abbey Road"
    mock_session.album.return_value = alb

    svc = AlbumService(mock_provider)
    found = svc.get_album("987654")

    assert found is not None
    assert found.title == "Abbey Road"
    mock_session.album.assert_called_with(987654)


def test_export_album_json_full_metadata(mock_session, mock_provider):
    alb = MagicMock(id=123)
    alb.name = "Thriller"
    alb.release_date = "1982-11-30"
    alb.artist.id = 1
    alb.artist.name = "Michael Jackson"
    alb.artists = [alb.artist]

    t1 = MagicMock(id=555)
    t1.name = "Billie Jean"
    t1.track_num = 6
    t1.isrc = "USSM18200001"
    t1.duration = 294
    t1.artist = alb.artist
    t1.artists = [alb.artist]
    t1.album = alb

    alb.tracks.return_value = [t1]
    mock_session.album.return_value = alb

    svc = AlbumService(mock_provider)
    json_out = svc.export_album("123", export_format="json")
    parsed = json.loads(json_out)

    assert parsed["type"] == "album"
    assert parsed["name"] == "Thriller"
    assert parsed["num_tracks"] == 1
    assert parsed["tracks"][0]["name"] == "Billie Jean"
    assert parsed["tracks"][0]["isrc"] == "USSM18200001"
    assert parsed["tracks"][0]["artist"]["name"] == "Michael Jackson"


def test_export_album_m3u_and_csv(mock_session, mock_provider):
    alb = MagicMock(id=123)
    alb.name = "Thriller"
    alb.artist.name = "Michael Jackson"
    t1 = MagicMock(id=555)
    t1.name = "Billie Jean"
    t1.track_num = 6
    t1.duration = 294
    t1.isrc = "USSM18200001"
    t1.artist.name = "Michael Jackson"

    alb.tracks.return_value = [t1]
    mock_session.album.return_value = alb

    svc = AlbumService(mock_provider)

    # M3U
    m3u_out = svc.export_album("123", export_format="m3u8")
    assert "#EXTM3U" in m3u_out
    assert "Billie Jean" in m3u_out

    # CSV
    csv_out = svc.export_album("123", export_format="csv")
    assert "USSM18200001" in csv_out
    assert "Billie Jean" in csv_out
