"""Near-miss questions: the master judges, never explains.

For a word model we pick the salient feature whose acceptable range is least
certain, make a copy of the master's own prototype stroke that changes only
that feature, and ask 「これも○○？」 (is this still the word?). Each answer
narrows the band by bisection, so a handful of yes/no taps fixes each edge.
The same synthesis gives the learner drills (one true stroke, one near-miss).
"""
from __future__ import annotations

import numpy as np

from .model import NEARMISS_FEATURES, WordModel, min_halfwidth
from .signals import FS, generic_features, smooth


def _resample_len(seg: np.ndarray, new_len: int) -> np.ndarray:
    new_len = max(2, int(new_len))
    if len(seg) < 2:
        return np.repeat(seg[:1] if len(seg) else np.zeros(1), new_len)
    x_old = np.linspace(0, 1, len(seg))
    x_new = np.linspace(0, 1, new_len)
    return np.interp(x_new, x_old, seg)


def _crossings(s: np.ndarray) -> tuple[int, int, float]:
    ss = smooth(s, 0.06)
    peak = float(ss.max()) if len(ss) else 0.0
    above = np.nonzero(ss >= 0.8 * peak)[0]
    if len(above) == 0:
        return 0, len(s) - 1, peak
    return int(above[0]), int(above[-1]), peak


def transform(s: np.ndarray, feature: str, param: float, seed: int = 7) -> np.ndarray:
    """Apply a one-feature change to a profile. `param` is a feature-specific knob."""
    s = np.asarray(s, dtype=float)
    i80, l80, _ = _crossings(s)
    if feature in ("peak", "mean_mid"):
        return s * param
    if feature == "duration":
        return _resample_len(s, len(s) * param)
    if feature == "rise":
        onset = _resample_len(s[: max(2, i80 + 1)], max(2, (i80 + 1) * param))
        return np.concatenate([onset, s[i80 + 1:]])
    if feature == "stop_time":
        tail = _resample_len(s[l80:], max(2, len(s[l80:]) * param))
        return np.concatenate([s[:l80], tail])
    if feature == "hold":
        mid = _resample_len(s[i80:l80 + 1], max(2, (l80 - i80 + 1) * param))
        return np.concatenate([s[:i80], mid, s[l80 + 1:]])
    if feature == "steady_cv":
        n = len(s)
        t = np.arange(n) / FS
        mod = 1.0 + param * np.sin(2 * np.pi * 2.5 * t)
        return np.clip(s * mod, 0, None)
    if feature == "jitter":
        rng = np.random.default_rng(seed)
        base = smooth(s, 0.15)
        noise = rng.standard_normal(len(s)) * (s > 0.05 * max(s.max(), 1e-9))
        if param >= 0:
            return np.clip(s + param * base * noise, 0, None)
        # negative param: smooth towards the base curve
        k = min(1.0, -param)
        return np.clip((1 - k) * s + k * base, 0, None)
    return s


KNOB_RANGE = {
    "peak": (0.2, 3.0), "mean_mid": (0.2, 3.0), "duration": (0.3, 3.0), "rise": (0.1, 6.0),
    "stop_time": (0.1, 6.0), "hold": (0.1, 5.0), "steady_cv": (0.0, 0.9), "jitter": (-1.0, 1.2),
}


def synthesize(proto: np.ndarray, feature: str, target: float) -> tuple[np.ndarray, float]:
    """Find a knob setting so that `feature` of the transformed profile is close to `target`."""
    lo, hi = KNOB_RANGE[feature]

    def val(knob: float) -> float:
        return generic_features(transform(proto, feature, knob)).get(feature, 0.0)

    v_lo, v_hi = val(lo), val(hi)
    increasing = v_hi >= v_lo
    a, b = lo, hi
    best_knob, best_err = lo, float("inf")
    for _ in range(28):
        m = (a + b) / 2.0
        v = val(m)
        err = abs(v - target)
        if err < best_err:
            best_knob, best_err = m, err
        if (v < target) == increasing:
            a = m
        else:
            b = m
    out = transform(proto, feature, best_knob)
    return out, generic_features(out).get(feature, 0.0)


def next_question(model: WordModel, proto: np.ndarray) -> dict | None:
    """Choose the most uncertain band edge and render a near-miss there."""
    bands = [b for b in model.bands() if b.feature in NEARMISS_FEATURES]
    if not bands or proto is None or len(proto) < 5:
        return None
    best = None
    for b in bands:
        hw = max((b.hi - b.lo) / 2.0, min_halfwidth(b.feature, b.center))
        yes = model.nm_yes.get(b.feature, [])
        no = model.nm_no.get(b.feature, [])
        for side in ("hi", "lo"):
            if side == "hi":
                inner = max([b.center] + [v for v in yes if v > b.center])
                outs = [v for v in no if v > b.center]
                outer = min(outs) if outs else b.center + 2.6 * hw
                if outs:
                    target = (inner + outer) / 2.0
                else:
                    target = max(inner, b.hi) + 0.6 * hw
            else:
                inner = min([b.center] + [v for v in yes if v < b.center])
                outs = [v for v in no if v < b.center]
                outer = max(outs) if outs else max(0.0, b.center - 2.6 * hw)
                if outs:
                    target = (inner + outer) / 2.0
                else:
                    target = max(0.0, min(inner, b.lo) - 0.6 * hw)
            width = abs(outer - inner) / max(hw, 1e-9)
            asked = len([v for v in yes + no if (v > b.center) == (side == "hi")])
            if target <= 0 and b.feature not in ("jitter", "steady_cv"):
                continue
            priority = width * (b.salience + 0.5) / (1 + 0.6 * asked)
            if best is None or priority > best[0]:
                best = (priority, b, side, target)
    if best is None:
        return None
    _, band, side, target = best
    prof, actual = synthesize(proto, band.feature, target)
    if abs(actual - band.center) < 0.25 * min_halfwidth(band.feature, band.center):
        return None  # cannot render a visible change for this feature
    return {"feature": band.feature, "side": side, "target": float(target), "value": float(actual),
            "profile": [float(x) for x in prof], "fs": FS,
            "band": {"lo": band.lo, "hi": band.hi, "center": band.center}}


def drill_pair(model: WordModel, proto: np.ndarray, rng: np.random.Generator, gap: float = 0.9) -> dict | None:
    """One true example and one near-miss outside a band, in random order.

    `gap` is how far outside the band the near-miss sits, in half-widths:
    drills start exaggerated (large gap) and fade towards the real edge.
    """
    bands = [b for b in model.bands() if b.feature in NEARMISS_FEATURES]
    if not bands or proto is None or len(proto) < 5:
        return None
    b = bands[int(rng.integers(0, len(bands)))]
    hw = max((b.hi - b.lo) / 2.0, min_halfwidth(b.feature, b.center))
    side = "hi" if rng.random() < 0.5 else "lo"
    target = b.hi + gap * hw if side == "hi" else max(0.0, b.lo - gap * hw)
    miss, actual = synthesize(proto, b.feature, target)
    if abs(actual - b.center) < 0.5 * hw:
        side = "hi"
        miss, actual = synthesize(proto, b.feature, b.hi + 1.2 * hw)
    true_first = bool(rng.random() < 0.5)
    a_prof, b_prof = (proto, miss) if true_first else (miss, proto)
    return {"feature": b.feature, "answer": "A" if true_first else "B",
            "A": [float(x) for x in a_prof], "B": [float(x) for x in b_prof], "fs": FS}
