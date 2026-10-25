# KOTSU コツ — the master's words, measured

KOTSU learns what a master's own craft words (グッ, スーッ, ピタッ, ジワッ…) mean in
measured motion and pressure, from the corrections the master already gives on the job.
It then teaches a foreign learner the master's Japanese word, with feedback in English
that fades away, and records an unassisted test as a skill passport entry.

No extra hardware: phones (motion sensors, touch, microphone), laptop trackpads (drag),
and the Force Touch trackpad of a MacBook in Safari (pressure).

## 1. Install (once, on the Mac that runs the server)

```bash
cd kotsu
python3 -m pip install -r requirements.txt
brew install cloudflared          # only for phones' motion sensors (gives an https address)
python3 tests/test_pipeline.py    # 9 checks, all should print "ok"
```

Windows: `py -m pip install -r requirements.txt` and `winget install --id Cloudflare.cloudflared`.

## 2. Run

```bash
python3 run.py --tunnel           # live: dashboard + public https address + QR code for phones
python3 run.py --seed             # rehearsal: adds two synthetic masters to every screen
python3 run.py --port 8010        # if 8000 is taken
python3 run.py --data ../kotsu-app-v3/data   # keep using the results you captured with v3
```

To present with this morning's captures without touching the v3 folder, copy them in first:
`cp -R ../kotsu-app-v3/data ./data && python3 run.py --port 8010`. Then in **Setup**, rename the
masters to match the slides (Real Master → Master A, Guest master → Master B, Master → Master C)
and add a master called **Judge** for the judge's own グッ. The Dictionary card shows up to four masters.

The dashboard opens at http://localhost:8000 — use **Chrome** (Japanese speech recognition).

**Pitch screen:** http://localhost:8000/pitch (use the same port you started with). Fifteen tabs that read this laptop's data live: → or Space for the next step, ← back, L jumps to Live, G to the knack graph, F full screen, ? for keys. In Safari on a Force Touch MacBook the Live tab reads the trackpad itself; in Chrome, press on the `/force` station (Safari) of any laptop. Put a workshop clip at `data/media/field.mp4` and the Real skills tab shows a play button.
On the projector, open http://localhost:8000/?demo : only the five demo tabs, slightly larger.

| Device | Open | Browser | What it gives |
|---|---|---|---|
| Server Mac (projector) | `http://localhost:8000/` | Chrome | dashboard, master's microphone, drag pad |
| Phone | `https://….trycloudflare.com/phone` (QR in terminal and in **Connect a phone**) | Safari / Chrome | Tool mode: motion of the phone held like a tool. Draw mode: a finger stroke |
| Second MacBook | `https://….trycloudflare.com/force` | **Safari** | Force Touch pressure (グッ, ジワッ, ギュッ) |
| Learner's laptop (remote) | `https://….trycloudflare.com/` | Chrome | the same dashboard, Coach tab |

Phones need https for motion sensors; without the tunnel use the phone's **Draw** mode, the
laptop drag pad, or the trackpad station. Use a phone hotspot rather than venue Wi-Fi.
Speech recognition needs internet; if it fails, tap the word buttons on Listen.

## 3. The loop on screen

1. **Listen** — the apprentice makes a stroke; the master says 「ちがう、もっとスーッと」 and shows it.
   KOTSU stores the try (negative), the word, and the demo (positive) as one lesson.
   Also understood: 「グッと」 said while showing (demo), 「そうそう」 (approves the last stroke),
   「ダメな例」 (the next stroke is the wrong way). New words (ズバッ) are added automatically.
2. **Dictionary** — each word as this master means it: the features it is about, the master's
   range (shaded), and a plain meaning in English. **Same word, different masters** overlays
   two masters' versions of one word and reports whether KOTSU can tell whose it is.
3. **Knack graph** — masters and learners on the left, the master's words in the middle, the measured
   feel on the right. The lines are the meaning KOTSU learned from each master's corrections; click a
   feature (for example rise time) to see every master's range for the same word. Learner lines carry
   守 (practising with help), 破 (reached the master's range) or 離 (passed alone). Lessons light up live.
4. **Coach** — the learner practises the master's word. Feedback names the biggest measured gap
   and repeats the master's word, for example 「スーッ」. Help fades after two passes in a row.
   **Word only (control)** gives just the word and ✓/✗ — use it for the pilot.
5. **Passport** — an unassisted test (3 strokes, 2 must pass) becomes an entry with the master's
   name, the stroke, the score and a check code.
6. **Results** — held-out word recognition, whose-word accuracy, and the pilot table
   (tries to first reach the master's range: word only vs KOTSU feedback).

Extra tabs: **Ask** (KOTSU changes one thing about the master's stroke and asks 「これも○○？」;
each はい/いいえ moves the edge of the range), **Drill** (pick the master's stroke from a near-miss),
**Transcripts** (find corrections in a real OJT video transcript), **Robot** (simulated replay).

## 4. Honesty rules for the pitch

- `--seed` data and `tools/simulate.py` are **synthetic**. Their masters are named "(synthetic)".
  Reset (Setup → Reset everything…) before showing live numbers.
- `examples/example_transcript_synthetic.txt` is synthetic. Show a real transcript in the pitch.
- The pilot is a pilot: say the n out loud.

## 5. Tools

```bash
python3 tools/simulate.py --url http://localhost:8000   # rehearse Listen without devices (synthetic)
python3 tools/mine_transcript.py transcript.txt --csv moments.csv
```

## 6. Files

```
run.py                 launcher (tunnel, QR, seed)
kotsu/words.py         craft-word lexicon, parsing what the master says, new-word detection
kotsu/signals.py       features of a stroke; phone motion segmentation
kotsu/model.py         per-master word models: salience, ranges, scoring, plain-English feedback
kotsu/nearmiss.py      near-miss strokes for questions and drills
kotsu/engine.py        lessons, coaching with fading help, passport, results
kotsu/mine.py          transcript mining
kotsu/store.py         JSON persistence (data/kotsu.json) and voice clips
kotsu/server.py        FastAPI + WebSocket
static/                dashboard, phone page, trackpad station
tests/test_pipeline.py end-to-end checks
```

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| Phone says motion needs https | open the trycloudflare link (QR), not the 192.168… one |
| iPhone shows no readings | tap Start, then Allow motion access; reload if it was refused |
| Trackpad page shows no pressure | open /force in Safari on a MacBook with Force Touch |
| Speech shows "network" error | needs internet (hotspot); or tap the word buttons |
| Strokes are scored instead of captured | another laptop is coaching: press **Switch to listening** |
| Port in use | `python3 run.py --port 8010` |
| Start over | Setup → Reset everything… → type RESET |
