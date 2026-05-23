"""
Lightweight Chess.com Public API client.

Docs: https://www.chess.com/news/view/published-data-api
No auth required. Chess.com requires a real User-Agent header.
"""
from datetime import datetime, timezone

import requests
from django.core.cache import cache

BASE_URL = "https://api.chess.com/pub/player"
HEADERS = {
    "User-Agent": "ChessAnalyst/0.1 (student project; github.com/Ardakorkmaz0/ChessAnalyst)",
    "Accept": "application/json",
}
TIMEOUT = 10           # seconds
CACHE_TTL = 300        # 5 min for the aggregated player blob
ARCHIVE_TTL = 60 * 60  # 1 hour per monthly archive
HISTORY_MONTHS = 12    # how far back we pull rating history
SPARKLINE_POINTS = 30  # small card sparkline — last N games


# -----------------------------------------------------------------------
# Low-level fetch
# -----------------------------------------------------------------------

def _get(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError):
        return None


def fetch_profile(username):
    return _get(f"{BASE_URL}/{username.lower()}")


def fetch_stats(username):
    return _get(f"{BASE_URL}/{username.lower()}/stats")


def fetch_country_name(code):
    """Country name for a 2-letter code, cached 24h."""
    if not code:
        return None
    cache_key = f"chesscom:country:{code.upper()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None
    data = _get(f"https://api.chess.com/pub/country/{code.upper()}")
    name = (data or {}).get("name") or ""
    cache.set(cache_key, name, 60 * 60 * 24)
    return name or None


def _country_code_from_url(country_url):
    """Extract 2-letter ISO code from 'https://api.chess.com/pub/country/US'."""
    if not country_url:
        return None
    code = country_url.rstrip("/").rsplit("/", 1)[-1]
    if len(code) == 2 and code.isalpha():
        return code.upper()
    return None


def fetch_archives(username):
    """List of monthly archive URLs (oldest → newest)."""
    data = _get(f"{BASE_URL}/{username.lower()}/games/archives")
    if not data:
        return []
    return data.get("archives", [])


def fetch_month_games(archive_url):
    """Returns the games list from a monthly archive URL."""
    data = _get(archive_url)
    if not data:
        return []
    return data.get("games", [])


# -----------------------------------------------------------------------
# Shape helpers
# -----------------------------------------------------------------------

def _rating_block(stats, key):
    section = stats.get(key)
    if not section:
        return None
    last = section.get("last") or {}
    best = section.get("best") or {}
    record = section.get("record") or {}
    return {
        "current": last.get("rating"),
        "best": best.get("rating"),
        "wins": record.get("win", 0),
        "losses": record.get("loss", 0),
        "draws": record.get("draw", 0),
    }


