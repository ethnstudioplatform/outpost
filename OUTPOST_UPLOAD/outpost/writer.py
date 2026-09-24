"""OUTPOST v5 script desk.

1. Pick ONE story from the live items.
2. Read the full articles behind it.
3. Write a question-led script where every line has a scene for the screen.
4. Validate: every number and quote must appear in the source text.
"""
import json
import re
from datetime import datetime, timezone

import requests

from . import articles, config

OUTLETS = {
    "bbc.co.uk": "BBC", "bbc.com": "BBC", "aljazeera.com": "Al Jazeera", "theguardian.com": "The Guardian",
    "dw.com": "DW", "france24.com": "France 24", "npr.org": "NPR", "news.sky.com": "Sky News",
    "news.un.org": "UN News", "un.org": "UN", "reuters.com": "Reuters", "apnews.com": "AP",
    "reliefweb.int": "ReliefWeb", "cnn.com": "CNN", "nytimes.com": "NYT", "kyivindependent.com": "Kyiv Independent",
}

PICK = """You are the story editor for OUTPOST, a faceless conflict-news channel (TikTok, Reels, Shorts).
From the numbered ITEMS pick the ONE story that is best for a 45 to 60 second explainer: concrete places,
concrete numbers, a clear change or contradiction someone would want explained. Prefer stories with 2+ items.
Return ONLY JSON: {"items": [indices of every item about that story, max 4], "why": "one line"}"""

WRITE = """You are the script desk for OUTPOST, a faceless conflict-news channel on TikTok, Reels and Shorts.
Style: calm, precise, investigative. A person explaining one story clearly. No hype, no drama words.

HARD RULES (accuracy first, always)
1. Facts come ONLY from the numbered SOURCES. Never add numbers, names, places or events not in them.
2. Attribute claims: "the UN says", "the BBC reports", "Israel's military says". Claims by governments,
   armies or armed groups are framed as claims.
3. Write numbers as digits exactly as they appear in the source (e.g. 46,000). No arithmetic of your own.
4. No speculation, no taking sides, no graphic detail.
5. At most ONE background line (long-established fact) with "context": true and "src": [].

SHAPE (8 to 11 lines, 45 to 60 seconds read aloud; each line one sentence, max 22 words)
 - Open on the most surprising concrete fact. No greeting, no "breaking".
 - Build the explanation in order: where, what happened, the numbers, who says what.
 - The whole video answers ONE question. The last line calls back to the first so a replay feels natural.

SCENES: every line gets a "scene" that the screen shows while it is spoken. Types:
 {"type":"number","value":"46,000","label":"up to 28 chars","detail":"up to 36 chars, e.g. date range"}
 {"type":"pin","place":"PLACE_ID","head":"up to 18 chars","notes":["up to 30 chars", "up to 30 chars"]}
 {"type":"route","from":"PLACE_ID","to":"PLACE_ID","style":"cut|attack|path","label":"up to 20 chars","note":"up to 30 chars"}
 {"type":"flow","from":"PLACE_ID","to":["PLACE_ID",...],"label":"up to 20 chars"}   (people or forces moving)
 {"type":"compare","a":{"value":"15,786","label":"up to 18 chars"},"b":{"value":"46,000","label":"up to 18 chars"}}
 {"type":"quote","quote":"exact words copied from a source, max 16 words","who":"name, role (max 34 chars)"}
 {"type":"statement","lines":["2 to 3 very short lines", "words from the spoken line"]}
 {"type":"wide"}  (pull back over every place, good for the last line)
Use a mix. Never the same type three lines in a row. Use "compare" only when both numbers are in the sources.

PLACES: list every place used by a scene: {"id":"MOKHA","name":"Mokha","lat":13.32,"lon":43.25}. Only places
named in the sources, approximate centre coordinates. 2 to 7 places.

COVER: 3 short lines (max 22 chars each) that pose the video's question as a riddle, using the key numbers,
e.g. ["15,786 in 8 months.", "46,000 in a week.", "What changed?"].

Return ONLY JSON:
{"question": "...", "cover": ["","",""], "region": "COUNTRY / AREA, max 30 chars, uppercase",
 "places": [...], "lines": [{"text": "...", "src": [source numbers], "context": false, "scene": {...}}],
 "caption": "1 to 2 neutral sentences", "hashtags": ["up to 5, no #"]}"""


def _post(system, user, model, max_tokens):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": max_tokens, "system": system,
              "messages": [{"role": "user", "content": user}]},
        timeout=150)
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json()["content"])
    return json.loads(re.search(r"\{.*\}", text, re.S).group(0))


