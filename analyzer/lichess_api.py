"""
Lightweight Lichess Public API client.

Docs: https://lichess.org/api
No auth required. Rate limit generous. Single endpoint /rating-history
returns full timeline per perf type — no archive walking needed.
"""
from datetime import datetime, timezone

import requests
from django.core.cache import cache

BASE_URL = "https://lichess.org/api"
HEADERS = {
    "User-Agent": "ChessAnalyst/0.1 (student project; github.com/Ardakorkmaz0/ChessAnalyst)",
    "Accept": "application/json",
}
TIMEOUT = 10
CACHE_TTL = 300         # 5 min
SPARKLINE_POINTS = 30


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


def fetch_user(username):
    return _get(f"{BASE_URL}/user/{username}")


def fetch_rating_history(username):
    """Returns list of {name, points: [[y, m_0idx, d, rating], ...]}."""
    return _get(f"{BASE_URL}/user/{username}/rating-history")


def fetch_perf_stats(username, perf_type):
    """Per-perf detailed stats, includes W/L/D under stat.count."""
    return _get(f"{BASE_URL}/user/{username}/perf/{perf_type}")


# -----------------------------------------------------------------------
# Shape helpers
# -----------------------------------------------------------------------

PERF_KEYS = (
    ("bullet",    "Bullet"),
    ("blitz",     "Blitz"),
    ("rapid",     "Rapid"),
    ("classical", "Classical"),
)


def _perf_block(perfs, key):
    section = perfs.get(key)
    if not section:
        return None
    games = section.get("games", 0)
    rating = section.get("rating")
    if not rating or games == 0:
        return None
    return {
        "current": rating,
        "best": None,                    # filled later from history high
        "games_total": games,
        "rd": section.get("rd"),
        "prog": section.get("prog"),
        "wins": 0,
        "losses": 0,
        "draws": 0,
    }


def _fill_wld(block, username, perf_key):
    """Hit /perf/{perf_key} and write W/L/D into block."""
    stats = fetch_perf_stats(username, perf_key)
    if not stats:
        return
    count = (stats.get("stat") or {}).get("count") or {}
    block["wins"]   = count.get("win", 0)
    block["losses"] = count.get("loss", 0)
    block["draws"]  = count.get("draw", 0)


def _parse_history_for(history, perf_name):
    """Returns chronological list of ratings for the named perf."""
    target = perf_name.lower()
    for series in history or []:
        if (series.get("name") or "").lower() == target:
            return [pt[3] for pt in series.get("points", []) if len(pt) >= 4]
    return []


def _humanize_seen_at(ms_ts):
    if not ms_ts:
        return None
    last = datetime.fromtimestamp(ms_ts / 1000, tz=timezone.utc)
    delta = datetime.now(tz=timezone.utc) - last
    if delta.days >= 1:
        return f"{delta.days} day{'s' if delta.days > 1 else ''} ago"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    mins = max(1, delta.seconds // 60)
    return f"{mins} minute{'s' if mins > 1 else ''} ago"


def _format_created(ms_ts):
    if not ms_ts:
        return None
    return datetime.fromtimestamp(ms_ts / 1000, tz=timezone.utc).strftime("%b %d, %Y")


def _sparkline_points(ratings, width=100, height=30):
    if not ratings or len(ratings) < 2:
        return None
    min_r, max_r = min(ratings), max(ratings)
    span = max_r - min_r or 1
    n = len(ratings) - 1
    pts = [
        f"{(i / n) * width:.1f},{height - ((r - min_r) / span) * height:.1f}"
        for i, r in enumerate(ratings)
    ]
    return " ".join(pts)


def _sparkline_area_path(ratings, width=100, height=30):
    pts = _sparkline_points(ratings, width, height)
    if not pts:
        return None
    return f"M{pts.replace(' ', ' L')} L{width},{height} L0,{height} Z"


def _calc_trend(ratings):
    if not ratings or len(ratings) < 2:
        return None, None
    first, last = ratings[0], ratings[-1]
    if first == 0:
        return None, None
    pct = (last - first) / first * 100
    return abs(round(pct, 1)), "up" if pct >= 0 else "down"


# -----------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------

def get_player_data(username):
    """Returns dict the lichess.html template consumes, or None on 404."""
    if not username:
        return None

    cache_key = f"lichess:player:{username.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    user = fetch_user(username)
    if user is None:
        return None

    history = fetch_rating_history(username) or []
    perfs = user.get("perfs") or {}

    def build(perf_key):
        block = _perf_block(perfs, perf_key)
        if not block:
            return None
        ratings_all = _parse_history_for(history, perf_key)
        ratings_small = ratings_all[-SPARKLINE_POINTS:]
        pct, direction = _calc_trend(ratings_small)
        block["sparkline_points"] = _sparkline_points(ratings_small)
        block["sparkline_area"]   = _sparkline_area_path(ratings_small)
        block["trend_pct"]        = pct
        block["trend_direction"]  = direction
        block["games_count"]      = len(ratings_all)
        block["raw_ratings"]      = ratings_all
        block["min_rating"]       = min(ratings_all) if ratings_all else None
        block["max_rating"]       = max(ratings_all) if ratings_all else None
        # Lichess: no per-perf best in /user, derive from history high
        block["best"] = max(ratings_all) if ratings_all else block.get("best")
        # W/L/D requires an extra call per perf — fill in-place
        _fill_wld(block, username, perf_key)
        return block

    profile = user.get("profile") or {}
    country_code = (profile.get("country") or "").upper() or None
    flag_url = f"https://flagcdn.com/{country_code.lower()}.svg" if country_code else None

    aggregate_count = user.get("count") or {}
    total_games = aggregate_count.get("all", 0)

    data = {
        "username":      user.get("username") or username,
        "display_name":  user.get("username") or username,
        "title":         user.get("title"),
        "patron":        bool(user.get("patron")),
        "verified":      bool(user.get("verified")),
        "tos_violation": bool(user.get("tosViolation")),
        "country_code":  country_code,
        "country_name":  None,        # lichess returns the code only
        "flag_url":      flag_url,
        "joined":        _format_created(user.get("createdAt")),
        "last_online":   _humanize_seen_at(user.get("seenAt")),
        "total_games":   total_games,
        "bullet":        build("bullet"),
        "blitz":         build("blitz"),
        "rapid":         build("rapid"),
        "classical":     build("classical"),
        "profile_url":   user.get("url") or f"https://lichess.org/@/{username}",
    }

    cache.set(cache_key, data, CACHE_TTL)
    return data
