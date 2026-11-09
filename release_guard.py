"""
release_guard.py — no new buys around scheduled economic data releases.

Right after a release, traders with the new numbers sell into stale bids
before the bot (and FRED) catch up: on Nov 6 the jobs report came out at
13:30 UTC and the bot's waiting bids were filled minutes before prices
dropped further. So for each release group, from RELEASE_GUARD_BEFORE_H hours
before a release to RELEASE_GUARD_AFTER_H hours after, the bot places no new
buys in that group's series and cancels its waiting buys there. Exits
(stop-loss / take-profit) are not affected.

The schedule comes from Kalshi itself: the economic-data markets close one
minute before their data is released (jobs 8:29 ET, CPI 8:29 ET, Fed 1:59 PM
ET), so "does a market in this group close within the window?" is the check.
No dates are hardcoded. If the lookup fails, the group is treated as inside
its window (missing a trade is cheaper than being picked off).
"""
import time
from datetime import datetime, timezone

# group name -> (series whose close times mark the release, series covered)
GROUPS = {
    "jobs": ("KXPAYROLLS",    ("KXPAYROLLS", "KXU3")),
    "CPI":  ("KXCPI",         ("KXCPI", "KXCPIYOY", "KXEGGS")),
    "Fed":  ("KXFEDDECISION", ("KXFED", "KXFEDDECISION", "KXRATECUTCOUNT")),
    "GDP":  ("KXGDP",         ("KXGDP",)),
}


def _ts(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _hhmm(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%b %d %H:%M UTC")


def blocked_series(client, config, now=None) -> dict:
    """{series: reason} for every series that's inside a release window now."""
    if not getattr(config, "RELEASE_GUARD", True):
        return {}
    now = now or time.time()
    before_s = getattr(config, "RELEASE_GUARD_BEFORE_H", 12) * 3600
    after_s = getattr(config, "RELEASE_GUARD_AFTER_H", 6) * 3600
    blocked = {}
    for group, (anchor, series) in GROUPS.items():
        closes = client.close_times_between(anchor, now - after_s, now + before_s)
        if closes is None:
            reason = f"{group} release schedule couldn't be checked — no new buys to be safe"
        elif closes:
            release = max(_ts(c) for c in closes)
            reason = (f"{group} data release window (release {_hhmm(release)}, "
                      f"buys resume {_hhmm(release + after_s)})")
        else:
            continue
        for s in series:
            blocked[s] = reason
    return blocked
