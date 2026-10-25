#!/usr/bin/env python3
"""Rehearse the Listen screen without any device: sends synthetic strokes and
the master's spoken corrections to a running KOTSU server.

    python tools/simulate.py                 # one lesson of each kind, slowly
    python tools/simulate.py --fast          # no pauses
    python tools/simulate.py --url http://localhost:8010

Everything this sends is synthetic and shows up as source "sim-…". Use it to
rehearse, never as the live demo.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kotsu.seed import drag_stroke, force_stroke  # noqa: E402


def post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--fast", action="store_true")
    a = ap.parse_args()
    pause = 0 if a.fast else 1.6
    post(a.url, "/api/session", {"mode": "capture"})

    def stroke(payload: dict, src: str) -> None:
        payload = dict(payload, src=src)
        post(a.url, "/api/stroke", payload)
        time.sleep(pause)

    def say(text: str) -> None:
        r = post(a.url, "/api/utterance", {"text": text, "source": "simulator"})
        print(f"  master: 「{text}」 -> {r.get('intent')} {r.get('word') or ''} : {r.get('action')}")
        time.sleep(pause)

    print("Lesson 1: スーッ (the apprentice's stroke shakes; the master corrects and shows)")
    stroke(drag_stroke("flat", seed=501, duration=1.3, peak=1.0, rise=0.25, stop=0.25, jitter=0.3), "sim-drag")
    say("ちがう、もっとスーッと")
    stroke(drag_stroke("flat", seed=502, duration=1.6, peak=1.0, rise=0.2, stop=0.2, jitter=0.02), "sim-drag")
    print("Lesson 2: グッ on the trackpad (too soft; the master presses firmly)")
    stroke(force_stroke("flat", seed=503, duration=0.9, peak=1.0, rise=0.4, stop=0.2, jitter=0.02), "sim-force")
    say("ちがう、もっとグッと")
    stroke(force_stroke("flat", seed=504, duration=0.8, peak=2.4, rise=0.08, stop=0.15, jitter=0.02), "sim-force")
    print("Lesson 3: approval (the apprentice gets it right)")
    stroke(force_stroke("flat", seed=505, duration=0.8, peak=2.3, rise=0.09, stop=0.15, jitter=0.02), "sim-force")
    say("そうそう、それ")
    print("Done. Open the Listen and Dictionary tabs.")


if __name__ == "__main__":
    main()
