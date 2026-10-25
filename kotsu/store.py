"""Persistence: one JSON file plus a folder of voice clips.

Everything KOTSU learns lives in data/kotsu.json so the team can stop and
restart the server without losing the master's dictionary.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

import numpy as np

from .model import WordModel
from .signals import FS, Stroke

DATA_DIR = Path(os.environ.get("KOTSU_DATA", Path(__file__).resolve().parent.parent / "data"))


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (DATA_DIR / "kotsu.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clip_dir = self.path.parent / "clips"
        self.clip_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.data = self._empty()
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except Exception:
                backup = self.path.with_suffix(f".broken-{int(time.time())}.json")
                self.path.rename(backup)

    @staticmethod
    def _empty() -> dict:
        return {"masters": {}, "learners": {}, "strokes": {}, "words": {}, "lessons": {},
                "utterances": [], "passports": {}, "drills": {}, "coaching": [], "version": 1}

    def save(self) -> None:
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)

    def reset(self) -> None:
        with self.lock:
            self.data = self._empty()
            # voice recordings can identify a person: reset removes them too
            for clip in self.clip_dir.iterdir():
                if clip.is_file():
                    clip.unlink()
            self.save()

    # ---------- people ----------
    def add_master(self, name: str) -> dict:
        with self.lock:
            for m in self.data["masters"].values():
                if m["name"] == name:
                    return m
            m = {"id": new_id("m"), "name": name, "created": time.time()}
            self.data["masters"][m["id"]] = m
            self.save()
            return m

    def rename_learner(self, lid: str, name: str) -> dict:
        with self.lock:
            l = self.data["learners"][lid]
            l["name"] = name
            self.save()
            return l

    def rename_master(self, mid: str, name: str) -> dict:
        with self.lock:
            m = self.data["masters"][mid]
            m["name"] = name
            self.save()
            return m

    def add_learner(self, name: str, lang: str = "en") -> dict:
        with self.lock:
            for l in self.data["learners"].values():
                if l["name"] == name:
                    l["lang"] = lang
                    return l
            l = {"id": new_id("l"), "name": name, "lang": lang, "created": time.time()}
            self.data["learners"][l["id"]] = l
            self.save()
            return l

    # ---------- strokes ----------
    def add_stroke(self, st: Stroke, role: str = "unlabelled", who: str | None = None) -> dict:
        with self.lock:
            d = st.summary()
            d["role"] = role
            d["who"] = who
            self.data["strokes"][st.id] = d
            # keep the file small: drop the oldest unlabelled strokes beyond 1500
            if len(self.data["strokes"]) > 1500:
                used = set()
                for w in self.data["words"].values():
                    used.update(w["pos"])
                    used.update(w["neg"])
                for entries in self.data["passports"].values():
                    used.update(e.get("stroke_id") for e in entries)
                for sid in list(self.data["strokes"].keys()):
                    if len(self.data["strokes"]) <= 1200:
                        break
                    if sid not in used:
                        del self.data["strokes"][sid]
            self.save()
            return d

    def stroke(self, sid: str) -> dict | None:
        return self.data["strokes"].get(sid)

    # ---------- words ----------
    @staticmethod
    def word_key(master_id: str, word: str, stype: str) -> str:
        return f"{master_id}|{word}|{stype}"

    def ensure_word(self, master_id: str, word: str, stype: str, is_new: bool = False) -> dict:
        key = self.word_key(master_id, word, stype)
        with self.lock:
            w = self.data["words"].get(key)
            if not w:
                w = {"key": key, "master_id": master_id, "word": word, "stype": stype, "pos": [], "neg": [],
                     "nm_yes": {}, "nm_no": {}, "clip": None, "lessons": [], "is_new_word": is_new,
                     "created": time.time(), "updated": time.time()}
                self.data["words"][key] = w
            return w

    def add_example(self, key: str, sid: str, positive: bool) -> None:
        with self.lock:
            w = self.data["words"][key]
            lst = w["pos"] if positive else w["neg"]
            other = w["neg"] if positive else w["pos"]
            if sid in other:
                other.remove(sid)
            if sid not in lst:
                lst.append(sid)
            w["updated"] = time.time()
            self.save()

    def model(self, key: str, exclude: set | None = None) -> WordModel:
        """The word model for one master's word. `exclude` leaves strokes out (for held-out tests)."""
        w = self.data["words"][key]
        strokes = self.data["strokes"]
        ex = exclude or set()
        pos = [strokes[s]["features"] for s in w["pos"] if s in strokes and s not in ex]
        neg = [strokes[s]["features"] for s in w["neg"] if s in strokes and s not in ex]
        used = set(w["pos"]) | set(w["neg"])
        base = [s["features"] for sid, s in strokes.items()
                if sid not in used and sid not in ex and s.get("stype") == w["stype"] and s.get("who") == w["master_id"]
                and s.get("role") in ("unlabelled", "free")]
        if len(base) < 2:
            # fall back to this master's other words of the same source
            base = []
            for k2, w2 in self.data["words"].items():
                if k2 != key and w2["master_id"] == w["master_id"] and w2["stype"] == w["stype"]:
                    base += [strokes[s]["features"] for s in w2["pos"] if s in strokes and s not in ex]
        return WordModel(key=key, master_id=w["master_id"], word=w["word"], stype=w["stype"], pos=pos, neg=neg,
                         nm_yes=w.get("nm_yes", {}), nm_no=w.get("nm_no", {}), baseline=base)

    def prototype(self, key: str) -> tuple[np.ndarray | None, str | None]:
        w = self.data["words"][key]
        m = self.model(key)
        idx = m.prototype_index()
        if idx is None:
            return None, None
        pos_ids = [s for s in w["pos"] if s in self.data["strokes"]]
        sid = pos_ids[idx]
        st = self.data["strokes"][sid]
        prof = np.asarray(st["profile"], dtype=float)
        if abs(st.get("fs", FS) - FS) > 1e-6:
            n = int(round(len(prof) * FS / st["fs"]))
            prof = np.interp(np.linspace(0, len(prof) - 1, max(2, n)), np.arange(len(prof)), prof)
        return prof, sid

    def record_nearmiss(self, key: str, feature: str, value: float, yes: bool) -> None:
        with self.lock:
            w = self.data["words"][key]
            target = w.setdefault("nm_yes" if yes else "nm_no", {})
            target.setdefault(feature, []).append(float(value))
            w["updated"] = time.time()
            self.save()

    # ---------- lessons ----------
    def add_lesson(self, key: str, try_id: str | None, demo_id: str | None, text: str) -> dict:
        with self.lock:
            les = {"id": new_id("lesson"), "word_key": key, "try_id": try_id, "demo_id": demo_id, "text": text, "t": time.time()}
            self.data["lessons"][les["id"]] = les
            self.data["words"][key]["lessons"].append(les["id"])
            self.save()
            return les

    def log_utterance(self, entry: dict) -> None:
        with self.lock:
            self.data["utterances"].append(entry)
            self.data["utterances"] = self.data["utterances"][-200:]
            self.save()

    # ---------- passport and drills ----------
    def add_passport(self, learner_id: str, entry: dict) -> None:
        with self.lock:
            lst = self.data["passports"].setdefault(learner_id, [])
            lst = [e for e in lst if e["word_key"] != entry["word_key"]]
            lst.append(entry)
            self.data["passports"][learner_id] = lst
            self.save()

    def add_coaching(self, rec: dict) -> dict:
        with self.lock:
            self.data.setdefault("coaching", []).append(rec)
            self.data["coaching"] = self.data["coaching"][-500:]
            self.save()
            return rec

    def drill_result(self, learner_id: str, correct: bool) -> dict:
        with self.lock:
            d = self.data["drills"].setdefault(learner_id, {"right": 0, "total": 0})
            d["total"] += 1
            d["right"] += 1 if correct else 0
            d["streak"] = d.get("streak", 0) + 1 if correct else max(0, d.get("streak", 0) - 2)
            self.save()
            return d
