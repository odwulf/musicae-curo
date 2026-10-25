"""Unit tests for TidalProvider replacement relationships and catalog relinking."""

from unittest.mock import MagicMock, patch
import pytest

from curo.providers.tidal.adapter import TidalProvider


def test_tidal_get_track_replacement():
    session = MagicMock()
    session.access_token = "mock-token-xyz"
    session.token_type = "Bearer"

    mock_raw_track = MagicMock()
    mock_raw_track.id = 36142925
    mock_raw_track.name = "Je veux prendre ta main"
    mock_raw_track.artist.name = "Frank Alamo"
    mock_raw_track.artist.id = 3545740
    mock_raw_track.album.name = "Tendres Annees"
    mock_raw_track.album.id = 36142918
    mock_raw_track.duration = 136
    mock_raw_track.available = True
    session.track.return_value = mock_raw_track

    provider = TidalProvider(session=session)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": {
            "id": "36142925",
            "type": "tracks",
        },
        "links": {
            "self": "/tracks/26995301/relationships/replacement",
        },
    }

    with patch("requests.get", return_value=mock_response) as mock_get:
        rep = provider.get_track_replacement("26995301")
        assert rep is not None
        assert str(rep.id) == "36142925"
        assert rep.title == "Je veux prendre ta main"
        assert rep.artist.name == "Frank Alamo"
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        assert "https://openapi.tidal.com/v2/tracks/26995301/relationships/replacement" in args[0]
        assert kwargs["headers"]["Accept"] == "application/vnd.api+json"
        assert kwargs["headers"]["Authorization"] == "Bearer mock-token-xyz"


def test_tidal_get_track_replacement_not_found():
    session = MagicMock()
    session.access_token = "mock-token-xyz"
    provider = TidalProvider(session=session)

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.json.return_value = {"status": 404, "title": "Not Found"}

    with patch("requests.get", return_value=mock_response):
        rep = provider.get_track_replacement("114002987")
        assert rep is None


def test_tidal_get_playlist_track_replacements():
    session = MagicMock()
    session.access_token = "mock-token-xyz"
    provider = TidalProvider(session=session)

    # Page 1 response
    page1_resp = MagicMock()
    page1_resp.status_code = 200
    page1_resp.json.return_value = {
        "data": {
            "id": "pl-uuid",
            "type": "playlists",
            "relationships": {
                "items": {
                    "data": [
                        {
                            "id": "36142925",
                            "type": "tracks",
                            "meta": {
                                "replacement": {
                                    "status": "REPLACED",
                                    "original": {"id": "26995301", "type": "tracks"},
                                }
                            },
                        },
                        {
                            "id": "114002987",
                            "type": "tracks",
                            "meta": {"replacement": {"status": "NOT_REPLACED"}},
                        },
                    ],
                    "links": {"next": "/playlists/pl-uuid/relationships/items?page[cursor]=xyz"},
                }
            },
        }
    }

    # Page 2 response
    page2_resp = MagicMock()
    page2_resp.status_code = 200
    page2_resp.json.return_value = {
        "data": [
            {
                "id": "16573616",
                "type": "tracks",
                "meta": {
                    "replacement": {
                        "status": "REPLACED",
                        "original": {"id": "26995307", "type": "tracks"},
                    }
                },
            }
        ],
        "links": {"next": None},
    }

    with patch("requests.get", side_effect=[page1_resp, page2_resp]):
        replacements = provider.get_playlist_track_replacements("pl-uuid")
        assert replacements == {
            "26995301": "36142925",
            "26995307": "16573616",
        }
