#!/usr/bin/env python3
"""
Fetches Vienna (Austria) EPEX Spot hourly electricity prices from the free
aWattar API and renders a single self-contained HTML page: a bar chart of
today's prices, current price with a low/medium/high tag, a comparison
against the OPTIMA Aktiv fixed rate, and today's average vs. the trailing
7-day average.

Since aWattar publishes the whole day's prices in advance, the page embeds
the full day as JSON and a small client-side script picks the "current"
hour using the viewer's own clock (fixed to Europe/Vienna regardless of
device timezone) - so the display stays correct hour to hour even if this
script doesn't get re-run by GitHub Actions exactly on schedule.

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
        bars_svg.append(
            f'<rect data-hour="{h["hour"]}" x="{x}" y="{y}" width="{bar_w}" '
            f'height="{bar_h}" fill="{fill}"></rect>'
        )

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

    day_divider = ""
    for i in range(1, len(hours)):
        if hours[i]["start"].date() != hours[i - 1]["start"].date():
            x_div = left_margin + i * (bar_w + gap) - gap / 2
            day_divider = (
                f'<line x1="{x_div}" y1="0" x2="{x_div}" y2="{chart_h}" '
                f'stroke="#999" stroke-width="1" stroke-dasharray="2,2"></line>'
            )
            break

    return (
        f'<svg viewBox="0 0 {width} {chart_h + 24}" preserveAspectRatio="xMidYMid meet" '
        f'role="img" aria-label="Hourly price bar chart, current hour highlighted live, '
        f'with Y-axis, OPTIMA Aktiv reference line and visible min/max">'
        + "".join(bars_svg) + baseline + axis_line + reference_line + day_divider + minmax_labels + labels + "</svg>"
    )


def render_html(chart_hours, today_hours, week_hours, now, diagnostics):
    chart_prices = [h["price"] for h in chart_hours]
    tiers = {h["hour"]: classify(h["price"], chart_prices) for h in chart_hours}

    day_prices = [h["price"] for h in today_hours]
    today_avg = sum(day_prices) / len(day_prices)

    chart = render_svg_bars(
        chart_hours, now.hour,
        reference_price=OPTIMA_AKTIV_CT_PER_KWH,
        reference_label="OPTIMA Aktiv",
    )

    is_stale = OPTIMA_AKTIV_MONTH != now.strftime("%B %Y")
    stale_warning = " \u26a0 rate may be outdated" if is_stale else ""
    optima_line = f"(OPTIMA Aktiv {OPTIMA_AKTIV_MONTH}: {OPTIMA_AKTIV_CT_PER_KWH:.1f} ct/kWh){stale_warning}"

    if week_hours:
        week_avg = sum(h["price"] for h in week_hours) / len(week_hours)
        pct = (today_avg - week_avg) / week_avg * 100 if week_avg else 0
        sign = "+" if pct >= 0 else "-"
        avg_line = (
            f"Today's Avg. = {today_avg:.1f} ct/kWh : "
            f"7-Day Rolling Avg. = {week_avg:.1f} ct/kWh "
            f"({sign}{abs(pct):.1f}%)"
        )
    else:
        avg_line = f"Today's Avg. = {today_avg:.1f} ct/kWh"

    hours_json = json.dumps([
        {"hour": h["hour"], "price": round(h["price"], 2), "tier": tiers[h["hour"]]}
        for h in chart_hours
    ])

    css = (
        "html,body{margin:0;padding:0;height:100%;background:#ffffff;color:#111111;"
        "font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;}"
        ".wrap{box-sizing:border-box;width:100vw;height:100vh;height:100dvh;"
        "padding:max(2.5vmin,env(safe-area-inset-top)) max(2.5vmin,env(safe-area-inset-right)) "
        "max(2.5vmin,env(safe-area-inset-bottom)) max(2.5vmin,env(safe-area-inset-left));"
        "display:flex;flex-direction:column;justify-content:space-between;overflow:hidden;}"
        ".row{display:flex;justify-content:space-between;align-items:baseline;}"
        ".top{border-bottom:2px solid #111;padding-bottom:6px;font-size:3.2vmin;flex:0 0 auto;}"
        ".optima{font-size:2.2vmin;color:#555;flex:0 0 auto;}"
        ".price{font-size:9vmin;font-weight:600;line-height:1;}"
        ".unit{font-size:3.2vmin;font-weight:400;}"
        ".tag{border:2px solid #111;padding:4px 18px;font-size:3.2vmin;font-weight:600;"
        "text-transform:uppercase;margin-left:24px;}"
        ".tag.low{background:#111;color:#fff;}"
        ".pricerow{flex:0 0 auto;}"
        ".chart{flex:1 1 auto;min-height:0;display:flex;align-items:center;justify-content:center;overflow:hidden;}"
        ".chart svg{width:100%;height:100%;display:block;}"
        ".bottom{border-top:2px solid #111;padding-top:6px;font-size:2.4vmin;flex:0 0 auto;}"
    )

    js = (
        "function viennaParts(){"
        "const f=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Vienna',"
        "hour:'2-digit',minute:'2-digit',hour12:false});"
        "const parts=f.formatToParts(new Date());"
        "const get=t=>parts.find(p=>p.type===t).value;"
        "return {hour:parseInt(get('hour')),minute:get('minute'),hourStr:get('hour')};}"
        "const HOURS=" + hours_json + ";"
        "function update(){"
        "const v=viennaParts();"
        "let entry=HOURS.find(h=>h.hour===v.hour);"
        "if(!entry){entry=HOURS[HOURS.length-1];}"
        "document.getElementById('cprice').textContent=entry.price.toFixed(1);"
        "const tag=document.getElementById('ctag');"
        "tag.textContent=entry.tier;"
        "tag.className='tag '+entry.tier;"
        "document.querySelectorAll('[data-hour]').forEach(function(el){"
        "el.setAttribute('fill', parseInt(el.getAttribute('data-hour'))===v.hour ? '#111111' : '#c9c9c9');"
        "});"
        "document.getElementById('nowtime').textContent=v.hourStr+':'+v.minute;"
        "}"
        "update();setInterval(update,60000);"
    )

    diag_comment = "<!--\n" + "\n".join(diagnostics) + "\n-->"

    lines = []
    lines.append("<!doctype html>")
    lines.append("<html lang='en'>")
    lines.append("<head>")
    lines.append("<meta charset='utf-8'>")
    lines.append("<meta name='viewport' content='width=device-width, initial-scale=1, viewport-fit=cover'>")
    lines.append("<meta http-equiv='refresh' content='1800'>")
    lines.append("<meta name='apple-mobile-web-app-capable' content='yes'>")
    lines.append("<meta name='apple-mobile-web-app-status-bar-style' content='black'>")
    lines.append("<meta name='apple-mobile-web-app-title' content='Vienna Price'>")
    lines.append("<meta name='mobile-web-app-capable' content='yes'>")
    lines.append("<title>Vienna Spot Price</title>")
    lines.append("<style>" + css + "</style>")
    lines.append("</head>")
    lines.append("<body>")
    lines.append(diag_comment)
    lines.append("<div class='wrap'>")
    lines.append("<div class='row top'>")
    lines.append("<span>Vienna &middot; " + now.strftime("%a %d %b") + "</span>")
    lines.append("<span>Now: <span id='nowtime'>--:--</span></span>")
    lines.append("</div>")
    lines.append("<div class='optima'>" + optima_line + "</div>")
    lines.append("<div class='row pricerow'>")
    lines.append("<div>")
    lines.append("<div class='price'><span id='cprice'>--</span><span class='unit'> ct/kWh</span></div>")
    lines.append("</div>")
    lines.append("<div class='tag' id='ctag'>--</div>")
    lines.append("</div>")
    lines.append("<div class='chart'>" + chart + "</div>")
    lines.append("<div class='row bottom'>")
    lines.append("<span>" + avg_line + "</span>")
    lines.append("</div>")
    lines.append("</div>")
    lines.append("<script>" + js + "</script>")
    lines.append("</body>")
    lines.append("</html>")

    return "\n".join(lines)


def main():
    now = datetime.now(VIENNA_TZ)
    midnight_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    midnight_yesterday = midnight_today - timedelta(days=1)
    midnight_day_after_tomorrow = midnight_today + timedelta(days=2)
    midnight_7d_ago = midnight_today - timedelta(days=7)
    current_hour_dt = now.replace(minute=0, second=0, microsecond=0)

    diagnostics = [f"script run at (Vienna time) = {now.isoformat()}"]

    # Wide fetch: yesterday through day-after-tomorrow. This comfortably
    # covers any possible rolling window (13h back / 10h forward from any
    # current hour never needs more than 1 day of slack either direction).
    # aWattar simply won't have tomorrow's data yet before ~14:00 - that's
    # fine, we just get fewer entries and the window is narrower until then.
    diagnostics.append(
        f"wide fetch requested: {midnight_yesterday.isoformat()} to {midnight_day_after_tomorrow.isoformat()}"
    )
    wide_raw = fetch_range(midnight_yesterday, midnight_day_after_tomorrow)
    all_hours = to_hours(wide_raw)
    if all_hours:
        diagnostics.append(
            f"wide fetch received {len(all_hours)} entries, "
            f"first={all_hours[0]['start'].isoformat()}, last={all_hours[-1]['start'].isoformat()}"
        )

    # The rolling chart window: 14 hours before the current hour, the
    # current hour itself, and 9 hours after = 24 slots when fully available.
    # This split keeps a full hour of buffer past aWattar's ~14:00-14:10
    # publish time before the window first needs tomorrow's data (at 15:00).
    window_start = current_hour_dt - timedelta(hours=14)
    window_end = current_hour_dt + timedelta(hours=9)
    chart_hours = [h for h in all_hours if window_start <= h["start"] <= window_end]
    diagnostics.append(
        f"chart window: {window_start.isoformat()} to {window_end.isoformat()} "
        f"-> {len(chart_hours)} bars"
    )

    # Today's Avg / 7-Day Rolling Avg stay anchored to the calendar day,
    # independent of whatever's currently scrolled into the chart.
    today_hours = [h for h in all_hours if h["start"].date() == now.date()]

    diagnostics.append(f"week fetch requested: {midnight_7d_ago.isoformat()} to {midnight_today.isoformat()}")
    week_raw = fetch_range(midnight_7d_ago, midnight_today)
    week_hours = to_hours(week_raw)

    if not chart_hours or not today_hours:
        raise RuntimeError("No price data found from aWattar for the chart window or today.")

    html = render_html(chart_hours, today_hours, week_hours, now, diagnostics)

    import os
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Wrote {OUTPUT_PATH}")
    for line in diagnostics:
        print("  " + line)


if __name__ == "__main__":
    main()
