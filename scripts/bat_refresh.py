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
        "spec": {"include": ["6-speed"], "exclude": ["challenge stradale", "conversion"],
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
    # APPEND new cars here, never insert. CSV_FIRST pins only the first four
    # columns (L-O); every car after that takes its appreciation.csv column from
    # this dict's order, and the WEEKEND CAR VERDICT sheet charts R2:R14 -- the
    # Alfa GTV -- by position, not by name. Inserting above it silently repoints
    # that series at whatever lands in R instead.
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
    # Every Emira that has sold on BaT so far is the V6 (supercharged Toyota 2GR),
    # almost all of them First Edition 6-speeds. Three of those titles omit "V6",
    # so an include on it would drop real cars — the i4 / Turbo SE is excluded by
    # name instead, as a guard for when those start trading. Only two years of
    # price history, so isThin() flags it until 2027; that is honest for a car
    # this new and is exactly what the guard is for.
    "Lotus Emira V6 (2024-25)": {
        "url": "https://bringatrailer.com/lotus/emira/",
        "color": "#0FA3A3",
        "blurb": "Supercharged Toyota V6, six-speed - the last analogue Lotus.",
        "spec": {"exclude": ["i4", "turbo se"],
                 "year_min": 2023, "year_max": 2026, "lo": 50000, "hi": 250000},
        "maint": 3,
    },
    # Every 550 Maranello was a gated six-speed - the F1 box arrived with the
    # 575M in 2002 - so no transmission include is needed. The model page is
    # mostly Schedoni luggage, wheels, engines and exhausts, which all parse as
    # SOLD and top out at $39k against a $83k cheapest real car, hence the $60k
    # floor. The Barchetta shares the page but is a 448-car roadster trading at
    # 3-5x, so it is excluded by name. Kilometer-odometer cars stay in all-comps
    # and drop out of the driven series, same as every other car here.
    "Ferrari 550 Maranello": {
        "url": "https://bringatrailer.com/ferrari/550-maranello/",
        "color": "#7A8490",
        "blurb": "1996-2001 front-engined V12 coupe, gated six-speed - every one a manual.",
        "spec": {"include": ["550 maranello"], "exclude": ["barchetta", "575"],
                 "year_min": 1996, "year_max": 2001, "lo": 60000, "hi": 900000},
        "maint": 6,
    },
    # F355: manual only, like the 360. BaT titles every manual "6-Speed"; the
    # untitled late cars are nearly all F1, so 6-speed is required and F1 is
    # excluded by name. Challenge race cars (and their seats, wheels and signs)
    # share the page and are excluded, as are luggage/exhausts via the $60k floor.
    # One manual coupe is titled "355 GTB" rather than Berlinetta, hence any_of.
    # The soft-top Spider is left out: he asked for the coupe and the targa.
    # "6-Speed Conversion" = an F1 car converted to manual after the fact - not a
    # factory gated car, and it trades differently, so it is excluded here and
    # on the 360, matching the NSX and R8 filters.
    "Ferrari F355 Berlinetta (gated manual)": {
        "url": "https://bringatrailer.com/ferrari/f355/",
        "color": "#E8B100",
        "blurb": "Gated six-speed coupe, 1995-1999 - the F1 paddle cars are a different market.",
        "spec": {"include": ["6-speed"], "exclude": ["challenge", "spider", "gts", "f1", "conversion"],
                 "year_min": 1995, "year_max": 1999, "lo": 60000, "hi": 600000},
        "any_of": ["berlinetta", "355 gtb"],
        "maint": 7,
    },
    "Ferrari F355 GTS (gated manual)": {
        "url": "https://bringatrailer.com/ferrari/f355/",
        "color": "#A0522D",
        "blurb": "Targa-top GTS, gated six-speed, 1995-1999 - the lift-out roof, not the Spider.",
        "spec": {"include": ["gts", "6-speed"], "exclude": ["challenge", "spider", "f1", "conversion"],
                 "year_min": 1995, "year_max": 1999, "lo": 60000, "hi": 600000},
        "maint": 7,
    },
}


