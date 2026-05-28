"""
Lightweight Lichess Public API client.

Docs: https://lichess.org/api
No auth required. Rate limit generous. Single endpoint /rating-history
returns full timeline per perf type — no archive walking needed.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json

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
PARALLEL_WORKERS = 6
GAME_CACHE_VERSION = 1

# Reused HTTP session for connection pooling
_session = requests.Session()
_session.headers.update(HEADERS)


# -----------------------------------------------------------------------
# Low-level fetch
# -----------------------------------------------------------------------

def _get(url):
    try:
        resp = _session.get(url, timeout=TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError):
        return None


def fetch_user(username):
    return _get(f"{BASE_URL}/user/{username}")


# -----------------------------------------------------------------------
# Player search (autocomplete)
# -----------------------------------------------------------------------

SEARCH_TTL = 60 * 30          # 30 min
# Prestige order so titled players returned by autocomplete float to the top.
TITLE_RANK = {"GM": 0, "WGM": 1, "IM": 2, "WIM": 3, "FM": 4, "WFM": 5,
              "CM": 6, "WCM": 7, "NM": 8, "WNM": 9, "LM": 10}


def search_player(query):
    """Fuzzy player search via Lichess' autocomplete endpoint.

    Lichess autocomplete is prefix-based and returns a fixed ~12 results
    ordered by username length. We re-rank so titled players (GM/IM/FM/...)
    surface first — the closest analogue to the Chess.com title priority.
    Lichess has no profile photos, so the UI renders a letter avatar.
    """
    query = (query or "").strip()
    if len(query) < 3:        # Lichess autocomplete requires >= 3 chars
        return []

    cache_key = f"lichess:search:v2:{query.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    data = {}
    try:
        resp = _session.get(
            f"{BASE_URL}/player/autocomplete",
            params={"term": query, "object": "true"},
            timeout=TIMEOUT,
        )
        if resp.ok:
            data = resp.json()
    except (requests.RequestException, ValueError):
        data = {}

    cards = []
    for item in (data.get("result") or []):
        name = item.get("name") or item.get("id")
        if not name:
            continue
        cards.append({
            "username": item.get("id") or name.lower(),
            "name": name,
            "title": item.get("title") or "",
            "flair": item.get("flair") or "",
            "patron": bool(item.get("patron")),
        })

    # Stable sort: titled players to the top by prestige, otherwise keep
    # Lichess' own relevance/length ordering.
    cards.sort(key=lambda c: TITLE_RANK.get(c["title"], 50))
    results = cards[:8]

    cache.set(cache_key, results, SEARCH_TTL)
    return results


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


def _parse_dated_history_for(history, perf_name):
    """Returns [(timestamp_ms, rating), ...] for the named perf."""
    target = perf_name.lower()
    for series in history or []:
        if (series.get("name") or "").lower() != target:
            continue
        out = []
        for pt in series.get("points", []):
            if len(pt) < 4:
                continue
            y, m_0idx, d, rating = pt[0], pt[1], pt[2], pt[3]
            try:
                ts = datetime(y, m_0idx + 1, d, tzinfo=timezone.utc).timestamp() * 1000
            except (ValueError, OverflowError):
                continue
            out.append([int(ts), rating])
        return out
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

    # Fetch user + rating history + 4 per-perf stats in parallel.
    # 6 HTTP calls run concurrently → total time ≈ slowest single call.
    perf_keys = ("bullet", "blitz", "rapid", "classical")
    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
        f_user    = executor.submit(fetch_user, username)
        f_history = executor.submit(fetch_rating_history, username)
        f_perfs   = {pk: executor.submit(fetch_perf_stats, username, pk) for pk in perf_keys}

        user = f_user.result()
        if user is None:
            return None
        history = f_history.result() or []
        perf_stats_map = {pk: f_perfs[pk].result() for pk in perf_keys}

    perfs = user.get("perfs") or {}

    def build(perf_key):
        block = _perf_block(perfs, perf_key)
        if not block:
            return None
        ratings_all = _parse_history_for(history, perf_key)
        dated_all   = _parse_dated_history_for(history, perf_key)
        ratings_small = ratings_all[-SPARKLINE_POINTS:]
        pct, direction = _calc_trend(ratings_small)
        block["sparkline_points"] = _sparkline_points(ratings_small)
        block["sparkline_area"]   = _sparkline_area_path(ratings_small)
        block["trend_pct"]        = pct
        block["trend_direction"]  = direction
        block["games_count"]      = len(ratings_all)
        block["raw_ratings"]      = ratings_all
        block["dated_history"]    = dated_all
        block["min_rating"]       = min(ratings_all) if ratings_all else None
        block["max_rating"]       = max(ratings_all) if ratings_all else None
        block["best"] = max(ratings_all) if ratings_all else block.get("best")
        # W/L/D from the pre-fetched perf stats (no new HTTP call)
        stats = perf_stats_map.get(perf_key)
        if stats:
            count = (stats.get("stat") or {}).get("count") or {}
            block["wins"]   = count.get("win", 0)
            block["losses"] = count.get("loss", 0)
            block["draws"]  = count.get("draw", 0)
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

def fetch_recent_games_lichess(username, max_games=500):
    """Returns list of game dicts via NDJSON streaming."""
    url = f"{BASE_URL}/games/user/{username}"
    params = {"max": max_games}
    try:
        resp = _session.get(url, params=params, headers={"Accept": "application/x-ndjson"}, timeout=30, stream=True)
        if not resp.ok:
            return []
        games = []
        for line in resp.iter_lines():
            if line:
                try:
                    games.append(json.loads(line))
                except ValueError:
                    continue
        return games
    except requests.RequestException:
        return []


def _normalize_perf(perf):
    value = (perf or "").replace("_", "").replace("-", "").lower()
    if value in {"bullet", "ultrabullet"}:
        return "bullet"
    if value == "blitz":
        return "blitz"
    if value == "rapid":
        return "rapid"
    if value == "classical":
        return "classical"
    return value or None


def _player_side(game, username):
    target = (username or "").lower()
    players = game.get("players") or {}
    for side in ("white", "black"):
        user = (players.get(side) or {}).get("user") or {}
        names = (user.get("name"), user.get("id"))
        if any((name or "").lower() == target for name in names):
            return side
    return None


def _normalize_game(game, username):
    side = _player_side(game, username)
    if not side:
        return None

    winner = game.get("winner")
    if not winner:
        result = "draw"
    elif winner == side:
        result = "win"
    else:
        result = "loss"

    players = game.get("players") or {}
    mine = players.get(side) or {}
    opponent = players.get("black" if side == "white" else "white") or {}
    ts = game.get("createdAt") or game.get("lastMoveAt")
    if not ts:
        return None

    return {
        "ts": ts,
        "result": result,
        "time_class": _normalize_perf(game.get("perf") or game.get("speed")),
        "my_rating": mine.get("rating"),
        "opp_rating": opponent.get("rating"),
    }


def get_all_games(username, months_back=12, max_games=2000):
    """Returns normalized games shaped like the Chess.com tilt input."""
    if not username:
        return []

    cache_key = f"lichess:games:v{GAME_CACHE_VERSION}:{username.lower()}:{months_back}:{max_games}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    url = f"{BASE_URL}/games/user/{username}"
    params = {
        "max": max_games,
        "sort": "dateAsc",
        "moves": "false",
        "clocks": "false",
        "evals": "false",
        "opening": "false",
    }
    if months_back:
        since = datetime.now(timezone.utc) - timedelta(days=31 * months_back)
        params["since"] = int(since.timestamp() * 1000)

    try:
        resp = _session.get(
            url,
            params=params,
            headers={"Accept": "application/x-ndjson"},
            timeout=30,
            stream=True,
        )
        if not resp.ok:
            return []

        games = []
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                raw = json.loads(line)
            except ValueError:
                continue
            game = _normalize_game(raw, username)
            if game:
                games.append(game)
    except requests.RequestException:
        return []

    games.sort(key=lambda game: game.get("ts") or 0)
    if games:
        cache.set(cache_key, games, CACHE_TTL)
    return games
