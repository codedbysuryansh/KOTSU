"""Synthetic sample data for rehearsal, clearly labelled as such.

Runs real lessons through the engine (tries, corrections, demos) so every
screen has something to show before a live capture. Two synthetic masters
teach the same words with different hands, so the "same word, two masters"
comparison has something to compare. Never present these masters as real
people: their names say "(synthetic)".
"""
from __future__ import annotations

import time

import numpy as np

from .signals import FS, synth_profile


def _positions_from_speed(speed: np.ndarray, px_scale: float = 600.0) -> tuple[list, list, list]:
    t = (np.arange(len(speed)) / FS * 1000.0).tolist()
    x = np.cumsum(speed / FS) * px_scale
    y = 300 + 0.0 * x
    return t, x.tolist(), y.tolist()


def drag_stroke(kind: str, seed: int, **kw) -> dict:
    s = synth_profile(kind, seed=seed, **kw)
    t, x, y = _positions_from_speed(s)
    return {"stype": "drag", "t": t, "x": x, "y": y}


def force_stroke(kind: str, seed: int, **kw) -> dict:
    s = synth_profile(kind, seed=seed, **kw) * 1.0
    t = (np.arange(len(s)) / FS * 1000.0).tolist()
    return {"stype": "force", "t": t, "f": np.clip(s, 0, 3).tolist()}


def seed_sample(engine) -> int:
    store = engine.store
    prev = (engine.mode, engine.master_id)
    rng = np.random.default_rng(3)
    n = 0

    def stroke(payload: dict) -> None:
        nonlocal n
        engine.ingest_stroke("drag-seed" if payload["stype"] == "drag" else "force-seed", payload)
        n += 1

    def jit(v: float, rel: float = 0.06) -> float:
        return float(v * (1.0 + rel * rng.standard_normal()))

    # ---------- master A: quick, crisp hands ----------
    a = store.add_master("Sample master A (synthetic)")
    engine.set_session(mode="capture", master_id=a["id"])
    for i in range(4):  # スーッ: one smooth, even glide, corrected from a shaky try
        stroke(drag_stroke("flat", seed=10 + i, duration=1.4, peak=1.0, rise=0.25, stop=0.25, jitter=0.25))
        engine.utterance("ちがう、もっとスーッと", source="seed")
        stroke(drag_stroke("flat", seed=20 + i, duration=jit(1.5), peak=jit(1.0), rise=0.2, stop=0.2, jitter=0.02))
    for i in range(4):  # ピタッ: a dead stop at the end
        stroke(drag_stroke("flat", seed=30 + i, duration=1.0, peak=1.1, rise=0.2, stop=0.35, jitter=0.03))
        engine.utterance("もっとピタッと", source="seed")
        stroke(drag_stroke("flat", seed=40 + i, duration=jit(1.0), peak=jit(1.1), rise=0.2, stop=0.04, jitter=0.03))
    for i in range(4):  # グッ on the trackpad: fast rise, high peak
        stroke(force_stroke("flat", seed=50 + i, duration=0.9, peak=1.0, rise=0.45, stop=0.2, jitter=0.02))
        engine.utterance("ちがう、グッと", source="seed")
        stroke(force_stroke("flat", seed=60 + i, duration=jit(0.8), peak=jit(2.3), rise=jit(0.08), stop=0.15, jitter=0.02))
    for i in range(4):  # ジワッ on the trackpad: slow build-up
        stroke(force_stroke("slow", seed=70 + i, duration=jit(1.6), peak=jit(1.6), rise=jit(1.1), stop=0.2, jitter=0.02))
        engine.utterance("ジワッと", source="seed")

    # ---------- master B: the same words, heavier and slower hands ----------
    b = store.add_master("Sample master B (synthetic)")
    engine.set_session(mode="capture", master_id=b["id"])
    for i in range(4):  # B's グッ: a heavier press that arrives later and is held
        stroke(force_stroke("flat", seed=80 + i, duration=1.0, peak=1.2, rise=0.12, stop=0.2, jitter=0.02))
        engine.utterance("ちがう、もっとグッと", source="seed")
        stroke(force_stroke("flat", seed=90 + i, duration=jit(1.4), peak=jit(2.8), rise=jit(0.3), stop=0.25, jitter=0.02))
    for i in range(4):  # B's スーッ: a long, slow glide
        stroke(drag_stroke("flat", seed=100 + i, duration=1.2, peak=1.0, rise=0.2, stop=0.2, jitter=0.2))
        engine.utterance("ちがう、スーッと", source="seed")
        stroke(drag_stroke("flat", seed=110 + i, duration=jit(2.3), peak=jit(0.7), rise=0.35, stop=0.35, jitter=0.02))
    for i in range(4):  # B's ジワッ
        stroke(force_stroke("slow", seed=120 + i, duration=jit(2.2), peak=jit(2.0), rise=jit(1.6), stop=0.25, jitter=0.02))
        engine.utterance("ジワッと", source="seed")

    engine.set_session(mode=prev[0], master_id=a["id"])
    time.sleep(0.01)
    return n
