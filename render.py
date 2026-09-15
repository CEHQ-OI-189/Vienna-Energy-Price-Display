#!/usr/bin/env python3
"""
Fetches Vienna (Austria) EPEX Spot hourly electricity prices from the free
aWattar API and renders a single self-contained HTML page showing the full,
bill-accurate OPTIMA Voll Aktiv gross price (not the raw wholesale price) -
directly comparable to the OPTIMA Aktiv reference line.

Robustness design: the server embeds a WIDE buffer of hourly data (~3 days:
yesterday through day-after-tomorrow, whatever's actually published). The
browser itself - not the server - decides which 24-hour slice to show,
recomputing it from the real device clock every 60 seconds. This means even
if the GitHub Action that regenerates this page misses several scheduled
runs in a row, the displayed window stays correctly positioned, just drawn
from a slightly older (but still valid) buffer, rather than silently going
stale/misaligned like a server-side-only window would.

No API key needed. Designed to run on a schedule (e.g. GitHub Actions) and
its output (docs/index.html) published as a static site.
"""

import json
import urllib.request
from datetime import datetime, timezone, timedelta

VIENNA_TZ = timezone(timedelta(hours=2))  # CEST; close enough for display purposes
AWATTAR_URL = "https://api.awattar.at/v1/marketdata"
OUTPUT_PATH = "docs/index.html"

# --- UPDATE THIS MANUALLY, MONTHLY ---
# Wien Energie's OPTIMA Aktiv price is NOT fixed - it's recalculated monthly
# against a price index (FM 22). Check your Wien Energie account/bill for the
# current month's ct/kWh rate (the gross, all-in figure they show you) and
# update BOTH values below whenever it changes.
OPTIMA_AKTIV_CT_PER_KWH = 22.8547
OPTIMA_AKTIV_MONTH = "September 2026"

# --- OPTIMA Voll Aktiv gross price formula ---
# Verified against Wien Energie's own published Preisblätter:
#   net = (EPEX ct/kWh x 1.07) + 1.42 ct - 0.20 ct (Basismix discount)
#   gross = net x 1.07 (7% Gebrauchsabgabe, Vienna network customers)
#                x 1.20 (20% USt/VAT)
# Both OPTIMA Aktiv and OPTIMA Voll Aktiv go through this same net->gross
# tax/duty conversion, so applying it here makes the chart directly,
# honestly comparable to the OPTIMA_AKTIV_CT_PER_KWH figure above.
VOLL_AKTIV_PCT_MARKUP = 1.07
VOLL_AKTIV_FIXED_SURCHARGE_CT = 1.42
BASISMIX_DISCOUNT_CT = 0.20
GEBRAUCHSABGABE_MULT = 1.07
UST_MULT = 1.20

# Rolling chart window: 14 hours before the current hour, the current hour
# itself, and 9 hours after = 24 slots when fully available. This split
# keeps a full hour of buffer past aWattar's ~14:00-14:10 publish time
# before the window first needs tomorrow's data (at 15:00).
WINDOW_HOURS_PAST = 14
WINDOW_HOURS_FUTURE = 9


def epex_eur_mwh_to_gross_ct(epex_eur_mwh):
    epex_ct = epex_eur_mwh / 10.0
    net = (epex_ct * VOLL_AKTIV_PCT_MARKUP) + VOLL_AKTIV_FIXED_SURCHARGE_CT - BASISMIX_DISCOUNT_CT
    gross = net * GEBRAUCHSABGABE_MULT * UST_MULT
    return gross


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
    """Convert raw aWattar entries into a list of
    {start (datetime), ts_ms (int), hour, price (gross ct/kWh)}."""
    out = []
    for entry in raw:
        start = to_local(entry["start_timestamp"])
        price = epex_eur_mwh_to_gross_ct(entry["marketprice"])
        out.append({
            "start": start,
            "ts_ms": entry["start_timestamp"],
            "hour": start.hour,
            "price": price,
        })
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


def render_svg_bars(hours, current_ts_ms, reference_price=None, reference_label=""):
    """Server-side initial render (first paint / no-JS fallback). The
    client-side JS rebuilds this same chart on an ongoing basis using
    identical layout math, from a wider embedded buffer."""
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

    left_margin = 40
    right_margin = 90
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
        fill = "#111111" if h["ts_ms"] == current_ts_ms else "#c9c9c9"
        bars_svg.append(
            f'<rect x="{x}" y="{y}" width="{bar_w}" height="{bar_h}" fill="{fill}"></rect>'
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
        f'<svg id="pricechart" viewBox="0 0 {width} {chart_h + 24}" preserveAspectRatio="xMidYMid meet" '
        f'role="img" aria-label="Hourly OPTIMA Voll Aktiv gross price, current hour highlighted live, '
        f'with Y-axis, OPTIMA Aktiv reference line and visible min/max">'
        + "".join(bars_svg) + baseline + axis_line + reference_line + day_divider + minmax_labels + labels + "</svg>"
    )


