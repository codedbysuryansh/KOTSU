"""Turning raw sensor streams into strokes and stroke features.

Sources
  phone  : continuous DeviceMotion (+ optional microphone features) from a phone
           held like a tool. Segmented here into strokes.
  drag   : a pointer/trackpad drag on a laptop (one message per stroke).
  touch  : a finger stroke on a phone screen (one message per stroke).
  force  : a MacBook Force Touch trackpad press (one message per stroke).
  sim    : synthetic strokes for testing and rehearsal.

Every stroke is reduced to an "intensity" profile (speed for motion,
pressure for force) resampled to 100 Hz, plus a feature vector.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

FS = 100.0  # Hz, resampling rate for profiles

# Feature metadata: display names, units, the minimum half-width of a band,
# and whether the band minimum is relative to the value (rel) or absolute (abs).
FEATURES: dict[str, dict] = {
    "duration":   {"en": "length of the stroke", "unit": "s", "min": 0.08, "mode": "abs"},
    "peak":       {"en": "peak strength", "unit": "", "min": 0.10, "mode": "rel"},
    "mean_mid":   {"en": "strength through the middle", "unit": "", "min": 0.10, "mode": "rel"},
    "rise":       {"en": "time to reach full strength", "unit": "s", "min": 0.05, "mode": "abs"},
    "steady_cv":  {"en": "evenness through the middle", "unit": "", "min": 0.05, "mode": "abs"},
    "jitter":     {"en": "shakiness", "unit": "", "min": 0.03, "mode": "abs"},
    "stop_time":  {"en": "how quickly it stops", "unit": "s", "min": 0.04, "mode": "abs"},
    "hold":       {"en": "time held at full strength", "unit": "s", "min": 0.08, "mode": "abs"},
    "pulses":     {"en": "number of taps", "unit": "", "min": 0.6, "mode": "abs"},
    "peak_acc":   {"en": "push at the start", "unit": "m/s²", "min": 0.12, "mode": "rel"},
    "wobble":     {"en": "tool wobble (angle)", "unit": "°/s", "min": 0.15, "mode": "rel"},
    "sound_level": {"en": "loudness", "unit": "", "min": 0.12, "mode": "rel"},
    "sound_rough": {"en": "roughness of the sound", "unit": "", "min": 0.05, "mode": "abs"},
}

SOURCE_FEATURES = {
    "phone": ["duration", "peak", "mean_mid", "rise", "steady_cv", "jitter", "stop_time", "peak_acc", "wobble", "sound_level", "sound_rough"],
    "drag": ["duration", "peak", "mean_mid", "rise", "steady_cv", "jitter", "stop_time", "pulses"],
    "touch": ["duration", "peak", "mean_mid", "rise", "steady_cv", "jitter", "stop_time", "pulses"],
    "force": ["duration", "peak", "rise", "hold", "steady_cv", "jitter", "stop_time", "pulses"],
    "sim": ["duration", "peak", "mean_mid", "rise", "steady_cv", "jitter", "stop_time", "pulses", "hold"],
}

INTENSITY_LABEL = {"phone": "speed (m/s)", "drag": "speed (px/s)", "touch": "speed (px/s)", "force": "pressure", "sim": "intensity"}


def source_type(src: str) -> str:
    base = src.split("-")[0].split(":")[0]
    return base if base in SOURCE_FEATURES else "sim"


def resample(t_ms: np.ndarray, y: np.ndarray, fs: float = FS) -> tuple[np.ndarray, np.ndarray]:
    """Resample (t in ms, y) to a uniform grid in seconds starting at 0."""
    t = (np.asarray(t_ms, dtype=float) - float(t_ms[0])) / 1000.0
    order = np.argsort(t, kind="stable")
    t, y = t[order], np.asarray(y, dtype=float)[order]
    # remove duplicate timestamps
    keep = np.concatenate([[True], np.diff(t) > 1e-6])
    t, y = t[keep], y[keep]
    if len(t) < 2:
        return np.array([0.0]), np.array([float(y[0]) if len(y) else 0.0])
    n = max(2, int(round(t[-1] * fs)) + 1)
    tu = np.arange(n) / fs
    return tu, np.interp(tu, t, y)


def smooth(y: np.ndarray, win_s: float, fs: float = FS) -> np.ndarray:
    k = max(1, int(round(win_s * fs)))
    if k <= 1 or len(y) < 3:
        return y.copy()
    if k % 2 == 0:
        k += 1
    pad = k // 2
    ypad = np.pad(y, pad, mode="edge")
    ker = np.ones(k) / k
    return np.convolve(ypad, ker, mode="valid")


def generic_features(s: np.ndarray, fs: float = FS) -> dict[str, float]:
    """Features of a 1-D intensity profile (non-negative)."""
    s = np.clip(np.asarray(s, dtype=float), 0.0, None)
    n = len(s)
    dur = (n - 1) / fs if n > 1 else 0.0
    if n < 3 or float(s.max()) <= 1e-9:
        return {"duration": dur, "peak": 0.0, "mean_mid": 0.0, "rise": 0.0, "steady_cv": 0.0, "jitter": 0.0, "stop_time": 0.0, "hold": 0.0, "pulses": 0.0}
    ss = smooth(s, 0.06, fs)
    peak = float(ss.max())
    i_peak = int(np.argmax(ss))
    a, b = int(0.2 * n), max(int(0.2 * n) + 2, int(0.8 * n))
    mid = ss[a:b]
    mean_mid = float(mid.mean()) if len(mid) else float(ss.mean())
    steady_cv = float(mid.std() / (mid.mean() + 1e-9)) if len(mid) > 2 else 0.0
    above = np.nonzero(ss >= 0.8 * peak)[0]
    rise = float(above[0] / fs) if len(above) else float(i_peak / fs)
    hold = float((above[-1] - above[0]) / fs) if len(above) else 0.0
    # stop: time from the last 80% crossing to the first time below 10% after it
    last80 = int(above[-1]) if len(above) else i_peak
    tail = ss[last80:]
    below = np.nonzero(tail <= 0.1 * peak)[0]
    stop_time = float(below[0] / fs) if len(below) else float(len(tail) / fs)
    # jitter: high-frequency residual relative to level
    base = smooth(s, 0.15, fs)
    resid = s - base
    jitter = float(np.sqrt(np.mean(resid[a:b] ** 2)) / (mean_mid + 1e-9)) if b - a > 2 else 0.0
    # pulses: separated peaks above 50% of peak
    pulses = 0
    min_gap = int(0.12 * fs)
    last_i = -10 * min_gap
    thr = 0.5 * peak
    for i in range(1, n - 1):
        if ss[i] >= thr and ss[i] >= ss[i - 1] and ss[i] > ss[i + 1] and i - last_i >= min_gap:
            # require a dip below 35% before the next counted pulse
            if pulses == 0 or float(ss[last_i:i].min()) < 0.35 * peak:
                pulses += 1
                last_i = i
    return {"duration": dur, "peak": peak, "mean_mid": mean_mid, "rise": rise, "steady_cv": steady_cv,
            "jitter": jitter, "stop_time": stop_time, "hold": hold, "pulses": float(max(pulses, 1))}


@dataclass
class Stroke:
    id: str
    src: str
    stype: str
    t0: float               # server time (s) when the stroke started
    t1: float               # server time (s) when it ended
    profile: list[float]    # intensity, resampled at FS then decimated for storage
    fs: float               # sampling rate of `profile`
    features: dict[str, float]
    meta: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {"id": self.id, "src": self.src, "stype": self.stype, "t0": self.t0, "t1": self.t1,
                "features": {k: round(v, 4) for k, v in self.features.items()},
                "profile": [round(x, 4) for x in self.profile], "fs": self.fs, "meta": self.meta}


def decimate_profile(s: np.ndarray, max_pts: int = 600) -> tuple[list[float], float]:
    n = len(s)
    if n <= max_pts:
        return [float(x) for x in s], FS
    step = int(math.ceil(n / max_pts))
    return [float(x) for x in s[::step]], FS / step


def features_from_explicit(stype: str, t_ms: list[float], x: list[float] | None = None, y: list[float] | None = None,
                           p: list[float] | None = None, f: list[float] | None = None) -> tuple[np.ndarray, dict]:
    """Build intensity + features for drag/touch (positions) and force (pressure) strokes."""
    t_ms = np.asarray(t_ms, dtype=float)
    if stype == "force":
        _, s = resample(t_ms, np.asarray(f, dtype=float))
        feats = generic_features(s)
        feats.pop("mean_mid", None)
        return s, {k: feats[k] for k in SOURCE_FEATURES["force"] if k in feats}
    # positions -> speed
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    tu, xu = resample(t_ms, x)
    _, yu = resample(t_ms, y)
    if len(xu) < 3:
        s = np.zeros(3)
    else:
        vx = np.gradient(smooth(xu, 0.03)) * FS
        vy = np.gradient(smooth(yu, 0.03)) * FS
        s = np.hypot(vx, vy)
    feats = generic_features(s)
    out = {k: feats[k] for k in SOURCE_FEATURES.get(stype, SOURCE_FEATURES["drag"]) if k in feats}
    return s, out


class PhoneSegmenter:
    """Online stroke segmentation for a phone streamed as continuous samples.

    Input samples: t (ms, phone clock), ax, ay, az (m/s^2, gravity removed),
    gx, gy, gz (deg/s), optional lvl (mic RMS 0..1), flat (spectral flatness 0..1).
    """

    def __init__(self) -> None:
        self.buf: list[tuple] = []          # rolling window of recent samples
        self.in_stroke = False
        self.stroke: list[tuple] = []
        self.on_count = 0.0
        self.off_count = 0.0
        self.noise: list[float] = []
        self.ema = 0.0
        self.last_t = None

    def _energy(self, s: tuple) -> float:
        _, ax, ay, az, gx, gy, gz, *_ = s
        return math.sqrt(ax * ax + ay * ay + az * az) + 0.004 * math.sqrt(gx * gx + gy * gy + gz * gz)

    def thresholds(self) -> tuple[float, float]:
        if len(self.noise) >= 30:
            arr = np.array(self.noise[-300:])
            med = float(np.median(arr))
            mad = float(np.median(np.abs(arr - med))) + 1e-6
            floor = med + 5 * mad
        else:
            floor = 0.25
        on = max(0.55, 2.2 * floor)
        return on, 0.55 * on

    def push(self, samples: list[tuple]) -> list[list[tuple]]:
        """Feed samples; returns a list of completed strokes (each a list of samples)."""
        done: list[list[tuple]] = []
        for s in samples:
            t = s[0]
            dt = 0.016 if self.last_t is None else max(0.0, min(0.1, (t - self.last_t) / 1000.0))
            self.last_t = t
            e = self._energy(s)
            self.ema = self.ema + (1 - math.exp(-dt / 0.05)) * (e - self.ema) if dt > 0 else e
            on, off = self.thresholds()
            self.buf.append(s)
            if len(self.buf) > 40:
                self.buf.pop(0)
            if not self.in_stroke:
                self.noise.append(self.ema)
                if len(self.noise) > 600:
                    self.noise.pop(0)
                self.on_count = self.on_count + dt if self.ema > on else 0.0
                if self.on_count >= 0.06:
                    self.in_stroke = True
                    pre = self.buf[-12:]           # ~200 ms pre-roll
                    self.stroke = list(pre)
                    self.off_count = 0.0
            else:
                self.stroke.append(s)
                self.off_count = self.off_count + dt if self.ema < off else 0.0
                too_long = (t - self.stroke[0][0]) > 5000
                if self.off_count >= 0.22 or too_long:
                    self.in_stroke = False
                    self.on_count = 0.0
                    if (self.stroke[-1][0] - self.stroke[0][0]) >= 250:
                        done.append(self.stroke)
                    self.stroke = []
        return done


def phone_features(samples: list[tuple]) -> tuple[np.ndarray, dict]:
    arr = np.array([list(s[:7]) + [s[7] if len(s) > 7 else 0.0, s[8] if len(s) > 8 else 0.0] for s in samples], dtype=float)
    t_ms = arr[:, 0]
    tu, ax = resample(t_ms, arr[:, 1])
    _, ay = resample(t_ms, arr[:, 2])
    _, az = resample(t_ms, arr[:, 3])
    _, gmag = resample(t_ms, np.sqrt(arr[:, 4] ** 2 + arr[:, 5] ** 2 + arr[:, 6] ** 2))
    _, lvl = resample(t_ms, arr[:, 7])
    _, flat = resample(t_ms, arr[:, 8])
    dt = 1.0 / FS
    # integrate acceleration to velocity, then remove linear drift so the tool is at rest at both ends
    vx, vy, vz = np.cumsum(ax) * dt, np.cumsum(ay) * dt, np.cumsum(az) * dt
    n = len(vx)
    ramp = np.linspace(0.0, 1.0, n)
    vx -= ramp * vx[-1]
    vy -= ramp * vy[-1]
    vz -= ramp * vz[-1]
    speed = np.sqrt(vx ** 2 + vy ** 2 + vz ** 2)
    feats = generic_features(speed)
    amag = np.sqrt(ax ** 2 + ay ** 2 + az ** 2)
    feats["peak_acc"] = float(smooth(amag, 0.04).max())
    a, b = int(0.2 * n), max(int(0.2 * n) + 2, int(0.8 * n))
    feats["wobble"] = float(np.std(gmag[a:b])) if b - a > 2 else 0.0
    feats["sound_level"] = float(np.mean(lvl)) if np.any(lvl) else 0.0
    feats["sound_rough"] = float(np.mean(flat)) if np.any(flat) else 0.0
    out = {k: feats[k] for k in SOURCE_FEATURES["phone"] if k in feats}
    if not np.any(lvl):
        out.pop("sound_level", None)
        out.pop("sound_rough", None)
    return speed, out


def synth_profile(kind: str, duration: float = 1.0, peak: float = 1.0, rise: float = 0.2, stop: float = 0.12,
                  jitter: float = 0.02, pulses: int = 1, seed: int | None = None) -> np.ndarray:
    """Synthetic intensity profiles used by the simulator, seed data and near-miss rendering."""
    rng = np.random.default_rng(seed)
    n = max(5, int(duration * FS))
    t = np.arange(n) / FS
    if kind == "pulses":
        s = np.zeros(n)
        centers = np.linspace(0.15 * duration, 0.85 * duration, max(1, pulses))
        for c in centers:
            s += peak * np.exp(-0.5 * ((t - c) / 0.035) ** 2)
    else:
        env = np.ones(n) * peak
        r = max(1, int(rise * FS))
        env[:r] = peak * (np.linspace(0, 1, r) ** (1.0 if kind != "slow" else 2.2))
        st = max(1, int(stop * FS))
        env[-st:] = env[-st:] * np.linspace(1, 0, st)
        s = env
    s = s + jitter * peak * rng.standard_normal(n) * (s > 0.05 * peak)
    return np.clip(s, 0.0, None)
