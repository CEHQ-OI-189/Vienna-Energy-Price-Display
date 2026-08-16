#!/usr/bin/env python3
"""
Fetches Vienna (Austria) EPEX Spot hourly electricity prices from the free
aWattar API and renders a single self-contained HTML page: a bar chart of
today's prices, current price with a low/medium/high tag, a comparison
against the OPTIMA Aktiv fixed rate, and today's average vs. the trailing
7-day average.

No API key needed. Designed to be run on a schedule (e.g. GitHub Actions)
and its output (docs/index.html) published as a static site.
"""

import json
import urllib.request
from datetime import datetime, timezone, timedelta

VIENNA_TZ = timezone(timedelta(hours=2))  # CEST; close enough for display purposes
AWATTAR_URL = "https://api.awattar.at/v1/marketdata"
OUTPUT_PATH = "docs/index.html"

# --- UPDATE THIS MANUALLY ---
# Wien Energie's OPTIMA Aktiv price is NOT fixed - it's recalculated monthly
# against a price index (FM 22). Check your Wien Energie account/bill for the
# current month's ct/kWh rate and update BOTH values below whenever it changes.
OPTIMA_AKTIV_CT_PER_KWH = 18.7289
OPTIMA_AKTIV_MONTH = "August 2026"


def fetch_range(start_dt, end_dt):
    """Fetch raw aWattar market data for an explicit [start, end) window."""
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    url = f"{AWATTAR_URL}?start={start_ms}&end={end_ms}"
    req = urllib.request.Request(url, headers={"User-Agent": "vienna-display/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload["data"]


def to_local(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(VIENNA_TZ)


def to_hours(raw):
    """Convert raw aWattar entries into a list of {start (datetime), hour, price (ct/kWh)}."""
    out = []
    for entry in raw:
        start = to_local(entry["start_timestamp"])
        ct_per_kwh = entry["marketprice"] / 10.0  # EUR/MWh -> ct/kWh
        out.append({"start": start, "hour": start.hour, "price": ct_per_kwh})
    out.sort(key=lambda h: h["start"])
    return out


def classify(price, all_prices):
    sorted_p = sorted(all_prices)
    n = len(sorted_p)
    p33 = sorted_p[int(n * 0.33)]
    p66 = sorted_p[int(n * 0.66)]
    if price <= p33:
        return "low"
    elif price >= p66:
        return "high"
    return "medium"


def render_svg_bars(hours, current_hour, reference_price=None, reference_label=""):
    prices = [h["price"] for h in hours]
    pmin, pmax = min(prices), max(prices)
    pad = (pmax - pmin) * 0.1 or 1
    lo, hi = pmin - pad, pmax + pad
    if reference_price is not None:
        lo = min(lo, reference_price - pad)
        hi = max(hi, reference_price + pad)

    chart_h = 140
    bar_w = 22
    gap = 8
    bars_w = len(hours) * (bar_w + gap)

    left_margin = 40   # room for the Y-axis line + min/max labels
    right_margin = 90  # room for the reference line's label to extend past the bars
    plot_right = left_margin + bars_w
    width = plot_right + right_margin

    def y_of(price):
        norm = (price - lo) / (hi - lo) if hi > lo else 0.5
        return chart_h - norm * chart_h

    bars_svg = []
    for i, h in enumerate(hours):
        x = left_margin + i * (bar_w + gap)
        bar_h = max(2, chart_h - y_of(h["price"]))
        y = chart_h - bar_h
        fill = "#111111" if h["hour"] == current_hour else "#c9c9c9"
        bars_svg.append(f'<rect x="{x}" y="{y}" width="{bar_w}" height="{bar_h}" fill="{fill}"></rect>')

    baseline = f'<line x1="{left_margin}" y1="{chart_h}" x2="{plot_right}" y2="{chart_h}" stroke="#111" stroke-width="2"></line>'
    axis_line = f'<line x1="{left_margin}" y1="0" x2="{left_margin}" y2="{chart_h}" stroke="#111" stroke-width="2"></line>'

    reference_line = ""
    if reference_price is not None:
        y_ref = y_of(reference_price)
        reference_line = (
            f'<line x1="{left_margin}" y1="{y_ref}" x2="{width}" y2="{y_ref}" '
            f'stroke="#111" stroke-width="1.5" stroke-dasharray="5,4"></line>'
            f'<text x="{width - 2}" y="{y_ref - 5}" font-size="11" fill="#555" text-anchor="end">'
            f'{reference_label}</text>'
        )

    # Y-axis min/max labels, right-aligned against the axis line
    y_max_price = y_of(pmax)
    y_min_price = y_of(pmin)
    minmax_labels = (
        f'<text x="{left_margin - 6}" y="{y_max_price + 4}" font-size="11" fill="#555" text-anchor="end">{pmax:.1f} ct</text>'
        f'<text x="{left_margin - 6}" y="{y_min_price + 4}" font-size="11" fill="#555" text-anchor="end">{pmin:.1f} ct</text>'
    )

    labels = ""
    for i, h in enumerate(hours):
        if h["hour"] % 2 == 0:
            x = left_margin + i * (bar_w + gap)
            labels += f'<text x="{x + 2}" y="{chart_h + 16}" font-size="9" fill="#555">{h["hour"]:02d}h</text>'

    return (
        f'<svg viewBox="0 0 {width} {chart_h + 24}" width="100%" height="180" '
        f'role="img" aria-label="Hourly price bar chart for today, current hour highlighted, '
        f'with Y-axis, OPTIMA Aktiv reference line and day min/max">'
        + "".join(bars_svg) + baseline + axis_line + reference_line + minmax_labels + labels + "</svg>"
    )


def render_html(today_hours, week_hours, now):
    prices = [h["price"] for h in today_hours]
    current = next((h for h in today_hours if h["hour"] == now.hour), today_hours[-1])
    tier = classify(current["price"], prices)

    chart = render_svg_bars(
        today_hours, now.hour,
        reference_price=OPTIMA_AKTIV_CT_PER_KWH,
        reference_label="OPTIMA Aktiv",
    )

    is_stale = OPTIMA_AKTIV_MONTH != now.strftime("%B %Y")
    stale_warning = " \u26a0 rate may be outdated" if is_stale else ""
    optima_line = f"(OPTIMA Aktiv {OPTIMA_AKTIV_MONTH}: {OPTIMA_AKTIV_CT_PER_KWH:.1f} ct/kWh){stale_warning}"

    today_avg = sum(prices) / len(prices)
    if week_hours:
        week_avg = sum(h["price"] for h in week_hours) / len(week_hours)
        pct = (today_avg - week_avg) / week_avg * 100 if week_avg else 0
        sign = "+" if pct >= 0 else "-"
        avg_line = (
            f"Today's Avg. = {today_avg:.1f} ct/kWh : "
            f"7-Day Rolling Avg. = {week_avg:.1f} ct/kWh "
            f"({sign}{abs(pct):.0f}%)"
        )
    else:
        avg_line = f"Today's Avg. = {today_avg:.1f} ct/kWh"

    css = (
        "html,body{margin:0;padding:0;min-height:100%;background:#ffffff;color:#111111;"
        "font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;}"
        ".wrap{box-sizing:border-box;width:100vw;min-height:100vh;padding:4vmin;"
        "display:flex;flex-direction:column;justify-content:center;overflow:auto;}"
        ".row{display:flex;justify-content:space-between;align-items:baseline;}"
        ".top{border-bottom:2px solid #111;padding-bottom:10px;margin-bottom:10px;font-size:3.2vmin;}"
        ".optima{font-size:2.2vmin;color:#555;margin-bottom:14px;}"
        ".price{font-size:9vmin;font-weight:600;line-height:1;}"
        ".unit{font-size:3.2vmin;font-weight:400;}"
        ".tag{border:2px solid #111;padding:4px 18px;font-size:3.2vmin;font-weight:600;"
        "text-transform:uppercase;margin-left:24px;}"
        ".tag.low{background:#111;color:#fff;}"
        ".chart{margin-top:24px;}"
        ".bottom{border-top:2px solid #111;padding-top:10px;margin-top:18px;font-size:2.4vmin;}"
    )

    lines = []
    lines.append("<!doctype html>")
    lines.append("<html lang='en'>")
    lines.append("<head>")
    lines.append("<meta charset='utf-8'>")
    lines.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    lines.append("<meta http-equiv='refresh' content='900'>")
    lines.append("<title>Vienna Spot Price</title>")
    lines.append("<style>" + css + "</style>")
    lines.append("</head>")
    lines.append("<body>")
    lines.append("<div class='wrap'>")
    lines.append("<div class='row top'>")
    lines.append("<span>Vienna &middot; " + now.strftime("%a %d %b") + "</span>")
    lines.append("<span>Last updated: " + now.strftime("%H:%M") + "</span>")
    lines.append("</div>")
    lines.append("<div class='optima'>" + optima_line + "</div>")
    lines.append("<div class='row'>")
    lines.append("<div>")
    lines.append("<div class='price'>" + f"{current['price']:.1f}" + "<span class='unit'> ct/kWh</span></div>")
    lines.append("</div>")
    lines.append("<div class='tag " + tier + "'>" + tier + "</div>")
    lines.append("</div>")
    lines.append("<div class='chart'>" + chart + "</div>")
    lines.append("<div class='row bottom'>")
    lines.append("<span>" + avg_line + "</span>")
    lines.append("</div>")
    lines.append("</div>")
    lines.append("</body>")
    lines.append("</html>")

    return "\n".join(lines)


def main():
    now = datetime.now(VIENNA_TZ)
    midnight_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    midnight_tomorrow = midnight_today + timedelta(days=1)
    midnight_7d_ago = midnight_today - timedelta(days=7)

    today_raw = fetch_range(midnight_today, midnight_tomorrow)
    today_hours = [h for h in to_hours(today_raw) if h["start"].date() == now.date()]

    week_raw = fetch_range(midnight_7d_ago, midnight_today)
    week_hours = to_hours(week_raw)

    if not today_hours:
        raise RuntimeError("No price data found for today from aWattar.")

    html = render_html(today_hours, week_hours, now)

    import os
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {OUTPUT_PATH} with {len(today_hours)} hourly prices today, "
          f"{len(week_hours)} hourly prices in the trailing 7-day comparison window.")


if __name__ == "__main__":
    main()
