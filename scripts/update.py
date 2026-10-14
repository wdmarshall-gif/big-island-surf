#!/usr/bin/env python3
"""Refresh data.json for the Big Island Surf page.

Sources:
  - NWS Honolulu Surf Zone Forecast (SRFHFO): discussion + the two Big Island zones only
  - Open-Meteo marine and weather models: 7-day offshore swell and wind for the Kona and Hilo sides

A source that fails keeps its previous block in data.json, so the page never goes blank.
Standard library only.

Usage:
  python3 scripts/update.py                     # fetch everything, write data.json
  python3 scripts/update.py --srf-file FILE     # parse a saved SRF product instead of fetching it
  python3 scripts/update.py --no-outlook         # skip the 7-day model fetch
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "data.json"))
HST = dt.timezone(dt.timedelta(hours=-10))
NWS_UA = "big-island-surf (https://github.com/wdmarshall-gif/big-island-surf)"
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
WEEKDAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


def log(msg):
    print(msg, file=sys.stderr)


def get(url, accept="*/*"):
    req = urllib.request.Request(url, headers={
        "User-Agent": NWS_UA,
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


# ---------------------------------------------------------------- 7-day outlook (Open-Meteo)

MARINE_API = "https://marine-api.open-meteo.com/v1/marine"
WEATHER_API = "https://api.open-meteo.com/v1/forecast"
M_TO_FT = 3.28084
# Offshore sea points are tried in order until one has wave data (points too close to land come back empty).
OUTLOOK_SIDES = [
    {"key": "kona", "name": "Kona side", "faces": 270,
     "sea": [(19.60, -156.20), (19.60, -156.35), (19.55, -156.50)],
     "wind": (19.64, -155.99)},
    {"key": "hilo", "name": "Hilo side", "faces": 60,
     "sea": [(19.80, -154.85), (19.85, -154.70), (19.90, -154.55)],
     "wind": (19.72, -155.08)},
]


def om(url, params):
    return json.loads(get(url + "?" + urllib.parse.urlencode(params), "application/json"))


def to_ft(values, unit):
    k = M_TO_FT if (unit or "m").strip().lower() in ("m", "meters", "metre", "metres") else 1.0
    return [None if v is None else round(v * k, 1) for v in values]


def rnd(values, nd=0):
    return [None if v is None else (round(v, nd) if nd else int(round(v))) for v in values]


def fetch_side(sd):
    marine = None
    for lat, lon in sd["sea"]:
        m = om(MARINE_API, {
            "latitude": lat, "longitude": lon,
            "hourly": "swell_wave_height,wind_wave_height",
            "daily": ",".join([
                "swell_wave_height_max", "swell_wave_direction_dominant", "swell_wave_period_max",
                "wind_wave_height_max", "wave_height_max"]),
            "timezone": "Pacific/Honolulu", "forecast_days": 7,
        })
        if any(v is not None for v in m.get("daily", {}).get("swell_wave_height_max") or []):
            marine, at = m, (lat, lon)
            break
    if marine is None:
        raise ValueError("no wave data near %s" % sd["name"])
    wind = om(WEATHER_API, {
        "latitude": sd["wind"][0], "longitude": sd["wind"][1],
        "daily": "wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant",
        "wind_speed_unit": "mph", "timezone": "Pacific/Honolulu", "forecast_days": 7,
    })
    d, du = marine["daily"], marine.get("daily_units", {})
    h, hu = marine["hourly"], marine.get("hourly_units", {})
    w = wind["daily"]
    swell = to_ft(d["swell_wave_height_max"], du.get("swell_wave_height_max"))
    wwave = to_ft(d["wind_wave_height_max"], du.get("wind_wave_height_max"))
    seas = to_ft(d["wave_height_max"], du.get("wave_height_max"))
    sdir, sper = rnd(d["swell_wave_direction_dominant"]), rnd(d["swell_wave_period_max"])
    wspd, wgst, wdir = rnd(w["wind_speed_10m_max"]), rnd(w["wind_gusts_10m_max"]), rnd(w["wind_direction_10m_dominant"])
    wdates = w["time"]
    days = []
    for i, date in enumerate(d["time"]):
        j = wdates.index(date) if date in wdates else None
        days.append({
            "date": date,
            "swellFt": swell[i], "swellDir": sdir[i], "swellPer": sper[i],
            "windWaveFt": wwave[i], "seasFt": seas[i],
            "windMph": wspd[j] if j is not None else None,
            "gustMph": wgst[j] if j is not None else None,
            "windDir": wdir[j] if j is not None else None,
        })
    return {
        "key": sd["key"], "name": sd["name"], "faces": sd["faces"],
        "sea": {"lat": at[0], "lon": at[1]}, "windAt": {"lat": sd["wind"][0], "lon": sd["wind"][1]},
        "days": days,
        "hourly": {
            "start": h["time"][0], "stepHours": 1,
            "swellFt": to_ft(h["swell_wave_height"], hu.get("swell_wave_height")),
            "windWaveFt": to_ft(h["wind_wave_height"], hu.get("wind_wave_height")),
        },
    }


def fetch_outlook():
    return {
        "source": "Open-Meteo marine and weather models",
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sides": [fetch_side(sd) for sd in OUTLOOK_SIDES],
    }


# ---------------------------------------------------------------- main

def main(argv):
    srf_file = argv[argv.index("--srf-file") + 1] if "--srf-file" in argv else None
    try:
        with open(OUT, encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:  # noqa: BLE001
        prev = {}

    data = dict(prev)
    data.pop("bin", None)
    data.pop("buoy", None)
    ok = []
    try:
        text = open(srf_file, encoding="utf-8").read() if srf_file else fetch_srf_text()
        data["noaa"] = parse_srf(text)
        ok.append("noaa")
    except Exception as e:  # noqa: BLE001
        log("NWS forecast failed: %s" % e)
    if "--no-outlook" not in argv:
        try:
            data["outlook"] = fetch_outlook()
            ok.append("outlook")
        except Exception as e:  # noqa: BLE001
            log("7-day outlook failed: %s" % e)

    if not ok:
        log("every source failed; data.json left unchanged")
        return 1 if not prev else 0
    data["updatedAt"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    noaa = data.get("noaa", {})
    log("wrote %s: sources ok=%s, NWS issued %s" % (OUT, ",".join(ok), noaa.get("issued")))
    for sd in data.get("outlook", {}).get("sides", []):
        log("  %s: %s" % (sd["name"], ", ".join("%s %sft/%ss/%s wind %s" % (
            x["date"][5:], x["swellFt"], x["swellPer"], x["swellDir"], x["windMph"]) for x in sd["days"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
