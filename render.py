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


def fetch_prices():
    """Fetch raw hourly market data from aWattar."""
    req = urllib.request.Request(AWATTAR_URL, headers={"User-Agent": "vienna-display/1.0"})
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


def render_svg_bars(hours, current_hour):
    prices = [h["price"] for h in hours]
    pmin, pmax = min(prices), max(prices)
    pad = (pmax - pmin) * 0.1 or 1
    lo, hi = pmin - pad, pmax + pad

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
    labels = ""
    for i, h in enumerate(hours):
        if h["hour"] % 6 == 0:
            x = i * (bar_w + gap)
            labels += f'<text x="{x}" y="{chart_h + 16}" font-size="11" fill="#555">{h["hour"]:02d}:00</text>'

    return (
        f'<svg viewBox="0 0 {width} {chart_h + 24}" width="100%" height="180" '
        f'role="img" aria-label="Hourly price bar chart for today, current hour highlighted">'
        + "".join(bars_svg) + baseline + labels + "</svg>"
    )


def render_html(hours, now):
    prices = [h["price"] for h in hours]
    current = next((h for h in hours if h["hour"] == now.hour), hours[-1])
    tier = classify(current["price"], prices)

    cheapest_left = sorted(
        [h for h in hours if h["hour"] >= now.hour], key=lambda h: h["price"]
    )[:2]
    cheapest_str = ", ".join(f'{h["hour"]:02d}:00' for h in cheapest_left) or "-"

    chart = render_svg_bars(hours, now.hour)

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="900">
<title>Vienna Spot Price</title>
<style>
  html, body {{
    margin: 0; padding: 0; height: 100%;
    background: #ffffff; color: #111111;
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
  }}
  .wrap {{
