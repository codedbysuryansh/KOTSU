"""Per-master word models: what a craft word means in this master's hands.

A WordModel holds positive examples (the master's demonstrations, approved
strokes) and negative examples (the apprentice tries the master corrected,
near-misses the master rejected). From them it learns:
  * salience  : which features this word is about (グッ -> peak and rise),
  * bands     : the range of each salient feature the master accepts,
  * meaning   : a plain description of the word relative to how this
                master usually moves.
It scores a learner's stroke against the bands and explains the biggest gap
in plain English, using only measured differences and the master's word.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .signals import FEATURES

# Which features each known word is about, with the expected direction
# relative to the master's usual strokes (+1 higher, -1 lower).
PRIORS: dict[str, dict[str, int]] = {
    "スーッ": {"steady_cv": -1, "jitter": -1, "duration": +1},
    "スッ": {"duration": -1, "peak": +1},
    "グッ": {"peak": +1, "rise": -1},
    "グイッ": {"peak_acc": +1, "peak": +1, "rise": -1},
    "ピタッ": {"stop_time": -1},
    "ジワッ": {"rise": +1},
    "ギュッ": {"hold": +1, "peak": +1},
    "トン": {"duration": -1, "peak": +1},
    "トントン": {"pulses": +1},
    "ガタガタ": {"jitter": +1, "wobble": +1, "steady_cv": +1},
    "ガリガリ": {"sound_rough": +1, "jitter": +1},
    "シャーッ": {"steady_cv": -1, "sound_level": +1},
    "サッ": {"duration": -1, "stop_time": -1},
    "フワッ": {"peak": -1, "rise": +1},
    "カチッ": {"stop_time": -1, "peak": +1},
}

NEARMISS_FEATURES = ["duration", "peak", "mean_mid", "rise", "steady_cv", "jitter", "stop_time", "hold"]

MOTION_TYPES = {"phone", "drag", "touch", "sim"}

# Corrective feedback: feature -> direction the learner must move -> text.
# "up" means the learner's value is below the band and must increase.
FEEDBACK = {
    "duration": {"up": "Make the stroke longer.", "down": "Make it shorter and quicker."},
    "peak": {"up": {"motion": "Move faster at the peak.", "force": "Press harder at the peak."},
             "down": {"motion": "Move more slowly at the peak.", "force": "Press more lightly."}},
    "mean_mid": {"up": {"motion": "Keep more speed through the middle.", "force": "Keep more pressure through the middle."},
                 "down": {"motion": "Slow down through the middle.", "force": "Ease the pressure in the middle."}},
    "rise": {"up": "Build up more slowly.", "down": "Reach full strength faster."},
    "steady_cv": {"up": "Let it vary a little more.", "down": "Keep it even through the middle."},
    "jitter": {"up": "Make it livelier.", "down": "Smoother, no shaking."},
    "stop_time": {"up": "Ease out gently at the end.", "down": "Stop crisply, right at the end."},
    "hold": {"up": "Hold it longer at full strength.", "down": "Release sooner."},
    "pulses": {"up": "Add another tap.", "down": "Use fewer taps."},
    "peak_acc": {"up": "A stronger push at the start.", "down": "A gentler start."},
    "wobble": {"up": "Let the tool turn a little more.", "down": "Keep the tool angle steady."},
    "sound_level": {"up": "Firmer contact, a louder sound.", "down": "Lighter contact, a quieter sound."},
    "sound_rough": {"up": "A rougher, scratchier sound.", "down": "A cleaner, smoother sound."},
}

DIRECTION_WORDS = {
    +1: {"duration": "longer", "peak": "stronger peak", "mean_mid": "more through the middle", "rise": "slower build-up",
         "steady_cv": "less even", "jitter": "shakier", "stop_time": "softer stop", "hold": "held longer",
         "pulses": "more taps", "peak_acc": "harder start", "wobble": "more wobble", "sound_level": "louder", "sound_rough": "rougher sound"},
    -1: {"duration": "shorter", "peak": "lighter peak", "mean_mid": "less through the middle", "rise": "faster build-up",
         "steady_cv": "more even", "jitter": "smoother", "stop_time": "crisper stop", "hold": "released sooner",
         "pulses": "fewer taps", "peak_acc": "gentler start", "wobble": "steadier angle", "sound_level": "quieter", "sound_rough": "cleaner sound"},
}


def min_halfwidth(f: str, center: float) -> float:
    meta = FEATURES.get(f, {"min": 0.1, "mode": "rel"})
    if meta["mode"] == "rel":
        return max(meta["min"] * abs(center), 1e-3)
    return meta["min"]


def _feedback_text(f: str, direction: str, stype: str) -> str:
    en = FEEDBACK.get(f, {}).get(direction, "Closer to the master’s stroke.")
    if isinstance(en, dict):
        en = en["force" if stype == "force" else "motion"]
    return en


@dataclass
class Band:
    feature: str
    center: float
    lo: float
    hi: float
    salience: float
    direction: int  # what the word means relative to this master's baseline (+1 / -1 / 0)


@dataclass
class WordModel:
    key: str
    master_id: str
    word: str
    stype: str
    pos: list[dict] = field(default_factory=list)      # feature dicts of positive examples
    neg: list[dict] = field(default_factory=list)      # feature dicts of negative examples
    nm_yes: dict[str, list[float]] = field(default_factory=dict)
    nm_no: dict[str, list[float]] = field(default_factory=dict)
    baseline: list[dict] = field(default_factory=list)  # this master's other strokes of the same source

    # ---------- statistics ----------
    def feature_names(self) -> list[str]:
        names: set[str] = set()
        for d in self.pos:
            names.update(d.keys())
        return [f for f in FEATURES if f in names]

    @staticmethod
    def _vals(rows: list[dict], f: str) -> np.ndarray:
        return np.array([r[f] for r in rows if f in r and r[f] is not None and math.isfinite(r[f])], dtype=float)

    def salience(self) -> dict[str, tuple[float, int]]:
        """feature -> (salience score, direction relative to baseline/negatives)."""
        out: dict[str, tuple[float, int]] = {}
        prior = PRIORS.get(self.word, {})
        for f in self.feature_names():
            p = self._vals(self.pos, f)
            if len(p) == 0:
                continue
            mu = float(p.mean())
            sd_p = float(p.std()) if len(p) > 1 else 0.0
            ref = self._vals(self.neg, f)
            if len(ref) == 0:
                ref = self._vals(self.baseline, f)
            score, direction = 0.0, 0
            if len(ref) >= 1:
                mu_r = float(ref.mean())
                sd_r = float(ref.std()) if len(ref) > 1 else 0.0
                pooled = math.sqrt((sd_p ** 2 + sd_r ** 2) / 2.0)
                pooled = max(pooled, min_halfwidth(f, mu) * 0.75)
                score = abs(mu - mu_r) / pooled
                direction = 1 if mu > mu_r else -1
            if f in prior:
                score += 1.2
                if direction == 0:
                    direction = prior[f]
            out[f] = (score, direction)
        return out

    def bands(self, k: int = 3) -> list[Band]:
        sal = self.salience()
        if not sal:
            return []
        ranked = sorted(sal.items(), key=lambda kv: -kv[1][0])
        chosen = [kv for kv in ranked if kv[1][0] >= 0.8][:k] or ranked[:1]
        bands = []
        for f, (s, d) in chosen:
            p = self._vals(self.pos, f)
            c = float(p.mean())
            hw = max(2.0 * float(p.std()) if len(p) > 1 else 0.0, min_halfwidth(f, c) * 1.5)
            lo, hi = c - hw, c + hw
            yes = self.nm_yes.get(f, [])
            no = self.nm_no.get(f, [])
            ys_hi = [v for v in yes if v > c]
            ns_hi = [v for v in no if v > c]
            ys_lo = [v for v in yes if v < c]
            ns_lo = [v for v in no if v < c]
            if ns_hi:
                inner = max([c] + [v for v in ys_hi if v < min(ns_hi)])
                hi = (inner + min(ns_hi)) / 2.0
            elif ys_hi:
                hi = max(hi, max(ys_hi))
            if ns_lo:
                inner = min([c] + [v for v in ys_lo if v > max(ns_lo)])
                lo = (inner + max(ns_lo)) / 2.0
            elif ys_lo:
                lo = min(lo, min(ys_lo))
            # negative examples (corrected tries) pull the band edge on their side
            # to halfway between the master's demo and the try
            mhw = min_halfwidth(f, c)
            for v in self._vals(self.neg, f):
                if v > c:
                    hi = min(hi, max(c + 0.6 * mhw, (c + v) / 2.0))
                elif v < c:
                    lo = max(lo, min(c - 0.6 * mhw, (c + v) / 2.0))
            lo = max(lo, 0.0)  # every feature is non-negative
            bands.append(Band(f, c, lo, hi, s, d))
        return bands

    # ---------- use ----------
    def score(self, feats: dict[str, float]) -> dict:
        bands = self.bands()
        if not bands:
            return {"score": 0, "passed": False, "features": [], "worst": None}
        rows = []
        tot_w = 0.0
        acc = 0.0
        for b in bands:
            v = feats.get(b.feature)
            if v is None:
                continue
            hw = max((b.hi - b.lo) / 2.0, 1e-9)
            if b.lo <= v <= b.hi:
                q, direction = 1.0, None
            elif v < b.lo:
                q, direction = max(0.0, 1.0 - (b.lo - v) / hw), "up"
            else:
                q, direction = max(0.0, 1.0 - (v - b.hi) / hw), "down"
            w = max(b.salience, 0.5)
            tot_w += w
            acc += w * q
            rows.append({"feature": b.feature, "value": float(v), "lo": float(b.lo), "hi": float(b.hi), "center": float(b.center),
                         "q": float(q), "direction": direction, "name_en": FEATURES[b.feature]["en"]})
        score = 100.0 * acc / tot_w if tot_w else 0.0
        worst = min(rows, key=lambda r: r["q"]) if rows else None
        passed = bool((bool(rows) and all(r["q"] >= 0.999 for r in rows)) or float(score) >= 85.0)
        fb = None
        if worst is not None and worst["direction"] is not None:
            en = _feedback_text(worst["feature"], worst["direction"], self.stype)
            fb = {"feature": worst["feature"], "direction": worst["direction"], "en": f"{en} 「{self.word}」"}
        return {"score": round(float(score), 1), "passed": passed, "features": rows, "worst": fb}

    def meaning(self) -> dict:
        parts_en = []
        for b in self.bands():
            if b.direction:
                parts_en.append(DIRECTION_WORDS[b.direction].get(b.feature, b.feature))
        return {"en": ", ".join(parts_en) or "still learning"}

    def prototype_index(self) -> int | None:
        """Index of the positive example closest to the mean on the salient features."""
        bands = self.bands()
        if not self.pos:
            return None
        best, best_d = 0, float("inf")
        for i, d in enumerate(self.pos):
            dist = 0.0
            for b in bands:
                hw = max((b.hi - b.lo) / 2.0, 1e-9)
                dist += ((d.get(b.feature, b.center) - b.center) / hw) ** 2
            if dist < best_d:
                best, best_d = i, dist
        return best

    def similarity(self, feats: dict[str, float]) -> float:
        """0..1 closeness of a stroke to this word (used to name a stroke)."""
        bands = self.bands()
        if not bands:
            return 0.0
        d = 0.0
        for b in bands:
            v = feats.get(b.feature)
            if v is None:
                continue
            hw = max((b.hi - b.lo) / 2.0, 1e-9)
            d += ((v - b.center) / hw) ** 2
        return float(math.exp(-0.5 * d / max(1, len(bands))))
