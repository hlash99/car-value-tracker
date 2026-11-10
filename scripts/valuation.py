#!/usr/bin/env python3
"""Mileage-standardised value estimate from Bring a Trailer sold comps.

The headline value used to be the plain median of the latest 3-7 sales. On
these cars the odometer moves a sale price more than a few months of market
drift does (a 5k-mile 550 Maranello and a 45k-mile one are ~2x apart), so that
median mostly reported WHICH cars happened to sell last: three garage queens in
a row read as a rally. This module prices every comp on a common mileage first.

Method (deliberately simple, every step checked by backtest()):

  1. Mileage elasticity g: slope of log(price) on log(miles + 5,000) WITHIN
     12-month bins counted back from the valuation date - so market drift
     between years cannot leak into it - shrunk toward a prior of -0.20 worth
     ~8 sales. A car whose odometer does not explain price (old restored cars)
     keeps a slope near the prior's small effect rather than a noisy fit.
  2. Each comp is moved to the target mileage: price x ((m_target + 5k) /
     (m_comp + 5k)) ** g. Comps with no stated mileage are used unadjusted.
  3. The estimate is the weighted MEDIAN of those, weight halving every 90 days
     of age. A median, so one outlier sale cannot drag it; no hard window, so a
     rarely-traded car leans on its latest sales however old they are - and
     says so through `n_eff` and `newest_days`.
  4. Spread: sigma is the weighted MAD (x1.4826) of the adjusted comps around
     the estimate, in log terms. `lo`/`hi` is an 80% band on the estimate
     itself (sigma x 1.2533 / sqrt(n_eff)); `sale_lo`/`sale_hi` is the 80%
     range for ONE car's hammer price at that mileage, scaled by SALE_K so the
     band's out-of-sample coverage is what it claims (see backtest()).

No trend extrapolation: adding a local price slope did not reduce the
out-of-sample error (2026-11 backtest), so it is left out.

Stdlib only. Imported by bat_refresh.py; never fetches anything.
"""
import math
import statistics

DAY = 86400.0
YEAR = 365.2425 * DAY
OFFSET = 5000.0          # miles added before the log, so 200 vs 2,000 mi is not a 10x gap
HALF_LIFE = 90.0         # days for a comp's weight to halve
PRIOR_G = -0.20          # prior mileage elasticity ...
PRIOR_N = 8.0            # ... worth about this many sales
Z80 = 1.2816             # 80% two-sided normal quantile
SALE_K = 1.05            # single-sale band multiplier: fat tails. Calibrated on 2021-24 (see backtest)
DRIFT = 0.30             # extra log-sd per year of average comp age (market drift)
MIN_COMPS = 5
MIN_NEFF = 3.0           # effective comps the weights must be worth ...
MAX_HALF_LIFE = 1500.0   # ... unless that takes a half-life beyond this (days)


def lm(miles):
    return math.log(miles + OFFSET)


def old_rule(sold, now, mx=7, mn=3, days=90):
    """The previous headline: median of the latest <=7 sales inside 90 days,
    else the latest 3. Kept as the baseline every backtest is scored against."""
    if len(sold) < mn:
        return None
    pool = [s for s in sold[-mx:] if now - s["ts"] <= days * DAY]
    if len(pool) < mn:
        pool = sold[-mn:]
    return statistics.median(s["price"] for s in pool)


