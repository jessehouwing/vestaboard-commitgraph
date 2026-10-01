"""GitHub Commit Graph plugin for FiestaBoard.

Renders a GitHub user's contribution activity as a full-board grid of colour
tiles: one tile per day, sized to the board being rendered on (45 days on a
Note, 132 days on a Flagship, more on a note array).

Layout: the most recent day sits in the bottom-right corner. Walking back in
time, each column is filled from the bottom up before moving one column to the
left, so the oldest day ends up in the top-left corner. Boards do not have
seven rows, so days are not grouped into week columns.

Data comes from the GitHub GraphQL API (contribution calendar, requires a
token) or, when no token is configured, from the public REST events API.
"""

import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote

import pytz
import requests

from src.board_chars import BoardChars
from src.config import Config
from src.devices import BoardContext
from src.plugins.base import PluginBase, PluginResult

logger = logging.getLogger(__name__)

PLUGIN_ID = "vestaboard_commitgraph"

# The board assumed when no board is bound (unit tests, legacy callers).
FALLBACK_BOARD = BoardContext.from_device_type("flagship")

GITHUB_API_URL = "https://api.github.com"
GITHUB_GRAPHQL_URL = f"{GITHUB_API_URL}/graphql"
REQUEST_TIMEOUT_SECONDS = 15

# GitHub limits a contributionsCollection to a span of at most one year.
GRAPHQL_MAX_SPAN_DAYS = 365

# The public events API returns at most 300 events (3 pages of 100).
REST_EVENTS_PER_PAGE = 100
REST_EVENTS_MAX_PAGES = 3

# GitHub usernames: alphanumerics and single hyphens, max 39 characters.
USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")

COLOR_MARKERS: Dict[str, str] = {
    "blank": " ",
    "red": "{red}",
    "orange": "{orange}",
    "yellow": "{yellow}",
    "green": "{green}",
    "blue": "{blue}",
    "violet": "{violet}",
    "white": "{white}",
    "black": "{black}",
}

COLOR_CODES: Dict[str, int] = {
    "blank": BoardChars.SPACE,
    "red": BoardChars.RED,
    "orange": BoardChars.ORANGE,
    "yellow": BoardChars.YELLOW,
    "green": BoardChars.GREEN,
    "blue": BoardChars.BLUE,
    "violet": BoardChars.VIOLET,
    "white": BoardChars.WHITE,
    "black": BoardChars.BLACK,
}

DEFAULT_ACTIVE_COLOR = "green"
DEFAULT_EMPTY_COLOR = "blank"

# Public events that GitHub counts towards the contribution graph.
CONTRIBUTION_EVENT_TYPES = {
    "PushEvent",
    "PullRequestEvent",
    "PullRequestReviewEvent",
    "IssuesEvent",
    "CreateEvent",
}

CONTRIBUTIONS_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""


class GitHubError(Exception):
    """Raised when GitHub data cannot be retrieved."""


