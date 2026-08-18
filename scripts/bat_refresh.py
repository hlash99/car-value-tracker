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
    "Volvo P1800 (1800 family)": {
        "url": "https://bringatrailer.com/volvo/1800/",
        "color": "#4A7C59",
        "blurb": "1800 family incl. the ES shooting brake.",
        "spec": {"lo": 8000, "hi": 150000},
        "maint": 2,
    },
}


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


def main():
    with open(DATA) as f:
        d = json.load(f)
    years, pyears = d["years"], d["pyears"]
    log = []

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

    d["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    d["bat_status"] = " | ".join(log)
    with open(DATA, "w") as f:
        json.dump(d, f, indent=1)
    print("\n".join(log))


if __name__ == "__main__":
    main()
