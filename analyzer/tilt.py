"""
Behavioral / "tilt" analysis over a player's recent games.

A game dict looks like:
    {"ts": 1700000000000, "result": "win"|"loss"|"draw",
     "time_class": "blitz", "my_rating": 1500, "opp_rating": 1480}

These are pure functions, so they are easy to test in shell or with pytest.
"""
from datetime import datetime, timezone, timedelta

TILT_THRESHOLD = 0.08      # 8 percentage-point drop counts as "tilt"
MIN_SESSION_GAP_MIN = 60   # gap > 60 min splits sessions
MIN_BUCKET_SAMPLE = 5      # ignore buckets with fewer than this many games


# ---------------------------------------------------------------------------
# 1. Post-loss win rate (the headline metric)
# ---------------------------------------------------------------------------

def compute_post_loss_stats(games):
    """
    Compares win rate after a loss vs the overall baseline.
    Returns None if there isn't enough data.
    """
    if len(games) < 10:
        return None

    total = len(games)
    wins = sum(1 for g in games if g["result"] == "win")
    baseline = wins / total

    post_loss_wins = 0
    post_loss_total = 0
    for prev, curr in zip(games, games[1:]):
        if prev["result"] == "loss":
            post_loss_total += 1
            if curr["result"] == "win":
                post_loss_wins += 1

    if post_loss_total == 0:
        return None

    post_loss_wr = post_loss_wins / post_loss_total
    tilt_score = baseline - post_loss_wr

    def _pct(v):
        return f"{v * 100:.0f}" if v is not None else "-"

    return {
        "baseline_winrate": baseline,
        "post_loss_winrate": post_loss_wr,
        "tilt_score": tilt_score,
        "baseline_pct": _pct(baseline),
        "post_loss_pct": _pct(post_loss_wr),
        "tilt_pct": _pct(abs(tilt_score)) if tilt_score is not None else "-",
        "sample_size": post_loss_total,
        "is_tilting": tilt_score > TILT_THRESHOLD,
    }


# ---------------------------------------------------------------------------
# 2. Losing streaks
# ---------------------------------------------------------------------------

def compute_streak_stats(games):
    """Returns longest losing streak + histogram of streak lengths of 2 or more."""
    max_streak = 0
    current = 0
    histogram = {}

    def _bump():
        nonlocal current
        if current >= 2:
            histogram[current] = histogram.get(current, 0) + 1
        current = 0

    for g in games:
        if g["result"] == "loss":
            current += 1
            max_streak = max(max_streak, current)
        else:
            _bump()
    _bump()

    return {
        "max_loss_streak": max_streak,
        "histogram": histogram,
    }


# ---------------------------------------------------------------------------
# 3. Hourly win rate
# ---------------------------------------------------------------------------

def compute_hourly_winrate(games, tz_offset_hours=3):
    """Win rate per hour of day. tz_offset_hours=3 is Turkey time."""
    tz = timezone(timedelta(hours=tz_offset_hours))
    buckets = [[0, 0] for _ in range(24)]   # [wins, total] per hour

    for g in games:
        try:
            dt = datetime.fromtimestamp(g["ts"] / 1000, tz=tz)
        except (OSError, ValueError, OverflowError):
            continue
        hour = dt.hour
        buckets[hour][1] += 1
        if g["result"] == "win":
            buckets[hour][0] += 1

    return [
        {
            "hour": h,
            "winrate": (w / t) if t else None,
            "games": t,
        }
        for h, (w, t) in enumerate(buckets)
    ]


# ---------------------------------------------------------------------------
# 4. Session position effect
# ---------------------------------------------------------------------------

def compute_session_position_stats(games, gap_minutes=MIN_SESSION_GAP_MIN,
                                   min_sample=MIN_BUCKET_SAMPLE):
    """
    Group games into sessions (consecutive games with <gap_minutes between them).
    Returns win rate by position within a session.
    """
    if not games:
        return []

    sessions = [[games[0]]]
    for prev, curr in zip(games, games[1:]):
        gap = (curr["ts"] - prev["ts"]) / 1000 / 60   # minutes
        if gap > gap_minutes:
            sessions.append([curr])
        else:
            sessions[-1].append(curr)

    buckets = {}   # position -> [wins, total]
    for session in sessions:
        for idx, game in enumerate(session, 1):
            if idx not in buckets:
                buckets[idx] = [0, 0]
            buckets[idx][1] += 1
            if game["result"] == "win":
                buckets[idx][0] += 1

    return [
        {"position": pos, "winrate": w / t, "games": t}
        for pos, (w, t) in sorted(buckets.items())
        if t >= min_sample
    ]


# ---------------------------------------------------------------------------
# 5. Human-readable insights
# ---------------------------------------------------------------------------

def humanize_insights(post_loss, hourly, streaks, session_pos):
    """Returns list of {icon, text} dicts to display as bullet points."""
    insights = []

    # First I check the direct post-loss tilt metric.
    if post_loss and post_loss.get("tilt_score") is not None:
        score = post_loss["tilt_score"]
        if score > 0.05:
            insights.append({
                "icon": "warning",
                "text": (
                    f"After a loss, you win {post_loss['post_loss_winrate']*100:.0f}% - "
                    f"{score*100:.0f}% below your baseline of "
                    f"{post_loss['baseline_winrate']*100:.0f}%."
                ),
            })
        elif score < -0.03:
            insights.append({
                "icon": "ok",
                "text": (
                    f"You actually play BETTER after a loss "
                    f"({post_loss['post_loss_winrate']*100:.0f}% vs "
                    f"{post_loss['baseline_winrate']*100:.0f}%). Good emotional control."
                ),
            })
        else:
            insights.append({
                "icon": "ok",
                "text": "Your performance stays steady after losses - no significant tilt.",
            })

    # Then I look at losing streaks because they are easy to act on.
    if streaks and streaks.get("max_loss_streak", 0) >= 5:
        insights.append({
            "icon": "warning",
            "text": (
                f"Longest losing streak: {streaks['max_loss_streak']} games. "
                "Consider taking a break after 3 losses in a row."
            ),
        })

    # Hourly extremes show when the player usually performs best or worst.
    if hourly:
        valid = [h for h in hourly if h["winrate"] is not None and h["games"] >= 10]
        if valid:
            worst = min(valid, key=lambda h: h["winrate"])
            best = max(valid, key=lambda h: h["winrate"])
            if worst["winrate"] < 0.45 and worst["games"] >= 20:
                insights.append({
                    "icon": "warning",
                    "text": (
                        f"Worst hour: {worst['hour']:02d}:00 - "
                        f"{worst['winrate']*100:.0f}% win rate over {worst['games']} games."
                    ),
                })
            insights.append({
                "icon": "ok",
                "text": (
                    f"Best hour: {best['hour']:02d}:00 - "
                    f"{best['winrate']*100:.0f}% over {best['games']} games."
                ),
            })

    # Long sessions can reveal fatigue.
    if session_pos:
        late = [s for s in session_pos if s["position"] >= 8]
        if late:
            avg = sum(s["winrate"] for s in late) / len(late)
            total = sum(s["games"] for s in late)
            if avg < 0.45 and total >= 20:
                insights.append({
                    "icon": "warning",
                    "text": (
                        f"After 8+ games in one sitting, your win rate drops to "
                        f"{avg*100:.0f}%. Take breaks more often."
                    ),
                })

    return insights
