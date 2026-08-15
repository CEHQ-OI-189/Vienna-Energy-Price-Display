#!/usr/bin/env python3
"""
Fetches today's Vienna (Austria) EPEX Spot hourly electricity prices from the
free aWattar API and renders a single self-contained HTML page with a bar
chart, current price, and a low/medium/high tag.

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
OPTIMA_AKTIV_CT_PER_KWH = 14.94
OPTIMA_AKTIV_MONTH = "August 2026"


def fetch_prices():
    """Fetch raw hourly market data from aWattar, explicitly starting at
    today's midnight (Vienna time) so we get the FULL day, not just the
    hours remaining from right now onward (which is aWattar's default)."""
    now = datetime.now(VIENNA_TZ)
    midnight_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_ms = int(midnight_today.timestamp() * 1000)

    url = f"{AWATTAR_URL}?start={start_ms}"
    req = urllib.request.Request(url, headers={"User-Agent": "vienna-display/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload["data"]


def to_local(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(VIENNA_TZ)


def build_today_series(raw):
    """Filter raw aWattar data down to just today's 24 (ish) hourly slots,
    in Vienna local time, converted to ct/kWh."""
    now = datetime.now(VIENNA_TZ)
    today = now.date()

    hours = []
    for entry in raw:
        start = to_local(entry["start_timestamp"])
        if start.date() == today:
            ct_per_kwh = entry["marketprice"] / 10.0  # EUR/MWh -> ct/kWh
            hours.append({"hour": start.hour, "price": ct_per_kwh})

    hours.sort(key=lambda h: h["hour"])
    return hours, now


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
    bars_svg = []
    for i, h in enumerate(hours):
        x = i * (bar_w + gap)
        norm = (h["price"] - lo) / (hi - lo) if hi > lo else 0.5
        bar_h = max(2, norm * chart_h)
        y = chart_h - bar_h
        fill = "#111111" if h["hour"] == current_hour else "#c9c9c9"
        bars_svg.append(f'<rect x="{x}" y="{y}" width="{bar_w}" height="{bar_h}" fill="{fill}"></rect>')

    width = len(hours) * (bar_w + gap)
    baseline = f'<line x1="0" y1="{chart_h}" x2="{width}" y2="{chart_h}" stroke="#111" stroke-width="2"></line>'

    reference_line = ""
    if reference_price is not None:
        norm_ref = (reference_price - lo) / (hi - lo) if hi > lo else 0.5
        y_ref = chart_h - norm_ref * chart_h
        reference_line = (
            f'<line x1="0" y1="{y_ref}" x2="{width}" y2="{y_ref}" '
            f'stroke="#111" stroke-width="1.5" stroke-dasharray="5,4"></line>'
            f'<text x="{width - 4}" y="{y_ref - 5}" font-size="11" fill="#555" text-anchor="end">'
            f'{reference_label}</text>'
        )

    labels = ""
    for i, h in enumerate(hours):
        if h["hour"] % 6 == 0:
            x = i * (bar_w + gap)
            labels += f'<text x="{x}" y="{chart_h + 16}" font-size="11" fill="#555">{h["hour"]:02d}:00</text>'

    return (
        f'<svg viewBox="0 0 {width} {chart_h + 24}" width="100%" height="180" '
        f'role="img" aria-label="Hourly price bar chart for today, current hour highlighted, '
        f'with OPTIMA Aktiv reference line">'
        + "".join(bars_svg) + baseline + reference_line + labels + "</svg>"
    )


def render_html(hours, now):
    prices = [h["price"] for h in hours]
    current = next((h for h in hours if h["hour"] == now.hour), hours[-1])
    tier = classify(current["price"], prices)

    cheapest_left = sorted(
        [h for h in hours if h["hour"] >= now.hour], key=lambda h: h["price"]
    )[:2]
    cheapest_str = ", ".join(f'{h["hour"]:02d}:00' for h in cheapest_left) or "-"

    chart = render_svg_bars(
        hours, now.hour,
        reference_price=OPTIMA_AKTIV_CT_PER_KWH,
        reference_label=f"OPTIMA Aktiv {OPTIMA_AKTIV_MONTH} ({OPTIMA_AKTIV_CT_PER_KWH:.1f} ct)",
    )

    is_stale = OPTIMA_AKTIV_MONTH != now.strftime("%B %Y")
    stale_warning = " \u2014 \u26a0 CHECK THIS RATE, MONTH MAY BE OUTDATED" if is_stale else ""

    diff = OPTIMA_AKTIV_CT_PER_KWH - current["price"]
    if diff > 0:
        vs_optima = f"winning by {diff:.1f} ct vs OPTIMA Aktiv {OPTIMA_AKTIV_MONTH}{stale_warning}"
    elif diff < 0:
        vs_optima = f"losing by {abs(diff):.1f} ct vs OPTIMA Aktiv {OPTIMA_AKTIV_MONTH}{stale_warning}"
    else:
        vs_optima = f"exactly matching OPTIMA Aktiv {OPTIMA_AKTIV_MONTH}{stale_warning}"

    css = (
        "html,body{margin:0;padding:0;height:100%;background:#ffffff;color:#111111;"
        "font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;}"
        ".wrap{box-sizing:border-box;width:100vw;height:100vh;padding:5vw;"
        "display:flex;flex-direction:column;justify-content:center;}"
        ".row{display:flex;justify-content:space-between;align-items:baseline;}"
        ".top{border-bottom:2px solid #111;padding-bottom:10px;margin-bottom:18px;font-size:4vw;}"
        ".price{font-size:12vw;font-weight:600;line-height:1;}"
        ".unit{font-size:4vw;font-weight:400;}"
        ".tag{border:2px solid #111;padding:4px 18px;font-size:4vw;font-weight:600;"
        "text-transform:uppercase;margin-left:24px;}"
        ".tag.low{background:#111;color:#fff;}"
        ".chart{margin-top:24px;}"
        ".bottom{border-top:2px solid #111;padding-top:10px;margin-top:18px;font-size:3vw;}"
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
    lines.append("<span>Vienna &middot; " + now.strftime("%a %d %b") + " &middot; " + now.strftime("%H:%M") + "</span>")
    lines.append("<span>updates every 15 min</span>")
    lines.append("</div>")
    lines.append("<div class='row' style='font-size:3.2vw;color:#333;margin-top:6px;'>")
    lines.append("<span>" + vs_optima + "</span>")
    lines.append("</div>")
    lines.append("<div class='row'>")
    lines.append("<div>")
    lines.append("<div class='price'>" + f"{current['price']:.1f}" + "<span class='unit'> ct/kWh</span></div>")
    lines.append("</div>")
    lines.append("<div class='tag " + tier + "'>" + tier + "</div>")
    lines.append("</div>")
    lines.append("<div class='chart'>" + chart + "</div>")
    lines.append("<div class='row bottom'>")
    lines.append("<span>cheapest left today: <strong>" + cheapest_str + "</strong></span>")
    lines.append("<span>range today: " + f"{min(prices):.1f}" + "&ndash;" + f"{max(prices):.1f}" + " ct</span>")
    lines.append("</div>")
    lines.append("</div>")
    lines.append("</body>")
    lines.append("</html>")

    return "\n".join(lines)


def main():
    raw = fetch_prices()
    hours, now = build_today_series(raw)
    if not hours:
        raise RuntimeError("No price data found for today from aWattar.")
    html = render_html(hours, now)

    import os
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {OUTPUT_PATH} with {len(hours)} hourly prices, current hour {now.hour}:00")


if __name__ == "__main__":
    main()