def _humanize_last_online(ts):
    if not ts:
        return None
    last = datetime.fromtimestamp(ts, tz=timezone.utc)
    delta = datetime.now(tz=timezone.utc) - last
    if delta.days >= 1:
        return f"{delta.days} day{'s' if delta.days > 1 else ''} ago"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    mins = max(1, delta.seconds // 60)
    return f"{mins} minute{'s' if mins > 1 else ''} ago"


def _format_joined(ts):
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d, %Y")


# -----------------------------------------------------------------------
# Rating history from games archive
# -----------------------------------------------------------------------

TIME_CLASSES = ("bullet", "blitz", "rapid", "daily")


def _ratings_from_archive(username, archive_url):
    """
    Returns {time_class: [(end_time_ms, rating), ...]} from one monthly archive.
    Each entry is a (timestamp_ms, rating) pair so the combined `Both` chart
    can align two platforms on a real time axis.
    """
    cache_key = f"chesscom:archive_dated:{username.lower()}:{archive_url}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = {tc: [] for tc in TIME_CLASSES}
    username_lc = username.lower()
    for game in fetch_month_games(archive_url):
        tc = game.get("time_class")
        if tc not in result:
            continue
        white = game.get("white") or {}
        black = game.get("black") or {}
        if (white.get("username") or "").lower() == username_lc:
            rating = white.get("rating")
        elif (black.get("username") or "").lower() == username_lc:
            rating = black.get("rating")
        else:
            rating = None
        end_time = game.get("end_time")
        if rating and end_time:
            result[tc].append([end_time * 1000, rating])   # list (JSON-serializable)

    cache.set(cache_key, result, ARCHIVE_TTL)
    return result


def _extract_rating_history(username, months_back=HISTORY_MONTHS):
    """
    Returns two parallel views over the last `months_back` months:
      - `ratings`: {time_class: [rating, rating, ...]}            (chronological)
      - `dated`:   {time_class: [(ts_ms, rating), ...]}            (chronological)
    """
    archives = fetch_archives(username)
    if not archives:
        empty = {tc: [] for tc in TIME_CLASSES}
        return {"ratings": empty, "dated": dict(empty)}

    recent = archives[-months_back:]
    ratings = {tc: [] for tc in TIME_CLASSES}
    dated   = {tc: [] for tc in TIME_CLASSES}

    for archive_url in recent:
        archive_data = _ratings_from_archive(username, archive_url)
        for tc in TIME_CLASSES:
            for ts, r in archive_data[tc]:
                ratings[tc].append(r)
                dated[tc].append([ts, r])

    return {"ratings": ratings, "dated": dated}


def _sparkline_points(ratings, width=100, height=30):
    """Convert a list of ratings into an SVG polyline 'points' attribute."""
    if not ratings or len(ratings) < 2:
        return None
    min_r, max_r = min(ratings), max(ratings)
    span = max_r - min_r or 1
    n = len(ratings) - 1
    pts = []
    for i, r in enumerate(ratings):
        x = (i / n) * width
        y = height - ((r - min_r) / span) * height
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def _sparkline_area_path(ratings, width=100, height=30):
    """Same as polyline but closed area (for gradient fill underneath)."""
    pts = _sparkline_points(ratings, width, height)
    if not pts:
        return None
    return f"M{pts.replace(' ', ' L')} L{width},{height} L0,{height} Z"


def _calc_trend(ratings):
    """Returns (percent_change, direction) or (None, None)."""
    if not ratings or len(ratings) < 2:
        return None, None
    first, last = ratings[0], ratings[-1]
    if first == 0:
        return None, None
    pct = (last - first) / first * 100
    direction = "up" if pct >= 0 else "down"
    return abs(round(pct, 1)), direction


# -----------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------

def get_player_data(username):
    """Single dict the chesscom.html template consumes. Cached 5 min."""
    if not username:
        return None

    cache_key = f"chesscom:player:{username.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    profile = fetch_profile(username)
    if profile is None:
        return None

    stats = fetch_stats(username) or {}
    history_pair = _extract_rating_history(username)
    history_ratings = history_pair["ratings"]
    history_dated   = history_pair["dated"]

    def build(key, hist_key):
        block = _rating_block(stats, key)
        if not block:
            return None
        ratings_all = history_ratings.get(hist_key, [])
        dated_all   = history_dated.get(hist_key, [])
        ratings_small = ratings_all[-SPARKLINE_POINTS:]
        pct, direction = _calc_trend(ratings_small)
        block["sparkline_points"] = _sparkline_points(ratings_small)
        block["sparkline_area"]   = _sparkline_area_path(ratings_small)
        block["trend_pct"]        = pct
        block["trend_direction"]  = direction
        block["games_count"]      = len(ratings_all)
        block["raw_ratings"]      = ratings_all
        block["dated_history"]    = dated_all          # [(ts_ms, rating), ...]
        block["min_rating"]       = min(ratings_all) if ratings_all else None
        block["max_rating"]       = max(ratings_all) if ratings_all else None
        return block

    total_games = sum(
        (r["wins"] + r["losses"] + r["draws"]) if r else 0
        for r in [_rating_block(stats, k) for k in
                  ("chess_bullet", "chess_blitz", "chess_rapid", "chess_daily")]
    )

    country_code = _country_code_from_url(profile.get("country"))
    country_name = fetch_country_name(country_code) if country_code else None
    flag_url = f"https://flagcdn.com/{country_code.lower()}.svg" if country_code else None

    data = {
        "username":     profile.get("username") or username,
        "display_name": profile.get("name") or profile.get("username") or username,
        "avatar":       profile.get("avatar"),
        "status":       profile.get("status"),
        "title":        profile.get("title"),
        "country_code": country_code,
        "country_name": country_name,
        "flag_url":     flag_url,
        "joined":       _format_joined(profile.get("joined")),
        "last_online":  _humanize_last_online(profile.get("last_online")),
        "total_games":  total_games,
        "bullet":       build("chess_bullet", "bullet"),
        "blitz":        build("chess_blitz",  "blitz"),
        "rapid":        build("chess_rapid",  "rapid"),
        "daily":        build("chess_daily",  "daily"),
        "profile_url":  profile.get("url"),
    }

    cache.set(cache_key, data, CACHE_TTL)
    return data
