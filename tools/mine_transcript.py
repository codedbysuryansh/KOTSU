#!/usr/bin/env python3
"""Find craft-word lessons in a transcript file, offline.

    python tools/mine_transcript.py transcript.txt
    python tools/mine_transcript.py lesson.srt --csv moments.csv

Accepts YouTube "Show transcript" text (time on its own line or before the
text), .srt and .vtt subtitles, or plain lines without times.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kotsu.mine import mine  # noqa: E402


def fmt(t):
    if t is None:
        return "-"
    return f"{int(t // 60)}:{int(t % 60):02d}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--csv", default=None, help="also write the moments to a CSV file")
    a = ap.parse_args()
    r = mine(Path(a.file).read_text(encoding="utf-8", errors="replace"))
    print(f"{r['lines']} lines read · {len(r['moments'])} moments · {len(r['triplets'])} full lessons (correction -> approval)")
    for w in r["words"]:
        print(f"  {w['word']:<8} x{w['count']}  {'' if w['known'] else '(new word)'}")
    print()
    for m in r["moments"]:
        clips = []
        if m.get("try_clip"):
            clips.append(f"try {fmt(m['try_clip'][0])}-{fmt(m['try_clip'][1])}")
        if m.get("demo_clip"):
            clips.append(f"demo {fmt(m['demo_clip'][0])}-{fmt(m['demo_clip'][1])}")
        print(f"{fmt(m['t']):>6}  {m['intent']:<18} {m['word'] or '':<8} {m['text']}   {' · '.join(clips)}")
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            wr.writerow(["time_s", "intent", "word", "new_word", "text", "try_from", "try_to", "demo_from", "demo_to"])
            for m in r["moments"]:
                tc, dc = m.get("try_clip") or ["", ""], m.get("demo_clip") or ["", ""]
                wr.writerow([m["t"], m["intent"], m["word"] or "", m["new_word"], m["text"], *tc, *dc])
        print("\nWrote " + a.csv)


if __name__ == "__main__":
    main()
