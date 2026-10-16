"""Bounded strength allocation without a second portfolio or cash ledger."""

import math
from collections.abc import Mapping


def strength_weights(
    scores: Mapping[str, float],
    exposure: float,
    *,
    minimum: float = 0.15,
    maximum: float = 0.25,
    lower_bounds: Mapping[str, float] | None = None,
    upper_bounds: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Water-fill score differences; capped residuals go to the other names."""
    assets = sorted(scores)
    values = [*scores.values(), exposure, minimum, maximum]
    if any(
        isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v)
        for v in values
    ):
        raise ValueError("Allocation inputs must be finite numbers")
    if not 0 <= minimum <= maximum <= 1 or not 0 <= exposure <= 1:
        raise ValueError("Invalid allocation bounds")
    if not assets:
        if exposure > 1e-12:
            raise ValueError("An empty selection cannot invest")
        return {}
    lower = {a: (lower_bounds or {}).get(a, minimum) for a in assets}
    upper = {a: (upper_bounds or {}).get(a, maximum) for a in assets}
    if any(
        not math.isfinite(lower[a])
        or not math.isfinite(upper[a])
        or not minimum <= lower[a] <= upper[a] <= maximum
        for a in assets
    ):
        raise ValueError("Invalid per-asset bounds")
    if exposure < sum(lower.values()) - 1e-12 or exposure > sum(upper.values()) + 1e-12:
        raise ValueError("Exposure cannot satisfy the holding bounds")
    result = dict(lower)
    baseline = min(scores.values())
    strength = {a: scores[a] - baseline for a in assets}
    if not all(math.isfinite(v) for v in strength.values()):
        raise ValueError("Score differences overflowed")
    remaining = max(0.0, exposure - sum(result.values()))
    for _ in range(len(assets) + 2):
        if remaining <= 1e-12:
            break
        active = [a for a in assets if upper[a] - result[a] > 1e-12]
        total = sum(strength[a] for a in active)
        used = 0.0
        for a in active:
            share = strength[a] / total if total else 1 / len(active)
            addition = min(upper[a] - result[a], remaining * share)
            result[a] += addition
            used += addition
        remaining -= used
    if remaining > 1e-10:
        raise ValueError("Allocation did not converge")
    return result
