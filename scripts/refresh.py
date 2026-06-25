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
    "Porsche 997.2 Turbo S": (90, 200),  # tight: exclude new 992 Turbo S & exceptional cars
}
PRICE_RE = re.compile(r"\$([0-9]{2,3},[0-9]{3})")

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
            lo, hi = WINDOW[name]
            try:
                vals = scrape(page, car["cars_url"], lo, hi)
                if len(vals) >= 3:
                    asking = round(statistics.median(vals))
                    adopted = round(asking * car.get("factor", 0.9))
                    car["asking"] = asking
                    car["hist"][-1] = adopted
                    car["proj"][0] = adopted
                    updated_any = True
                    log.append(f"{name}: n={len(vals)} asking≈${asking}k -> value≈${adopted}k")
                else:
                    log.append(f"{name}: only {len(vals)} prices found — kept last-good ${car['hist'][-1]}k")
            except Exception as e:
                log.append(f"{name}: scrape failed ({e.__class__.__name__}) — kept last-good")
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
