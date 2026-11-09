#!/usr/bin/env python3
"""TEMPORARY (branch only): dump sale-level BaT comps for offline backtesting."""
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import bat_refresh as br
from bat_history import fetch_all_completed, parse_sold

cache = json.load(open(br.ODO_CACHE))
flags = json.load(open(br.FLAGS_CACHE))
budget = {"left": 60}
out, log = {}, []
for name, cfg in br.BAT_CARS.items():
    try:
        all_sold, driven = br.collect(cfg, cache, budget, flags)
        out[name] = all_sold
        log.append(f"{name}: {len(all_sold)}")
    except Exception as e:
        log.append(f"{name}: FAILED {type(e).__name__} {e}")
for name, cfg in br.VALUE_ONLY_CARS.items():
    try:
        items, _ = fetch_all_completed(cfg["url"])
        sold = parse_sold(items, cfg["spec"])
        for s in sold:
            s["odo"] = br.odometer(s, cache, budget)
        out[name] = sold
        log.append(f"{name}: {len(sold)}")
    except Exception as e:
        log.append(f"{name}: FAILED {type(e).__name__} {e}")
json.dump(out, open(os.path.join(ROOT, "_dump", "sales.json"), "w"), indent=0)
print("\n".join(log))
