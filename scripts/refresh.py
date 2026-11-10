#!/usr/bin/env python3
"""Twice-daily refresh: scrape Cars.com nationwide asking medians for each model,
recompute the adopted market value (asking x per-car factor), and update data.json.

Best-effort and resilient: if a source blocks the CI runner or returns too few
prices, that car keeps its last-good value. Run by .github/workflows/refresh.yml.
"""
import json, os, re, statistics, sys, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data.json")

# Plausible asking-price window per car ($000s) to reject mislabeled / new-car / outlier listings.
WINDOW = {
    "Ferrari 458 Italia": (150, 650),
    "Ferrari F12 Berlinetta": (200, 850),
    "Ferrari 812 Superfast": (300, 850),
    "Ferrari 812 GTS": (550, 1300),
}
PRICE_RE = re.compile(r"\$([0-9]{2,3},[0-9]{3})")

# BaT-valued cars that also carry a Cars.com ASKING median for the evidence
# table. Only `asking` / `asking_src` are written - the value, history and
# projection stay with scripts/bat_refresh.py. These need a mileage floor, which
# Cars.com's URL cannot express (it has a max, not a min), so the search sorts by
# mileage descending and the floor is applied to each listing's own odometer,
# read from the page's embedded srp_results JSON. Cars.com cannot filter Z07 or
# the high wing (dealers rarely list them), so the asking pool is all Z06s.
# `asking` (the evidence column) is the 30k+ median; `asking_pts` keeps every
# listing on the page for the value blend in bat_refresh.py.
ASKING_ONLY = {
    "Chevrolet Corvette C8 Z06 (no Z07/wing)": {
        "url": ("https://www.cars.com/shopping/results/?stock_type=used&makes[]=chevrolet"
                "&models[]=chevrolet-corvette&year_min=2023&keyword=Z06&sort=mileage_desc"
                "&maximum_distance=all&zip=94027&page_size=100"),
        "window": (60, 250),
        "min_miles": 30000,
    },
}
SRP_RE = re.compile(r"<script[^>]*>\s*(\{\"srp_results\".*?)</script>", re.S)


def listings_from_srp(html):
    """(price $000s, mileage) per listing card from Cars.com's embedded JSON."""
    m = SRP_RE.search(html)
    if not m:
        return []
    out = []
    for x in json.loads(m.group(1))["srp_results"].get("results", []):
        if x.get("__typename") != "SrpListingGridCard":
            continue
        try:
            c = json.loads(x["analytics"]["context"])
            out.append((int(c["price"]) / 1000.0, int(c["mileage"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out

def prices_from_html(html, lo, hi):
    out = []
    for m in PRICE_RE.findall(html):
        v = int(m.replace(",", "")) / 1000.0
        if lo <= v <= hi:
            out.append(v)
    return out

def scrape(page, url, lo, hi):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector("[data-test='vehicleCard'], .vehicle-card, [class*='listing']", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(2500)
    return prices_from_html(page.content(), lo, hi)

def now_s():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def main():
    with open(DATA) as f:
        d = json.load(f)

    updated_any = False
    log = []
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print("Playwright not available:", e); sys.exit(0)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
            viewport={"width": 1400, "height": 1000})
        page = ctx.new_page()
        for name, car in d["cars"].items():
            # src == "bat" cars belong to scripts/bat_refresh.py. The 997.2 was moved
            # to BaT in 2026-08 and its Cars.com window was dropped on purpose so the
            # two scrapers cannot fight over the same car — but it still carries a
            # legacy cars_url, so `src` is the authoritative signal here, not the URL.
            if car.get("src") == "bat" or not car.get("cars_url"):
                continue
            # Safety net for a genuinely new Cars.com car added before its window:
            # log and skip rather than exiting non-zero. A crash here also strands
            # bat_refresh.py's work, since the workflow never reaches its commit step.
            if name not in WINDOW:
                log.append(f"{name}: no price window configured — skipped")
                continue
            lo, hi = WINDOW[name]
            try:
                vals = scrape(page, car["cars_url"], lo, hi)
                if len(vals) >= 3:
                    asking = round(statistics.median(vals))
                    adopted = round(asking * car.get("factor", 0.9))
                    car["asking"] = asking
                    if not car.get("recent"):   # recent sales outrank asking x factor
                        car["hist"][-1] = adopted
                        # Rebase the whole base-case path, not just its first
                        # point (which left e.g. $464k -> $402k next year).
                        r = (car.get("cagr") or {}).get("base")
                        car["proj"] = ([round(adopted * (1 + r) ** i, 1) for i in range(len(car["proj"]))]
                                       if r is not None else [adopted] + car["proj"][1:])
                    updated_any = True
                    log.append(f"{name}: n={len(vals)} asking≈${asking}k -> value≈${adopted}k")
                else:
                    log.append(f"{name}: only {len(vals)} prices found — kept last-good ${car['hist'][-1]}k")
            except Exception as e:
                log.append(f"{name}: scrape failed ({e.__class__.__name__}) — kept last-good")
        for name, cfg in ASKING_ONLY.items():
            car = d["cars"].get(name)
            if not car:
                continue
            car["cars_url"] = cfg["url"]
            lo, hi = cfg["window"]
            try:
                page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)
                pts = [(p, mi) for p, mi in listings_from_srp(page.content()) if lo <= p <= hi]
                vals = [p for p, mi in pts if mi >= cfg["min_miles"]]
                if len(pts) >= 3:
                    # Every listing on the page (the highest-mileage ~24, by the
                    # sort) feeds bat_refresh.py's asking blend, mileage-scaled.
                    car["asking_pts"] = [[round(p, 1), mi] for p, mi in pts]
                if len(vals) >= 3:
                    car["asking"] = round(statistics.median(vals), 1)
                    car["asking_src"] = (f"Cars.com {now_s()} (n={len(vals)}, "
                                         f"{cfg['min_miles'] // 1000}k+ mi; Z07/wing not filterable there)")
                    updated_any = True
                    log.append(f"{name}: n={len(vals)} asking≈${car['asking']}k (asking only)")
                else:
                    log.append(f"{name}: only {len(vals)} asking prices at {cfg['min_miles'] // 1000}k+ mi"
                               f" — kept last-good asking")
            except Exception as e:
                log.append(f"{name}: asking scrape failed ({e.__class__.__name__}) — kept last-good")
        browser.close()

    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    d["updated"] = now.isoformat().replace("+00:00", "Z")
    d["refresh_status"] = ("auto-refresh " + now.strftime("%Y-%m-%d %H:%MZ") + " · "
                           + ("updated " if updated_any else "no source updates · ")
                           + "; ".join(log))
    with open(DATA, "w") as f:
        json.dump(d, f, indent=2)
    print("\n".join(log))
    print("updated_any:", updated_any)

if __name__ == "__main__":
    main()