def elasticity(sold, now):
    """Shrunk within-12-month-bin slope of log price on log(miles + OFFSET)."""
    bins = {}
    for s in sold:
        if s.get("odo") is None:
            continue
        bins.setdefault(int((now - s["ts"]) // YEAR), []).append(s)
    sxx = sxy = 0.0
    for g in bins.values():
        if len(g) < 3:
            continue
        xs = [lm(s["odo"]) for s in g]
        ys = [math.log(s["price"]) for s in g]
        xm, ym = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx += sum((x - xm) ** 2 for x in xs)
        sxy += sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    k = PRIOR_N * 0.5                 # prior pseudo-sales at a typical x-variance of 0.5
    return (sxy + k * PRIOR_G) / (sxx + k)


def wquantile(pairs, q):
    """Weighted quantile of (value, weight) pairs, linear between mid-weights."""
    pairs = sorted(pairs)
    tot = sum(w for _, w in pairs)
    if tot <= 0:
        return None
    acc, pts = 0.0, []
    for v, w in pairs:
        pts.append(((acc + w / 2) / tot, v))
        acc += w
    if q <= pts[0][0]:
        return pts[0][1]
    for (c0, v0), (c1, v1) in zip(pts, pts[1:]):
        if q <= c1:
            return v0 + (v1 - v0) * ((q - c0) / (c1 - c0) if c1 > c0 else 0.0)
    return pts[-1][1]


def ref_miles(sold, floor=None):
    """Mileage the headline is quoted at: the median odometer of the car's own
    comps (of those at or above `floor`, for a car valued on a mileage floor)."""
    odos = [s["odo"] for s in sold if s.get("odo") is not None and (not floor or s["odo"] >= floor)]
    if not odos:
        return floor
    m = statistics.median(odos)
    return int(round(m, -2)) if m >= 1000 else int(round(m, -1))


def estimate(sold, now, miles=None, gamma=None):
    """Value of this car at `miles` as of `now` (epoch seconds), from `sold`
    (dicts with ts, price, odo), all of which must predate `now`. Returns None
    with fewer than MIN_COMPS comps. Dollar fields are in whole dollars."""
    sold = [s for s in sold if s["ts"] <= now]
    if len(sold) < MIN_COMPS:
        return None
    g = elasticity(sold, now) if gamma is None else gamma
    if miles is None:
        miles = ref_miles(sold)
    adj, newest = [], None
    for s in sold:
        age = (now - s["ts"]) / DAY
        newest = age if newest is None else min(newest, age)
        f = g * (lm(miles) - lm(s["odo"])) if (miles is not None and s.get("odo") is not None) else 0.0
        adj.append((math.log(s["price"]) + f, age))
    # A rarely-traded car has one sale inside 90 days and the weighted median
    # collapses onto it (the old rule's "three is the floor" lesson). Stretch
    # the half-life until the weights are worth MIN_NEFF sales.
    hl = HALF_LIFE
    while True:
        pairs = [(v, 0.5 ** (age / hl)) for v, age in adj]
        sw = sum(w for _, w in pairs)
        n_eff = sw * sw / sum(w * w for _, w in pairs)
        if n_eff >= MIN_NEFF or hl >= MAX_HALF_LIFE:
            break
        hl *= 1.5
    wage = sum(w * age for (_, w), (_, age) in zip(pairs, adj))
    mu = wquantile(pairs, 0.5)
    mad = wquantile([(abs(v - mu), w) for v, w in pairs], 0.5)
    # With a handful of effective comps the local MAD is itself noise (two
    # near-identical sales read as "no spread"), so blend it with the car's
    # long-run dispersion, which needs no recency.
    all_mad = statistics.median(abs(v - mu2) for v, mu2 in _detrended(sold, g, miles))
    sig_loc, sig_all = 1.4826 * mad, 1.4826 * all_mad
    lam = n_eff / (n_eff + 6.0)
    sigma = math.sqrt(lam * sig_loc ** 2 + (1 - lam) * sig_all ** 2)
    # Old comps say less about today: the market has had time to move since.
    age_yr = wage / sw / 365.2425
    se = math.sqrt((1.2533 * sigma) ** 2 / n_eff + (DRIFT * age_yr) ** 2)
    sd_sale = SALE_K * math.sqrt(sigma ** 2 + se ** 2)
    return {
        "value": math.exp(mu), "miles": miles, "gamma": g,
        "n": len(sold), "n_eff": n_eff, "newest_days": newest, "age_days": wage / sw, "half_life": hl,
        "sigma": sigma, "se": se,
        "lo": math.exp(mu - Z80 * se), "hi": math.exp(mu + Z80 * se),
        "sale_lo": math.exp(mu - Z80 * sd_sale), "sale_hi": math.exp(mu + Z80 * sd_sale),
    }


def _detrended(sold, g, miles):
    """(adjusted log price, median of its own 12-month neighbours) per comp:
    long-run dispersion around a moving level, for sigma's fallback."""
    adj = []
    for s in sold:
        f = g * (lm(miles) - lm(s["odo"])) if (miles is not None and s.get("odo") is not None) else 0.0
        adj.append((s["ts"], math.log(s["price"]) + f))
    out = []
    for t, v in adj:
        near = [x for tt, x in adj if abs(tt - t) <= YEAR / 2]
        out.append((v, statistics.median(near)))
    return out


def dispersion(sold, gamma=0.0, miles=None):
    """Long-run log dispersion of a car's sale prices around their own
    12-month neighbourhood median (robust: MAD x 1.4826). With gamma=0 it is
    the spread of RAW prices - what a pooled median's sampling error rests on."""
    if len(sold) < 3:
        return None
    return 1.4826 * statistics.median(abs(v - m) for v, m in _detrended(sold, gamma, miles))


def median_ci(n_from, n_to, sigma, z=Z80):
    """Half-width, in log terms, of the 80% interval on the RATIO of two pooled
    medians of n_from and n_to sales: each median's standard error is
    1.2533 x sigma / sqrt(n). A resampling bootstrap gives much the same answer
    on big pools but collapses on a 2- or 3-sale pool; this does not."""
    if not sigma or not n_from or not n_to:
        return None
    return z * 1.2533 * sigma * math.sqrt(1.0 / n_from + 1.0 / n_to)


def adjusted_price(s, gamma, miles):
    """One comp's price moved to `miles` (unchanged if its odometer is unknown)."""
    if miles is None or s.get("odo") is None:
        return s["price"]
    return s["price"] * math.exp(gamma * (lm(miles) - lm(s["odo"])))


def at_miles(est, miles):
    """Re-quote an estimate() result at another mileage (same comps, same g)."""
    k = math.exp(est["gamma"] * (lm(miles) - lm(est["miles"])))
    return est["value"] * k


def backtest(sales_by_car, start_ts, min_prior=8):
    """Walk forward: predict every sale on/after start_ts from the sales before
    it. Scores the old rule, this estimator given the sold car's mileage, and
    this estimator blind (at the reference mileage - the headline's own job).
    Returns typical (median) and mean absolute % miss for each, plus how often
    the 80% single-sale band actually held the price."""
    e_old, e_new, e_blind, cover, n = [], [], [], 0, 0
    for sold in sales_by_car.values():
        sold = sorted(sold, key=lambda s: s["ts"])
        for i, s in enumerate(sold):
            if s["ts"] < start_ts or i < min_prior:
                continue
            prior = sold[:i]
            old = old_rule(prior, s["ts"])
            est = estimate(prior, s["ts"], s.get("odo"))
            blind = estimate(prior, s["ts"], None, gamma=est["gamma"]) if est else None
            if not (old and est and blind):
                continue
            n += 1
            e_old.append(abs(math.log(old / s["price"])))
            e_new.append(abs(math.log(est["value"] / s["price"])))
            e_blind.append(abs(math.log(blind["value"] / s["price"])))
            cover += est["sale_lo"] <= s["price"] <= est["sale_hi"]
    if not n:
        return None
    pc = lambda v, f: round(100 * (math.exp(f(v)) - 1), 1)
    return {"n": n,
            "old_median_pct": pc(e_old, statistics.median), "old_mean_pct": pc(e_old, statistics.mean),
            "new_median_pct": pc(e_new, statistics.median), "new_mean_pct": pc(e_new, statistics.mean),
            "blind_median_pct": pc(e_blind, statistics.median), "blind_mean_pct": pc(e_blind, statistics.mean),
            "band_cover_pct": round(100.0 * cover / n, 1)}
