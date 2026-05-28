import datetime as _datetime

from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from . import chesscom_api, lichess_api, tilt as tilt_module
from .forms import UserProfileForm
from .models import UserProfile


# ---------------------------------------------------------------------------
# Tilt-panel context builders
#
# Public entry points:
#   _build_chesscom_tilt_context(username, player_data=None, tz_offset=3)
#   _build_lichess_tilt_context(username, player_data=None, tz_offset=3)
#
# Internal pipeline:
#   _compute_slices(games, ..., tz_offset)
#       -> dict {period_key: {tc_key: slice_stats}}
#   _active_context(slices, default_period, default_tc, period_defs)
#       -> flat context vars for the template (tilt_baseline_pct, ...)
#   _slice_stats(rows, tz_offset)
#       -> stats for one specific (period × time class) slice
# ---------------------------------------------------------------------------

TIME_CLASSES = ("bullet", "blitz", "rapid", "classical", "daily")
PERIOD_DEFS = (
    ("30d", "30D", "last 30 days", 30),
    ("90d", "90D", "last 90 days", 90),
    ("6m",  "6M",  "last 6 months", 183),
    ("12m", "12M", "last 12 months", 365),
    ("all", "All", "all time", None),
)
DEFAULT_PERIOD = "12m"
DEFAULT_TC = "total"


def _baseline_winrate_pct(rows):
    if not rows:
        return None
    wins = sum(1 for r in rows if r["result"] == "win")
    return round((wins / len(rows)) * 100, 1)


def _delta_display(post_loss):
    """Returns (display_string, css_class) for the post-loss delta."""
    if not post_loss or post_loss.get("post_loss_winrate") is None:
        return "-", "is-neutral"

    delta_pp = round(
        (post_loss["post_loss_winrate"] - post_loss["baseline_winrate"]) * 100
    )
    if delta_pp > 2:
        return f"+{delta_pp}%", "is-positive"
    if delta_pp < -2:
        return f"{delta_pp}%", "is-negative"
    return f"{delta_pp}%", "is-neutral"


def _status_for(rows, post_loss):
    """Returns (status_text, status_class)."""
    if len(rows) < 10:
        return "Need data", "is-neutral"
    if post_loss and post_loss.get("is_tilting"):
        return "Tilt detected", "is-warning"
    return "Stable", "is-ok"


def _round_winrates(rows, key="winrate"):
    """Round winrate field to 3 decimals in-place — shrinks JSON payload."""
    for row in rows:
        v = row.get(key)
        if v is not None:
            row[key] = round(v, 3)
    return rows


def _slice_stats(rows, tz_offset):
    """All stats for one (period × time-class) slice."""
    baseline_pct = _baseline_winrate_pct(rows)
    post_loss = tilt_module.compute_post_loss_stats(rows)
    streaks = tilt_module.compute_streak_stats(rows) if rows else {
        "max_loss_streak": 0,
        "histogram": {},
    }
    hourly = tilt_module.compute_hourly_winrate(rows, tz_offset_hours=tz_offset)
    session_pos = tilt_module.compute_session_position_stats(rows)
    insights = tilt_module.humanize_insights(post_loss, hourly, streaks, session_pos)
    delta_text, delta_class = _delta_display(post_loss)
    status_text, status_class = _status_for(rows, post_loss)

    return {
        "total_games": len(rows),
        "baseline_pct": f"{baseline_pct:.0f}" if baseline_pct is not None else "-",
        "baseline_value": baseline_pct,
        "post_loss_pct": post_loss.get("post_loss_pct") if post_loss else "-",
        "delta_display": delta_text,
        "delta_class": delta_class,
        "max_loss_streak": streaks.get("max_loss_streak", 0),
        "status_text": status_text,
        "status_class": status_class,
        "hourly": _round_winrates(hourly),               # round → smaller JSON
        "session_pos": _round_winrates(session_pos[:30]),  # cap at 30 + round
        "streak_histogram": streaks.get("histogram", {}),
        "insights": insights[:3],
    }


def _compute_slices(games, tz_offset, latest_ts_ms):
    """Returns {period_key: {tc_key: slice_stats}} for all combinations."""
    day_ms = 24 * 60 * 60 * 1000
    slices = {}

    for period_key, _, _, days in PERIOD_DEFS:
        if days is None:
            period_games = games
        else:
            cutoff = latest_ts_ms - (days * day_ms)
            period_games = [g for g in games if (g.get("ts") or 0) >= cutoff]

        slices[period_key] = {"total": _slice_stats(period_games, tz_offset)}
        for tc in TIME_CLASSES:
            tc_games = [g for g in period_games if g.get("time_class") == tc]
            slices[period_key][tc] = _slice_stats(tc_games, tz_offset)

    return slices


