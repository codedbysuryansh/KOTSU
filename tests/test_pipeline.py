"""End-to-end check of the engine with synthetic strokes. Run: python -m pytest -q  (or python tests/test_pipeline.py)"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("KOTSU_DATA", tempfile.mkdtemp())

import numpy as np  # noqa: E402

from kotsu.engine import Engine  # noqa: E402
from kotsu.seed import drag_stroke, force_stroke, seed_sample  # noqa: E402
from kotsu.signals import FS, PhoneSegmenter, phone_features, synth_profile  # noqa: E402
from kotsu.store import Store  # noqa: E402


def fresh() -> Engine:
    s = Store(Path(tempfile.mkdtemp()) / "k.json")
    return Engine(s)


def master_a(e: Engine) -> str:
    return next(m["id"] for m in e.store.data["masters"].values() if m["name"].startswith("Sample master A"))


def word_key(e: Engine, word: str) -> str:
    return next(w["key"] for w in e.dictionary(master_a(e)) if w["word"] == word)


def test_lessons_and_bands():
    e = fresh()
    seed_sample(e)
    words = {w["word"]: w for w in e.dictionary(master_a(e))}
    assert {"スーッ", "ピタッ", "グッ", "ジワッ"} <= set(words)
    gu = words["グッ"]
    feats = {b["feature"] for b in gu["bands"]}
    assert "peak" in feats and "rise" in feats, gu["bands"]
    assert words["スーッ"]["n_neg"] == 4 and words["スーッ"]["n_lessons"] == 4


def test_nearmiss_narrows_band():
    e = fresh()
    seed_sample(e)
    key = word_key(e, "グッ")
    before = {b["feature"]: (b["lo"], b["hi"]) for b in e.word_summary(key)["bands"]}
    asked = 0
    for _ in range(6):
        q = e.nearmiss_next(key)
        if not q:
            break
        asked += 1
        # a simulated master: グッ needs a peak of at least 1.8 and a rise under 0.15 s
        ok = True
        if q["feature"] == "peak":
            ok = q["value"] >= 1.8
        if q["feature"] == "rise":
            ok = q["value"] <= 0.15
        e.nearmiss_answer(q["id"], ok)
    after = {b["feature"]: (b["lo"], b["hi"]) for b in e.word_summary(key)["bands"]}
    assert asked >= 3
    assert after != before


def test_coaching_to_passport():
    e = fresh()
    seed_sample(e)
    learner = e.store.add_learner("Ravi", "en")
    key = word_key(e, "スーッ")
    e.coach_start(learner["id"], key)
    bad = e.ingest_stroke("drag-1", drag_stroke("flat", seed=99, duration=1.4, peak=1.0, rise=0.2, stop=0.2, jitter=0.3))
    ev = [x for x in e.drain() if x["type"] == "stroke"][-1]
    assert ev["coach"]["passed"] is False and ev["coach"]["feedback"]["en"]
    for i in range(8):
        e.ingest_stroke("drag-1", drag_stroke("flat", seed=200 + i, duration=1.55, peak=1.0, rise=0.2, stop=0.2, jitter=0.02))
    evs = [x for x in e.drain() if x["type"] == "stroke"]
    assert evs[-1]["coach"]["level"] == 0, [x["coach"] for x in evs]
    e.coach_test()
    for i in range(3):
        e.ingest_stroke("drag-1", drag_stroke("flat", seed=300 + i, duration=1.55, peak=1.0, rise=0.2, stop=0.2, jitter=0.02))
    last = [x for x in e.drain() if x["type"] == "stroke"][-1]["coach"]
    assert last["test"]["done"] and last["test"]["passed"], last
    pp = e.passport(learner["id"])
    assert pp["entries"] and pp["entries"][0]["word"] == "スーッ"


def test_drill_pair():
    e = fresh()
    seed_sample(e)
    key = word_key(e, "ピタッ")
    d = e.drill_next(key, None)
    assert d and len(d["A"]) > 10 and len(d["B"]) > 10
    r = e.drill_answer(d["id"], "A")
    assert r["answer"] in ("A", "B")


def test_phone_segmenter_and_features():
    seg = PhoneSegmenter()
    rng = np.random.default_rng(1)
    samples = []
    t = 0.0
    # 1 s idle noise, a 1.2 s stroke (accelerate then decelerate), 1 s idle
    for i in range(60):
        t += 1000 / 60
        samples.append((t, *(0.03 * rng.standard_normal(3)), *(2 * rng.standard_normal(3))))
    n = 72
    for i in range(n):
        t += 1000 / 60
        a = 2.5 * np.sin(2 * np.pi * i / n)
        samples.append((t, a + 0.05 * rng.standard_normal(), 0.05 * rng.standard_normal(), 0.0, 5.0, 3.0, 1.0))
    for i in range(60):
        t += 1000 / 60
        samples.append((t, *(0.03 * rng.standard_normal(3)), *(2 * rng.standard_normal(3))))
    strokes = seg.push(samples)
    assert len(strokes) == 1, len(strokes)
    speed, feats = phone_features(strokes[0])
    assert 0.8 < feats["duration"] < 2.0 and feats["peak"] > 0.2, feats


def test_utterance_flow_with_new_word():
    e = fresh()
    # a judge's own word, said right after pressing the trackpad
    e.ingest_stroke("force-1", force_stroke("flat", seed=1, duration=0.7, peak=2.0, rise=0.06, stop=0.1, jitter=0.02))
    r = e.utterance("ズバッと")
    assert r["word"] == "ズバッ" and "labelled" in r["action"]
    assert any(w["word"] == "ズバッ" for w in e.dictionary())


def test_same_word_two_masters():
    e = fresh()
    seed_sample(e)
    c = e.compare("グッ", "force")
    assert len(c["masters"]) == 2
    assert c["who"]["n"] == 8 and c["who"]["accuracy"] >= 0.75, c["who"]
    m = {x["master"]: x for x in c["masters"]}
    pa = next(v for k, v in m.items() if " A " in k)
    pb = next(v for k, v in m.items() if " B " in k)
    peak_a = next(b["center"] for b in pa["bands"] if b["feature"] == "peak")
    peak_b = next(b["center"] for b in pb["bands"] if b["feature"] == "peak")
    assert peak_b > peak_a
    assert {"word": "グッ", "stype": "force", "masters": 2} in e.comparable_words()


def test_recognition_and_pilot():
    e = fresh()
    seed_sample(e)
    r = e.recognition()
    assert r["n"] >= 20 and r["accuracy"] is not None and r["accuracy"] > r["chance"], r
    learner = e.store.add_learner("Asha", "en")
    key = word_key(e, "スーッ")
    e.coach_start(learner["id"], key, condition="word_only")
    e.ingest_stroke("drag-1", drag_stroke("flat", seed=7, duration=1.4, peak=1.0, rise=0.2, stop=0.2, jitter=0.3))
    ev = [x for x in e.drain() if x["type"] == "stroke"][-1]["coach"]
    assert ev["feedback"] is None and ev["features"] == [] and ev["condition"] == "word_only"
    e.ingest_stroke("drag-1", drag_stroke("flat", seed=8, duration=1.55, peak=1.0, rise=0.2, stop=0.2, jitter=0.02))
    e.coach_start(learner["id"], word_key(e, "ピタッ"), condition="kotsu")
    e.ingest_stroke("drag-1", drag_stroke("flat", seed=9, duration=1.0, peak=1.1, rise=0.2, stop=0.04, jitter=0.03))
    p = e.pilot()
    assert p["word_only"]["sessions"] == 1 and p["word_only"]["tries"] == [2], p
    assert p["kotsu"]["sessions"] == 1 and p["kotsu"]["tries"] == [1], p
    res = e.results()
    assert res["counts"]["synthetic_masters"] == 2


def test_transcript_mining():
    from kotsu.mine import mine
    text = """0:05
