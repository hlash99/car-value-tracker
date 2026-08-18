#!/usr/bin/env python3
"""Deep Bring a Trailer SOLD history for a model page.

The BaT model pages only embed the first 24 completed auctions, but the
listings-filter REST endpoint behind the "load more" control will page through
the whole archive (~12 years for the NSX). Three things are needed to make it
filter to a single model, all discovered 2026-08-16:

  1. an `X-WP-Nonce` header, read from BAT_MODEL_LISTINGS_COMPLETED_TOOLBAR
     on the model page;
  2. the keyword_pages ids, read from `auctionsCompletedInitialData.base_filter`
     on the same page;
  3. the filter must be nested under `base_filter` in a JSON body -- passing
     keyword_pages at the top level, or form-encoded, is silently ignored and
     you get the unfiltered 258k-listing firehose instead.

`per_page` is capped at 48; 100 returns HTTP 400.

Stdlib only, so it runs on a bare GitHub Actions runner.
"""
import json
import re
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
FILTER_URL = "https://bringatrailer.com/wp-json/bringatrailer/1.0/data/listings-filter"
PER_PAGE = 48                      # 100 -> HTTP 400
NONCE_RE = re.compile(r"BAT_MODEL_LISTINGS_COMPLETED_TOOLBAR\s*=\s*(\{.*?\});", re.S)
INITIAL_RE = re.compile(r"auctionsCompletedInitialData\s*=\s*(\{.*?\});", re.S)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
SOLD_RE = re.compile(r"\$([0-9,]+)")



# BaT titles usually lead with mileage: "43k-Mile 1994 Acura NSX 5-Speed", or
# occasionally "127,000-Mile". Kilometer cars are deliberately NOT converted --
# they are a different market and are treated as unknown mileage instead.
MILES_K_RE = re.compile(r"\b([\d.]+)k-Mile", re.I)
MILES_FULL_RE = re.compile(r"\b([\d,]{4,})-Mile", re.I)
KM_RE = re.compile(r"-Kilometer", re.I)


def title_miles(title):
    """Odometer reading parsed out of a BaT title, or None if it is not stated."""
    if KM_RE.search(title):
        return None
    m = MILES_K_RE.search(title)
    if m:
        return int(float(m.group(1)) * 1000)
    m = MILES_FULL_RE.search(title)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def _get(url, data=None, headers=None, timeout=60, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {"User-Agent": UA})
            return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def model_handshake(model_url):
    """Return (nonce, keyword_pages, items_total) for a BaT model page."""
    html = _get(model_url)
    nm = NONCE_RE.search(html)
    im = INITIAL_RE.search(html)
    if not nm or not im:
        raise RuntimeError(f"could not read BaT handshake from {model_url}")
    nonce = json.loads(nm.group(1))["restNonce"]
    initial = json.loads(im.group(1))
    kp = initial.get("base_filter", {}).get("keyword_pages") or []
    return nonce, kp, initial.get("items_total", 0)


def fetch_all_completed(model_url, max_pages=60, sleep=0.8):
    """Every completed auction BaT will hand over for this model, newest first."""
    nonce, kp, total = model_handshake(model_url)
    if not kp:
        raise RuntimeError(f"no keyword_pages for {model_url}")
    headers = {"User-Agent": UA, "X-WP-Nonce": nonce,
               "Content-Type": "application/json", "Referer": model_url}
    out, page = [], 1
    while page <= max_pages:
        body = {"page": page, "per_page": PER_PAGE, "get_items": 1, "sort": "td",
                "base_filter": {"keyword_pages": kp}}
        raw = _get(FILTER_URL, data=json.dumps(body).encode(), headers=headers)
        payload = json.loads(raw)
        items = payload.get("items", [])
        out.extend(items)
        if page >= (payload.get("pages_total") or 1) or not items:
            break
        page += 1
        time.sleep(sleep)
    return out, total


