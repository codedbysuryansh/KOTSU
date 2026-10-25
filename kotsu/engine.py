"""The KOTSU engine: lessons from real teaching, grounding, questions, coaching.

Framework-free so it can be tested without a server. The web server calls
these methods and broadcasts whatever lands in `self.events`.

Modes
  capture : master and apprentice are working. Strokes are tries or demos;
            the master's words decide which (LISTEN -> GROUND).
  coach   : a learner practises one word; every stroke is scored (TRANSFER).
  free    : strokes are only named ("looks like グッ").
"""
from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np

from .nearmiss import drill_pair, next_question
from .signals import (FS, INTENSITY_LABEL, PhoneSegmenter, Stroke, decimate_profile, features_from_explicit,
                      generic_features, phone_features, source_type)
from .store import Store, new_id
from .words import parse_utterance, word_info

TRY_WINDOW_S = 120.0      # a correction refers to a stroke that ended at most this long ago (roleplay needs time)
DEMO_WINDOW_S = 120.0     # the master's demo must come within this long after the word
WORD_AFTER_ACTION_S = 3.0  # "say the word while you do it": label a stroke that just ended


class Engine:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.lock = threading.RLock()
        self.events: deque = deque(maxlen=2000)
        self.mode = "capture"
        self.master_id: str | None = None
        self.learner_id: str | None = None
        self.current_word: str | None = None
        self.pending: dict | None = None
        self.recent: deque = deque(maxlen=50)       # (stroke_id, t1, src, labelled)
        self.segmenters: dict[str, PhoneSegmenter] = {}
        self.coach: dict | None = None
        self.questions: dict[str, dict] = {}
        self.drills: dict[str, dict] = {}
        self.sources: dict[str, float] = {}         # src -> last seen
        self.rng = np.random.default_rng(11)
        if not self.store.data["masters"]:
            m = self.store.add_master("Master")
            self.master_id = m["id"]
        else:
            self.master_id = next(iter(self.store.data["masters"]))

    # ---------------- events ----------------
    def emit(self, typ: str, **payload) -> None:
        payload["type"] = typ
        payload["t"] = time.time()
        self.events.append(payload)

    def drain(self) -> list[dict]:
        out = []
        while self.events:
            out.append(self.events.popleft())
        return out

    # ---------------- session ----------------
    def set_session(self, mode: str | None = None, master_id: str | None = None, learner_id: str | None = None,
                    word: str | None = None) -> dict:
        with self.lock:
            if mode:
                self.mode = mode
            if master_id:
                self.master_id = master_id
            if learner_id:
                self.learner_id = learner_id
            if word is not None:
                self.current_word = word or None
            self.pending = None
            self.emit("session", session=self.session())
            return self.session()

    def session(self) -> dict:
        return {"mode": self.mode, "master_id": self.master_id, "learner_id": self.learner_id,
                "current_word": self.current_word, "pending": self.pending,
                "coach": self._coach_public(), "sources": {k: round(time.time() - v, 1) for k, v in self.sources.items()}}

    # ---------------- ingestion ----------------
    def seen(self, src: str) -> None:
        self.sources[src] = time.time()

    def ingest_samples(self, src: str, samples: list[list[float]]) -> list[dict]:
        """Continuous phone motion: segment into strokes."""
        with self.lock:
            self.seen(src)
            seg = self.segmenters.setdefault(src, PhoneSegmenter())
            tuples = [tuple(float(v) for v in s) for s in samples if len(s) >= 7]
            done = seg.push(tuples)
            out = []
            for stroke_samples in done:
                speed, feats = phone_features(stroke_samples)
                dur = (stroke_samples[-1][0] - stroke_samples[0][0]) / 1000.0
                out.append(self._new_stroke(src, "phone", speed, feats, duration=dur))
            return out

    def ingest_stroke(self, src: str, payload: dict) -> dict | None:
        """A complete stroke from a drag pad, touch screen, Force Touch trackpad or simulator."""
        with self.lock:
            self.seen(src)
            stype = payload.get("stype") or source_type(src)
            if stype == "sim" or ("s" in payload and "t" not in payload):
                s = np.asarray(payload["s"], dtype=float)
                fs = float(payload.get("fs", FS))
                if abs(fs - FS) > 1e-6:
                    s = np.interp(np.linspace(0, len(s) - 1, max(2, int(len(s) * FS / fs))), np.arange(len(s)), s)
                feats = generic_features(s)
                stype = "sim" if stype not in ("drag", "touch", "force", "phone") else stype
                return self._new_stroke(src, stype, s, {k: v for k, v in feats.items()}, duration=(len(s) - 1) / FS)
            t = payload.get("t") or []
            if len(t) < 3:
                return None
            if stype == "force":
                s, feats = features_from_explicit("force", t, f=payload.get("f"))
            else:
                s, feats = features_from_explicit(stype, t, x=payload.get("x"), y=payload.get("y"), p=payload.get("p"))
            return self._new_stroke(src, stype, s, feats, duration=(t[-1] - t[0]) / 1000.0)

    def _new_stroke(self, src: str, stype: str, s: np.ndarray, feats: dict, duration: float) -> dict:
        now = time.time()
        prof, fs = decimate_profile(np.asarray(s, dtype=float))
        st = Stroke(id=new_id("s"), src=src, stype=stype, t0=now - duration, t1=now, profile=prof, fs=fs, features=feats)
        role, who = "free", None
        if self.mode == "capture":
            role, who = "unlabelled", self.master_id
        elif self.mode == "coach":
            role, who = "learner", self.learner_id
        d = self.store.add_stroke(st, role=role, who=who)
        self.recent.append({"id": st.id, "t1": now, "src": src, "stype": stype, "labelled": False})
        info = {"stroke": d, "named": self._name_stroke(stype, feats), "intensity": INTENSITY_LABEL.get(stype, "intensity")}
        if self.mode == "capture":
            self._capture_on_stroke(d)
        elif self.mode == "coach":
            info["coach"] = self._coach_on_stroke(d)
        self.emit("stroke", **info)
        return d

    def _name_stroke(self, stype: str, feats: dict) -> dict | None:
        best = None
        for key, w in self.store.data["words"].items():
            if w["master_id"] != self.master_id or w["stype"] != stype or not w["pos"]:
                continue
            sim = self.store.model(key).similarity(feats)
            if best is None or sim > best["similarity"]:
                best = {"word": w["word"], "key": key, "similarity": round(sim, 3)}
        return best if best and best["similarity"] >= 0.35 else None

    # ---------------- LISTEN: capture mode ----------------
    def _last_stroke(self, within: float, unlabelled_only: bool = True) -> dict | None:
        now = time.time()
        for r in reversed(self.recent):
            if now - r["t1"] > within:
                break
            if unlabelled_only and r["labelled"]:
                continue
            return r
        return None

    def _mark(self, sid: str) -> None:
        for r in self.recent:
            if r["id"] == sid:
                r["labelled"] = True

    def _capture_on_stroke(self, d: dict) -> None:
        p = self.pending
        if p and time.time() <= p["until"] and p.get("negative"):
            key = self.store.ensure_word(self.master_id, p["word"], d["stype"], p.get("is_new", False))["key"]
            self.store.add_example(key, d["id"], positive=False)
            self.store.data["strokes"][d["id"]]["role"] = "bad_example"
            self._mark(d["id"])
            lesson = self.store.add_lesson(key, d["id"], None, p.get("text", ""))
            self.pending = None
            self.emit("lesson", lesson=lesson, word=self.word_summary(key) if self.store.data["words"][key]["pos"] else None)
        elif p and time.time() <= p["until"]:
            key = self.store.ensure_word(self.master_id, p["word"], d["stype"], p.get("is_new", False))["key"]
            self.store.add_example(key, d["id"], positive=True)
            self.store.data["strokes"][d["id"]]["role"] = "demo"
            self._mark(d["id"])
            try_id = p.get("try_id")
            if try_id:
                tw = self.store.data["strokes"].get(try_id)
                if tw and tw["stype"] == d["stype"]:
                    self.store.add_example(key, try_id, positive=False)
            lesson = self.store.add_lesson(key, try_id, d["id"], p.get("text", ""))
            self.pending = None
            self.emit("lesson", lesson=lesson, word=self.word_summary(key))
        elif p:
            self.pending = None

    def utterance(self, text: str, source: str = "speech") -> dict:
        with self.lock:
            parsed = parse_utterance(text)
            entry = {"text": text, "word": parsed.word, "intent": parsed.intent, "new": parsed.is_new_word,
                     "source": source, "t": time.time(), "action": None}
            if self.mode != "capture":
                entry["action"] = "ignored (not in capture mode)"
                self.store.log_utterance(entry)
                self.emit("utterance", **entry)
                return entry
            word = parsed.word or self.current_word
            if parsed.intent == "correction" and word:
                last = self._last_stroke(TRY_WINDOW_S)
                self.current_word = word
                self.pending = {"word": word, "try_id": last["id"] if last else None, "until": time.time() + DEMO_WINDOW_S,
                                "text": text, "is_new": parsed.is_new_word}
                if last:
                    self._mark(last["id"])
                    self.store.data["strokes"][last["id"]]["role"] = "try"
                entry["action"] = "correction: waiting for the master’s demo" + ("" if last else " (no recent try)")
            elif parsed.intent == "demonstration" and word:
                self.current_word = word
                last = self._last_stroke(WORD_AFTER_ACTION_S)
                if last:
                    key = self.store.ensure_word(self.master_id, word, last["stype"], parsed.is_new_word)["key"]
                    self.store.add_example(key, last["id"], positive=True)
                    self.store.data["strokes"][last["id"]]["role"] = "demo"
                    self._mark(last["id"])
                    lesson = self.store.add_lesson(key, None, last["id"], text)
                    entry["action"] = "labelled the stroke that just ended"
                    self.emit("lesson", lesson=lesson, word=self.word_summary(key))
                else:
                    self.pending = {"word": word, "try_id": None, "until": time.time() + DEMO_WINDOW_S, "text": text,
                                    "is_new": parsed.is_new_word}
                    entry["action"] = "waiting for the master’s stroke"
            elif parsed.intent == "bad_example" and (word or self.current_word):
                from .words import is_fault
                if is_fault(parsed.word) and self.current_word and not is_fault(self.current_word):
                    word = self.current_word   # 「ダメな例、ガタガタ」 while teaching スーッ: a wrong スーッ
                self.current_word = word
                self.pending = {"word": word, "try_id": None, "until": time.time() + DEMO_WINDOW_S, "text": text,
                                "negative": True, "is_new": parsed.is_new_word}
                entry["action"] = "waiting for the master’s wrong-way example"
            elif parsed.intent == "approval" and self.current_word:
                last = self._last_stroke(15.0)
                if last:
                    key = self.store.ensure_word(self.master_id, self.current_word, last["stype"])["key"]
                    self.store.add_example(key, last["id"], positive=True)
                    self.store.data["strokes"][last["id"]]["role"] = "approved"
                    self._mark(last["id"])
                    lesson = self.store.add_lesson(key, None, last["id"], text)
                    entry["action"] = "approved the last stroke"
                    self.emit("lesson", lesson=lesson, word=self.word_summary(key))
                else:
                    entry["action"] = "approval, but no recent stroke"
            else:
                entry["action"] = "no craft word heard"
            self.store.log_utterance(entry)
            self.store.save()
            self.emit("utterance", **entry, session=self.session())
            return entry

    def label(self, sid: str, word: str, positive: bool) -> dict:
        with self.lock:
            st = self.store.stroke(sid)
            if not st:
                raise KeyError(sid)
            key = self.store.ensure_word(self.master_id, word, st["stype"])["key"]
            self.store.add_example(key, sid, positive)
            st["role"] = "demo" if positive else "try"
            self.store.save()
            ws = self.word_summary(key)
            self.emit("word", word=ws)
            return ws

    # ---------------- GROUND: dictionary ----------------
    def word_summary(self, key: str) -> dict:
        w = self.store.data["words"][key]
        m = self.store.model(key)
        bands = m.bands()
        proto, proto_id = self.store.prototype(key)
        master = self.store.data["masters"].get(w["master_id"], {"name": "?"})
        info = word_info(w["word"])
        # the master's own variability over time: where the band narrows is the knack
        env = None
        profs = [np.asarray(self.store.data["strokes"][s]["profile"], dtype=float) for s in w["pos"] if s in self.store.data["strokes"]]
        profs = [p for p in profs if len(p) >= 5]
        if len(profs) >= 2:
            grid = np.linspace(0, 1, 100)
            mat = np.array([np.interp(grid, np.linspace(0, 1, len(p)), p) for p in profs])
            mu, sd = mat.mean(axis=0), mat.std(axis=0)
            env = {"mean": [round(float(x), 4) for x in mu], "lo": [round(float(x), 4) for x in np.clip(mu - sd, 0, None)],
                   "hi": [round(float(x), 4) for x in mu + sd],
                   "duration": round(float(np.mean([(len(p) - 1) / FS for p in profs])), 3)}
        return {
            "key": key, "word": w["word"], "stype": w["stype"], "master": master["name"], "master_id": w["master_id"],
            "romaji": info["romaji"], "gloss_en": info["gloss_en"], "known": info["known"],
            "n_pos": len(w["pos"]), "n_neg": len(w["neg"]), "n_lessons": len(w["lessons"]),
            "n_questions": sum(len(v) for v in w.get("nm_yes", {}).values()) + sum(len(v) for v in w.get("nm_no", {}).values()),
            "meaning": m.meaning(),
            "bands": [{"feature": b.feature, "center": b.center, "lo": b.lo, "hi": b.hi, "salience": round(b.salience, 2),
                       "direction": b.direction} for b in bands],
            "prototype": [round(float(x), 4) for x in proto] if proto is not None else [], "prototype_id": proto_id, "fs": FS,
            "intensity": INTENSITY_LABEL.get(w["stype"], "intensity"), "clip": w.get("clip"),
            "is_new_word": w.get("is_new_word", False), "updated": w.get("updated"), "envelope": env,
        }

    def dictionary(self, master_id: str | None = None) -> list[dict]:
        out = []
        for key, w in self.store.data["words"].items():
            if master_id and w["master_id"] != master_id:
                continue
            if not w["pos"]:
                continue
            out.append(self.word_summary(key))
        out.sort(key=lambda x: -(x.get("updated") or 0))
        return out

    # ---------------- SHARPEN: near-miss questions ----------------
    def nearmiss_next(self, key: str) -> dict | None:
        with self.lock:
            proto, _ = self.store.prototype(key)
            q = next_question(self.store.model(key), proto)
            if not q:
                return None
            qid = new_id("q")
            q.update({"id": qid, "key": key, "word": self.store.data["words"][key]["word"],
                      "prototype": [float(x) for x in proto]})
            self.questions[qid] = q
            self.emit("question", question=q)
            return q

    def nearmiss_answer(self, qid: str, yes: bool) -> dict:
        with self.lock:
            q = self.questions.pop(qid)
            self.store.record_nearmiss(q["key"], q["feature"], q["value"], yes)
            ws = self.word_summary(q["key"])
            self.emit("answer", qid=qid, yes=yes, word=ws, feature=q["feature"], value=q["value"])
            return ws

    # ---------------- TRANSFER: coaching ----------------
    def _coach_public(self) -> dict | None:
        c = self.coach
        if not c:
            return None
        return {"learner_id": c["learner_id"], "key": c["key"], "level": c["level"], "attempts": len(c["attempts"]),
                "test": c["test"], "condition": c.get("condition", "kotsu"),
                "word": self.store.data["words"][c["key"]]["word"] if c["key"] in self.store.data["words"] else None}

    def coach_start(self, learner_id: str, key: str, condition: str = "kotsu") -> dict:
        """condition "kotsu": measured feedback that fades. "word_only": the control, only the word and pass/fail."""
        with self.lock:
            condition = condition if condition in ("kotsu", "word_only") else "kotsu"
            self.mode = "coach"
            self.learner_id = learner_id
            rec = self.store.add_coaching({"id": new_id("c"), "learner_id": learner_id, "key": key, "condition": condition,
                                           "started": time.time(), "attempts": [], "first_pass": None})
            self.coach = {"learner_id": learner_id, "key": key, "level": 3 if condition == "kotsu" else 0, "streak_pass": 0,
                          "streak_fail": 0, "attempts": [], "test": None, "condition": condition, "rec": rec}
            self.emit("session", session=self.session())
            return self.session()

    def coach_test(self) -> dict:
        with self.lock:
            if not self.coach:
                raise ValueError("no coaching session")
            self.coach["test"] = {"results": [], "needed": 3, "done": False, "passed": None}
            self.emit("session", session=self.session())
            return self.session()

    def _coach_on_stroke(self, d: dict) -> dict | None:
        c = self.coach
        if not c or c["key"] not in self.store.data["words"]:
            return None
        w = self.store.data["words"][c["key"]]
        if d["stype"] != w["stype"]:
            return {"error": f"this word was learned on {w['stype']}, but the stroke came from {d['stype']}"}
        res = self.store.model(c["key"]).score(d["features"])
        c["attempts"].append({"sid": d["id"], "score": res["score"], "passed": res["passed"]})
        rec = c.get("rec")
        if rec is not None and not (c["test"] and not c["test"]["done"]):
            rec["attempts"].append({"score": res["score"], "passed": res["passed"], "t": time.time()})
            if res["passed"] and rec["first_pass"] is None:
                rec["first_pass"] = len(rec["attempts"])
            self.store.save()
        word_only = c.get("condition") == "word_only"
        out = {"score": res["score"], "passed": res["passed"], "level": c["level"], "features": [] if word_only else res["features"],
               "feedback": None if (word_only or res["passed"]) else res["worst"], "word": w["word"], "key": c["key"],
               "condition": c.get("condition", "kotsu")}
        if c["test"] and not c["test"]["done"]:
            t = c["test"]
            t["results"].append({"sid": d["id"], "score": res["score"], "passed": res["passed"]})
            out["test"] = {"n": len(t["results"]), "needed": t["needed"]}
            out["feedback"] = None
            out["features"] = []
            if len(t["results"]) >= t["needed"]:
                n_pass = sum(1 for r in t["results"] if r["passed"])
                t["done"] = True
                t["passed"] = n_pass >= 2
                out["test"]["done"] = True
                out["test"]["passed"] = t["passed"]
                if t["passed"]:
                    best = max(t["results"], key=lambda r: r["score"])
                    master = self.store.data["masters"].get(w["master_id"], {"name": "?"})
                    entry = {"word_key": c["key"], "word": w["word"], "stype": w["stype"], "master": master["name"],
                             "score": round(float(np.mean([r["score"] for r in t["results"]])), 1),
                             "stroke_id": best["sid"], "attempts": len(c["attempts"]), "t": time.time(),
                             "condition": c.get("condition", "kotsu"),
                             "verify": new_id("v").split("_")[1].upper()}
                    self.store.add_passport(c["learner_id"], entry)
                    out["passport"] = entry
            return out
        if word_only:
            out["level"] = 0
            out["ready_for_test"] = any(a["passed"] for a in c["attempts"])
            return out
        if res["passed"]:
            c["streak_pass"] += 1
            c["streak_fail"] = 0
            if c["streak_pass"] >= 2 and c["level"] > 0:
                c["level"] -= 1
                c["streak_pass"] = 0
        else:
            c["streak_fail"] += 1
            c["streak_pass"] = 0
            if c["streak_fail"] >= 2 and c["level"] < 3:
                c["level"] += 1
                c["streak_fail"] = 0
        out["level"] = c["level"]
        out["ready_for_test"] = c["level"] == 0
        return out

    # ---------------- DRILL ----------------
    def drill_next(self, key: str, learner_id: str | None) -> dict | None:
        """Exaggerated contrasts first, fading towards the real band edge as the learner gets them right."""
        with self.lock:
            proto, _ = self.store.prototype(key)
            st = self.store.data["drills"].get(learner_id or "anon", {"right": 0, "total": 0})
            streak = st.get("streak", 0)
            gap = max(0.35, 2.2 - 0.35 * streak)
            pair = drill_pair(self.store.model(key), proto, self.rng, gap=gap)
            if not pair:
                return None
            did = new_id("d")
            self.drills[did] = {"answer": pair["answer"], "learner_id": learner_id, "key": key}
            pub = {k: v for k, v in pair.items() if k != "answer"}
            pub.update({"id": did, "key": key, "word": self.store.data["words"][key]["word"], "gap": round(gap, 2)})
            return pub

    def drill_answer(self, did: str, choice: str) -> dict:
        with self.lock:
            d = self.drills.pop(did)
            correct = choice.upper() == d["answer"]
            stats = self.store.drill_result(d["learner_id"] or "anon", correct)
            return {"correct": correct, "answer": d["answer"], "stats": stats}

    # ---------------- RESULTS: honest numbers ----------------
    def _groups(self, min_pos: int = 2) -> dict[tuple[str, str], list[str]]:
        words, strokes = self.store.data["words"], self.store.data["strokes"]
        groups: dict[tuple[str, str], list[str]] = {}
        for key, w in words.items():
            if len([s for s in w["pos"] if s in strokes]) >= min_pos:
                groups.setdefault((w["master_id"], w["stype"]), []).append(key)
        return groups

    def recognition(self) -> dict:
        """Held-out test: hide one of the master's demos, rebuild every word without it, and ask which word it is."""
        words, strokes = self.store.data["words"], self.store.data["strokes"]
        n = correct = 0
        chance_acc = 0.0
        per_master: dict[str, dict] = {}
        for (mid, stype), keys in self._groups().items():
            if len(keys) < 2:
                continue
            for key in keys:
                for sid in [x for x in words[key]["pos"] if x in strokes]:
                    best, best_sim = None, -1.0
                    for k2 in keys:
                        m = self.store.model(k2, exclude={sid})
                        if not m.pos:
                            continue
                        sim = m.similarity(strokes[sid]["features"])
                        if sim > best_sim:
                            best, best_sim = k2, sim
                    ok = best == key
                    n += 1
                    correct += int(ok)
                    chance_acc += 1.0 / len(keys)
                    pm = per_master.setdefault(mid, {"master": self.store.data["masters"].get(mid, {}).get("name", "?"), "n": 0, "correct": 0})
                    pm["n"] += 1
                    pm["correct"] += int(ok)
        return {"n": n, "correct": correct, "accuracy": round(correct / n, 3) if n else None,
                "chance": round(chance_acc / n, 3) if n else None, "per_master": list(per_master.values()),
                "how": "Leave-one-out: each demo is hidden, the master's words are rebuilt without it, and KOTSU names it."}

    def compare(self, word: str, stype: str | None = None) -> dict:
        """The same word from different masters, and whether KOTSU can tell whose it is."""
        words, strokes = self.store.data["words"], self.store.data["strokes"]
        keys = [k for k, w in words.items() if w["word"] == word and (stype is None or w["stype"] == stype)
                and len([s for s in w["pos"] if s in strokes]) >= 1]
        if stype is None and keys:
            # pick the input type with the most masters
            by: dict[str, list[str]] = {}
            for k in keys:
                by.setdefault(words[k]["stype"], []).append(k)
            stype = max(by, key=lambda t: len(by[t]))
            keys = by[stype]
        out = {"word": word, "stype": stype, "masters": [self.word_summary(k) for k in keys], "who": None}
        if len(keys) >= 2:
            n = correct = 0
            for key in keys:
                for sid in [x for x in words[key]["pos"] if x in strokes]:
                    sims = []
                    for k2 in keys:
                        m = self.store.model(k2, exclude={sid})
                        sims.append((m.similarity(strokes[sid]["features"]) if m.pos else -1.0, k2))
                    n += 1
                    correct += int(max(sims)[1] == key)
            out["who"] = {"n": n, "correct": correct, "accuracy": round(correct / n, 3) if n else None,
                          "chance": round(1.0 / len(keys), 3)}
        return out

    def comparable_words(self) -> list[dict]:
        words, strokes = self.store.data["words"], self.store.data["strokes"]
        seen: dict[tuple[str, str], set] = {}
        for w in words.values():
            if any(s in strokes for s in w["pos"]):
                seen.setdefault((w["word"], w["stype"]), set()).add(w["master_id"])
        return [{"word": k[0], "stype": k[1], "masters": len(v)} for k, v in sorted(seen.items(), key=lambda kv: -len(kv[1])) if len(v) >= 2]

    def pilot(self) -> dict:
        """Tries to first reach the master's band, word only vs KOTSU feedback."""
        out: dict[str, dict] = {}
        for rec in self.store.data.get("coaching", []):
            if not rec.get("attempts"):
                continue
            g = out.setdefault(rec.get("condition", "kotsu"), {"sessions": 0, "learners": set(), "reached": 0, "tries": [],
                                                              "gave_up_after": []})
            g["sessions"] += 1
            if rec.get("learner_id"):
                g["learners"].add(rec["learner_id"])
            if rec.get("first_pass"):
                g["reached"] += 1
                g["tries"].append(rec["first_pass"])
            else:
                g["gave_up_after"].append(len(rec["attempts"]))
        for g in out.values():
            t = g["tries"]
            g["learners"] = len(g["learners"])
            g["reach_rate"] = round(g["reached"] / g["sessions"], 3) if g["sessions"] else None
            g["mean_tries"] = round(float(np.mean(t)), 2) if t else None
            g["median_tries"] = float(np.median(t)) if t else None
        return out

    # ---------------- KNACK GRAPH: masters -> words -> measured feel -> learners ----------------
    def graph(self) -> dict:
        d = self.store.data
        words = [self.word_summary(k) for k, w in d["words"].items() if any(s in d["strokes"] for s in w["pos"])]
        mids = {w["master_id"] for w in words}
        masters = [{"id": m["id"], "name": m["name"], "synthetic": "synthetic" in m["name"].lower()}
                   for m in d["masters"].values() if m["id"] in mids]
        feats: dict[str, float] = {}
        edges = []
        for w in words:
            for b in w["bands"]:
                feats[b["feature"]] = feats.get(b["feature"], 0.0) + float(b["salience"])
                edges.append({"word": w["key"], "feature": b["feature"], "salience": float(b["salience"]),
                              "direction": int(b["direction"] or 0), "lo": float(b["lo"]), "hi": float(b["hi"])})
        from .signals import FEATURES
        features = [{"id": f, "name": FEATURES.get(f, {}).get("en", f), "unit": FEATURES.get(f, {}).get("unit", "")}
                    for f, _ in sorted(feats.items(), key=lambda kv: -kv[1])]
        # learner stage per word, after 守破離: 守 practising with help, 破 reached the master's range, 離 passed alone
        stage: dict[tuple[str, str], str] = {}
        rank = {"守": 1, "破": 2, "離": 3}
        for rec in d.get("coaching", []):
            if not rec.get("attempts") or rec.get("key") not in d["words"]:
                continue
            st = "破" if rec.get("first_pass") else "守"
            k = (rec["learner_id"], rec["key"])
            if rank[st] > rank.get(stage.get(k, ""), 0):
                stage[k] = st
        for lid, entries in d["passports"].items():
            for e in entries:
                if e.get("word_key") in d["words"]:
                    stage[(lid, e["word_key"])] = "離"
        lids = sorted({k[0] for k in stage})
        learners = [{"id": lid, "name": d["learners"].get(lid, {}).get("name", "Learner")} for lid in lids]
        links = [{"learner": k[0], "word": k[1], "stage": v} for k, v in stage.items()]
        slim = [{k: w[k] for k in ("key", "word", "romaji", "gloss_en", "master", "master_id", "stype", "n_pos", "n_neg",
                                   "n_lessons", "meaning", "prototype", "fs", "is_new_word")} for w in words]
        return {"masters": masters, "words": slim, "features": features, "edges": edges, "learners": learners,
                "links": links, "comparable": self.comparable_words()}

    def results(self) -> dict:
        d = self.store.data
        comp = self.comparable_words()
        who = []
        for c in comp[:6]:
            w = self.compare(c["word"], c["stype"])["who"]
            if w and w["n"]:
                who.append({"word": c["word"], "stype": c["stype"], **w})
        return {"recognition": self.recognition(), "pilot": self.pilot(), "comparable": comp, "who": who,
                "counts": {"masters": len(d["masters"]), "words": sum(1 for w in d["words"].values() if w["pos"]),
                           "lessons": len(d["lessons"]), "corrections": sum(1 for l in d["lessons"].values() if l.get("try_id")),
                           "questions": sum(sum(len(v) for v in w.get("nm_yes", {}).values()) + sum(len(v) for v in w.get("nm_no", {}).values()) for w in d["words"].values()),
                           "passports": sum(len(v) for v in d["passports"].values()),
                           "synthetic_masters": sum(1 for m in d["masters"].values() if "synthetic" in m["name"].lower())}}

    # ---------------- PROVE ----------------
    def passport(self, learner_id: str) -> dict:
        l = self.store.data["learners"].get(learner_id)
        entries = self.store.data["passports"].get(learner_id, [])
        out = []
        for e in entries:
            st = self.store.stroke(e["stroke_id"])
            out.append({**e, "profile": st["profile"] if st else [], "fs": st["fs"] if st else FS})
        return {"learner": l, "entries": out, "drills": self.store.data["drills"].get(learner_id, {"right": 0, "total": 0})}
