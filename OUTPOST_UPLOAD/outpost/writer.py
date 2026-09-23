"""Turns raw items into a narrated script. Every line must cite item indices."""
import json
import re
from datetime import datetime, timezone

import requests

from . import config

SYSTEM = """You are the script desk for OUTPOST, a faceless conflict-news channel on TikTok, Reels and Shorts.
Viewers scroll fast and get bored fast. Every line must earn the next second.

HARD RULES (accuracy first, always)
1. News facts come ONLY from the numbered ITEMS. Never add numbers, names, places or events not in them.
2. Attribute news claims to the outlet in the sentence ("AP reports...", "Reuters says...").
3. Claims by a government, army or armed group are framed as claims ("Russia's defence ministry says...").
4. No casualty figure unless it is in an item. No speculation, no taking sides, no graphic detail.
5. Pick ONE story that has 2 or more items. Never a roundup.
6. Every line: one sentence, 12 words or fewer, punchy, easy to say aloud. No filler like "signal check".
7. At most ONE background line (long-established fact, e.g. when the war began): "context": true, "src": [].

STRUCTURE (7 to 9 lines, about 25 to 35 seconds read aloud)
 1. HOOK: the most striking sourced fact, said so the viewer must keep watching. No greeting.
 2. WHERE: the place and what hit it, attributed.
 3-5. WHAT: one concrete fact per line, numbers if the items have them.
 6. CONTEXT: one background line.
 7. STAKES: why it matters, from the items.
 8. LOOP: a short last line that calls back to the hook so a replay feels natural. Still sourced.

BEATS (what the screen does on each line). Give every line a "beat":
 "hook"     line 1 only
 "lock"     map zooms and locks onto the line's place (needs "loc")
 "track"    an attack path, ONLY if an item names where it was launched FROM and where it hit (needs "arc")
 "readout"  a typed terminal readout, give "readout": up to 30 chars, words copied from the line
 "stat"     giant number, give "stat": {"value": number exactly as in the item, "label": up to 18 chars}
 "timeline" for the context line with a start year, give "year"
 "quote"    for an official's claim, give "who": up to 24 chars
 "wide"     pull back to show every place in the story (good for the loop line)
Never use the same beat twice in a row.

PLACES: "loc": {"name": "CITY, COUNTRY" uppercase, "lat": number, "lon": number}, approximate centre of a place
NAMED in the items. "arc": {"from": loc, "to": loc, "type": "missile|drone|airstrike|artillery|naval|troops"}.

Return ONLY JSON:
{"headline": "up to 5 words uppercase, the hook in brief",
 "hook_bar": "2 to 4 words uppercase from line 1, e.g. DRONES HIT KYIV",
 "region": "up to 20 chars uppercase",
 "location": main loc,
 "lines": [{"text": "...", "src": [item numbers], "context": false, "beat": "hook", "loc": null,
            "arc": null, "stat": null, "readout": null, "year": null, "who": null}],
 "caption": "1 to 2 sentence neutral social caption",
 "hashtags": ["up to 5, no # symbol"]}"""


def _items_block(items):
    return "\n".join(f"[{i}] ({it['domain']}, {it['date']}) {it['title']}"
                     + (f" | {it['summary']}" if it.get("summary") else "")
                     for i, it in enumerate(items))


def _call_claude(items) -> dict:
    last = None
    for model in (config.ANTHROPIC_MODEL, "claude-haiku-4-5"):
        try:
            return _call_model(items, model)
        except Exception as e:
            print(f"[writer] {model} failed: {e}")
            last = e
    raise last


def _call_model(items, model) -> dict:
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": config.ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": 2500, "system": SYSTEM,
              "messages": [{"role": "user", "content": "ITEMS:\n" + _items_block(items)}]},
        timeout=90,
    )
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json()["content"])
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0))