def _tz_label(tz_offset):
    sign = "+" if tz_offset >= 0 else "-"
    return f"UTC{sign}{abs(tz_offset)}"


def _active_context(slices, tz_offset):
    """Pull the default slice and flatten its stats into template context vars."""
    active = slices[DEFAULT_PERIOD][DEFAULT_TC]
    time_class_counts = {
        "total": active["total_games"],
        **{tc: slices[DEFAULT_PERIOD][tc]["total_games"] for tc in TIME_CLASSES},
    }

    return {
        "tilt_state":            "ok",
        "tilt_total_games":      active["total_games"],
        "tilt_period_label":     "last 12 months",
        "tilt_status_text":      active["status_text"],
        "tilt_status_class":     active["status_class"],
        "tilt_insights":         active["insights"],
        "tilt_baseline_pct":     active["baseline_pct"],
        "tilt_post_loss_pct":    active["post_loss_pct"],
        "tilt_delta_display":    active["delta_display"],
        "tilt_delta_class":      active["delta_class"],
        "tilt_max_loss_streak":  active["max_loss_streak"],
        "tilt_time_class_counts": time_class_counts,
        "tilt_period_defs":      PERIOD_DEFS,
        "tilt_tz_label":         _tz_label(tz_offset),
        "tilt_payload": {
            "default_period": DEFAULT_PERIOD,
            "default_tc":     DEFAULT_TC,
            "periods": [
                {"key": k, "short_label": short, "label": label}
                for k, short, label, _ in PERIOD_DEFS
            ],
            "stats": slices,
        },
    }


def _build_tilt_context_from_games(games, player_data=None, tz_offset=3):
    """
    Main orchestrator. Returns template context vars for the tilt panel.

    States:
      - 'unavailable' — profile has games but archive didn't return them
      - 'empty'       — fewer than 10 games available
      - 'ok'          — full analysis context
    """
    games = sorted(games or [], key=lambda g: g.get("ts") or 0)
    game_count = len(games)

    if game_count < 10:
        known_total = (player_data or {}).get("total_games") or 0
        # Profile says player has games but we couldn't load them
        if known_total >= 10 and game_count == 0:
            return {"tilt_state": "unavailable", "tilt_game_count": game_count}
        return {"tilt_state": "empty", "tilt_game_count": game_count}

    latest_ts_ms = int(timezone.now().timestamp() * 1000)
    slices = _compute_slices(games, tz_offset, latest_ts_ms)
    return _active_context(slices, tz_offset)


def _user_tz_offset(user):
    """Read the user's timezone offset (hours from UTC). Defaults to 3 (Turkey)."""
    profile = getattr(user, "userprofile", None)
    if profile is None:
        try:
            profile, _ = UserProfile.objects.get_or_create(user=user)
        except Exception:
            return 3
    # NB: don't use `or 3` here — UTC (offset=0) is a valid value, not falsy default.
    value = getattr(profile, "timezone_offset", None)
    return 3 if value is None else value


def _build_chesscom_tilt_context(username, player_data=None, tz_offset=3):
    games = chesscom_api.get_all_games(username, months_back=None)
    return _build_tilt_context_from_games(games, player_data, tz_offset)


def _build_lichess_tilt_context(username, player_data=None, tz_offset=3):
    games = lichess_api.get_all_games(username, months_back=None)
    return _build_tilt_context_from_games(games, player_data, tz_offset)


def _slices_for_range(games, tz_offset, from_ts_ms, to_ts_ms):
    """Returns {tc_key: slice_stats} for games filtered by [from_ts_ms, to_ts_ms]."""
    if from_ts_ms is not None or to_ts_ms is not None:
        period_games = [
            g for g in games
            if (from_ts_ms is None or (g.get("ts") or 0) >= from_ts_ms)
            and (to_ts_ms is None or (g.get("ts") or 0) <= to_ts_ms)
        ]
    else:
        period_games = games

    result = {"total": _slice_stats(period_games, tz_offset)}
    for tc in TIME_CLASSES:
        tc_games = [g for g in period_games if g.get("time_class") == tc]
        result[tc] = _slice_stats(tc_games, tz_offset)
    return result


def _parse_iso_date_to_ts_ms(value, end_of_day=False):
    """Parse YYYY-MM-DD into UTC timestamp in milliseconds. Returns None on empty."""
    if not value:
        return None
    parsed = _datetime.datetime.strptime(value, "%Y-%m-%d")
    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    parsed = parsed.replace(tzinfo=_datetime.timezone.utc)
    return int(parsed.timestamp() * 1000)


