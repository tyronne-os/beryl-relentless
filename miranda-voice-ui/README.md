# Miranda Voice Studio

Browse and preview all 7 Kokoro female voices — live audio from your local GPU node.
No internet required. Works fully offline (USB-safe).

## Quick start

```bash
# 1. Start your GPU node + Kokoro server (one command)
bash ../miranda.sh

# 2. Open the studio
bash launch.sh
```

That's it. The studio opens in your browser and previews voices directly from your
Kokoro server running at `localhost:8012`.

## Voices

| Voice ID       | Name      | Accent   | Style                   |
|---------------|-----------|----------|-------------------------|
| `af_heart`    | Heart     | American | Warm, conversational    |
| `af_bella`    | Bella     | American | Bright, energetic       |
| `af_sarah`    | Sarah     | American | Clear, professional     |
| `af_nicole`   | Nicole    | American | Calm, measured          |
| `af_sky`      | Sky       | American | Youthful, approachable  |
| `bf_emma`     | Emma      | British  | Polished, articulate    |
| `bf_isabella` | Isabella  | British  | Elegant, confident      |

All voices are built into **Kokoro 82M INT8** — no extra downloads.

## Set the active voice

```bash
# In your .env or shell, before starting Miranda:
export TTS_VOICE=af_heart
```

Or set it in GCP Secret Manager (key: `TTS_VOICE`) so Miranda picks it up automatically.

## Using on USB

1. Copy the entire `miranda-voice-ui/` folder to your USB drive.
2. On any computer, open `index.html` directly in a browser.
3. Change the **API host** field to wherever Kokoro is running
   (e.g. `http://your-server-ip:8012` or a tunnelled address).

## Serve mode (for stricter browsers)

Some browsers block local `file://` audio. If Preview doesn't play:

```bash
bash launch.sh --serve        # serves at http://localhost:8090
bash launch.sh --serve 9000   # custom port
```

## Files

```
miranda-voice-ui/
  index.html    ← the full studio (zero dependencies, works offline)
  launch.sh     ← open or serve the studio
  README.md     ← this file
```

---

Part of the **Berylize Labs** stack — [berylize.com](https://berylize.com)
