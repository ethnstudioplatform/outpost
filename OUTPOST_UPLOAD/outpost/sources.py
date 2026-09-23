"""Live data intake. Each source returns a list of items:
{"title", "url", "domain", "date", "country", "source"}
Every fact that reaches a video must trace back to one of these items.
"""
import json
import time
from urllib.parse import urlparse

import requests

from . import config

UA = {"User-Agent": "OUTPOST/0.1 (conflict data desk)"}


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _trusted(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in config.TRUSTED_DOMAINS)


def gdelt() -> list[dict]:
    """GDELT DOC 2.0: global news monitoring, no key needed."""
    params = {
        "query": config.GDELT_QUERY,
        "mode": "artlist",
        "format": "json",
        "maxrecords": 150,
        "timespan": config.GDELT_TIMESPAN,
        "sort": "hybridrel",
    }
    for attempt in range(3):
        try:
            r = requests.get("https://api.gdeltproject.org/api/v2/doc/doc",
                             params=params, headers=UA, timeout=30)
            r.raise_for_status()
            arts = r.json().get("articles", [])
            break
        except Exception as e:  # GDELT rate limits hard, back off and retry
            print(f"[gdelt] attempt {attempt + 1} failed: {e}")
            time.sleep(6 * (attempt + 1))
    else:
        return []

    items = []
    for a in arts:
        dom = _domain(a.get("url", ""))
        if not _trusted(dom):
            continue
        items.append({
            "title": a.get("title", "").strip(),
            "url": a.get("url"),
            "domain": dom,
            "date": a.get("seendate", ""),
            "country": a.get("sourcecountry", ""),
            "source": "GDELT",
        })
    print(f"[gdelt] {len(arts)} articles, {len(items)} from trusted outlets")
    return items


def reliefweb() -> list[dict]:
    """UN OCHA ReliefWeb. Needs an approved appname (free, request on reliefweb.int)."""
    if not config.RELIEFWEB_APPNAME:
        return []
    body = {
        "limit": 20,
        "sort": ["date.created:desc"],
        "filter": {"field": "theme.name", "value": "Protection and Human Rights"},
        "fields": {"include": ["title", "url_alias", "source.shortname",
                               "date.created", "primary_country.name"]},
    }
    try:
        r = requests.post("https://api.reliefweb.int/v2/reports",
                          params={"appname": config.RELIEFWEB_APPNAME},
                          json=body, headers=UA, timeout=30)
        r.raise_for_status()
        data = r.json().get("data", [])
    except Exception as e:
        print(f"[reliefweb] failed: {e}")
        return []
    items = []
    for d in data:
        f = d.get("fields", {})
        src = (f.get("source") or [{}])[0].get("shortname", "ReliefWeb")
        items.append({
            "title": f.get("title", "").strip(),
            "url": f.get("url_alias") or f"https://reliefweb.int/node/{d.get('id')}",
            "domain": "reliefweb.int",
            "date": f.get("date", {}).get("created", ""),
            "country": (f.get("primary_country") or {}).get("name", ""),
            "source": f"ReliefWeb/{src}",
        })
    print(f"[reliefweb] {len(items)} reports")
    return items


def load_seen() -> set:
    try:
        return set(json.loads(config.STATE_FILE.read_text())["seen"])
    except Exception:
        return set()


def save_seen(urls: set):
    keep = sorted(urls)[-2000:]
    config.STATE_FILE.write_text(json.dumps({"seen": keep}, indent=0))


def collect(demo: bool = False) -> list[dict]:
    if demo:
        return json.loads((config.ROOT / "fixtures" / "demo_items.json").read_text())
    seen = load_seen()
    items, urls, titles = [], set(), set()
    for it in gdelt() + reliefweb():
        key = it["title"].lower()[:60]
        if not it["title"] or it["url"] in seen or it["url"] in urls or key in titles:
            continue
        urls.add(it["url"])
        titles.add(key)
        items.append(it)
    print(f"[collect] {len(items)} fresh items")
    return items[:40]