@login_required
@require_GET
def tilt_range(request):
    """AJAX: returns tilt slices for a custom date range."""
    platform = (request.GET.get("platform") or "").strip().lower()
    from_str = request.GET.get("from") or ""
    to_str = request.GET.get("to") or ""

    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    # Optional ?username= lets the panel work while previewing another player.
    override = (request.GET.get("username") or "").strip()

    if platform == "chesscom":
        username = override or (profile.chesscom_username or "").strip()
        fetch = lambda u: chesscom_api.get_all_games(u, months_back=None)
    elif platform == "lichess":
        username = override or (profile.lichess_username or "").strip()
        fetch = lambda u: lichess_api.get_all_games(u, months_back=None)
    else:
        return JsonResponse({"error": "invalid_platform"}, status=400)

    if not username:
        return JsonResponse({"error": "no_username"}, status=400)

    try:
        from_ts_ms = _parse_iso_date_to_ts_ms(from_str, end_of_day=False)
        to_ts_ms = _parse_iso_date_to_ts_ms(to_str, end_of_day=True)
    except ValueError:
        return JsonResponse({"error": "invalid_date"}, status=400)

    if from_ts_ms is not None and to_ts_ms is not None and from_ts_ms > to_ts_ms:
        return JsonResponse({"error": "invalid_range"}, status=400)

    games = fetch(username)
    tz_offset = _user_tz_offset(request.user)
    slices = _slices_for_range(games, tz_offset, from_ts_ms, to_ts_ms)

    if from_str and to_str:
        label = f"{from_str} – {to_str}"
    elif from_str:
        label = f"since {from_str}"
    elif to_str:
        label = f"up to {to_str}"
    else:
        label = "all time"

    return JsonResponse({
        "stats": slices,
        "label": label,
        "total_games": slices["total"]["total_games"],
    })


# ---------------------------------------------------------------------------
# Page views
# ---------------------------------------------------------------------------

def home_both(request):
    """Combined view — chess.com on the left, lichess on the right."""
    empty_payload = {
        'chesscom': {'bullet': [], 'blitz': [], 'rapid': [], 'classical': [], 'daily': []},
        'lichess':  {'bullet': [], 'blitz': [], 'rapid': [], 'classical': [], 'daily': []},
    }
    context = {'platform': 'both', 'combined_payload': empty_payload}

    if not request.user.is_authenticated:
        context['state'] = 'guest'
        return render(request, 'analyzer/both.html', context)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    cc_username = (profile.chesscom_username or '').strip()
    lc_username = (profile.lichess_username or '').strip()

    if not cc_username and not lc_username:
        context['state'] = 'no_usernames'
        return render(request, 'analyzer/both.html', context)

    context['state'] = 'ok'
    chesscom_data = chesscom_api.get_player_data(cc_username) if cc_username else None
    lichess_data  = lichess_api.get_player_data(lc_username)  if lc_username else None
    context['chesscom'] = chesscom_data
    context['lichess']  = lichess_data

    def _dated(player, tc):
        if not player:
            return []
        block = player.get(tc)
        if not block:
            return []
        return block.get('dated_history') or []

    context['combined_payload'] = {
        'chesscom': {
            'bullet':    _dated(chesscom_data, 'bullet'),
            'blitz':     _dated(chesscom_data, 'blitz'),
            'rapid':     _dated(chesscom_data, 'rapid'),
            'classical': [],
            'daily':     _dated(chesscom_data, 'daily'),
        },
        'lichess': {
            'bullet':    _dated(lichess_data, 'bullet'),
            'blitz':     _dated(lichess_data, 'blitz'),
            'rapid':     _dated(lichess_data, 'rapid'),
            'classical': _dated(lichess_data, 'classical'),
            'daily':     [],
        },
    }
    return render(request, 'analyzer/both.html', context)


def home_chesscom(request):
    """Chess.com stats page with inline tilt panel."""
    context = {'platform': 'chesscom'}

    if not request.user.is_authenticated:
        context['state'] = 'guest'
        return render(request, 'analyzer/chesscom.html', context)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    own_username = (profile.chesscom_username or '').strip()
    viewed = (request.GET.get('u') or '').strip()

    # ?u=<username> previews another player without touching the saved profile.
    username = viewed or own_username
    context['own_username'] = own_username
    context['viewed_username'] = username
    context['viewing_other'] = bool(viewed) and viewed.lower() != own_username.lower()

    if not username:
        context['state'] = 'no_username'
        return render(request, 'analyzer/chesscom.html', context)

    data = chesscom_api.get_player_data(username)
    if data is None:
        context['state'] = 'not_found'
        context['attempted_username'] = username
        return render(request, 'analyzer/chesscom.html', context)

    tz_offset = _user_tz_offset(request.user)
    context['state'] = 'ok'
    context['player'] = data
    context.update(_build_chesscom_tilt_context(username, data, tz_offset))
    return render(request, 'analyzer/chesscom.html', context)


