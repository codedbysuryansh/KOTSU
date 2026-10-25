"""Mine craft-word corrections from a transcript of real on-the-job teaching.

Paste the transcript of a training video or a recorded OJT session (YouTube's
"Show transcript" text, an .srt/.vtt subtitle file, or plain lines). Every
line the master speaks is parsed the same way KOTSU parses live speech. The
output is a list of moments worth capturing:

  correction : 「ちがう、もっとグッと」  -> the apprentice's try just before,
               the master's demo just after (a try -> word -> demo triplet)
  demo       : 「グッと」 said while showing
  approval   : 「そうそう」 after a try (that try becomes a positive example)

Each moment carries clip windows (seconds) so the video can be cut into
labelled examples. Nothing here invents data: no transcript, no moments.
"""
from __future__ import annotations

import re
from collections import Counter

from .words import parse_utterance, word_info

TRY_BEFORE_S = 6.0
DEMO_AFTER_S = 8.0
TRIPLET_WINDOW_S = 30.0

_TS = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?"
TS_LINE = re.compile(r"^\s*\[?" + _TS + r"\]?\s*(?:-->\s*\S+)?\s*[-–:]?\s*(.*)$")


def _seconds(h: str | None, m: str, s: str, frac: str | None) -> float:
    v = int(h or 0) * 3600 + int(m) * 60 + int(s)
    if frac:
        v += int(frac) / (10 ** len(frac))
    return float(v)


def parse_lines(text: str) -> list[tuple[float | None, str]]:
    """(time in seconds or None, utterance) for every spoken line."""
    out: list[tuple[float | None, str]] = []
    t: float | None = None
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line or line.upper() == "WEBVTT" or re.fullmatch(r"\d+", line):
            continue
        m = TS_LINE.match(line)
        if m:
            t = _seconds(m.group(1), m.group(2), m.group(3), m.group(4))
            rest = m.group(5).strip()
            if not rest:
                continue
            line = rest
        line = re.sub(r"<[^>]+>", "", line).strip()  # vtt styling tags
        if line:
            out.append((t, line))
    return out


def mine(text: str) -> dict:
    lines = parse_lines(text)
    moments = []
    for i, (t, line) in enumerate(lines):
        p = parse_utterance(line)
        if p.intent == "none":
            continue
        if p.intent != "approval" and not p.word:
            continue
        kind = {"correction": "correction", "demonstration": "demo", "bad_example": "wrong-way example",
                "approval": "approval"}[p.intent]
        mo = {"i": i, "t": t, "text": line, "word": p.word, "intent": kind, "new_word": p.is_new_word}
        if t is not None:
            if kind == "correction":
                mo["try_clip"] = [max(0.0, t - TRY_BEFORE_S), t]
                mo["demo_clip"] = [t, t + DEMO_AFTER_S]
            elif kind in ("demo", "wrong-way example"):
                mo["demo_clip"] = [max(0.0, t - 2.0), t + DEMO_AFTER_S]
            elif kind == "approval":
                mo["try_clip"] = [max(0.0, t - TRY_BEFORE_S), t]
        moments.append(mo)

    # carry the word forward to approvals ("そうそう" approves the word being taught)
    current = None
    for mo in moments:
        if mo["word"]:
            current = mo["word"]
        elif mo["intent"] == "approval":
            mo["word"] = current

    # a full lesson: correction, then approval of the same word soon after
    triplets = []
    for a in moments:
        if a["intent"] != "correction":
            continue
        for b in moments:
            if b["i"] <= a["i"] or b["intent"] != "approval" or b["word"] != a["word"]:
                continue
            if a["t"] is not None and b["t"] is not None and b["t"] - a["t"] > TRIPLET_WINDOW_S:
                break
            if a["t"] is None and b["i"] - a["i"] > 6:
                break
            triplets.append({"word": a["word"], "correction": a["text"], "approval": b["text"], "t": a["t"], "t_ok": b["t"]})
            break

    counts = Counter(mo["word"] for mo in moments if mo["word"])
    return {
        "lines": len(lines),
        "moments": moments,
        "triplets": triplets,
        "by_intent": dict(Counter(mo["intent"] for mo in moments)),
        "words": [{"word": w, "count": c, **{k: v for k, v in word_info(w).items() if k in ("romaji", "gloss_en", "known")}}
                  for w, c in counts.most_common()],
        "new_words": sorted({mo["word"] for mo in moments if mo["new_word"] and mo["word"]}),
        "timed": any(t is not None for t, _ in lines),
    }
