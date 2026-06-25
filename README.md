# Exotic Car Value Tracker

Interactive tracker of median values for five collector cars — Ferrari 458 Italia, F12 Berlinetta,
812 Superfast, 812 GTS, and Porsche 997.2 Turbo S — over a 6-year history (2020–2026) with a
5-year projection (2026–2031), benchmarked against the S&P 500, Apple, and U.S. inflation.

**Live site:** https://hlash99.github.io/car-value-tracker/

## Features
- Toggle nominal vs inflation-adjusted, bear/base/bull projection scenarios, and maintenance-adjusted returns.
- Interactive "when does the 997 equal the 812?" crossover calculator.
- Year-by-year tables, evidence table, and reference assets.

## Data & auto-refresh
- `data.json` holds all values. 2026 figures are Cars.com nationwide asking medians cross-checked
  against recent Bring a Trailer sold results; the adopted value leans to sold/transaction levels.
- `.github/workflows/refresh.yml` runs `scripts/refresh.py` twice daily (and on demand) to re-scrape
  Cars.com asking medians and update `data.json`. It is **best-effort**: if a source blocks the CI
  runner, each car keeps its last-good value and the status line notes it.
- 2020 baselines and intermediate years are reconstructed estimates; treat 6-year % as ±5–8 pts.

Not investment advice.