@login_required
@require_GET
def chesscom_search(request):
    """AJAX autocomplete: resolve a Chess.com username to a result card."""
    results = chesscom_api.search_player(request.GET.get('q', ''))
    return JsonResponse({'results': results})


def home_lichess(request):
    """Lichess stats page with inline tilt panel."""
    context = {'platform': 'lichess'}

    if not request.user.is_authenticated:
        context['state'] = 'guest'
        return render(request, 'analyzer/lichess.html', context)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    own_username = (profile.lichess_username or '').strip()
    viewed = (request.GET.get('u') or '').strip()

    # ?u=<username> previews another player without touching the saved profile.
    username = viewed or own_username
    context['own_username'] = own_username
    context['viewed_username'] = username
    context['viewing_other'] = bool(viewed) and viewed.lower() != own_username.lower()

    if not username:
        context['state'] = 'no_username'
        return render(request, 'analyzer/lichess.html', context)

    data = lichess_api.get_player_data(username)
    if data is None:
        context['state'] = 'not_found'
        context['attempted_username'] = username
        return render(request, 'analyzer/lichess.html', context)

    tz_offset = _user_tz_offset(request.user)
    context['state'] = 'ok'
    context['player'] = data
    context.update(_build_lichess_tilt_context(username, data, tz_offset))
    return render(request, 'analyzer/lichess.html', context)


@login_required
@require_GET
def lichess_search(request):
    """AJAX autocomplete: fuzzy Lichess player search."""
    results = lichess_api.search_player(request.GET.get('q', ''))
    return JsonResponse({'results': results})


# ---------------------------------------------------------------------------
# Player comparison
# ---------------------------------------------------------------------------

def _parse_compare_slot(value):
    """'chesscom:magnuscarlsen' -> ('chesscom', 'magnuscarlsen'); None if invalid."""
    if not value or ":" not in value:
        return None
    platform, _, username = value.partition(":")
    platform = platform.strip().lower()
    username = username.strip()
    if platform not in ("chesscom", "lichess") or not username:
        return None
    return platform, username


def _compare_player_meta(data, platform):
    return {
        "platform": platform,
        "username": data.get("username"),
        "name": data.get("display_name") or data.get("username"),
        "title": data.get("title") or "",
        "avatar": data.get("avatar") or "",
        "country": data.get("country_name") or data.get("country_code") or "",
        "flag_url": data.get("flag_url") or "",
    }


def _compare_tc_block(data):
    """{bullet: [[ts,rating],...], blitz: [...], ...} from a player_data dict."""
    out = {}
    for tc in ("bullet", "blitz", "rapid", "classical", "daily"):
        block = (data or {}).get(tc)
        out[tc] = (block.get("dated_history") if block else None) or []
    return out


@login_required
def compare(request):
    """Side-by-side rating comparison of two players (any platform mix).

    URL: /compare/?a=chesscom:magnuscarlsen&b=lichess:arda_22
    """
    context = {"platform": "compare"}
    slots = {
        "a": _parse_compare_slot(request.GET.get("a")),
        "b": _parse_compare_slot(request.GET.get("b")),
    }

    players, payload, errors = {}, {}, []
    for key, slot in slots.items():
        if not slot:
            continue
        plat, uname = slot
        data = (chesscom_api.get_player_data(uname) if plat == "chesscom"
                else lichess_api.get_player_data(uname))
        if data is None:
            errors.append(uname)
            continue
        players[key] = _compare_player_meta(data, plat)
        payload[key] = _compare_tc_block(data)

    context["compare_a"] = players.get("a")
    context["compare_b"] = players.get("b")
    context["compare_cards"] = [(k, players[k]) for k in ("a", "b") if k in players]
    context["compare_errors"] = errors
    context["compare_ready"] = bool(players.get("a") and players.get("b"))
    context["compare_payload"] = payload
    return render(request, "analyzer/compare.html", context)


def signup(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('analyzer:home')
    else:
        form = UserCreationForm()
    return render(request, 'registration/signup.html', {'form': form})


@login_required
def profile(request):
    user_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    saved = False

    if request.method == 'POST':
        form = UserProfileForm(request.POST, instance=user_profile)
        if form.is_valid():
            form.save()
            saved = True
    else:
        form = UserProfileForm(instance=user_profile)

    return render(request, 'analyzer/profile.html', {'form': form, 'saved': saved})
