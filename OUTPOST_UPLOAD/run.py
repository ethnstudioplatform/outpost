"""OUTPOST: one command, one finished video.

    python run.py            live data
    python run.py --demo     placeholder data, for testing the look
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
    args = ap.parse_args()

    items = sources.collect(demo=args.demo)
    if len(items) < 2:
        print("[outpost] not enough fresh trusted items, no video this run")
        return 0

    script = writer.write(items)
    if args.demo:
        demo = json.loads((config.ROOT / "fixtures" / "demo_script.json").read_text())
        script.update({k: demo[k] for k in ("headline", "region", "location", "lines", "caption")})
        used = sorted({s for ln in script["lines"] for s in ln["src"]})
        script["sources"] = [{"n": i, "outlet": items[i]["domain"], "title": items[i]["title"],
                              "url": items[i]["url"]} for i in used]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%MZ")
    out = config.OUT_DIR / stamp
    out.mkdir(parents=True, exist_ok=True)

    print(f"[voice] engine: {voice.engine_name()}")
    durs, wavs = [], []
    for i, ln in enumerate(script["lines"]):
        wav = out / f"line{i:02d}.wav"
        durs.append(voice.speak(ln["text"], wav))
        wavs.append(wav)

    timeline, total = render.build_timeline(durs)
    audio = out / "audio.wav"
    beeps = [0.15 + i * 0.38 for i in range(5)] + [st for st, _ in timeline]
    voice.build_track([(st, w) for (st, _), w in zip(timeline, wavs)] + [(total - 0.1, None)],
                      audio, beeps)
    for w in wavs:
        w.unlink()

    video = out / "outpost.mp4"
    render.render(script, timeline, total, audio, video, out / "thumb.png")
    audio.unlink()

    cap = writer.caption_text(script)
    (out / "caption.txt").write_text(cap)
    (out / "script.json").write_text(json.dumps(script, indent=2))
    print(f"[outpost] done: {video} ({total:.1f}s)")

    if not args.demo:
        seen = sources.load_seen() | {it["url"] for it in items}
        sources.save_seen(seen)
    if not args.no_send:
        review = "\n".join(f"{i+1}. {l['text']}" + ("  [CONTEXT, CHECK]" if l.get("context") else "")
                           for i, l in enumerate(script["lines"]))
        telegram.send_video(video, f"OUTPOST DRAFT {script['stamp']}\n{script['headline']}")
        telegram.send_text("SCRIPT\n" + review + "\n\nCAPTION\n" + cap +
                           "\n\nApprove or reject in GitHub > Actions.")
    # expose paths to GitHub Actions
    gh = config.ROOT / "out" / "latest.txt"
    gh.write_text(str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
