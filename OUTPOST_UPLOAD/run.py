"""OUTPOST: one command, one finished video.

    python run.py            live data
    python run.py --demo     fixed Yemen script, for testing the look
    python run.py --no-send  skip the Telegram preview
"""
import argparse
import json
import sys
from datetime import datetime, timezone

from outpost import config, render, sources, telegram, voice, writer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--script", default="", help="render a hand-written script from fixtures/")
    args = ap.parse_args()

    if args.demo or args.script:
        path = config.ROOT / "fixtures" / (args.script or "demo_script_v5.json")
        script = json.loads(path.read_text())
        now = datetime.now(timezone.utc)
        script.update({"stamp": now.strftime("%d%b%y %H%MZ").upper(), "date": now.strftime("%-d %b %Y").upper(),
                       "headline": " ".join(script["cover"])})
        script.setdefault("sources", [])
        args.demo = True
        items = []
    else:
        items = sources.collect()
        if len(items) < 2:
            print("[outpost] not enough fresh trusted items, no video this run")
            return 0
        script = writer.write(items)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%MZ")
    out = config.OUT_DIR / stamp
    out.mkdir(parents=True, exist_ok=True)

    if script.get("voice_speed"):
        voice.VOICE_SPEED = float(script["voice_speed"])
    if script.get("gap") is not None:
        render.GAP = float(script["gap"])
    if script.get("hook"):
        render.LEAD = 0.05  # v6: voice starts on the first frame
    engine = voice.engine_name()
    print(f"[voice] engine: {engine}")
    durs, wavs = [], []
    for i, ln in enumerate(script["lines"]):
        wav = out / f"line{i:02d}.wav"
        durs.append(voice.speak(ln["text"], wav))
        wavs.append(wav)

    timeline, total = render.build_timeline(durs, [l["scene"] for l in script["lines"]])
    audio = out / "audio.wav"
    voice.build_track([(st, w) for (st, _), w in zip(timeline, wavs)] + [(total - 0.2, None)],
                      audio, render.sfx_events(script, timeline))
    for w in wavs:
        w.unlink()

    video = out / "outpost.mp4"
    render.render(script, timeline, total, audio, video, out / "thumb.png")
    audio.unlink()

    cap = writer.caption_text(script)
    (out / "caption.txt").write_text(cap)
    (out / "script.json").write_text(json.dumps(script, indent=2))
    print(f"[outpost] done: {video} ({total:.1f}s), terrain={script.get('_terrain')}")

    if not args.demo:
        seen = sources.load_seen() | set(script.get("source_urls", []))
        sources.save_seen(seen)
    if not args.no_send:
        # Telegram gets the video only, captioned with the post title and hashtags, ready to paste.
        tags = " ".join("#" + str(h).strip("#").replace(" ", "") for h in script.get("hashtags", []))
        title = (script.get("caption") or script.get("headline", "")).strip().replace("\u2014", ",")
        telegram.send_video(video, f"{title}\n\n{tags}".strip())
    (config.ROOT / "out" / "latest.txt").write_text(str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
