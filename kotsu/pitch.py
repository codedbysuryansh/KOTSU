"""Data for the pitch screen (/pitch): everything KOTSU has captured, in one payload,
plus the live "whose グッ is this?" check and saving a judge's presses as a new master."""
from __future__ import annotations

import time

FEATURED_FEATURES = ("rise", "peak", "hold", "duration", "steady_cv")


def _profile(st: dict | None) -> dict | None:
    if not st:
        return None
    return {"profile": st.get("profile") or [], "fs": st.get("fs") or 100.0, "features": st.get("features") or {}}


def _featured_word(engine) -> tuple[str, str] | None:
    comp = engine.comparable_words()
    if comp:
        return comp[0]["word"], comp[0]["stype"]
    words = engine.store.data["words"]
    best = max(words.values(), key=lambda w: len(w["pos"]), default=None)
    return (best["word"], best["stype"]) if best else None


def _lessons_by_key(store) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    lessons = store.data.get("lessons") or {}
    for l in (lessons.values() if isinstance(lessons, dict) else lessons):
        out.setdefault(l.get("word_key"), []).append(l)
    return out


def _match_attempts(store, rec: dict, stype: str) -> list[dict]:
    """Coaching records keep scores and times; find the learner's stroke for each attempt."""
    strokes = [s for s in store.data["strokes"].values()
               if s.get("role") == "learner" and s.get("who") == rec.get("learner_id") and s.get("stype") == stype]
    used: set[str] = set()
    out = []
    for a in rec.get("attempts") or []:
        t = a.get("t") or 0
        cands = [s for s in strokes if s["id"] not in used and t - 4.0 <= (s.get("t1") or 0) <= t + 0.5]
        st = max(cands, key=lambda s: s.get("t1") or 0) if cands else None
        if st:
            used.add(st["id"])
        out.append({"score": a.get("score"), "passed": bool(a.get("passed")), "stroke": _profile(st), "sid": st["id"] if st else None})
    return out


def payload(engine) -> dict:
    store = engine.store
    data = store.data
    masters = [{"id": m["id"], "name": m["name"], "color": i} for i, m in enumerate(data["masters"].values())]
    learners = {l["id"]: l["name"] for l in data["learners"].values()}
    res = engine.results()
    out: dict = {"masters": masters, "results": res, "compare": None, "lesson": None, "replay": None,
                 "passport": None, "featured": None, "t": time.time()}
    fw = _featured_word(engine)
    if not fw:
        return out
    word, stype = fw
    out["featured"] = {"word": word, "stype": stype}
    out["compare"] = engine.compare(word, stype)

    # the master who taught this word most often
    by_key = _lessons_by_key(store)
    keys = [k for k, w in data["words"].items() if w["word"] == word and w["stype"] == stype and w["pos"]]
    if not keys:
        return out
    key = max(keys, key=lambda k: (len(by_key.get(k, [])), len(data["words"][k]["pos"])))
    summary = engine.word_summary(key)

    # one real lesson: the try he corrected, his words, his demo
    for l in sorted(by_key.get(key, []), key=lambda l: -(l.get("t") or 0)):
        tr, de = store.stroke(l.get("try_id")) if l.get("try_id") else None, store.stroke(l.get("demo_id")) if l.get("demo_id") else None
        if tr and de:
            out["lesson"] = {"key": key, "word": word, "master": summary["master"], "master_id": summary["master_id"],
                             "text": l.get("text") or "", "try": _profile(tr), "demo": _profile(de),
                             "meaning": summary["meaning"], "bands": summary["bands"], "n_pos": summary["n_pos"],
                             "n_neg": summary["n_neg"], "n_lessons": summary["n_lessons"]}
            break

    # one real coaching session this morning, the biggest jump to a pass
    model = store.model(key)
    best, best_gain = None, -1e9
    for rec in data.get("coaching") or []:
        att = rec.get("attempts") or []
        if rec.get("key") != key or rec.get("condition", "kotsu") != "kotsu" or len(att) < 2 or not rec.get("first_pass"):
            continue
        gain = float(att[rec["first_pass"] - 1].get("score") or 0) - float(att[0].get("score") or 0)
        if gain > best_gain:
            best, best_gain = rec, gain
    if best:
        attempts = _match_attempts(store, best, stype)
        for a in attempts:
            if a["stroke"] and not a["passed"]:
                fb = model.score(a["stroke"]["features"]).get("worst")
                a["note"] = fb["en"] if fb else None
        out["replay"] = {"key": key, "word": word, "master": summary["master"], "master_id": summary["master_id"],
                         "learner": learners.get(best.get("learner_id"), "Learner"),
                         "target": {"profile": summary["prototype"] or [], "fs": summary["fs"], "envelope": summary["envelope"]},
                         "bands": summary["bands"], "attempts": attempts, "first_pass": best.get("first_pass"),
                         "t": best.get("started")}

    # a passport entry for this word, best score first
    entries = []
    for lid, items in (data.get("passports") or {}).items():
        for e in items:
            if e.get("word") == word:
                w = data["words"].get(e.get("word_key") or "", {})
                name = data["masters"].get(w.get("master_id"), {}).get("name") or e.get("master")
                entries.append(dict(e, learner=learners.get(lid, "Learner"), master=name))
    if entries:
        out["passport"] = max(entries, key=lambda e: (e.get("word_key") == key, e.get("score") or 0))
    return out


def whose(engine, sid: str, word: str | None = None) -> dict:
    """How well one stroke fits each master's version of the word."""
    store = engine.store
    st = store.stroke(sid)
    if not st:
        raise KeyError(sid)
    if not word:
        fw = _featured_word(engine)
        word = fw[0] if fw else "グッ"
    rows = []
    masters = store.data["masters"]
    for k, w in store.data["words"].items():
        if w["word"] != word or w["stype"] != st["stype"] or not w["pos"]:
            continue
        m = store.model(k, exclude={sid})
        if not m.pos:
            continue
        sc = m.score(st["features"])
        rows.append({"key": k, "master_id": w["master_id"], "master": masters.get(w["master_id"], {}).get("name", "?"),
                     "score": sc["score"], "passed": sc["passed"], "similarity": round(m.similarity(st["features"]), 4),
                     "note": (sc["worst"] or {}).get("en") if not sc["passed"] else None})
    rows.sort(key=lambda r: (-r["score"], -r["similarity"]))
    feats = {f: st["features"].get(f) for f in FEATURED_FEATURES if f in st["features"]}
    return {"sid": sid, "word": word, "stype": st["stype"], "stroke": _profile(st), "features": feats,
            "ranking": rows, "closest": rows[0] if rows else None}


def save_judge(engine, sids: list[str], word: str | None = None, name: str = "Judge") -> dict:
    """Keep a judge's presses as demos of the word for a new master called Judge."""
    store = engine.store
    if not word:
        fw = _featured_word(engine)
        word = fw[0] if fw else "グッ"
    mid = next((m["id"] for m in store.data["masters"].values() if m["name"].strip().lower() == name.lower()), None)
    if not mid:
        mid = store.add_master(name)["id"]
    key = None
    with engine.lock:
        for sid in sids:
            st = store.stroke(sid)
            if not st:
                continue
            key = store.ensure_word(mid, word, st["stype"])["key"]
            store.add_example(key, sid, True)
            st["role"], st["who"] = "demo", mid
        store.save()
    if not key:
        raise KeyError("no strokes")
    ws = engine.word_summary(key)
    engine.emit("word", word=ws)
    return {"master_id": mid, "word": ws}
