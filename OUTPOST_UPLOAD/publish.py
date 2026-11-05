"""OUTPOST: post a finished video to TikTok through Buffer (Buffer is a TikTok-approved posting partner).

    python publish.py --probe
        channels, schema details for TikTok posts, and the last posts with their status
    python publish.py --video-url URL --caption-file tiktok.txt [--due-at 2026-11-05T09:00:00Z]
        posts at the exact time given (Buffer customScheduled), or straight away without --due-at
    python publish.py --status [--hours 48]
        the TikTok posts Buffer handled recently, with status (sent, scheduled, error)

Needs the BUFFER_API_KEY secret. The TikTok channel is found automatically (or set BUFFER_CHANNEL_ID).
Buffer fetches the video from a public URL, so the workflow attaches the MP4 to a GitHub release first.
Writes publish_result.json next to the caption file so the desk can read what happened.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

API = "https://api.buffer.com"


def gql(query, variables=None, quiet=False):
    key = os.environ.get("BUFFER_API_KEY", "").strip()
    if not key:
        sys.exit("[buffer] BUFFER_API_KEY is not set")
    for attempt in range(3):
        r = requests.post(API, json={"query": query, "variables": variables or {}},
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                          timeout=60)
        if r.status_code == 429 and attempt < 2:
            wait = min(int(r.headers.get("Retry-After", "30") or 30), 300)
            print(f"[buffer] rate limited, waiting {wait}s")
            time.sleep(wait + 2)
            continue
        break
    try:
        data = r.json()
    except ValueError:
        print(f"[buffer] HTTP {r.status_code}: {r.text[:300]}")
        return {}
    if data.get("errors") and not quiet:
        print("[buffer] errors:", json.dumps(data["errors"])[:800])
    return data.get("data") or {}


def orgs():
    acc = gql("query { account { organizations { id name } } }").get("account") or {}
    return acc.get("organizations") or []


def channels():
    out = []
    for org in orgs():
        d = {}
        for fields in ("id name displayName service isQueuePaused isDisconnected",
                       "id name displayName service isQueuePaused"):
            q = "query($o: OrganizationId!) { channels(input: {organizationId: $o}) { %s } }" % fields
            d = gql(q, {"o": org["id"]}, quiet=True)
            if d:
                break
        for c in d.get("channels") or []:
            c["org"] = org.get("name")
            c["orgId"] = org.get("id")
            out.append(c)
    return out


def tiktok_channel():
    cid = os.environ.get("BUFFER_CHANNEL_ID", "").strip()
    chans = channels()
    tt = [c for c in chans if str(c.get("service", "")).lower() == "tiktok"]
    if cid:
        tt = [c for c in tt if c["id"] == cid] or [{"id": cid, "orgId": chans[0]["orgId"] if chans else ""}]
    if not tt:
        sys.exit("[buffer] no TikTok channel connected in Buffer")
    if tt[0].get("isDisconnected"):
        print("[buffer] WARNING: Buffer says the TikTok channel is disconnected: reconnect it in Buffer")
    return tt[0]


def type_fields(name):
    q = """query($n: String!) { __type(name: $n) { name kind
      fields { name type { name kind ofType { name kind } } }
      inputFields { name type { name kind ofType { name kind } } }
      enumValues { name } } }"""
    t = (gql(q, {"n": name}, quiet=True) or {}).get("__type")
    if not t:
        return f"{name}: not visible (introspection off or no such type)"
    parts = [f["name"] for f in (t.get("fields") or t.get("inputFields") or [])]
    parts += [e["name"] for e in (t.get("enumValues") or [])]
    return f"{name}: " + ", ".join(parts)


POST_FIELDS_TRY = [
    "id text status dueAt sentAt createdAt channelId error { message } externalLink",
    "id text status dueAt sentAt createdAt channelId externalLink",
    "id text status dueAt createdAt channelId",
]


def recent_posts(channel, statuses=("sent", "error", "scheduled"), first=10):
    out = []
    for st in statuses:
        for fields, ctype in [(f, t) for f in POST_FIELDS_TRY for t in ("ChannelId", "String")]:
            q = """query($o: OrganizationId!, $c: %s!) {
              posts(first: %d, input: {organizationId: $o, filter: {status: [%s], channelIds: [$c]},
                    sort: [{field: dueAt, direction: desc}]}) { edges { node { %s } } } }""" % (ctype, first, st, fields)
            d = gql(q, {"o": channel["orgId"], "c": channel["id"]}, quiet=True)
            if d:
                for e in (d.get("posts") or {}).get("edges") or []:
                    n = e["node"]
                    n.setdefault("status", st)
                    out.append(n)
                break
    out.sort(key=lambda n: n.get("dueAt") or "", reverse=True)
    return out


def show(n):
    err = (n.get("error") or {}).get("message") if isinstance(n.get("error"), dict) else n.get("error")
    return (f"[buffer] post {n.get('id')} status={n.get('status')} due={n.get('dueAt')} sent={n.get('sentAt')} "
            f"link={n.get('externalLink')} error={err} text={str(n.get('text'))[:60]!r}")


def create(channel, video_url, text, mode, due_at=None):
    q = """mutation($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess { post { id dueAt } }
        ... on MutationError { message }
      }
    }"""
    inp = {"text": text, "channelId": channel["id"], "schedulingType": "automatic", "mode": mode,
           "assets": [{"video": {"url": video_url, "metadata": {"thumbnailOffset": 0}}}]}
    if due_at:
        inp["dueAt"] = due_at
    d = gql(q, {"input": inp})
    res = d.get("createPost") or {}
    if res.get("post"):
        print(f"[buffer] {mode}: post {res['post'].get('id')} due {res['post'].get('dueAt')}")
        return res["post"]
    print(f"[buffer] {mode} refused: {res.get('message') or d}")
    return None


def watch(channel, post_id, until, every=30):
    """Poll until Buffer reports the post as sent or error, or until the deadline."""
    last = None
    while datetime.now(timezone.utc) < until:
        for n in recent_posts(channel, first=8):
            if n.get("id") == post_id:
                last = n
                if n.get("status") in ("sent", "error"):
                    return n
        time.sleep(every)
    return last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--video-url", default="")
    ap.add_argument("--caption-file", default="")
    ap.add_argument("--due-at", default=os.environ.get("POST_AT", "").strip(),
                    help="ISO time to publish; empty means now")
    ap.add_argument("--wait", type=int, default=int(os.environ.get("BUFFER_WAIT", "300")),
                    help="seconds to wait for Buffer to confirm a post that is due now")
    a = ap.parse_args()

    if a.probe or a.status:
        chans = channels()
        for c in chans:
            print(f"[buffer] channel {c.get('service')}: {c.get('displayName') or c.get('name')} id={c.get('id')} "
                  f"queue_paused={c.get('isQueuePaused')} disconnected={c.get('isDisconnected')} org={c.get('org')}")
        if a.probe:
            for name in ("CreatePostInput", "PostInputMetaData", "TikTokPostMetadataInput", "Post",
                         "PostStatus", "ShareMode", "PostVideoAssetInput", "VideoAssetInput"):
                print("[buffer] schema " + type_fields(name))
        tt = [c for c in chans if str(c.get("service", "")).lower() == "tiktok"]
        for c in tt:
            for n in recent_posts(c, first=10):
                print(show(n))
        return 0

    out_dir = os.path.dirname(os.path.abspath(a.caption_file)) if a.caption_file else "."
    result = {"ok": False, "video_url": a.video_url, "due_at": a.due_at or None}
    text = open(a.caption_file, encoding="utf-8").read().strip() if a.caption_file else ""
    channel = tiktok_channel()
    result["channel"] = channel.get("displayName") or channel.get("name")

    due = None
    if a.due_at:
        try:
            due = datetime.fromisoformat(a.due_at.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            print(f"[buffer] could not read --due-at {a.due_at!r}, posting now instead")
    # a slot already passed (render ran long) or less than 3 minutes away: post now
    if due and due < datetime.now(timezone.utc) + timedelta(minutes=3):
        print(f"[buffer] slot {due.isoformat()} has passed or is too close, posting now")
        due = None

    post = None
    if due:
        post = create(channel, a.video_url, text, "customScheduled", due.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
    if not post:
        post = create(channel, a.video_url, text, "shareNow")
    if not post:
        post = create(channel, a.video_url, text, "shareNext")
    if post:
        result.update(ok=True, post_id=post.get("id"), buffer_due=post.get("dueAt"), status="scheduled")
        if not due and a.wait > 0:
            n = watch(channel, post.get("id"), datetime.now(timezone.utc) + timedelta(seconds=a.wait))
            if n:
                print(show(n))
                err = n.get("error")
                result.update(status=n.get("status"), link=n.get("externalLink"),
                              error=(err or {}).get("message") if isinstance(err, dict) else err)
                if n.get("status") == "error":
                    result["ok"] = False
    with open(os.path.join(out_dir, "publish_result.json"), "w") as f:
        json.dump(result, f, indent=1)
    print("[publish] " + json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
