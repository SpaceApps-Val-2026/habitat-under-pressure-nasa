"""Mann-Kendall with the autocorrelation correction adopted for A5 ("C2s").

Yue and Wang (2004) with the first lag, never letting Var(S) fall below the classic variance, and Sen's slope with
the Gilbert interval computed from that same variance. Decided by Guillem on 13 November 2026 after simulating six
methods; see .dev/notes/2026-11-13-preparacion-a5.md, sections 6 to 10, and docs/decisiones.md.
"""
import math

import numpy as np
import pymannkendall as mk
from scipy.stats import norm

CORRECTION = "yue_wang_lag1_floor"


def c2s(values, alpha=0.05):
    """(p, z, var_s) of the corrected test, or None when the series has no variation."""
    x = np.asarray(values, dtype=float)
    classic = mk.original_test(x, alpha=alpha)
    if not classic.var_s > 0:
        return None
    # A detrended series without variation has no autocorrelation; the classic variance applies. np.errstate is
    # per thread, unlike warnings filters, so it also holds when the executor runs steps in parallel.
    with np.errstate(divide="ignore", invalid="ignore"):
        corrected = mk.yue_wang_modification_test(x, alpha=alpha, lag=1).var_s
    var_s = max(corrected, classic.var_s) if np.isfinite(corrected) else classic.var_s
    s = classic.s
    z = 0.0 if s == 0 else (s - math.copysign(1, s)) / math.sqrt(var_s)
    return float(2 * (1 - norm.cdf(abs(z)))), float(z), float(var_s)


def sen_interval(values, var_s, confidence):
    """Gilbert interval for Sen's slope of an evenly spaced series, with the ranks of scipy.stats.theilslopes."""
    x = np.asarray(values, dtype=float)
    i, j = np.triu_indices(len(x), 1)
    slopes = np.sort((x[j] - x[i]) / (j - i))
    count, half = len(slopes), norm.ppf(0.5 + confidence / 2) * math.sqrt(var_s)
    low = max(int(np.round((count - half) / 2)) - 1, 0)
    high = min(int(np.round((count + half) / 2)), count - 1)
    return float(slopes[low]), float(slopes[high])
