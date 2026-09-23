"""Turns raw items into a narrated script. Every line must cite item indices."""
import json
import re
from datetime import datetime, timezone

import requests

from . import config

SYSTEM = """You are the script desk for OUTPOST, a faceless, neutral conflict-monitoring channel.
Style: calm, clipped military briefing. Tense but controlled. Plain English, read aloud by a synthetic voice.
The viewer knows nothing: explain who is fighting whom and why it matters, briefly.

HARD RULES
1. News facts come ONLY from the numbered ITEMS. Never add numbers, names, places or events not in them.
2. Attribute every news claim to its outlet in the sentence ("The BBC reports...", "According to Al Jazeera...").
3. Claims by a government, army or armed group are framed as claims ("Israel's military says...").
4. No casualty figure unless it appears in an item, and then attributed.
5. Never take sides, never predict, no graphic detail, no emojis.
6. Each line max 110 characters, one sentence, easy to say aloud. Spell out abbreviations the voice would trip on.
7. BACKGROUND lines: up to TWO lines of long-established, uncontroversial context (when and how the conflict
   began, who the parties are). Mark them "context": true, "src": []. Add "year" if a start year is part of it.
8. Pick the single most significant story with 2+ items if possible. Otherwise a roundup of up to 3 stories.

STRUCTURE (6 to 8 lines):
 1. Hook: the key development, sourced.
 2-4. What happened: details, each sourced.
 then 1-2 background lines (context).
 then why it matters: one sourced or context line.
 last: sign-off, e.g. "Outpost will keep monitoring."

VISUAL DATA (used for on-screen animation):
- "location": the main place, {"name": "CITY, COUNTRY" uppercase, "lat": number, "lon": number}.
  Use the approximate centre of a place that is NAMED in the items. null if no place is named.
- Per line optional "loc": {"name","lat","lon"} when that line is about a different named place.
- Per line optional "stat": {"value": "the number exactly as written in the item", "label": "<=22 chars uppercase"}
  ONLY when that line quotes a number that appears in its source item.

Return ONLY JSON:
{"headline": "<=36 chars, uppercase, no punctuation except / and -",
 "region": "<=20 chars uppercase",
 "location": {...} or null,
 "lines": [{"text": "...", "src": [item numbers], "context": false, "year": null, "loc": null, "stat": null}],
 "caption": "1-2 sentence neutral social caption",
 "hashtags": ["up to 5, no # symbol"]}"""


def _items_block(items):
    return "\n".join(f"[{i}] ({it['domain']}, {it['date']}) {it['title']}"
                     + (f" | {it['summary']}" if it.get("summary") else "")
                     for i, it in enumerate(items))


def _call_claude(items) -> dict:
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": config.ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": config.ANTHROPIC_MODEL, "max_tokens": 2000, "system": SYSTEM,
              "messages": [{"role": "user", "content": "ITEMS:\n" + _items_block(items)}]},
        timeout=90,
    )
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json()["content"])
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0))


def _validate(script: dict, items: list) -> dict:
    lines, ctx = [], 0
    max_ctx = 2
    for ln in script.get("lines", []):
        text = re.sub(r"\s+", " ", str(ln.get("text", ""))).strip()
        text = text.replace("\u2014", ",").replace("\u2013", "-")
        srcs = [s for s in ln.get("src", []) if isinstance(s, int) and 0 <= s < len(items)]
        is_ctx = bool(ln.get("context"))
        is_signoff = "outpost" in text.lower()
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
        yr = ln.get("year")
        if is_ctx and yr and re.fullmatch(r"(19|20)\d\d", str(yr)):
            entry["year"] = int(yr)
        lines.append(entry)
    script["lines"] = lines[: config.MAX_LINES]
    script["location"] = _loc(script.get("location"))
    return script


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
