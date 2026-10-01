"""Tests for the vestaboard_commitgraph plugin."""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import pytz
import requests

from src.board_chars import BoardChars
from src.devices import BoardContext
from src.plugins.manifest import validate_manifest, validate_preview_completeness
from src.plugins.sources import plugin_id_from_repo_name

from plugins.vestaboard_commitgraph import (
    CommitGraphPlugin,
    Plugin,
    build_grid,
    grid_position,
)

MODULE = "plugins.vestaboard_commitgraph"
TODAY = date(2026, 10, 1)
FLAGSHIP = BoardContext.from_device_type("flagship")
NOTE = BoardContext.from_device_type("note")


class FixedDatetime(datetime):
    """datetime whose now() is pinned to noon on TODAY."""

    @classmethod
    def now(cls, tz=None):
        value = datetime(TODAY.year, TODAY.month, TODAY.day, 12, 0, 0)
        return tz.localize(value) if tz is not None else value


def _response(status=200, payload=None):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    return response


def _graphql_payload(counts):
    days = [{"date": d.isoformat(), "contributionCount": c} for d, c in counts.items()]
    return {
        "data": {
            "user": {
                "contributionsCollection": {
                    "contributionCalendar": {"weeks": [{"contributionDays": days}]}
                }
            }
        }
    }


def _plugin(manifest, config):
    plugin = CommitGraphPlugin(manifest)
    plugin.config = config
    return plugin


@pytest.fixture(autouse=True)
def fixed_now():
    with patch(f"{MODULE}.datetime", FixedDatetime), patch(
        f"{MODULE}.Config"
    ) as config:
        config.GENERAL_TIMEZONE = "UTC"
        yield


class TestManifest:
    def test_id_matches_repository_name(self, manifest):
        assert manifest["id"] == plugin_id_from_repo_name("vestaboard-commitgraph")
        assert manifest["id"] == "vestaboard_commitgraph"

    def test_manifest_is_valid(self, manifest):
        valid, errors = validate_manifest(manifest)
        assert valid, errors
        assert validate_preview_completeness(manifest) == []

    def test_plugin_id_matches_manifest(self, manifest):
        assert Plugin is CommitGraphPlugin
        assert CommitGraphPlugin(manifest).plugin_id == manifest["id"]


class TestLayout:
    def test_today_is_bottom_right(self):
        assert grid_position(0, 6, 22) == (5, 21)

    def test_column_fills_bottom_to_top(self):
        assert [grid_position(n, 6, 22) for n in range(6)] == [
            (5, 21), (4, 21), (3, 21), (2, 21), (1, 21), (0, 21)
        ]
        assert grid_position(6, 6, 22) == (5, 20)

    @pytest.mark.parametrize("rows,cols", [(6, 22), (3, 15)])
    def test_oldest_day_is_top_left(self, rows, cols):
        assert grid_position(rows * cols - 1, rows, cols) == (0, 0)

    def test_build_grid_marks_active_days(self):
        counts = {TODAY: 3, TODAY - timedelta(days=44): 1, TODAY - timedelta(days=45): 9}
        grid = build_grid(TODAY, counts, 3, 15, BoardChars.GREEN, BoardChars.SPACE)
        assert len(grid) == 3 and all(len(row) == 15 for row in grid)
        assert grid[2][14] == BoardChars.GREEN
        assert grid[0][0] == BoardChars.GREEN
        # Day 45 is outside a Note's 45-day window.
        flat = [code for row in grid for code in row]
        assert flat.count(BoardChars.GREEN) == 2


class TestValidateConfig:
    def test_valid(self, manifest, sample_config):
        assert CommitGraphPlugin(manifest).validate_config(sample_config) == []

    def test_missing_username(self, manifest):
        errors = CommitGraphPlugin(manifest).validate_config({})
        assert any("username" in e.lower() for e in errors)

    @pytest.mark.parametrize("name", ["-bad", "bad-", "a b", "../x", "a" * 40, "a--b"])
    def test_invalid_username(self, manifest, name):
        assert CommitGraphPlugin(manifest).validate_config({"username": name})

    def test_invalid_color(self, manifest, sample_config):
        sample_config["active_color"] = "pink"
        assert CommitGraphPlugin(manifest).validate_config(sample_config)