def _ask(system, user, max_tokens=3500):
    last = None
    for model in (config.ANTHROPIC_MODEL, "claude-haiku-4-5"):
        try:
            return _post(system, user, model, max_tokens)
        except Exception as e:
            print(f"[writer] {model} failed: {e}")
            last = e
    raise last


def _outlet(it):
    return OUTLETS.get(it["domain"], it["domain"].split(".")[0].title())


def _date(it):
    d = str(it.get("date", ""))
    m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", d)
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3])).strftime("%-d %b %Y")
        except ValueError:
            pass
    return datetime.now(timezone.utc).strftime("%-d %b %Y")


# ---------- validation helpers ----------
def _norm(s):
    s = str(s).lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s)


def _nums(s):
    """Multi-digit numbers in a string, commas removed: '46,000 people' -> ['46000']."""
    return [n.replace(",", "") for n in re.findall(r"\d[\d,]*\.?\d*", str(s)) if len(n.replace(",", "")) >= 2]


def _nums_ok(s, blob_nums):
    return all(n in blob_nums for n in _nums(s))


def _clip(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip().replace("—", ",").replace("–", "-")
    return s[:n]


def _validate(script, srcs):
    blob = "\n".join(s["title"] + "\n" + s.get("summary", "") + "\n" + s.get("body", "") for s in srcs)
    bl = _norm(blob)
    bnums = set(_nums(blob))
    for yr in range(2000, 2031):
        bnums.add(str(yr))   # years are fine to mention

    places = {}
    for p in script.get("places", []) or []:
        try:
            pid = str(p["id"]).upper()[:24]
            lat, lon = float(p["lat"]), float(p["lon"])
            name = _clip(p.get("name", pid), 24)
            if -90 <= lat <= 90 and -180 <= lon <= 180 and name.split(",")[0].lower() in bl:
                places[pid] = {"id": pid, "name": name, "lat": lat, "lon": lon}
        except Exception:
            continue
    # drop places far from the rest (bad coordinates)
    if len(places) >= 3:
        import statistics
        mlat = statistics.median(p["lat"] for p in places.values())
        mlon = statistics.median(p["lon"] for p in places.values())
        places = {k: v for k, v in places.items() if abs(v["lat"] - mlat) < 12 and abs(v["lon"] - mlon) < 16}

    lines, ctx = [], 0
    for ln in script.get("lines", []) or []:
        text = _clip(ln.get("text"), 170)
        src = [i for i in ln.get("src", []) if isinstance(i, int) and 0 <= i < len(srcs)]
        is_ctx = bool(ln.get("context"))
        if not text:
            continue
        if is_ctx:
            if ctx >= 1:
                continue
            ctx += 1
        elif not src:
            print(f"[writer] dropped unsourced: {text}")
            continue
        if not is_ctx and not _nums_ok(text, bnums):
            print(f"[writer] dropped, number not in sources: {text}")
            continue
        sc = _scene(ln.get("scene") or {}, places, bl, bnums)
        note = ""
        if src:
            s0 = srcs[src[0]]
            note = f"{_outlet(s0)}, {s0['nice_date']}"
        lines.append({"text": text, "src": src, "context": is_ctx, "scene": sc, "note": note})
    lines = lines[:12]

    cover = [_clip(c, 24) for c in (script.get("cover") or [])][:3]
    if len(cover) != 3 or not all(_nums_ok(c, bnums) for c in cover):
        cover = []
    return {"question": _clip(script.get("question"), 120), "cover": cover,
            "region": _clip(script.get("region"), 32).upper(), "places": places, "lines": lines,
            "caption": _clip(script.get("caption"), 400), "hashtags": script.get("hashtags", [])[:5]}


def _scene(sc, places, bl, bnums):
    t = str(sc.get("type", "")).lower()
    P = lambda k: places.get(str(sc.get(k, "")).upper())
    if t == "number":
        v = _clip(sc.get("value"), 14)
        if v and _nums(v) and _nums_ok(v, bnums) and _nums_ok(sc.get("label", ""), bnums) and _nums_ok(sc.get("detail", ""), bnums):
            return {"type": "number", "value": v, "label": _clip(sc.get("label"), 30), "detail": _clip(sc.get("detail"), 40)}
    elif t == "pin" and P("place"):
        notes = [_clip(n, 32) for n in (sc.get("notes") or [])][:2]
        if all(_nums_ok(n, bnums) for n in notes) and _nums_ok(sc.get("head", ""), bnums):
            return {"type": "pin", "place": P("place")["id"], "head": _clip(sc.get("head"), 20), "notes": notes}
    elif t == "route" and P("from") and P("to") and P("from") is not P("to"):
        return {"type": "route", "from": P("from")["id"], "to": P("to")["id"],
                "style": sc.get("style") if sc.get("style") in ("cut", "attack", "path") else "path",
                "label": _clip(sc.get("label"), 22), "note": _clip(sc.get("note"), 32)}
    elif t == "flow" and P("from"):
        tos = [places[str(x).upper()]["id"] for x in (sc.get("to") or []) if str(x).upper() in places]
        tos = [x for x in tos if x != P("from")["id"]][:4]
        if tos:
            return {"type": "flow", "from": P("from")["id"], "to": tos, "label": _clip(sc.get("label"), 22)}
    elif t == "compare":
        a, b = sc.get("a") or {}, sc.get("b") or {}
        va, vb = _nums(a.get("value", "")), _nums(b.get("value", ""))
        if va and vb and _nums_ok(a.get("value"), bnums) and _nums_ok(b.get("value"), bnums) \
                and _nums_ok(a.get("label", ""), bnums) and _nums_ok(b.get("label", ""), bnums):
            return {"type": "compare",
                    "a": {"value": _clip(a["value"], 14), "label": _clip(a.get("label"), 20), "n": float(va[0])},
                    "b": {"value": _clip(b["value"], 14), "label": _clip(b.get("label"), 20), "n": float(vb[0])}}
    elif t == "quote":
        q = _clip(sc.get("quote"), 140).strip('"“” ')
        if q and _norm(q) in bl:
            return {"type": "quote", "quote": q, "who": _clip(sc.get("who"), 36)}
        print(f"[writer] quote not verbatim in sources, dropped: {q[:60]}")
    elif t == "statement":
        ls = [_clip(x, 18) for x in (sc.get("lines") or [])][:3]
        if ls and all(_nums_ok(x, bnums) for x in ls):
            return {"type": "statement", "lines": ls}
    elif t == "wide":
        return {"type": "wide"}
    return {"type": "wide"}


def _items_block(items):
    return "\n".join(f"[{i}] ({it['domain']}, {it['date']}) {it['title']}"
                     + (f" | {it['summary']}" if it.get("summary") else "") for i, it in enumerate(items))


def write(items: list) -> dict:
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("no ANTHROPIC_API_KEY")
    pick = _ask(PICK, "ITEMS:\n" + _items_block(items), 400)
    idx = [i for i in pick.get("items", []) if isinstance(i, int) and 0 <= i < len(items)][:4] or [0]
    print(f"[writer] story: {pick.get('why', '')} -> items {idx}")
    srcs = []
    for i in idx:
        it = dict(items[i])
        it["body"] = articles.fetch_text(it["url"])
        it["nice_date"] = _date(it)
        print(f"[writer] [{len(srcs)}] {it['domain']}: {len(it['body'])} chars of article text")
        srcs.append(it)
    block = "\n\n".join(f"[{k}] {_outlet(s)}, {s['nice_date']}\nTITLE: {s['title']}\n"
                        f"SUMMARY: {s.get('summary', '')}\nTEXT:\n{s['body'] or '(not available)'}"
                        for k, s in enumerate(srcs))
    best = None
    for attempt in range(2):
        raw = _ask(WRITE, "SOURCES:\n" + block)
        sc = _validate(raw, srcs)
        print(f"[writer] attempt {attempt + 1}: {len(sc['lines'])} valid lines, {len(sc['places'])} places")
        if best is None or len(sc["lines"]) > len(best["lines"]):
            best = sc
        if len(sc["lines"]) >= 6:
            break
    if len(best["lines"]) < 4:
        raise RuntimeError("too few valid lines")
    now = datetime.now(timezone.utc)
    best["stamp"] = now.strftime("%d%b%y %H%MZ").upper()
    best["date"] = now.strftime("%-d %b %Y").upper()
    best["headline"] = " ".join(best["cover"]) if best["cover"] else best["question"]
    used = sorted({s for ln in best["lines"] for s in ln["src"]})
    best["sources"] = [{"n": i, "outlet": _outlet(srcs[i]), "title": srcs[i]["title"], "url": srcs[i]["url"]}
                       for i in used]
    best["source_urls"] = [s["url"] for s in srcs]
    return best


def caption_text(script: dict) -> str:
    out = [script.get("caption", "").strip(), "", "SOURCES:"]
    for s in script["sources"]:
        out.append(f"{s['outlet']}: {s['url']}")
    if any(l.get("context") for l in script["lines"]):
        out.append("(One background line is general context, not from the sources above.)")
    out += ["", "Automated data desk. Facts from named outlets, not our own reporting.",
            " ".join("#" + str(h).strip("#").replace(" ", "") for h in script.get("hashtags", []))]
    return "\n".join(out).replace("—", ",")