def grid_position(days_ago: int, rows: int, cols: int) -> Tuple[int, int]:
    """Return the ``(row, col)`` tile for the day *days_ago* days before today.

    Today is bottom-right; each column fills bottom to top going back in time,
    then continues at the bottom of the column to its left.
    """
    return rows - 1 - (days_ago % rows), cols - 1 - (days_ago // rows)


def build_grid(
    today: date,
    counts: Dict[date, int],
    rows: int,
    cols: int,
    active_code: int,
    empty_code: int,
) -> List[List[int]]:
    """Build a ``rows`` x ``cols`` grid of character codes, one tile per day."""
    grid = [[empty_code] * cols for _ in range(rows)]
    for days_ago in range(rows * cols):
        day = today - timedelta(days=days_ago)
        if counts.get(day, 0) > 0:
            row, col = grid_position(days_ago, rows, cols)
            grid[row][col] = active_code
    return grid


class CommitGraphPlugin(PluginBase):
    """Render a GitHub user's contribution graph across the whole board."""

    @property
    def plugin_id(self) -> str:
        return PLUGIN_ID

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        errors = []

        username = (config.get("username") or "").strip()
        if not username:
            errors.append("GitHub username is required")
        elif not USERNAME_RE.match(username):
            errors.append("GitHub username is not valid")

        for key in ("active_color", "empty_color"):
            value = config.get(key)
            if value is not None and value not in COLOR_MARKERS:
                errors.append(f"{key} must be one of: {', '.join(COLOR_MARKERS)}")

        errors.extend(self._validate_refresh_seconds(config))
        return errors

    def board_size(self) -> Tuple[int, int]:
        """The bound board's ``(rows, cols)``, defaulting to a Flagship."""
        board = self.board
        if board is None:
            return FALLBACK_BOARD.rows, FALLBACK_BOARD.cols
        return board.rows, board.cols

    def fetch_data(self) -> PluginResult:
        username = (self.config.get("username") or "").strip()
        if not username:
            return PluginResult(available=False, error="GitHub username not configured")
        if not USERNAME_RE.match(username):
            return PluginResult(available=False, error="GitHub username is not valid")

        token = (self.config.get("github_token") or "").strip()
        active_color = self._color_setting("active_color", DEFAULT_ACTIVE_COLOR)
        empty_color = self._color_setting("empty_color", DEFAULT_EMPTY_COLOR)

        rows, cols = self.board_size()
        tz = self._timezone()
        today = datetime.now(tz).date()
        start = today - timedelta(days=rows * cols - 1)

        try:
            if token:
                counts = self._fetch_graphql(username, token, start, today, tz)
            else:
                counts = self._fetch_rest_events(username, start, today, tz)
        except GitHubError as e:
            return PluginResult(available=False, error=str(e))
        except requests.RequestException as e:
            logger.warning("GitHub request failed: %s", type(e).__name__)
            return PluginResult(available=False, error="Could not reach GitHub")

        grid = build_grid(
            today, counts, rows, cols, COLOR_CODES[active_color], COLOR_CODES[empty_color]
        )
        lines = self._grid_to_lines(grid)

        days = [today - timedelta(days=n) for n in range(rows * cols)]
        active_days = sum(1 for d in days if counts.get(d, 0) > 0)
        contributions = sum(counts.get(d, 0) for d in days)
        streak = 0
        for d in days:
            if counts.get(d, 0) <= 0:
                break
            streak += 1

        data = {
            "commit_graph": "\n".join(lines),
            "username": username,
            "days": rows * cols,
            "active_days": active_days,
            "contributions": contributions,
            "streak": streak,
        }
        return PluginResult(available=True, data=data, formatted_lines=lines)

    def get_formatted_display(self) -> Optional[List[str]]:
        result = self.get_data(self.board)
        if not result.available:
            return None
        return result.formatted_lines

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _color_setting(self, key: str, default: str) -> str:
        value = self.config.get(key) or default
        return value if value in COLOR_MARKERS else default

    @staticmethod
    def _timezone() -> pytz.BaseTzInfo:
        try:
            return pytz.timezone(Config.GENERAL_TIMEZONE)
        except Exception:
            return pytz.UTC

    @staticmethod
    def _grid_to_lines(grid: List[List[int]]) -> List[str]:
        code_to_marker = {COLOR_CODES[name]: marker for name, marker in COLOR_MARKERS.items()}
        return ["".join(code_to_marker[code] for code in row) for row in grid]

    @staticmethod
    def _headers(token: str = "") -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "fiestaboard-vestaboard-commitgraph",
        }
        if token:
            headers["Authorization"] = " ".join(("Bearer", token))
        return headers

    def _fetch_graphql(
        self, username: str, token: str, start: date, end: date, tz: pytz.BaseTzInfo
    ) -> Dict[date, int]:
        """Contribution counts per day from the GraphQL contribution calendar."""
        counts: Dict[date, int] = {}
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(end, chunk_start + timedelta(days=GRAPHQL_MAX_SPAN_DAYS - 1))
            variables = {
                "login": username,
                "from": tz.localize(datetime.combine(chunk_start, time.min)).isoformat(),
                "to": tz.localize(datetime.combine(chunk_end, time(23, 59, 59))).isoformat(),
            }
            response = requests.post(
                GITHUB_GRAPHQL_URL,
                json={"query": CONTRIBUTIONS_QUERY, "variables": variables},
                headers=self._headers(token),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 401:
                raise GitHubError("GitHub token is invalid or expired")
            if response.status_code != 200:
                raise GitHubError(f"GitHub API returned HTTP {response.status_code}")

            payload = response.json()
            if payload.get("errors"):
                message = payload["errors"][0].get("message", "unknown error")
                raise GitHubError(f"GitHub API error: {message}")
            user = (payload.get("data") or {}).get("user")
            if user is None:
                raise GitHubError(f"GitHub user '{username}' not found")

            weeks = user["contributionsCollection"]["contributionCalendar"]["weeks"]
            for week in weeks:
                for day in week.get("contributionDays", []):
                    counts[date.fromisoformat(day["date"])] = int(day.get("contributionCount", 0))

            chunk_start = chunk_end + timedelta(days=1)
        return counts

    def _fetch_rest_events(
        self, username: str, start: date, end: date, tz: pytz.BaseTzInfo
    ) -> Dict[date, int]:
        """Contribution counts per day from the public REST events API.

        GitHub only returns public events from the last 90 days (at most 300),
        so older days stay empty. Configure a token for the full history.
        """
        counts: Dict[date, int] = {}
        url = f"{GITHUB_API_URL}/users/{quote(username, safe='')}/events/public"
        for page in range(1, REST_EVENTS_MAX_PAGES + 1):
            response = requests.get(
                url,
                params={"per_page": REST_EVENTS_PER_PAGE, "page": page},
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 404:
                raise GitHubError(f"GitHub user '{username}' not found")
            if response.status_code == 429 or (
                response.status_code == 403
                and response.headers.get("X-RateLimit-Remaining") == "0"
            ):
                raise GitHubError("GitHub rate limit exceeded; configure a token")
            if response.status_code == 422:
                # Requested beyond the pagination window GitHub allows.
                break
            if response.status_code != 200:
                raise GitHubError(f"GitHub API returned HTTP {response.status_code}")

            events = response.json()
            if not events:
                break
            reached_start = False
            for event in events:
                created = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
                day = created.astimezone(tz).date()
                if day < start:
                    reached_start = True
                    continue
                if day > end or not self._is_contribution(event):
                    continue
                counts[day] = counts.get(day, 0) + self._event_weight(event)
            if reached_start or len(events) < REST_EVENTS_PER_PAGE:
                break
        return counts

    @staticmethod
    def _is_contribution(event: Dict[str, Any]) -> bool:
        event_type = event.get("type")
        if event_type not in CONTRIBUTION_EVENT_TYPES:
            return False
        payload = event.get("payload") or {}
        if event_type in ("PullRequestEvent", "IssuesEvent"):
            return payload.get("action") == "opened"
        if event_type == "CreateEvent":
            return payload.get("ref_type") == "repository"
        return True

    @staticmethod
    def _event_weight(event: Dict[str, Any]) -> int:
        if event.get("type") == "PushEvent":
            payload = event.get("payload") or {}
            return max(1, int(payload.get("distinct_size") or payload.get("size") or 1))
        return 1


# Export the plugin class
Plugin = CommitGraphPlugin
