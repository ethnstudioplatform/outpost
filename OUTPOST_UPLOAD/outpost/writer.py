"""Turns raw items into a narrated script. Every line must cite item indices."""
import json
import re
from datetime import datetime, timezone

import requests

from . import config

SYSTEM = """You are the script desk for OUTPOST, a faceless, neutral conflict-monitoring channel.
Style: terse military-radio briefing, tense but calm, plain English, read aloud by a robotic voice.

HARD RULES
1. Use ONLY facts in the numbered ITEMS. Never add numbers, names, places or events that are not in them.
2. Attribute every claim to its outlet in the sentence ("Reuters reports...", "According to the BBC...").
3. Claims by a government, army or armed group are stated as claims ("Russia's defence ministry says...").
4. No casualty figure unless it appears in an item, and then attributed.
5. Never take sides, never speculate about what happens next, no graphic detail, no emojis.
6. Each line max 110 characters, one sentence, easy to say out loud. No abbreviations the voice would stumble on.
7. You MAY include at most ONE context line explaining why this matters. It must be long-established,
   uncontroversial background (e.g. when a war began). Mark it with "context": true and "src": [].
8. Pick the single most significant story with 2+ items if one exists, otherwise do a roundup of up to 4 stories.

Return ONLY JSON:
{"headline": "<=36 chars, uppercase, no punctuation except / and -",
 "lines": [{"text": "...", "src": [item numbers], "context": false}],
 "region": "<=24 chars uppercase, main region covered",
 "caption": "1-2 sentence social caption, neutral",
 "hashtags": ["5 or fewer, no # symbol"]}
Between 4 and 7 lines. First line is the hook. Last line is a sign-off like "Outpost will keep monitoring." """


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
        json={"model": config.ANTHROPIC_MODEL, "max_tokens": 1500, "system": SYSTEM,
              "messages": [{"role": "user", "content": "ITEMS:\n" + _items_block(items)}]},
        timeout=90,
    )
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json()["content"])
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0))


def _validate(script: dict, items: list) -> dict:
    lines, ctx = [], 0
    for ln in script.get("lines", []):
        text = re.sub(r"\s+", " ", str(ln.get("text", ""))).strip()
        text = text.replace("\u2014", ",").replace("\u2013", "-")
        srcs = [s for s in ln.get("src", []) if isinstance(s, int) and 0 <= s < len(items)]
        is_ctx = bool(ln.get("context"))
        is_signoff = "outpost" in text.lower()
        if not text or len(text) > config.MAX_LINE_CHARS + 20:
            continue
        if is_ctx:
            if ctx:
                continue
            ctx += 1
        elif not srcs and not is_signoff:
            print(f"[writer] dropped unsourced line: {text}")
            continue
        lines.append({"text": text, "src": srcs, "context": is_ctx})
    script["lines"] = lines[: config.MAX_LINES]
    return script


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