# Production figures, scoped to the SAME spec as each car's filter - the 360 is
# the gated-manual car, so its figures are gated-manual counts, not all 360s.
#
# Each car carries a worldwide figure and/or a North American one, plus its last
# model year. The page picks between them on the US 25-year import rule: once
# every model year is 25+ years old any example can be brought in, so the
# worldwide pool is the relevant one; before that only cars built for North
# America can be registered here, so the N. American count is. The switch
# happens by itself in last_my + 25. Where the preferred figure does not exist
# the page falls back to the other and says so. A leading "~" marks unofficial
# counts (chassis registers, VIN or dealer-data compilations). Applied to EVERY
# car in data.json. Absent: the Emira (in production, no audited total).
PRODUCTION = {
    "Corvette split-window (1963)": {
        "last_my": 1963,
        "world": {
            "label": "10,594 built",
            "detail": "1963 split-window coupes, body numbers 00001-10594. The 10,919 convertibles that made up the rest of the 21,513 total are excluded, as in the price data.",
            "src": "Corvette Action Center",
            "url": "https://www.corvetteactioncenter.com/c2-corvette-news/ebay-the-very-last-1963-corvette-split-window-coupe-built-is-for-sale/"
        },
        "na": None
    },
    "Ferrari 328 GTS/GTB": {
        "last_my": 1989,
        "world": {
            "label": "7,412 built",
            "detail": "6,068 GTS + 1,344 GTB, 1985-1989. One chassis register puts the GTS lower (3,067-4,979); 6,068 is the figure 308-328.com and most references use.",
            "src": "Wikipedia - Ferrari 328",
            "url": "https://en.wikipedia.org/wiki/Ferrari_328"
        },
        "na": None
    },
    "Ferrari Dino 246 GT/GTS": {
        "last_my": 1974,
        "world": {
            "label": "3,761 built",
            "detail": "2,487 GT (357 L + 506 M + 1,624 E series) + 1,274 GTS, 1969-1974. The f-register chassis list and Bonhams catalogue notes agree on 1,624 E-series GTs; the 3,569 on Wikipedia counts only 1,431.",
            "src": "f-register.com; Bonhams",
            "url": "https://f-register.com/About-the-Cars/Production-Numbers"
        },
        "na": None
    },
    "Ferrari 550 Maranello": {
        "last_my": 2001,
        "world": {
            "label": "3,083 built",
            "detail": "550 Maranello coupes, 1996-2001. The 448 Barchettas are counted separately and excluded here, as in the price data. A chassis register counts 3,735 including 33 WSR editions; RM Sotheby's catalogues use roughly 3,000-3,083.",
            "src": "Wikipedia - Ferrari 550",
            "url": "https://en.wikipedia.org/wiki/Ferrari_550"
        },
        "na": None
    },
    "Volvo P1800 (1800 family)": {
        "last_my": 1973,
        "world": {
            "label": "~47,500 built",
            "detail": "39,407 coupes (P1800 / 1800S / 1800E) + 8,077 1800ES, 1961-1973. The quoted total of 47,492 is 8 more than those parts sum to.",
            "src": "Wikipedia - Volvo P1800",
            "url": "https://en.wikipedia.org/wiki/Volvo_P1800"
        },
        "na": None
    },
    "Alfa Romeo GTV 1750/2000": {
        "last_my": 1976,
        "world": {
            "label": "81,728 built",
            "detail": "44,269 1750 GTV (1967-72) + 37,459 2000 GTV (1971-76), all markets.",
            "src": "Wikipedia, citing carsfromitaly.net",
            "url": "https://en.wikipedia.org/wiki/Alfa_Romeo_105/115_Series_Coup%C3%A9s"
        },
        "na": None
    },
    "Porsche Singer 911": {
        "last_my": 1994,
        "world": {
            "label": "300+ built, ongoing",
            "detail": "Singer completed its 300th reimagined 911 in February 2024 and is still building. No later total has been published. Every Singer is built on a 1989-94 964 and titled by that year, so all are past the 25-year line.",
            "src": "Singer Vehicle Design",
            "url": "https://singervehicledesign.com/press/singer-celebrates-300th-restoration-in-california/"
        },
        "na": None
    },
    "Acura NSX (NA2 manual)": {
        "last_my": 2001,
        "world": None,
        "na": {
            "label": "1,173 US manuals",
            "detail": "US-market NA2 six-speeds, 1997-2001 (309 / 230 / 215 incl. 51 Zanardi / 263 / 156 by year); 81 automatics on top, 1,254 in all. No worldwide NA2 or manual split is published - Honda's only worldwide figure is 18,734 NSXs of every kind, 1990-2005.",
            "src": "Ben Lin's US production data (NSX Prime chart)",
            "url": "https://www.nsxprime.com/threads/revised-nsx-production-charts-na1-91-96-na2-97-01-facelift-02-05.218479/"
        }
    },
    "Ferrari 360 (gated manual)": {
        "last_my": 2005,
        "world": None,
        "na": {
            "label": "1,139 US manuals",
            "detail": "US-market gated six-speeds: 469 Modena + 670 Spider, out of 4,199 US cars and 16,365 worldwide (Challenge Stradale excluded). No worldwide manual count is published. The f-register chassis list counts 2,115 manual Spiders worldwide (of 7,565) but has no manual split for coupes.",
            "src": "Sports Car Market, Mar 2013 (via Wikipedia)",
            "url": "https://en.wikipedia.org/wiki/Ferrari_360"
        }
    },
    "Lotus Evora GT (2020-21)": {
        "last_my": 2021,
        "world": None,
        "na": {
            "label": "722 to the US",
            "detail": "374 MY2020 + 348 MY2021 US cars, 535 of them manual. Owner-reported from Lotus Certificates of Provenance, not an official Lotus release. The Evora GT name was North America only, so this is effectively its whole production.",
            "src": "LotusTalk owner thread",
            "url": "https://www.lotustalk.com/threads/my-certificate-of-provenance-arrives-and-2021-evora-total-build-counts.486380/"
        }
    },
    "Audi R8 gen1 V10 (gated)": {
        "last_my": 2015,
        "world": None,
        "na": {
            "label": "~743 to the US",
            "detail": "US gated six-speed V10 coupes: 717 V10 (12 MY09, 208 MY10, 199 MY11, 212 MY12, 51 MY14, 35 MY15) + 26 V10 plus. Compiled from Audi of America data by R. N. Labas, who notes about 10 cars may sit in the wrong year. No worldwide manual count exists; Audi built just over 26,000 first-gen R8s of every kind.",
            "src": "R. N. Labas R8 V10 register (Audi of America data)",
            "url": "https://www.r8talk.com/threads/production-numbers-for-us-manual-transmission-v8s-v10s.129153/"
        }
    },
    "Porsche 997.2 Turbo S": {
        "last_my": 2013,
        "world": {
            "label": "3,095 coupes",
            "detail": "997.2 Turbo S coupes, MY2011-2013, worldwide. No North American coupe split survives: PCNA data put the North American total at 2,333 Turbo S of both bodies, and 222 of the MY2013 coupes. There was no 997.1 Turbo S; the '2,000' sometimes quoted was a launch-era figure the factory-archive count does not support.",
            "src": "Marc Bongers, Porsche Serienfahrzeuge (ex-Porsche archive); Streather",
            "url": "https://rennlist.com/forums/997-turbo-forum/848248-so-when-do-the-997-turbo-s-begin-to-appreciate-2.html"
        },
        "na": None
    },
    "Ferrari 458 Italia": {
        "last_my": 2015,
        "world": {
            "label": "~11,856 built",
            "detail": "458 Italia coupes, 2009-2015; Spider and Speciale are separate. An earlier count by the same register, quoted by Forza, was 9,944. Ferrari does not publish model totals; this is a count of chassis numbers. Ferrari North America has never published US numbers by model, and NHTSA recall filings give only multi-model totals.",
            "src": "f-register.com production list (Matthias Urban)",
            "url": "https://f-register.com/About-the-Cars/Production-Numbers"
        },
        "na": None
    },
    "Ferrari F12 Berlinetta": {
        "last_my": 2017,
        "world": {
            "label": "~4,802 built",
            "detail": "F12berlinetta, 2012-2017; the 799 F12tdf are separate. Ferrari does not publish model totals; this is a count of chassis numbers. Ferrari North America has never published US numbers by model, and NHTSA recall filings give only multi-model totals.",
            "src": "f-register.com production list (Matthias Urban)",
            "url": "https://f-register.com/About-the-Cars/Production-Numbers"
        },
        "na": None
    },
    "Ferrari 812 Superfast": {
        "last_my": 2023,
        "world": {
            "label": "~5,124 built",
            "detail": "812 Superfast, 2017-2023; the Competizione (999 official) and Competizione A (599 official) are separate. Ferrari does not publish model totals; this is a count of chassis numbers. Ferrari North America has never published US numbers by model, and NHTSA recall filings give only multi-model totals.",
            "src": "f-register.com production list (Matthias Urban)",
            "url": "https://f-register.com/About-the-Cars/Production-Numbers"
        },
        "na": None
    },
    "Ferrari 812 GTS": {
        "last_my": 2023,
        "world": {
            "label": "~5,348 built",
            "detail": "812 GTS, 2019-2022. Ferrari does not publish model totals; this is a count of chassis numbers. Ferrari North America has never published US numbers by model, and NHTSA recall filings give only multi-model totals.",
            "src": "f-register.com production list (Matthias Urban)",
            "url": "https://f-register.com/About-the-Cars/Production-Numbers"
        },
        "na": None
    },
    "Ferrari F355 Berlinetta (gated manual)": {
        "last_my": 1999,
        "na": None,
        "world": {
            "label": "~3,822 built",
            "detail": "Gated-manual F355 Berlinettas, 1994-1999, worldwide: 3,931 manual chassis in the register less the 109 Challenge conversions. Another 1,049 were F1. Totals reconcile within ~2% of the 4,871 Berlinettas usually quoted.",
            "src": "f-register.com production list (Matthias Urban)",
            "url": "https://f-register.com/About-the-Cars/Production-Numbers"
        }
    },
    "Ferrari F355 GTS (gated manual)": {
        "last_my": 1999,
        "na": None,
        "world": {
            "label": "~2,003 built",
            "detail": "Gated-manual F355 GTS targas, 1994-1998, worldwide; another 526 were F1. Totals reconcile within ~2% of the 2,577 GTS usually quoted.",
            "src": "f-register.com production list (Matthias Urban)",
            "url": "https://f-register.com/About-the-Cars/Production-Numbers"
        }
    }
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


# Sales needed at EACH end of a window before the page stops flagging it "thin".
THIN_POOL = 5


def _frac_year(s):
    """Sale date as a fractional year, e.g. mid-2025 -> 2025.5."""
    return 1970 + s["ts"] / (365.2425 * 86400)


def _cpi_at(t):
    """CPI-U at a fractional year. The table holds annual averages, which sit
    at mid-year, so interpolate between mid-years and hold flat past the ends."""
    ys = sorted(CPI_BY_YEAR)
    x = t - 0.5
    if x <= ys[0]:
        return CPI_BY_YEAR[ys[0]]
    if x >= ys[-1]:
        return CPI_BY_YEAR[ys[-1]]
    y0 = int(x)
    f = x - y0
    return CPI_BY_YEAR[y0] + (CPI_BY_YEAR[y0 + 1] - CPI_BY_YEAR[y0]) * f


def windows(sold):
    """Total and per-year appreciation over the 5y and 10y windows, from POOLED
    two-year ends rather than single calendar years.

    Comparing one year's median with another's let 3-5 sales set a car's whole
    5-yr figure: the F355 Berlinetta read +89% off a 2026 median of three cars
    (one a 17k-mile $355k outlier) while its 2021-22 -> 2025-26 pooled median
    was flat. Each end is now the median of a two-calendar-year pool - the latest
    two years, and the two years ~5 (or ~10) before them, moving later if that
    pool is empty, as before. The span is measured between the pools' average
    SALE DATES, so a part-year at the end does not flatter the CAGR, and CPI is
    read at those same dates so the page can deflate exactly. n_from / n_to are
    published so the page can flag a window resting on few sales."""
    if not sold:
        return {}
    L = max(int(s["date"][:4]) for s in sold)
    pool = lambda y0, y1: [s for s in sold if y0 <= int(s["date"][:4]) <= y1]
    top = pool(L - 1, L)
    if len(top) < 2:
        return {}
    to_med = statistics.median(s["price"] for s in top)
    t1 = statistics.mean(_frac_year(s) for s in top)
    out = {}
    for win in (5, 10):
        base = None
        for y0 in range(L - 1 - win, L - 2):      # base pool must end before the top pool starts
            p = pool(y0, y0 + 1)
            if len(p) >= 2:
                base = (y0, p)
                break
        if not base:
            continue
        y0, p = base
        from_med = statistics.median(s["price"] for s in p)
        t0 = statistics.mean(_frac_year(s) for s in p)
        span = round(t1 - t0, 1)
        if span < 1:
            continue
        out[f"w{win}"] = {
            "from_year": y0, "to_year": L,
            "from_label": f"{y0}-{str(y0 + 1)[2:]}", "to_label": f"{L - 1}-{str(L)[2:]}",
            "from": round(from_med / 1000.0, 1), "to": round(to_med / 1000.0, 1),
            "n_from": len(p), "n_to": len(top),
            "total_pct": round((to_med / from_med - 1) * 100, 1),
            "cagr_pct": cagr(from_med, to_med, span),
            "span_years": span,
            "cpi_from": round(_cpi_at(t0), 1), "cpi_to": round(_cpi_at(t1), 1),
            # Average sale dates of each pool - the page measures AAPL / S&P
            # over exactly these dates so the comparison is like for like.
            "t_from": round(t0, 3), "t_to": round(t1, 3),
            "pooled": True,
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

        appr = windows(all_sold)
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
        # A driven series also needs two usable years to form a window. The 550
        # has five 30k+ sales but only one year with n>=2, and an empty `appr`
        # is truthy in the page JS - it showed blank cells instead of falling
        # back to all comps with the dagger. Drop any stale block too, so a car
        # that loses its driven window falls back rather than keeping old numbers.
        drv_appr = windows(driven)
        if len(driven) >= 5 and drv_appr:
            car["driven"] = {
                "min_miles": DRIVEN_MILES,
                "n_comps": len(driven),
                "latest": round(s_drv[-1]["median"] / 1000.0, 1),
                "appr": drv_appr,
            }
        else:
            car.pop("driven", None)
        d["cars"][name] = car
        note = f"{name}: {len(all_sold)} comps"
        if car.get("driven"):
            note += f", {len(driven)} driven"
        log.append(note)

    for name, car in d["cars"].items():
        if name in PRODUCTION:
            car["production"] = PRODUCTION[name]
        else:
            car.pop("production", None)

    # A car whose scrape failed this run keeps its CSV column, rebuilt from the
    # per-year medians already in data.json. Without this a single BaT timeout
    # dropped the column and shifted every later car left - and the WEEKEND CAR
    # VERDICT sheet charts these columns by POSITION, so its series silently
    # repointed at the wrong car. Columns are always emitted in BAT_CARS order.
    csv_series = {}
    for name in BAT_CARS:
        if series_by_car.get(name):
            csv_series[name] = series_by_car[name]
        elif d["cars"].get(name, {}).get("annual"):
            csv_series[name] = [{"year": p["year"], "median": p["median"] * 1000}
                                for p in d["cars"][name]["annual"]]
            log.append(f"{name}: CSV column kept from last-good data")

    d["cpi_by_year"] = {str(k): v for k, v in CPI_BY_YEAR.items()}
    ny, nc = write_csv(csv_series)
    log.append(f"{CSV_NAME}: {ny}y x {nc} cars")
    d["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    d["bat_status"] = " | ".join(log)
    with open(DATA, "w") as f:
        json.dump(d, f, indent=1)
    print("\n".join(log))


if __name__ == "__main__":
    main()
