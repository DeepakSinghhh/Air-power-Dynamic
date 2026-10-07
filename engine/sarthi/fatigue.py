"""Crew fatigue: a two-process (homeostatic + circadian) effectiveness model.

Process S follows Borbely's two-process model of sleep regulation (rise time
constant ~18.2 h awake, decay ~4.2 h asleep). Process C is a 24 h cosine with an
afternoon peak. The mapping to an "effectiveness %" is an illustrative
calibration (~97% mid-morning after a full night, ~70% at 04:00 after 21 h
awake). Swap in a validated model (e.g. SAFTE) for anything beyond a demo.
"""
from __future__ import annotations

import numpy as np

from .geo import Intervals, mask_to_intervals
from .models import Crew

TAU_RISE_H = 18.2
TAU_DECAY_H = 4.2
S_AT_BEDTIME = 0.70
CIRCADIAN_PEAK_H = 17.0
NIGHT = (19 * 60, 29 * 60)  # 19:00 to 05:00 next day


def effectiveness(crew: Crew, t: np.ndarray | int) -> np.ndarray | float:
    """Predicted cognitive effectiveness (0-100) at minute(s) t, assuming no nap after waking."""
    t = np.asarray(t, dtype=float)
    s_wake = S_AT_BEDTIME * np.exp(-crew.sleep_hours / TAU_DECAY_H)
    awake_h = np.maximum(t - crew.wake_time, 0.0) / 60.0
    s = 1.0 - (1.0 - s_wake) * np.exp(-awake_h / TAU_RISE_H)
    clock_h = (t / 60.0) % 24.0
    c = np.cos(2 * np.pi * (clock_h - CIRCADIAN_PEAK_H) / 24.0)
    e = np.clip(100.0 - 45.0 * (s - 0.2) + 6.0 * c, 0.0, 100.0)
    return float(e) if e.ndim == 0 else e


def is_night(t: np.ndarray) -> np.ndarray:
    m = np.asarray(t) % 1440
    return (m >= NIGHT[0]) | (m < NIGHT[1] - 1440)


def fit_tot_domain(crew: Crew, recover_after_tot: int, lo: int, hi: int, threshold: float,
                   step: int = 1) -> Intervals:
    """TOT values in [lo, hi] at which the crew is fit at TOT and on recovery."""
    if hi < lo:
        return []
    times = np.arange(lo, hi + 1, step)
    ok = (effectiveness(crew, times) >= threshold) & \
         (effectiveness(crew, times + recover_after_tot) >= threshold)
    if not crew.night_qualified:
        ok &= ~is_night(times) & ~is_night(times + recover_after_tot)
    out = mask_to_intervals(times, ok, step)
    return [(a, min(b, hi)) for a, b in out if a <= hi]
