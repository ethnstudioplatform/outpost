"""OUTPOST: send a finished video to TikTok through Buffer (Buffer is a TikTok-approved posting app).

    python publish.py --probe                 list the Buffer channels this API key can see
    python publish.py --video-url URL --caption-file tiktok.txt [--mode shareNow|shareNext|addToQueue]

Needs the BUFFER_API_KEY secret. The TikTok channel is found automatically (or set BUFFER_CHANNEL_ID).
Buffer fetches the video from a public URL, so the workflow uploads the MP4 to a GitHub release first.
"""
import argparse
import json
import os
import sys

import requests

API = "https://api.buffer.com"


def gql(query, variables=None):
    key = os.environ.get("BUFFER_API_KEY", "").strip()
    if not key:
        sys.exit("[buffer] BUFFER_API_KEY is not set")
    r = requests.post(API, json={"query": query, "variables": variables or {}},
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, timeout=60)
    try:
        data = r.json()
    except ValueError:
        sys.exit(f"[buffer] HTTP {r.status_code}: {r.text[:300]}")
    if data.get("errors"):
        print("[buffer] errors:", json.dumps(data["errors"])[:800])
    return data.get("data") or {}


def channels():
    acc = gql("query { account { organizations { id name } } }").get("account") or {}
    out = []
    for org in acc.get("organizations") or []:
        q = "query($o: OrganizationId!) { channels(input: {organizationId: $o}) { id name displayName service isQueuePaused } }"
        d = gql(q, {"o": org["id"]})
        if not d:  # older schema spelling
            q = q.replace("OrganizationId!", "String!")
            d = gql(q, {"o": org["id"]})
        for c in d.get("channels") or []:
            c["org"] = org.get("name")
            out.append(c)
    return out


def tiktok_channel():
    cid = os.environ.get("BUFFER_CHANNEL_ID", "").strip()
    if cid:
        return cid
    tt = [c for c in channels() if str(c.get("service", "")).lower() == "tiktok"]
    if not tt:
        sys.exit("[buffer] no TikTok channel connected in Buffer")
    return tt[0]["id"]


def post(video_url, text, mode):
    q = """mutation($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess { post { id dueAt } }
        ... on MutationError { message }
      }
    }"""
    inp = {"text": text, "channelId": tiktok_channel(), "schedulingType": "automatic", "mode": mode,
           "assets": [{"video": {"url": video_url, "metadata": {"thumbnailOffset": 600}}}]}
    d = gql(q, {"input": inp})
    res = d.get("createPost") or {}
    if res.get("post"):
        print(f"[buffer] queued post {res['post'].get('id')} due {res['post'].get('dueAt')}")
        return True
    print(f"[buffer] not posted: {res.get('message') or d}")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--video-url", default="")
    ap.add_argument("--caption-file", default="")
    ap.add_argument("--mode", default=os.environ.get("BUFFER_MODE", "shareNow"))
    a = ap.parse_args()
    if a.probe:
        for c in channels():
            print(f"[buffer] channel {c.get('service')}: {c.get('displayName') or c.get('name')} id={c.get('id')} "
                  f"queue_paused={c.get('isQueuePaused')} org={c.get('org')}")
        return 0
    text = open(a.caption_file, encoding="utf-8").read().strip() if a.caption_file else ""
    if post(a.video_url, text, a.mode):
        return 0
    # news is time-sensitive: if Buffer refuses an instant post, put it at the front of the queue instead
    if a.mode == "shareNow" and post(a.video_url, text, "shareNext"):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
