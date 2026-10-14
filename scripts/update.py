#!/usr/bin/env python3
"""Refresh data.json for the Big Island Surf page.

Sources:
  - NWS Honolulu Surf Zone Forecast (SRFHFO): discussion + the two Big Island zones only
  - NOAA NDBC buoy 51206 (Hilo): latest swell / wind-wave reading and water temperature
  - Big Island Now surf page: the "Swell Summary" paragraphs

A source that fails keeps its previous block from data.json, so the page never goes blank.
Standard library only.

Usage:
  python3 scripts/update.py                     # fetch everything, write data.json
  python3 scripts/update.py --srf-file FILE     # parse a saved SRF product instead of fetching it
  python3 scripts/update.py --offline           # skip network for buoy and Big Island Now
"""
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.request
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "data.json"))
HST = dt.timezone(dt.timedelta(hours=-10))
NWS_UA = "big-island-surf (https://github.com/wdmarshall-gif/big-island-surf)"
WEB_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
WEEKDAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


def log(msg):
    print(msg, file=sys.stderr)


def get(url, accept="*/*", browser=False):
    req = urllib.request.Request(url, headers={
        "User-Agent": WEB_UA if browser else NWS_UA,
        "Accept": accept,
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def squash(s):
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------------------------------------------------------- NWS surf zone forecast

def fetch_srf_text():
    try:
        lst = json.loads(get("https://api.weather.gov/products/types/SRF/locations/HFO",
                             "application/ld+json"))
        items = sorted(lst.get("@graph", []), key=lambda p: p.get("issuanceTime", ""), reverse=True)
        prod = json.loads(get("https://api.weather.gov/products/" + items[0]["id"],
                              "application/ld+json"))
        return prod["productText"]
    except Exception as e:  # noqa: BLE001
        log("api.weather.gov failed (%s); trying tgftp" % e)
    return get("https://tgftp.nws.noaa.gov/data/raw/fz/fzhw52.phfo.srf.hfo.txt")


def parse_issue(line):
    m = re.match(r"^(\d{1,2})(\d{2}) (AM|PM) HST \w{3} (\w{3}) (\d{1,2}) (\d{4})$", line.strip())
    if not m:
        raise ValueError("unrecognized issue line: %r" % line)
    h, mi, ap, mon, d, y = m.groups()
    hour = int(h) % 12 + (12 if ap == "PM" else 0)
    return dt.datetime(int(y), MONTHS[mon], int(d), hour, int(mi), tzinfo=HST)


def event_date(label, ap, issued):
    base = issued.date()
    L = label.upper()
    if L.startswith("TONIGHT"):
        return base + dt.timedelta(days=1) if ap == "AM" else base
    if L.startswith("TODAY") or L.startswith("THIS") or L.startswith("REST OF"):
        return base
    wd = next((i for i, w in enumerate(WEEKDAYS) if L.startswith(w)), None)
    d = base + dt.timedelta(days=1) if wd is None else base + dt.timedelta(days=(wd - base.weekday()) % 7)
    if "NIGHT" in L and ap == "AM":
        d += dt.timedelta(days=1)
    return d


TIDE_RE = re.compile(r"(High|Low)\s+(-?\d+(?:\.\d+)?)\s+feet\s+(\d{1,2}):(\d{2})\s+(AM|PM)", re.I)


def parse_zone(name, lines, issued):
    zone = {
        "key": "leeward" if "leeward" in name.lower() else "windward",
        "name": name,
        "periods": [],
        "shores": [],
        "days": [],
        "tides": [],
    }

    # Surf table: between the first two underscore rules, then the "X Facing" rows.
    rules = [i for i, l in enumerate(lines) if re.match(r"^_{20,}\s*$", l.strip())]
    body_start = 0
    if len(rules) >= 2:
        head = [l for l in lines[rules[0] + 1:rules[1]] if l.strip()]
        labels = [p for p in re.split(r"\s{2,}", head[0].strip()) if p] if head else []
        cols = re.findall(r"\b(AM|PM)\b", head[-1]) if head else []
        per = len(cols) // max(1, len(labels)) if labels else 0
        for k, p in enumerate(labels):
            zone["periods"].append({"label": p.title() if p.isupper() else p,
                                    "cols": cols[k * per:(k + 1) * per]})
        for j in range(rules[1] + 1, len(lines)):
            l = lines[j]
            if l.strip().startswith("."):
                body_start = j
                break
            m = re.match(r"^\s*([A-Za-z]+)\s+Facing\s+(.*)$", l)
            if m:
                vals = re.findall(r"\d+\s*-\s*\d+|\d+|flat", m.group(2), re.I)
                zone["shores"].append({"facing": m.group(1).title(),
                                       "values": [re.sub(r"\s", "", v) for v in vals]})

    # Day blocks (.TONIGHT... / .WEDNESDAY...) with dotted fields and indented continuations.
    tides = {}
    cur = field = station = None
    for l in lines[body_start:]:
        s = l.strip()
        if not s:
            continue
        if s.startswith("_"):
            break
        mb = re.match(r"^\.([A-Z][A-Z ]*?)\.\.\.\s*$", s)
        if mb:
            cur = {"label": mb.group(1).title(), "weather": "", "tempLabel": "", "temp": "",
                   "winds": "", "sunrise": None, "sunset": None}
            zone["days"].append(cur)
            field = station = None
            continue
        if cur is None:
            continue
        dotted = re.match(r"^(\S.*?)\.{2,}\s*(.*)$", s)
        if l.startswith(" "):
            if field == "tides" and dotted:
                station = dotted.group(1).strip()
                text = dotted.group(2)
            elif field == "tides" and station:
                text = s
            elif field in ("weather", "temp", "winds"):
                cur[field] += " " + s
                continue
            else:
                continue
            for t in TIDE_RE.finditer(text):
                kind, ft, hh, mm, ap = t.groups()
                ap = ap.upper()
                d = event_date(cur["label"], ap, issued)
                hour = int(hh) % 12 + (12 if ap == "PM" else 0)
                when = dt.datetime(d.year, d.month, d.day, hour, int(mm), tzinfo=HST)
                tides.setdefault(station, []).append(
                    {"type": kind.title(), "ft": float(ft), "t": when.isoformat()})
            continue
        if not dotted:
            field = None
            continue
        key, val = dotted.group(1).strip(), dotted.group(2).strip()
        k = key.lower()
        station = None
        if k == "tides":
            field = "tides"
        elif k == "weather":
            field, cur["weather"] = "weather", val
        elif "temperature" in k:
            field, cur["tempLabel"], cur["temp"] = "temp", key.split()[0].title(), val
        elif k == "winds":
            field, cur["winds"] = "winds", val
        elif k in ("sunrise", "sunset"):
            field = None
            cur[k] = re.sub(r"\s*HST\.?$", "", val).strip()
        else:
            field = None

    for d in zone["days"]:
        for f in ("weather", "temp", "winds"):
            d[f] = squash(d[f])
    zone["tides"] = [{"station": s, "events": sorted(ev, key=lambda e: e["t"])}
                     for s, ev in tides.items()]
    return zone


def parse_srf(text):
    full = text.replace("\r", "")
    lines = [l.rstrip() for l in full.split("\n")]
    header = next((l.strip() for l in lines if re.match(r"^[A-Z]{4}\d{2} [A-Z]{4} \d{6}", l.strip())), "")
    issue_line = next(l.strip() for l in lines if re.match(r"^\d{3,4} (AM|PM) HST ", l.strip()))
    issued = parse_issue(issue_line)
    m = re.search(r"^\.DISCUSSION\.\.\.(.*?)(?=^HIZ\d|^&&|^\$\$)", full, re.S | re.M)
    discussion = squash(m.group(1)) if m else ""

    zones = []
    for chunk in full.split("$$"):
        cl = chunk.split("\n")
        idx = next((i for i, l in enumerate(cl) if re.match(r"^HIZ\d", l.strip())), None)
        if idx is None:
            continue
        j = idx
        while j < len(cl) and re.match(r"^[A-Z0-9>\-]+-$", cl[j].strip()):
            j += 1
        if j >= len(cl):
            continue
        name = cl[j].strip().rstrip("-").strip()
        if not name.lower().startswith("big island"):
            continue
        zones.append(parse_zone(name, cl[j + 1:], issued))
    if not zones:
        raise ValueError("no Big Island zones found in SRF product")
    return {
        "header": header,
        "issued": issue_line,
        "issuedISO": issued.isoformat(),
        "discussion": discussion,
        "zones": zones,
    }


# ---------------------------------------------------------------- NDBC buoy 51206

def ndbc_rows(text):
    lines = text.splitlines()
    header = next(l for l in lines if l.startswith("#YY"))[1:].split()
    rows = [dict(zip(header, l.split())) for l in lines if l.strip() and not l.startswith("#")]
    return rows


def num(v):
    try:
        return None if v in (None, "MM", "N/A") else float(v)
    except ValueError:
        return None


def fetch_buoy():
    spec = ndbc_rows(get("https://www.ndbc.noaa.gov/data/realtime2/51206.spec"))
    r = spec[0]
    t = dt.datetime(int(r["YY"]), int(r["MM"]), int(r["DD"]), int(r["hh"]), int(r["mm"]),
                    tzinfo=dt.timezone.utc).astimezone(HST)
    buoy = {
        "station": "51206", "name": "Hilo, HI", "t": t.isoformat(),
        "wvhtM": num(r.get("WVHT")), "swhM": num(r.get("SwH")), "swpS": num(r.get("SwP")),
        "swd": r.get("SwD") if r.get("SwD") not in ("MM", None) else None,
        "wwhM": num(r.get("WWH")), "wwpS": num(r.get("WWP")),
        "wwd": r.get("WWD") if r.get("WWD") not in ("MM", None) else None,
        "mwdDeg": num(r.get("MWD")),
    }
    try:
        met = ndbc_rows(get("https://www.ndbc.noaa.gov/data/realtime2/51206.txt"))
        w = next((num(x.get("WTMP")) for x in met if num(x.get("WTMP")) is not None), None)
        if w is not None:
            buoy["wtmpC"] = w
    except Exception as e:  # noqa: BLE001
        log("buoy water temp failed: %s" % e)
    return {k: v for k, v in buoy.items() if v is not None}


# ---------------------------------------------------------------- Big Island Now

class Blocks(HTMLParser):
    """Collects text of headings and paragraphs in document order."""
    TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.tag, self.buf, self.skip = [], None, [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.TAGS:
            self.flush()
            self.tag = tag
        elif tag == "br" and self.tag:
            self.buf.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.TAGS and self.tag == tag:
            self.flush()

    def handle_data(self, data):
        if self.tag and not self.skip:
            self.buf.append(data)

    def flush(self):
        if self.tag:
            t = squash("".join(self.buf))
            if t:
                self.out.append((self.tag, t))
        self.tag, self.buf = None, []


def fetch_bin():
    page = get("https://bigislandnow.com/surf/", "text/html", browser=True)
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', page, re.I)
    title = html.unescape(m.group(1)).split("|")[0].strip() if m else "Swell summary"
    p = Blocks()
    p.feed(page)
    p.flush()
    paras, started = [], False
    for tag, text in p.out:
        if not started:
            started = tag.startswith("h") and text.lower().startswith("swell summary")
            continue
        if tag.startswith("h") or text.lower().startswith("data courtesy"):
            break
        if len(text) > 40:
            paras.append(text)
    if not paras:
        # Fallback: plain text between "Swell Summary" and the regional widgets.
        plain = html.unescape(re.sub(r"<[^>]+>", "\n", re.sub(r"(?is)<(script|style).*?</\1>", "", page)))
        mm = re.search(r"Swell Summary\s*(.*?)(?:NORTH EAST|NORTH WEST|Data Courtesy)", plain, re.S)
        if mm:
            paras = [squash(x) for x in re.split(r"\n\s*\n", mm.group(1)) if len(squash(x)) > 40]
    if not paras:
        raise ValueError("swell summary not found")
    return {"title": title, "url": "https://bigislandnow.com/surf/", "paragraphs": paras}


# ---------------------------------------------------------------- main

def main(argv):
    srf_file = argv[argv.index("--srf-file") + 1] if "--srf-file" in argv else None
    offline = "--offline" in argv
    try:
        with open(OUT, encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:  # noqa: BLE001
        prev = {}

    data = dict(prev)
    ok = []
    try:
        text = open(srf_file, encoding="utf-8").read() if srf_file else fetch_srf_text()
        data["noaa"] = parse_srf(text)
        ok.append("noaa")
    except Exception as e:  # noqa: BLE001
        log("NWS forecast failed: %s" % e)
    if not offline:
        for key, fn in (("buoy", fetch_buoy), ("bin", fetch_bin)):
            try:
                data[key] = fn()
                ok.append(key)
            except Exception as e:  # noqa: BLE001
                log("%s failed: %s" % (key, e))

    if not ok:
        log("every source failed; data.json left unchanged")
        return 1 if not prev else 0
    data["updatedAt"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    noaa = data.get("noaa", {})
    log("wrote %s: sources ok=%s, NWS issued %s, buoy %s" % (
        OUT, ",".join(ok), noaa.get("issued"), data.get("buoy", {}).get("t")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
