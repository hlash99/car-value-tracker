#!/usr/bin/env python3
"""Populate the BaT-sourced cars in data.json from real Bring a Trailer SOLD history.

The original five cars on this dashboard are driven by Cars.com ASKING medians
(scripts/refresh.py). The cars added 2026-08 are driven by BaT SOLD prints
instead, which is a stronger source: actual transactions, roughly twelve years
deep, and filterable down to the exact spec and mileage band you would buy.

Each entry below carries the filter that defines the car. Two of them matter a
lot and are easy to get wrong:

  * `min_miles` - the appreciation on several of these lives almost entirely in
    garage queens. Filtering to driven cars inverts the story (the NSX runs
    +61% all-mileage but -3% at 30k+), so both series are computed and stored.
  * `any_of` - `parse_sold` ANDs its include list, but "manual" means
    "5-speed OR 6-speed". Each alternative is run separately and the results
    merged on listing URL.

Stdlib only. Run by .github/workflows/refresh.yml alongside refresh.py.
"""
import json
import os
import statistics
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bat_history import fetch_all_completed, parse_sold, annual_medians, cagr  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data.json")

# Mileage floor that separates "driven" from "stored" for the second series.
DRIVEN_MILES = 30000

BAT_CARS = {
    "Acura NSX (NA2 manual)": {
        "url": "https://bringatrailer.com/acura/nsx/",
        "color": "#C8102E",
        "blurb": "Pre-facelift NA2, 1997-2001, six-speed, coupe and targa.",
        "spec": {"include": ["nsx"], "exclude": ["conversion"],
                 "year_min": 1997, "year_max": 2001, "lo": 30000, "hi": 500000},
        "any_of": ["5-speed", "6-speed"],
        "maint": 3,
    },
    "Ferrari 360 (gated manual)": {
        "url": "https://bringatrailer.com/ferrari/360/",
        "color": "#E24B4A",
        "blurb": "Gated six-speed only - the F1 cars are a different market.",
        "spec": {"include": ["6-speed"], "exclude": ["challenge stradale"],
                 "lo": 40000, "hi": 500000},
        "maint": 6,
    },
    "Audi R8 gen1 V10 (gated)": {
        "url": "https://bringatrailer.com/audi/r8-v10-type-42/",
        "color": "#B01E28",
        "blurb": "Type 42 V10 coupe, gated six-speed, 2009-2015.",
        "spec": {"include": ["6-speed"],
                 "exclude": ["spyder", "convertible", "conversion", "tronic"],
                 "year_min": 2009, "year_max": 2015, "lo": 40000, "hi": 400000},
        "maint": 5,
    },
    "Corvette split-window (1963)": {
        "url": "https://bringatrailer.com/chevrolet/c2-corvette/",
        "color": "#2E6DB4",
        "blurb": "1963 coupe only - the one-year split rear window.",
        "spec": {"include": ["1963"], "exclude": ["convertible", "roadster"],
                 "lo": 40000, "hi": 1200000},
        "maint": 3,
    },
    "Porsche 997.2 Turbo S": {
        "url": "https://bringatrailer.com/porsche/997-turbo/",
        "color": "#185FA5",
        "blurb": "997.2 Turbo S coupe, 2010-2013 - the owned-car benchmark.",
        "spec": {"include": ["turbo s"], "exclude": ["cabriolet", "convertible"],
                 "year_min": 2010, "year_max": 2013, "lo": 60000, "hi": 400000},
        "maint": 3,
    },
    "Ferrari 328 GTS/GTB": {
        "url": "https://bringatrailer.com/ferrari/328/",
        "color": "#D94F3D",
        "blurb": "328 GTB and GTS, 1986-1989 - the last of the carburettor-era shape.",
        "spec": {"exclude": ["308"], "year_min": 1985, "year_max": 1990,
                 "lo": 40000, "hi": 500000},
        "maint": 5,
    },
    "Alfa Romeo GTV 1750/2000": {
        "url": "https://bringatrailer.com/alfa-romeo/gtv/",
        "color": "#A6192E",
        "blurb": "105-series 1750 and 2000 GTV, 1967-1976 - the Bertone coupe.",
        "spec": {"include": ["gtv"], "exclude": ["gtv6", "gtv-6", "spider", "junior"],
                 "year_min": 1967, "year_max": 1976, "lo": 12000, "hi": 250000},
        "maint": 2,
    },
    "Porsche Singer 911": {
        "url": "https://bringatrailer.com/porsche/singer/",
        "color": "#C9A227",
        "blurb": "Singer-reimagined 964 - a different market to everything else here.",
        "spec": {"lo": 250000, "hi": 3500000},
        "maint": 8,
    },
    # BaT files every Evora generation on one page, so "evora gt" alone also
    # catches the 2014 Evora GTS and two race cars (a GTN and a GT4 Cup). Those
    # three names plus a $60k floor leave only the 2020-21 US-market GT, whose
    # cheapest real sale was $73.5k. No driven series: not one has sold at 30k+
    # miles, so it falls back to all-comps with a dagger like the Dino.
    "Lotus Evora GT (2020-21)": {
        "url": "https://bringatrailer.com/lotus/evora/",
        "color": "#00915A",
        "blurb": "Final US Evora, 2020-2021 - supercharged V6, manual and auto.",
        "spec": {"include": ["evora gt"], "exclude": ["gts", "gtn", "gt4"],
                 "year_min": 2020, "year_max": 2021, "lo": 60000, "hi": 250000},
        "maint": 3,
    },
    "Volvo P1800 (1800 family)": {
        "url": "https://bringatrailer.com/volvo/1800/",
        "color": "#4A7C59",
        "blurb": "1800 family incl. the ES shooting brake.",
        "spec": {"lo": 8000, "hi": 150000},
        "maint": 2,
    },
    # BaT files the Dino under /ferrari/dino/ -- there is no 246-specific model
    # page. That page is unusually full of memorabilia (manuals, jacks, tool
    # kits, illuminated signs, a transaxle), all of which parse as SOLD. They
    # top out at $20,500 and no real car has sold below $200k, so the $100k
    # floor separates them cleanly. "206" cars are a rarer, different market and
    # fall out on the `246` include; the $1.1M V8-powered "Evo 3.6" restomod is
    # excluded by name because it is not a stock Dino at all.
    "Ferrari Dino 246 GT/GTS": {
        "url": "https://bringatrailer.com/ferrari/dino/",
        "color": "#E2703A",
        "blurb": "246 GT and GTS, 1969-1974 - the V6 junior Ferrari, badged Dino.",
        "spec": {"include": ["246"],
                 "exclude": ["v8-powered", "replica", "kit car"],
                 "year_min": 1969, "year_max": 1974,
                 "lo": 100000, "hi": 900000},
        "maint": 7,
    },
}