def parse_sold(items, spec):
    """Filter raw items down to real SOLD comps matching a model spec.

    spec keys: include / exclude (title substrings), year_min / year_max,
    lo / hi (sale price bounds in whole dollars).
    Reserve-not-met results say 'Bid to' rather than 'Sold for' and are dropped.
    """
    inc = [w.lower() for w in spec.get("include", [])]
    exc = [w.lower() for w in spec.get("exclude", [])]
    lo, hi = spec.get("lo", 0), spec.get("hi", 10**9)
    sold = []
    for it in items:
        title = it.get("title") or ""
        tl = title.lower()
        st = it.get("sold_text") or ""
        if "sold for" not in st.lower():
            continue
        if inc and not all(w in tl for w in inc):
            continue
        if any(w in tl for w in exc):
            continue
        ym = YEAR_RE.search(title)
        if ym and not (spec.get("year_min", 0) <= int(ym.group(0)) <= spec.get("year_max", 9999)):
            continue
        pm = SOLD_RE.search(st)
        ts = it.get("timestamp_end") or 0
        if not pm or not ts:
            continue
        price = int(pm.group(1).replace(",", ""))
        if not (lo <= price <= hi):
            continue
        mi = title_miles(title)
        lo_mi, hi_mi = spec.get("min_miles"), spec.get("max_miles")
        if lo_mi is not None or hi_mi is not None:
            if mi is None:                      # unknown mileage cannot satisfy a mileage filter
                continue
            if lo_mi is not None and mi < lo_mi:
                continue
            if hi_mi is not None and mi > hi_mi:
                continue
        sold.append({"date": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"),
                     "ts": ts, "price": price, "miles": mi, "title": title, "url": it.get("url")})
    sold.sort(key=lambda s: s["ts"])
    return sold


def annual_medians(sold, min_n=2):
    """Median sale price per calendar year, for the appreciation curve."""
    by_year = {}
    for s in sold:
        by_year.setdefault(s["date"][:4], []).append(s["price"])
    return [{"year": int(y), "median": round(statistics.median(v)), "n": len(v)}
            for y, v in sorted(by_year.items()) if len(v) >= min_n]


def cagr(first, last, years):
    if not first or not last or years <= 0:
        return None
    return round(((last / first) ** (1 / years) - 1) * 100, 1)


def curve(sold, today=None, min_n=2):
    """Total and per-year appreciation over 5y and 10y windows, plus the
    annual median series the chart draws."""
    series = annual_medians(sold, min_n=min_n)
    if len(series) < 2:
        return {"series": series, "n_sold": len(sold)}
    now = (today or datetime.now(timezone.utc)).year
    latest = series[-1]
    out = {"series": series, "n_sold": len(sold),
           "latest_year": latest["year"], "latest_median": latest["median"],
           "first_year": series[0]["year"], "first_median": series[0]["median"]}
    for win in (5, 10):
        target = latest["year"] - win
        base = None
        for p in series:                      # nearest year at or after target
            if p["year"] >= target:
                base = p
                break
        if base and base["year"] < latest["year"]:
            span = latest["year"] - base["year"]
            out[f"w{win}"] = {
                "from_year": base["year"], "from": base["median"],
                "to_year": latest["year"], "to": latest["median"],
                "total_pct": round((latest["median"] / base["median"] - 1) * 100, 1),
                "cagr_pct": cagr(base["median"], latest["median"], span),
                "span_years": span,
            }
    return out


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://bringatrailer.com/acura/nsx/"
    items, total = fetch_all_completed(url)
    print(f"{url}\n  raw items {len(items)} (site reports {total})")
    sold = parse_sold(items, {"lo": 15000, "hi": 400000})
    c = curve(sold)
    print(f"  sold comps {c['n_sold']}  years {c.get('first_year')}-{c.get('latest_year')}")
    for w in (5, 10):
        d = c.get(f"w{w}")
        if d:
            print(f"  {w}y: {d['from_year']} ${d['from']:,} -> {d['to_year']} ${d['to']:,} "
                  f"= {d['total_pct']:+}% total, {d['cagr_pct']:+}%/yr over {d['span_years']}y")
