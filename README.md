# Exotic Car Value Tracker

Interactive tracker of median values for a set of collector and enthusiast cars, benchmarked against
the S&P 500, Apple, and U.S. inflation, with a 6-year history (2020–2026) and a 5-year projection
(2026–2031). It started with five cars (Ferrari 458 Italia, F12 Berlinetta, 812 Superfast, 812 GTS
and Porsche 997.2 Turbo S); Bring a Trailer–tracked cars have been added since, most recently a
**used Chevrolet Corvette C8 Z06** (30k+ miles, no Z07 package, no high wing). The page counts the
cars from `data.json`, so no number is hard-coded here.

**Live site:** https://hlash99.github.io/car-value-tracker/

## Features
- Toggle nominal vs inflation-adjusted, bear/base/bull projection scenarios, and maintenance-adjusted returns.
- Interactive "when does the 997 equal the 812?" crossover calculator.
- Year-by-year tables, evidence table, and reference assets.

## Data & auto-refresh
- `data.json` holds all values. The original cars' 2026 figures are Cars.com nationwide asking medians
  cross-checked against recent Bring a Trailer sold results; the adopted value leans to sold/transaction
  levels. The other cars are Bring a Trailer sold medians (`scripts/bat_refresh.py`).
- `.github/workflows/refresh.yml` runs `scripts/refresh.py` (Cars.com), `scripts/bat_refresh.py`
  (BaT sold) and `scripts/bench_refresh.py` (AAPL / S&P total return) twice daily and on demand. It is
  **best-effort**: if a source blocks the CI runner, each car keeps its last-good value and the status
  line notes it.
- **Today's value** (`scripts/valuation.py`): every BaT comp is first moved to one mileage with the
  car's own mileage elasticity, then a median weighted to the newest sales (90-day half-life) is taken.
  Published per car as `est` (value, 80% band, the range one car sells within, reference mileage,
  elasticity) and summarised in top-level `valuation`. It is the headline only while it wins a
  walk-forward backtest that `bat_refresh.py` re-runs on every refresh (each of the last three years'
  sales predicted from the sales before it); otherwise the page falls back to the plain median of the
  latest sales by itself. Nov 2026: typical miss 14.8% vs 19.2%. The C8 Z06 keeps its own path below.
- **Return windows** carry an 80% interval (`ci_lo_pct` / `ci_hi_pct`, `ratio_lo` / `ratio_hi`) and a
  same-mileage figure (`cagr_adj_pct`), so a pool of three sales cannot pass for a trend.
- 2020 baselines and intermediate years are reconstructed estimates; treat 6-year % as ±5–8 pts.
- **Corvette C8 Z06**: launched as MY2023, so 2020–22 are blank rather than invented. Values are BaT
  sold medians for non-Z07, no-wing cars (Z07 / high wing checked on each listing page, cached in
  `scripts/listing_flags_cache.json`) × 0.82 to a 30k-mile basis, because no 30k+ Z06 has sold on BaT yet.
  The adopted value is half that BaT figure and half asking evidence: Cars.com asks plus CorvetteForum
  C8 classifieds asks (`scripts/forum_asks.py`; wanted/sold/new, Z07, aero/wing and Florida cars
  skipped), each scaled to 30k miles and × 0.86 for ask-to-sold.
  Its projection is depreciation (base −6%/yr, bear −10%, bull −2%); see the page's methodology note.

Not investment advice.