def _validate(script: dict, items: list) -> dict:
    lines, ctx = [], 0
    max_ctx = 1
    for ln in script.get("lines", []):
        text = re.sub(r"\s+", " ", str(ln.get("text", ""))).strip()
        text = text.replace("\u2014", ",").replace("\u2013", "-")
        srcs = [s for s in ln.get("src", []) if isinstance(s, int) and 0 <= s < len(items)]
        is_ctx = bool(ln.get("context"))
        is_signoff = False
        if not text or len(text) > config.MAX_LINE_CHARS + 20:
            continue
        if is_ctx:
            if ctx >= max_ctx:
                continue
            ctx += 1
        elif not srcs and not is_signoff:
            print(f"[writer] dropped unsourced line: {text}")
            continue
        entry = {"text": text, "src": srcs, "context": is_ctx}
        entry["loc"] = _loc(ln.get("loc"))
        stat = ln.get("stat")
        if isinstance(stat, dict) and srcs:
            val = str(stat.get("value", "")).strip()
            blob = " ".join(items[i]["title"] + " " + items[i].get("summary", "") for i in srcs)
            if val and re.search(r"\d", val) and val in blob:
                entry["stat"] = {"value": val[:10], "label": str(stat.get("label", ""))[:22].upper()}
        arc = ln.get("arc")
        if isinstance(arc, dict) and srcs and str(arc.get("type", "")).lower() in ARC_TYPES:
            a, b = _loc(arc.get("from")), _loc(arc.get("to"))
            blob = " ".join(items[i]["title"] + " " + items[i].get("summary", "") for i in srcs).upper()
            if a and b and _named(a["name"], blob) and _named(b["name"], blob):
                entry["arc"] = {"from": a, "to": b, "type": str(arc["type"]).lower()}
        kw = str(ln.get("keyword") or "").strip().upper()
        if kw and len(kw) <= 26 and all(w in text.upper() for w in kw.split()):
            entry["keyword"] = kw
        beat = str(ln.get("beat") or "").lower()
        entry["beat"] = beat if beat in BEATS else None
        ro = str(ln.get("readout") or "").strip().upper()
        if ro and len(ro) <= 32 and all(w.strip(".,:;") in text.upper() for w in ro.split()):
            entry["readout"] = ro
        who = str(ln.get("who") or "").strip().upper()
        if who and len(who) <= 26:
            entry["who"] = who
        yr = ln.get("year")
        if is_ctx and yr and re.fullmatch(r"(19|20)\d\d", str(yr)):
            entry["year"] = int(yr)
        lines.append(entry)
    script["lines"] = lines[: config.MAX_LINES]
    script["location"] = _loc(script.get("location"))
    hb = str(script.get("hook_bar") or "").strip().upper()
    first = lines[0]["text"].upper() if lines else ""
    script["hook_bar"] = hb if hb and len(hb) <= 24 and all(w in first for w in hb.split()) else ""
    return script


BEATS = {"hook", "lock", "track", "readout", "stat", "timeline", "quote", "wide"}
ARC_TYPES = {"missile", "drone", "airstrike", "artillery", "naval", "troops"}


def _named(place, blob):
    """First part of 'KHARKIV, UKRAINE' must be mentioned in the source text."""
    head = place.split(",")[0].strip()
    return len(head) >= 3 and head in blob


def _loc(v):
    try:
        lat, lon = float(v["lat"]), float(v["lon"])
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return {"name": str(v.get("name", ""))[:28].upper(), "lat": lat, "lon": lon}
    except Exception:
        pass
    return None


def _fallback(items) -> dict:
    """No API key: plain attributed headline roundup."""
    lines = [{"text": "Signal check. Here is what trusted outlets are reporting right now.", "src": [], "context": False}]
    for i, it in enumerate(items[:4]):
        outlet = it["domain"].split(".")[0].upper()
        t = it["title"].rstrip(".")
        if len(t) > 90:
            t = t[:87].rsplit(" ", 1)[0] + "..."
        lines.append({"text": f"{outlet} reports: {t}.", "src": [i], "context": False})
    lines.append({"text": "Outpost will keep monitoring.", "src": [], "context": False})
    return {"headline": "SITREP ROUNDUP", "region": "GLOBAL", "lines": lines,
            "caption": "Latest conflict headlines from trusted outlets. Sources below.",
            "hashtags": ["news", "worldnews", "sitrep", "conflict", "outpost"]}


def write(items: list) -> dict:
    script = None
    if config.ANTHROPIC_API_KEY:
        try:
            script = _validate(_call_claude(items), items)
            if len(script["lines"]) < 3:
                print("[writer] too few valid lines, using fallback")
                script = None
        except Exception as e:
            print(f"[writer] Claude call failed: {e}")
    if script is None:
        script = _fallback(items)

    now = datetime.now(timezone.utc)
    script["stamp"] = now.strftime("%d%b%y %H%MZ").upper()
    script["generated_utc"] = now.isoformat(timespec="seconds")
    used = sorted({s for ln in script["lines"] for s in ln["src"]})
    script["sources"] = [{"n": i, "outlet": items[i]["domain"], "title": items[i]["title"],
                          "url": items[i]["url"]} for i in used]
    script["ticker"] = [it["title"] for it in items[:8]]
    script["signal_count"] = len(items)
    script["outlet_count"] = len({it["domain"] for it in items})
    return script


def caption_text(script: dict) -> str:
    out = [script.get("caption", "").strip(), ""]
    out.append("SOURCES:")
    for s in script["sources"]:
        out.append(f"[{s['n']}] {s['outlet']}: {s['url']}")
    if any(l.get("context") for l in script["lines"]):
        out.append("(Background line is general context, not from the sources above.)")
    out.append("")
    out.append("Automated data desk. Headlines from named outlets, not our own reporting.")
    out.append(" ".join("#" + h.strip("#").replace(" ", "") for h in script.get("hashtags", [])))
    return "\n".join(out).replace("\u2014", ",")