def render_html(chart_hours, today_hours, week_hours, now, all_hours_wide, diagnostics):
    chart_prices = [h["price"] for h in chart_hours]
    current_entry = min(chart_hours, key=lambda h: abs(h["ts_ms"] - int(now.timestamp() * 1000)))
    tier = classify(current_entry["price"], chart_prices)

    day_prices = [h["price"] for h in today_hours]
    today_avg = sum(day_prices) / len(day_prices)

    chart = render_svg_bars(
        chart_hours, current_entry["ts_ms"],
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

    # The WIDE buffer (up to ~72h), for the client-side JS to re-slice from,
    # using absolute timestamps (not hour-of-day, which repeats across days).
    all_hours_json = json.dumps([
        {"ts": h["ts_ms"], "price": round(h["price"], 3)}
        for h in all_hours_wide
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

    # Client-side rolling-window engine. Mirrors render_svg_bars' layout
    # math exactly so the JS-rebuilt chart matches the server-rendered one.
    js = (
        "var ALL_HOURS=" + all_hours_json + ";"
        "var OPTIMA_PRICE=" + repr(OPTIMA_AKTIV_CT_PER_KWH) + ";"
        "var WIN_PAST=" + str(WINDOW_HOURS_PAST) + ",WIN_FUT=" + str(WINDOW_HOURS_FUTURE) + ";"
        "function viennaParts(ms){"
        "var f=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Vienna',"
        "year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});"
        "var parts=f.formatToParts(new Date(ms));"
        "var g=function(t){return parts.find(function(p){return p.type===t;}).value;};"
        "return {hour:parseInt(g('hour')),minute:g('minute'),dateKey:g('year')+'-'+g('month')+'-'+g('day')};"
        "}"
        "function classify(price, allPrices){"
        "var sorted=allPrices.slice().sort(function(a,b){return a-b;});"
        "var n=sorted.length;"
        "var p33=sorted[Math.floor(n*0.33)];"
        "var p66=sorted[Math.floor(n*0.66)];"
        "if(price<=p33)return 'low';"
        "if(price>=p66)return 'high';"
        "return 'medium';"
        "}"
        "function buildChartSVG(hours, currentTs, refPrice, refLabel){"
        "var prices=hours.map(function(h){return h.price;});"
        "var pmin=Math.min.apply(null,prices), pmax=Math.max.apply(null,prices);"
        "var pad=(pmax-pmin)*0.1 || 1;"
        "var lo=pmin-pad, hi=pmax+pad;"
        "if(refPrice!==null){lo=Math.min(lo,refPrice-pad);hi=Math.max(hi,refPrice+pad);}"
        "var chartH=140,barW=22,gap=8;"
        "var barsW=hours.length*(barW+gap);"
        "var leftM=40,rightM=90;"
        "var plotRight=leftM+barsW;"
        "var width=plotRight+rightM;"
        "function yOf(p){var norm=(hi>lo)?(p-lo)/(hi-lo):0.5;return chartH-norm*chartH;}"
        "var bars='';"
        "for(var i=0;i<hours.length;i++){"
        "var h=hours[i];"
        "var x=leftM+i*(barW+gap);"
        "var barH=Math.max(2,chartH-yOf(h.price));"
        "var y=chartH-barH;"
        "var fill=(h.ts===currentTs)?'#111111':'#c9c9c9';"
        "bars+='<rect x=\"'+x+'\" y=\"'+y+'\" width=\"'+barW+'\" height=\"'+barH+'\" fill=\"'+fill+'\"></rect>';"
        "}"
        "var baseline='<line x1=\"'+leftM+'\" y1=\"'+chartH+'\" x2=\"'+plotRight+'\" y2=\"'+chartH+'\" stroke=\"#111\" stroke-width=\"2\"></line>';"
        "var axisLine='<line x1=\"'+leftM+'\" y1=\"0\" x2=\"'+leftM+'\" y2=\"'+chartH+'\" stroke=\"#111\" stroke-width=\"2\"></line>';"
        "var refLine='';"
        "if(refPrice!==null){"
        "var yRef=yOf(refPrice);"
        "refLine='<line x1=\"'+leftM+'\" y1=\"'+yRef+'\" x2=\"'+width+'\" y2=\"'+yRef+'\" stroke=\"#111\" stroke-width=\"1.5\" stroke-dasharray=\"5,4\"></line>'"
        "+'<text x=\"'+(width-2)+'\" y=\"'+(yRef-5)+'\" font-size=\"11\" fill=\"#555\" text-anchor=\"end\">'+refLabel+'</text>';"
        "}"
        "var yMax=yOf(pmax), yMin=yOf(pmin);"
        "var minmax='<text x=\"'+(leftM-6)+'\" y=\"'+(yMax+4)+'\" font-size=\"11\" fill=\"#555\" text-anchor=\"end\">'+pmax.toFixed(1)+' ct</text>'"
        "+'<text x=\"'+(leftM-6)+'\" y=\"'+(yMin+4)+'\" font-size=\"11\" fill=\"#555\" text-anchor=\"end\">'+pmin.toFixed(1)+' ct</text>';"
        "var labels='';"
        "var dayDivider='';"
        "var prevDateKey=null;"
        "for(var i=0;i<hours.length;i++){"
        "var vp=viennaParts(hours[i].ts);"
        "if(vp.hour%2===0){"
        "var x=leftM+i*(barW+gap);"
        "labels+='<text x=\"'+(x+2)+'\" y=\"'+(chartH+16)+'\" font-size=\"9\" fill=\"#555\">'+(vp.hour<10?'0':'')+vp.hour+'h</text>';"
        "}"
        "if(prevDateKey!==null && vp.dateKey!==prevDateKey && dayDivider===''){"
        "var xDiv=leftM+i*(barW+gap)-gap/2;"
        "dayDivider='<line x1=\"'+xDiv+'\" y1=\"0\" x2=\"'+xDiv+'\" y2=\"'+chartH+'\" stroke=\"#999\" stroke-width=\"1\" stroke-dasharray=\"2,2\"></line>';"
        "}"
        "prevDateKey=vp.dateKey;"
        "}"
        "return '<svg id=\"pricechart\" viewBox=\"0 0 '+width+' '+(chartH+24)+'\" preserveAspectRatio=\"xMidYMid meet\" role=\"img\" aria-label=\"Live-updating price chart\">'"
        "+bars+baseline+axisLine+refLine+dayDivider+minmax+labels+'</svg>';"
        "}"
        "function update(){"
        "var nowMs=Date.now();"
        "var hourMs=3600000;"
        "var currentHourStart=Math.floor(nowMs/hourMs)*hourMs;"
        "var winStart=currentHourStart-WIN_PAST*hourMs;"
        "var winEnd=currentHourStart+WIN_FUT*hourMs;"
        "var visible=ALL_HOURS.filter(function(h){return h.ts>=winStart && h.ts<=winEnd;});"
        "if(visible.length===0)return;"
        "var current=visible.reduce(function(best,h){"
        "return Math.abs(h.ts-currentHourStart)<Math.abs(best.ts-currentHourStart)?h:best;"
        "},visible[0]);"
        "var prices=visible.map(function(h){return h.price;});"
        "var tier=classify(current.price,prices);"
        "document.getElementById('cprice').textContent=current.price.toFixed(1);"
        "var tag=document.getElementById('ctag');"
        "tag.textContent=tier;"
        "tag.className='tag '+tier;"
        "document.getElementById('chartwrap').innerHTML=buildChartSVG(visible,current.ts,OPTIMA_PRICE,'OPTIMA Aktiv');"
        "var vp=viennaParts(nowMs);"
        "document.getElementById('nowtime').textContent=(vp.hour<10?'0':'')+vp.hour+':'+vp.minute;"
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
    lines.append("<div class='price'><span id='cprice'>" + f"{current_entry['price']:.1f}" + "</span><span class='unit'> ct/kWh</span></div>")
    lines.append("</div>")
    lines.append("<div class='tag " + tier + "' id='ctag'>" + tier + "</div>")
    lines.append("</div>")
    lines.append("<div class='chart' id='chartwrap'>" + chart + "</div>")
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

    window_start = current_hour_dt - timedelta(hours=WINDOW_HOURS_PAST)
    window_end = current_hour_dt + timedelta(hours=WINDOW_HOURS_FUTURE)
    chart_hours = [h for h in all_hours if window_start <= h["start"] <= window_end]
    diagnostics.append(
        f"initial chart window: {window_start.isoformat()} to {window_end.isoformat()} "
        f"-> {len(chart_hours)} bars (client JS re-slices this live from the wide buffer)"
    )

    today_hours = [h for h in all_hours if h["start"].date() == now.date()]

    diagnostics.append(f"week fetch requested: {midnight_7d_ago.isoformat()} to {midnight_today.isoformat()}")
    week_raw = fetch_range(midnight_7d_ago, midnight_today)
    week_hours = to_hours(week_raw)

    if not chart_hours or not today_hours:
        raise RuntimeError("No price data found from aWattar for the chart window or today.")

    html = render_html(chart_hours, today_hours, week_hours, now, all_hours, diagnostics)

    import os
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Wrote {OUTPUT_PATH}")
    for line in diagnostics:
        print("  " + line)


if __name__ == "__main__":
    main()