# CPI-U annual averages, needed because the BaT windows reach back further than
# the dashboard's own 2020-2026 `years` axis. Without this the 10-yr figures
# cannot be inflation-adjusted at all.
CPI_BY_YEAR = {2014: 236.7, 2015: 237.0, 2016: 240.0, 2017: 245.1, 2018: 251.1,
               2019: 255.7, 2020: 257.8, 2021: 271.7, 2022: 296.3, 2023: 305.1,
               2024: 314.2, 2025: 323.6, 2026: 337.2}


def collect(cfg):
    """Sold comps for one car, honouring any_of by merging passes on URL."""
    items, _ = fetch_all_completed(cfg["url"])
    alts = cfg.get("any_of") or [None]
    merged = {}
    for alt in alts:
        spec = dict(cfg["spec"])
        if alt:
            spec["include"] = list(spec.get("include", [])) + [alt]
        for s in parse_sold(items, spec):
            merged[s["url"]] = s
    all_sold = sorted(merged.values(), key=lambda s: s["ts"])

    driven = {}
    for alt in alts:
        spec = dict(cfg["spec"])
        spec["min_miles"] = DRIVEN_MILES
        if alt:
            spec["include"] = list(spec.get("include", [])) + [alt]
        for s in parse_sold(items, spec):
            driven[s["url"]] = s
    return all_sold, sorted(driven.values(), key=lambda s: s["ts"])


def annual_detail(sold, min_n=2):
    """Per-year median, sample size and interquartile band, in $000s.

    `annual_medians` gives only year/median/n. The band and the per-year n are
    what let a consumer see that a window's ENDPOINT rests on a thin sample --
    the 997.2's 2026 median moved 34 points on a single extra sale -- so both
    are published rather than recomputed downstream.
    """
    by = {}
    for s in sold:
        by.setdefault(s["date"][:4], []).append(s["price"])
    out = []
    for y, v in sorted(by.items()):
        if len(v) < min_n:
            continue
        v = sorted(v)
        q = lambda f: v[min(len(v) - 1, max(0, int(round(f * (len(v) - 1)))))]
        out.append({"year": int(y), "median": round(statistics.median(v) / 1000.0, 1),
                    "n": len(v), "lo": round(q(0.25) / 1000.0, 1), "hi": round(q(0.75) / 1000.0, 1)})
    return out


def to_hist(series, years):
    """Annual medians -> one value per dashboard year, in $000s.

    Missing years are linearly interpolated; years outside the observed range
    carry the nearest observation. Returns None if there is nothing to work with.
    """
    if not series:
        return None
    by = {p["year"]: p["median"] / 1000.0 for p in series}
    known = sorted(by)
    out = []
    for y in years:
        if y in by:
            out.append(round(by[y], 1))
            continue
        lo = [k for k in known if k < y]
        hi = [k for k in known if k > y]
        if lo and hi:
            a, b = lo[-1], hi[0]
            t = (y - a) / (b - a)
            out.append(round(by[a] + (by[b] - by[a]) * t, 1))
        else:
            out.append(round(by[known[0] if not lo else known[-1]], 1))
    return out