はい、じゃあやってみて
0:12
ちがう、もっとグッと
0:20 そうそう、それ
00:00:31,000 --> 00:00:33,000
ここをグイっと持ち上げて
1:02 ダメな例、こうするとガタガタになる
1:10 最後はピタッと止める
1:15 ズバッと切る
"""
    r = mine(text)
    words = {w["word"] for w in r["words"]}
    assert {"グッ", "グイッ", "ガタガタ", "ピタッ", "ズバッ"} <= words, r["words"]
    corr = [m for m in r["moments"] if m["intent"] == "correction"]
    assert corr and corr[0]["t"] == 12.0 and corr[0]["try_clip"] == [6.0, 12.0]
    assert r["triplets"] and r["triplets"][0]["word"] == "グッ"
    assert "ズバッ" in r["new_words"]
    assert any(m["t"] == 31.0 and m["word"] == "グイッ" for m in r["moments"])


def test_pitch_screen_data():
    from kotsu import pitch
    e = fresh()
    seed_sample(e)
    p = pitch.payload(e)
    assert p["featured"] and len(p["compare"]["masters"]) == 2 and len(p["masters"]) >= 2, p["featured"]
    assert p["lesson"] is None or (p["lesson"]["try"]["profile"] and p["lesson"]["demo"]["profile"])
    # a new press is scored against each master's グッ, and can become a new master's demo
    e.set_session(mode="pitch")
    d = e.ingest_stroke("force-1", force_stroke("press", seed=41, duration=0.6, peak=2.7, rise=0.12, stop=0.08))
    assert d["role"] == "free"
    w = pitch.whose(e, d["id"], "グッ")
    assert len(w["ranking"]) == 2 and w["closest"]["score"] >= w["ranking"][-1]["score"], w["ranking"]
    saved = pitch.save_judge(e, [d["id"]], "グッ")
    assert saved["word"]["master"] == "Judge" and saved["word"]["n_pos"] == 1
    assert len(e.compare("グッ", "force")["masters"]) == 3


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
