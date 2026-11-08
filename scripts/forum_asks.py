#!/usr/bin/env python3
"""Private-party ASKING prices for the C8 Z06 from the CorvetteForum classifieds.

https://www.corvetteforum.com/forums/c8-corvettes-for-sale-178/ is a vBulletin
board: page 1 is the folder URL, later pages are .../index2.html, index3.html.
Thread titles are `<a ... id="thread_title_NNN">`. Many titles carry the price
and miles ("2024 Z06 3LZ Coupe | 11K Miles | $110,000 | Tampa, FL"); the first
post (`id="post_message_NNN"`) is read for every candidate anyway, because Z07,
the Carbon Aero package / high wing and the car's state are often only there.

Kept: used C8 Z06s with a readable asking price, started within MAX_AGE_DAYS.
Dropped: wanted / trade / sold / closed threads, new cars, Z07 or "Z06/07",
Carbon Aero / high wing, and Florida cars (his rule - flood and salt exposure).
Asks are asks: bat_refresh.py normalises them to a 30k-mile basis and applies
the same ask-to-sold haircut as the Cars.com asks before blending.

Polite: three index pages plus one first post per candidate, 1.5 s apart.
Stdlib only; any failure raises and the caller keeps last-good.
"""
import html as htmlmod
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://www.corvetteforum.com/forums/c8-corvettes-for-sale-178/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
PAGES = 3
SLEEP = 1.5
MAX_AGE_DAYS = 120

THREAD_RE = re.compile(r'<a[^>]*href="([^"]+)"[^>]*id="thread_title_(\d+)"[^>]*>(.*?)</a>', re.S)
POST_RE = re.compile(r'id="post_message_\d+"[^>]*>(.*?)</div>', re.S)
STARTED_RE = re.compile(r"\bon ([A-Z][a-z]{2} \d{1,2}, \d{4})")
SKIP_TITLE = re.compile(r"\b(wtb|wtt|iso|looking for|ready to buy|wanted|sold|no longer available|"
                        r"off the market|thread closed|traded in|pending)\b|^\s*new\b|\bnew 20\d\d\b", re.I)
EXCLUDE = re.compile(r"\bz07\b|z06\s*/\s*0?7\b|\baero\b|high[- ]?wing|\bwing\b|ultimate performance", re.I)
NEGATED = re.compile(r"(\bno|\bnon|\bnot|without|w/o)[\s-]*(?:(?:the|a|an|any)\s+)?$", re.I)


def excluded(text):
    """True if the text names Z07, the aero package or a wing - unless negated
    ("Non-Z07", "no wing")."""
    return any(not NEGATED.search(text[max(0, m.start() - 16):m.start()])
               for m in EXCLUDE.finditer(text))
FLORIDA = re.compile(r"\bflorida\b|,\s*fl\b|\bfl\.?\s*$|\b(tampa|miami|orlando|jacksonville|sarasota|"
                     r"naples|fort lauderdale|ft\.? lauderdale|boca raton)\b", re.I)
# "$110,000", "$105k", "asking 133k", "126,900 OBO", "$132,900"
PRICE_RE = re.compile(r"(\$\s*)?\b(\d{2,3})(?:,(\d{3})|\.(\d)\s*k|\s*k)\b(?!\s*(?:mi|miles|km)\b)", re.I)
NOT_PRICE = re.compile(r"msrp|sticker|window|paid|invoice|retail|original|new price|stickered|"
                       r"pp?f|warranty|tax", re.I)
MILES_RE = re.compile(r"\b(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k)?\s*(?:mi|miles|mile)\b", re.I)


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    return urllib.request.urlopen(req, timeout=60).read().decode("latin-1", "ignore")


def _text(s):
    return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_price(text):
    """Asking price in $000s, or None. A "$" or an asking word nearby wins over a
    bare number; numbers next to MSRP / sticker / paid are not the ask."""
    best = None
    for m in PRICE_RE.finditer(text):
        k = int(m.group(2)) + (int(m.group(3)) / 1000.0 if m.group(3) else 0) \
            + (int(m.group(4)) / 10.0 if m.group(4) else 0)
        if not (60 <= k <= 250):
            continue
        ctx = text[max(0, m.start() - 30):m.end() + 15]
        if NOT_PRICE.search(ctx):
            continue
        strong = bool(m.group(1)) or re.search(r"ask|price|obo|firm|\bfor\b", ctx, re.I)
        if strong:
            return round(k, 1)
        best = best or round(k, 1)
    return best


def parse_miles(text):
    for m in MILES_RE.finditer(text):
        v = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
        if 0 <= v <= 150000:
            return int(v)
    return None


def fetch_asks(now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    seen, cands = set(), []
    for p in range(1, PAGES + 1):
        page = _get(BASE if p == 1 else f"{BASE}index{p}.html")
        rows = THREAD_RE.findall(page)
        if p == 1 and not rows:
            raise RuntimeError("no thread titles on the CorvetteForum index")
        for url, tid, raw in rows:
            title = _text(raw)
            if tid in seen or not re.search(r"\bz06\b", title, re.I):
                continue
            seen.add(tid)
            i = page.find(f'id="thread_title_{tid}"')
            started = STARTED_RE.search(_text(page[i:i + 1500]))
            if started:
                try:
                    if datetime.strptime(started.group(1), "%b %d, %Y").replace(tzinfo=timezone.utc) < cutoff:
                        continue
                except ValueError:
                    pass
            if SKIP_TITLE.search(title) or excluded(title) or FLORIDA.search(title):
                continue
            cands.append((tid, url, title, started.group(1) if started else None))
        time.sleep(SLEEP)
    asks = []
    for tid, url, title, started in cands:
        try:
            m = POST_RE.search(_get(url))
        except Exception:
            continue
        finally:
            time.sleep(SLEEP)
        post = _text(m.group(1)) if m else ""
        if excluded(post) or FLORIDA.search(post) or re.search(r"\bsold\b|^delete", post[:80], re.I):
            continue
        price = parse_price(title) or parse_price(post)
        miles = parse_miles(title) or parse_miles(post)
        yr = re.search(r"\b(20(2[3-9]))\b", title)
        if price is None:
            continue
        asks.append({"id": tid, "title": title[:120], "url": url, "price": price, "miles": miles,
                     "year": int(yr.group(1)) if yr else None, "started": started})
    return asks


if __name__ == "__main__":
    for a in fetch_asks():
        print(a["price"], a["miles"], a["year"], a["started"], "|", a["title"])