def windows(series):
    """Total and per-year appreciation over the 5y and 10y windows."""
    if len(series) < 2:
        return {}
    latest = series[-1]
    out = {}
    for win in (5, 10):
        target = latest["year"] - win
        base = next((p for p in series if p["year"] >= target), None)
        if not base or base["year"] >= latest["year"]:
            continue
        span = latest["year"] - base["year"]
        out[f"w{win}"] = {
            "from_year": base["year"], "from": round(base["median"] / 1000.0, 1),
            "to_year": latest["year"], "to": round(latest["median"] / 1000.0, 1),
            "total_pct": round((latest["median"] / base["median"] - 1) * 100, 1),
            "cagr_pct": cagr(base["median"], latest["median"], span),
            "span_years": span,
        }
    return out


# The Google Sheet pulls this with IMPORTDATA, which re-fetches on its own
# schedule - so the chart there tracks this CI without anyone touching it.
# One row per calendar year, one column per car; values are YEAR-OVER-YEAR
# percent change in the annual median. Blank where a year has too few sales to
# median honestly (annual_medians requires n>=2).
CSV_NAME = "appreciation.csv"
CSV_FIRST = ["Acura NSX (NA2 manual)", "Ferrari 360 (gated manual)",
             "Audi R8 gen1 V10 (gated)", "Porsche 997.2 Turbo S"]


def write_csv(series_by_car):
    ordered = [c for c in CSV_FIRST if series_by_car.get(c)]
    ordered += [c for c in series_by_car if c not in ordered and series_by_car.get(c)]
    years = sorted({p["year"] for c in ordered for p in series_by_car[c]})
    lines = ["Year," + ",".join(ordered)]
    for y in years:
        row = [str(y)]
        for c in ordered:
            pts = {p["year"]: p["median"] for p in series_by_car[c]}
            prev = pts.get(y - 1)
            row.append(f"{(pts[y] / prev - 1) * 100:.1f}" if (y in pts and prev) else "")
        lines.append(",".join(row))
    with open(os.path.join(ROOT, CSV_NAME), "w") as f:
        f.write("\n".join(lines) + "\n")
    return len(years), len(ordered)


def main():
    with open(DATA) as f:
        d = json.load(f)
    years, pyears = d["years"], d["pyears"]
    log = []
    series_by_car = {}

    for name, cfg in BAT_CARS.items():
        try:
            all_sold, driven = collect(cfg)
        except Exception as e:                      # keep last-good on any failure
            log.append(f"{name}: FAILED ({type(e).__name__})")
            continue
        if len(all_sold) < 8:
            log.append(f"{name}: only {len(all_sold)} comps, skipped")
            continue

        s_all = annual_medians(all_sold)
        series_by_car[name] = s_all
        s_drv = annual_medians(driven)
        hist = to_hist(s_all, years)
        if not hist:
            log.append(f"{name}: no usable annual medians")
            continue

        appr = windows(s_all)
        base_cagr = (appr.get("w5") or {}).get("cagr_pct")
        r = max(-0.06, min(0.10, (base_cagr or 0) / 100.0))   # damp to a sane band
        v0 = hist[-1]

        car = d["cars"].get(name, {})
        car.update({
            "color": cfg["color"],
            "blurb": cfg["blurb"],
            "hist": hist,
            "proj": [round(v0 * (1 + r) ** i, 1) for i in range(len(pyears))],
            "maint": cfg["maint"],
            "cagr": {"base": round(r, 4), "bull": round(r + 0.03, 4), "bear": round(r - 0.03, 4)},
            "sold": round(hist[-1]),
            "src": "bat",
            "n_comps": len(all_sold),
            "appr": appr,
            "annual": annual_detail(all_sold),
            "bat_url": cfg["url"],
        })
        if s_drv and len(driven) >= 5:
            car["driven"] = {
                "min_miles": DRIVEN_MILES,
                "n_comps": len(driven),
                "latest": round(s_drv[-1]["median"] / 1000.0, 1),
                "appr": windows(s_drv),
            }
        d["cars"][name] = car
        note = f"{name}: {len(all_sold)} comps"
        if car.get("driven"):
            note += f", {len(driven)} driven"
        log.append(note)

    d["cpi_by_year"] = {str(k): v for k, v in CPI_BY_YEAR.items()}
    ny, nc = write_csv(series_by_car)
    log.append(f"{CSV_NAME}: {ny}y x {nc} cars")
    d["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    d["bat_status"] = " | ".join(log)
    with open(DATA, "w") as f:
        json.dump(d, f, indent=1)
    print("\n".join(log))


if __name__ == "__main__":
    main()