class TestFetchGraphQL:
    def test_renders_flagship(self, manifest, sample_config):
        sample_config["github_token"] = "test-token"
        counts = {TODAY: 2, TODAY - timedelta(days=1): 1, TODAY - timedelta(days=131): 4}
        with patch(f"{MODULE}.requests.post", return_value=_response(200, _graphql_payload(counts))) as post:
            result = _plugin(manifest, sample_config).get_data(FLAGSHIP)

        assert result.available, result.error
        lines = result.formatted_lines
        assert len(lines) == 6
        assert lines[5].endswith("{green}")
        assert lines[4].endswith("{green}")
        assert lines[0].startswith("{green}")
        assert lines[3].endswith(" ")
        assert result.data["commit_graph"] == "\n".join(lines)
        assert result.data["days"] == 132
        assert result.data["active_days"] == 3
        assert result.data["contributions"] == 7
        assert result.data["streak"] == 2

        _, kwargs = post.call_args
        assert kwargs["headers"]["Authorization"] == " ".join(("Bearer", "test-token"))
        variables = kwargs["json"]["variables"]
        assert variables["login"] == "octocat"
        assert variables["from"].startswith((TODAY - timedelta(days=131)).isoformat())
        assert variables["to"].startswith(TODAY.isoformat())

    def test_renders_note_with_custom_colors(self, manifest, sample_config):
        sample_config.update(github_token="t", active_color="blue", empty_color="black")
        with patch(f"{MODULE}.requests.post", return_value=_response(200, _graphql_payload({TODAY: 1}))):
            result = _plugin(manifest, sample_config).get_data(NOTE)

        assert result.available
        assert result.data["days"] == 45
        assert result.formatted_lines == [
            "{black}" * 15,
            "{black}" * 15,
            "{black}" * 14 + "{blue}",
        ]

    def test_large_board_is_chunked_per_year(self, manifest, sample_config):
        sample_config["github_token"] = "t"
        board = BoardContext("note_array", rows=24, cols=60)  # 1440 days
        with patch(f"{MODULE}.requests.post", return_value=_response(200, _graphql_payload({}))) as post:
            result = _plugin(manifest, sample_config).get_data(board)
        assert result.available
        assert post.call_count == 4

    def test_unknown_user(self, manifest, sample_config):
        sample_config["github_token"] = "t"
        with patch(f"{MODULE}.requests.post", return_value=_response(200, {"data": {"user": None}})):
            result = _plugin(manifest, sample_config).get_data(FLAGSHIP)
        assert not result.available
        assert "not found" in result.error

    def test_bad_token(self, manifest, sample_config):
        sample_config["github_token"] = "t"
        with patch(f"{MODULE}.requests.post", return_value=_response(401, {})):
            result = _plugin(manifest, sample_config).get_data(FLAGSHIP)
        assert not result.available
        assert "token" in result.error.lower()

    def test_network_error(self, manifest, sample_config):
        sample_config["github_token"] = "t"
        with patch(f"{MODULE}.requests.post", side_effect=requests.ConnectionError("boom")):
            result = _plugin(manifest, sample_config).get_data(FLAGSHIP)
        assert not result.available


class TestFetchRest:
    def test_uses_public_events_without_token(self, manifest, sample_config):
        events = [
            {"type": "PushEvent", "created_at": "2026-10-01T08:00:00Z", "payload": {"distinct_size": 3}},
            {"type": "WatchEvent", "created_at": "2026-09-30T08:00:00Z", "payload": {}},
            {"type": "IssuesEvent", "created_at": "2026-09-29T08:00:00Z", "payload": {"action": "closed"}},
            {"type": "PullRequestEvent", "created_at": "2026-09-28T08:00:00Z", "payload": {"action": "opened"}},
        ]
        with patch(f"{MODULE}.requests.get", return_value=_response(200, events)) as get:
            result = _plugin(manifest, sample_config).get_data(NOTE)

        assert result.available, result.error
        assert get.call_count == 1
        args, kwargs = get.call_args
        assert args[0] == "https://api.github.com/users/octocat/events/public"
        assert "Authorization" not in kwargs["headers"]
        assert result.data["active_days"] == 2
        assert result.data["contributions"] == 4
        assert result.data["streak"] == 1
        # Today (bottom-right) is active, the two days above it are not, and
        # three days ago starts the next column to the left, at the bottom.
        top, middle, bottom = result.formatted_lines
        assert bottom == " " * 13 + "{green}{green}"
        assert top == middle == " " * 15

    def test_events_in_configured_timezone(self, manifest, sample_config):
        events = [{"type": "PushEvent", "created_at": "2026-10-01T02:00:00Z", "payload": {"size": 1}}]
        with patch(f"{MODULE}.Config") as config, patch(
            f"{MODULE}.requests.get", return_value=_response(200, events)
        ):
            config.GENERAL_TIMEZONE = "America/Los_Angeles"
            result = _plugin(manifest, sample_config).get_data(NOTE)
        # 02:00 UTC on Oct 1st is still Sep 30th in Los Angeles: yesterday.
        assert result.data["streak"] == 0
        assert result.formatted_lines[1].endswith("{green}")

    def test_user_not_found(self, manifest, sample_config):
        with patch(f"{MODULE}.requests.get", return_value=_response(404, {})):
            result = _plugin(manifest, sample_config).get_data(NOTE)
        assert not result.available
        assert "not found" in result.error

    def test_rate_limited(self, manifest, sample_config):
        response = _response(403, {})
        response.headers = {"X-RateLimit-Remaining": "0"}
        with patch(f"{MODULE}.requests.get", return_value=response):
            result = _plugin(manifest, sample_config).get_data(NOTE)
        assert not result.available
        assert "rate limit" in result.error.lower()

    def test_forbidden_is_not_reported_as_rate_limit(self, manifest, sample_config):
        response = _response(403, {})
        response.headers = {}
        with patch(f"{MODULE}.requests.get", return_value=response):
            result = _plugin(manifest, sample_config).get_data(NOTE)
        assert not result.available
        assert "403" in result.error


class TestMisc:
    def test_missing_username_not_available(self, manifest):
        result = _plugin(manifest, {}).fetch_data()
        assert not result.available

    def test_invalid_username_never_requested(self, manifest):
        with patch(f"{MODULE}.requests.get") as get:
            result = _plugin(manifest, {"username": "../admin"}).fetch_data()
        assert not result.available
        get.assert_not_called()

    def test_defaults_to_flagship_without_board(self, manifest, sample_config):
        with patch(f"{MODULE}.requests.get", return_value=_response(200, [])):
            result = _plugin(manifest, sample_config).fetch_data()
        assert result.available
        assert result.data["days"] == 132
        assert result.formatted_lines == [" " * 22] * 6

    def test_formatted_display(self, manifest, sample_config):
        with patch(f"{MODULE}.requests.get", return_value=_response(200, [])):
            lines = _plugin(manifest, sample_config).get_formatted_display()
        assert lines == [" " * 22] * 6

    def test_timezone_fallback(self):
        with patch(f"{MODULE}.Config") as config:
            config.GENERAL_TIMEZONE = "Not/AZone"
            assert CommitGraphPlugin._timezone() is pytz.UTC
