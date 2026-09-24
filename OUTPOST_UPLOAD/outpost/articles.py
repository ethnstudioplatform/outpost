"""Fetch the full text of a news article so the script can use real detail,
not just the headline. Plain paragraph extraction, no third-party parser."""
import html
import re

import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
                    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
      "Accept-Language": "en-GB,en;q=0.9"}

JUNK = re.compile(r"(cookie|subscribe|newsletter|sign up|all rights reserved|follow us|"
                  r"read more|download the app|advertisement|copyright)", re.I)


def fetch_text(url: str, limit: int = 7000) -> str:
    try:
        r = requests.get(url, headers=UA, timeout=20)
        r.raise_for_status()
        page = r.text
    except Exception as e:
        print(f"[articles] {url[:70]} failed: {e}")
        return ""
    page = re.sub(r"(?is)<(script|style|noscript|svg|figure|aside|nav|footer|header)[^>]*>.*?</\1>", " ", page)
    paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", page)
    out, seen = [], set()
    for p in paras:
        t = html.unescape(re.sub(r"<[^>]+>", "", p))
        t = re.sub(r"\s+", " ", t).strip()
        if len(t) < 45 or JUNK.search(t) or t in seen:
            continue
        seen.add(t)
        out.append(t)
    text = "\n".join(out)
    return text[:limit]
