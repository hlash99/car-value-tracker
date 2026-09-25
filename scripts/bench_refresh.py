#!/usr/bin/env python3
"""Monthly TOTAL-RETURN series for the benchmarks the cars are compared with.

He would fund a car by selling AAPL, so the table measures each car's 5-yr
rate against AAPL and the S&P 500 over the SAME window as the car's own
pooled BaT window. That needs dividends-reinvested series at monthly
resolution, which the price-only June levels in `refs` cannot give.

  * S&P 500 - officialdata.org's monthly table (average monthly close,
    dividends reinvested, $100 base). Yahoo rate-limits (HTTP 429) and Stooq
    now sits behind a verification page, so neither is usable from CI.
  * AAPL - Nasdaq's quote API: split-adjusted daily closes plus the dividend
    history. Nasdaq reports dividends before a split UNADJUSTED ($0.77 in 2019
    vs $0.205 after the 2020 4:1), so they are divided down via SPLITS. Each
    dividend is reinvested at the ex-date close, then the index is averaged by
    month to match the S&P table's monthly-average convention.

Values are an index; only ratios between months are used. Keeps last-good on
any failure. Stdlib only. Run by .github/workflows/refresh.yml.
"""
import json
import os
import re
import statistics
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
SP_URL = "https://www.officialdata.org/us/stocks/s-p-500/2015?amount=100&endYear={y}"
NQ = "https://api.nasdaq.com/api/quote/{s}/{kind}?assetclass=stocks{extra}"

# Splits since the Nasdaq history starts (~10 yrs): (effective date, ratio).
SPLITS = {"AAPL": [("2020-08-31", 4)]}


def _get(url, accept="text/html"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    return urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore")


def sp500_tr():
    html = _get(SP_URL.format(y=datetime.now(timezone.utc).year))
    rows = re.findall(r"<tr>\s*<td[^>]*>(\d{4})</td>\s*<td[^>]*>(\d{1,2})</td>\s*"
                      r"<td[^>]*>[-\d.,%]+</td>\s*<td[^>]*>\$?([\d.,]+)</td>", html)
    if len(rows) < 60:
        raise RuntimeError(f"S&P table too short ({len(rows)} rows)")
    return {f"{y}-{int(m):02d}": float(v.replace(",", "")) for y, m, v in rows}


def stock_tr(sym):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hist = json.loads(_get(NQ.format(s=sym, kind="historical",
                                     extra=f"&fromdate=2015-01-01&todate={today}&limit=9999"),
                           "application/json"))
    px = {}
    for r in hist["data"]["tradesTable"]["rows"]:
        m, d, y = r["date"].split("/")
        px[f"{y}-{m}-{d}"] = float(r["close"].replace("$", "").replace(",", ""))
    divs = json.loads(_get(NQ.format(s=sym, kind="dividends", extra=""), "application/json"))
    dv = {}
    for r in divs["data"]["dividends"]["rows"]:
        m, d, y = r["exOrEffDate"].split("/")
        date = f"{y}-{m}-{d}"
        amt = float(r["amount"].replace("$", ""))
        for eff, ratio in SPLITS.get(sym, []):
            if date < eff:
                amt /= ratio
        dv[date] = dv.get(date, 0) + amt
    shares, daily = 1.0, {}
    for date in sorted(px):
        if date in dv:
            shares *= 1 + dv[date] / px[date]
        daily[date] = shares * px[date]
    by = {}
    for date, v in daily.items():
        by.setdefault(date[:7], []).append(v)
    if len(by) < 60:
        raise RuntimeError(f"{sym} history too short ({len(by)} months)")
    return {k: round(statistics.mean(v), 4) for k, v in sorted(by.items())}


def main():
    with open(DATA) as f:
        d = json.load(f)
    bench = d.get("bench") or {"series": {}}
    log = []
    for name, fn, src in (("S&P 500", sp500_tr, "officialdata.org, dividends reinvested"),
                          ("AAPL", lambda: stock_tr("AAPL"), "Nasdaq closes + dividends reinvested")):
        try:
            m = fn()
            bench["series"][name] = {"src": src, "m": m}
            log.append(f"{name}: {len(m)} months to {max(m)}")
        except Exception as e:                      # keep last-good
            log.append(f"{name}: FAILED ({type(e).__name__}: {e})")
    bench["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    bench["status"] = " | ".join(log)
    d["bench"] = bench
    with open(DATA, "w") as f:
        json.dump(d, f, indent=1)
    print("\n".join(log))


if __name__ == "__main__":
    main()
