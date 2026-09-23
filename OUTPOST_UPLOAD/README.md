# OUTPOST v0.1 // LIVE CONFLICT MONITOR

Automated green-terminal video desk for @outpost.feed (TikTok, Reels, Shorts).

```
GDELT + ReliefWeb  ->  trusted outlets only  ->  Claude writes sourced script
  ->  Piper robot voice + radio filter  ->  1080x1920 phosphor video
  ->  Telegram preview on your phone  ->  you press Approve  ->  post
```

Runs on GitHub Actions twice a day (08:00 and 18:00 UK time). No computer needs to be on.

## What's in here

| File | Job |
|---|---|
| `run.py` | One command, one finished video |
| `outpost/sources.py` | Pulls live headlines, keeps only trusted outlets, skips anything already used |
| `outpost/writer.py` | Claude writes the script. Lines without a source get dropped |
| `outpost/voice.py` | Piper TTS, pitched down, radio band-pass, bit crush, hum bed |
| `outpost/render.py` | The war-room screen: radar sweep, status board, typed log, wire ticker, CRT scanlines |
| `outpost/config.py` | Colours, outlets list, schedule settings |
| `.github/workflows/outpost.yml` | Schedule and approval gate |

## One-time setup (about 20 minutes)

### 1. Put it on GitHub
1. Make a free account at github.com if you don't have one.
2. New repository, name it `outpost`, set it to **Public**. There's nothing secret in the code, your keys go in Secrets. Public is also what makes the approval button free.
3. In Terminal:
   ```
   cd ~/Documents/OUTPOST
   git init && git add . && git commit -m "OUTPOST v0.1"
   git branch -M main
   git remote add origin https://github.com/YOUR-USERNAME/outpost.git
   git push -u origin main
   ```

### 2. Telegram preview bot
1. In Telegram, message **@BotFather**, send `/newbot`, name it e.g. `OUTPOST DESK`. Copy the token.
2. Send your new bot any message (like `hi`).
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser. Find `"chat":{"id":123456789` and copy the number.

### 3. Claude API key
console.anthropic.com > API Keys > Create. Add a few pounds of credit. Each video costs well under a penny of Claude usage.
Without a key it still works, it just reads out a plain headline roundup.

### 4. Add the secrets
Repo > Settings > Secrets and variables > Actions > New repository secret:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | from step 3 |
| `TELEGRAM_BOT_TOKEN` | from step 2 |
| `TELEGRAM_CHAT_ID` | from step 2 |
| `RELIEFWEB_APPNAME` | optional, UN humanitarian reports. Request one free at apidoc.reliefweb.int |

### 5. Turn on the approve button
Repo > Settings > Environments > New environment > name it `publish` > tick **Required reviewers** > add yourself > Save.

### 6. First run
Actions tab > OUTPOST transmission > Run workflow > tick **Use placeholder test data** > Run.
About 5 minutes later the test video lands in Telegram. Then run it again unticked for a real one.

## Daily flow
1. Twice a day a draft video + script + caption arrives in Telegram.
2. Read the script. Any line tagged `[CONTEXT, CHECK]` is background, not from the sources, so check it.
3. Happy: GitHub mobile app > Actions > the run > Review deployments > Approve. The final file and caption come back to Telegram ready to post.
   Not happy: Reject. Nothing happens.

## Editorial rules baked into the writer
- Only headlines from named outlets with real newsrooms (list in `config.py`)
- Every line attributed ("Reuters reports...")
- Government and army statements framed as claims
- No casualty numbers unless an outlet reported them
- No sides, no predictions, no graphic detail
- Sources listed in every caption

## Test on the Mac (optional)
```
brew install ffmpeg espeak-ng
pip3 install -r requirements.txt
python3 run.py --demo --no-send
open out/*/outpost.mp4
```

## Next steps
- Step 2: auto-upload after Approve (YouTube Shorts first, then Instagram, TikTok needs its API audit)
- Step 3: more formats (BY THE NUMBERS, TIMELINE, map plots from ACLED/UCDP)
- Step 4: clone the pipeline for the subject channels
