"""gta6-twitch-ccu-forecast-01 entry: launch-day Twitch forecaster.

Forecasts Grand Theft Auto VI's Twitch category viewers for hours 2..24
of its release day (2026-11-19 UTC), as five quantiles an hour, from the
first hour's print and the release-day curves of past AAA launches.

Method (fitted offline on the kit's comparables; see MODEL.md):
  - The house baseline is persistence: every hour stays at hour 1's
    count. That is wrong for a rolling-midnight console launch: hour 1
    (00:00-01:00 UTC) only has New Zealand / Australia / Japan / Europe
    playing, the US unlocks at 05:00Z (East) and 08:00Z (West), and the
    day peaks in the evening. So the median forecast follows the pooled
    launch-day shape of past rolling-midnight console launches.
  - Shape: for each comparable launch, r(h) = viewers(h) / viewers(1).
    Per hour, the median and standard deviation of log r across the
    selected comparables, smoothed with a 3-hour moving average.
    Quantiles are lognormal: q_tau(h) = p1 * exp(median(h) + z_tau *
    sigma(h)), sigma clamped to [0.35, 1.2].
  - Scale adjustment: across launches, bigger hour-1 counts have
    flatter shapes (corr(log p1, log peak/p1) ~ -0.6). A slope fitted
    on the selected comparables adjusts the median, weighted by each
    hour's share of the typical ramp, capped at +/-0.35 in log terms
    so a launch bigger than anything seen cannot bend the curve far.
  - Comparables selected at run time from the case file: rolling
    midnight launches, not early-access contaminated, not weak
    analogues; plus unknown-unlock console-only launches whose own
    curve has no cliff. Simultaneous-global launches are excluded:
    their hour 1 is pre-launch and their curve is a step, not a ramp.
    Fallbacks widen the pool if fewer than 5 curves qualify, and if
    no curve qualifies at all the entry falls back to the house
    persistence forecast, so it always emits a valid file.

Offline validation (leave-one-out over the 9 rolling, clean
comparables in the kit): mean pinball loss 0.279, typical median
miss ~49%, vs 0.395 / 82% for the house baseline. The documented
practice loss of the house baseline is ~0.387, matching the 0.395
reproduced here, so the comparison is on the same scale.

Run by the grader as `python3 -P -s /bot/main.py` (python:3.13-slim,
standard library only, no network, 8 s per case). Reads every
<id>.in in the input directory and writes <id>.out to the output
directory: {"hours": [{"hour": 2, "q": [q05, q25, q50, q75, q95]},
...]}. Deterministic: no randomness, inputs processed in name
order, comparables sorted by name before any statistic is taken.
"""

import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from statistics import NormalDist, median, pstdev

# The grader's -P leaves /bot off sys.path: put it back first, so an
# `import model` finds a model.py shipped beside this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
_NORM = NormalDist()
Z = [_NORM.inv_cdf(tau) for tau in LEVELS]

# Spread of the lognormal quantiles, in natural-log units.
SIGMA_FLOOR = 0.35
SIGMA_CAP = 1.2
# House baseline spread (per sqrt hour), used only if no comparable
# curve qualifies at all.
BASELINE_SIGMA = 0.15
# Cap on the log-p1 shape adjustment, in log units.
ADJ_CAP = 0.35
# Bounds on the fitted slope of log peak-ratio on log hour-1 count.
SLOPE_MIN = -0.5
SLOPE_MAX = 0.0
# A comparable with an hour-1 print below this is too thin to anchor a
# shape on (sparse SullyGnome sampling of a quiet category).
MIN_P1 = 100
# Minimum curves for the preferred pool before fallbacks widen it.
MIN_FIT = 5
# A consecutive-hour jump above this marks a simultaneous launch's
# unlock step, not a rolling ramp.
CLIFF = 3.0
# A peak above this multiple of hour 1 marks a pre-launch hour 1.
MAX_PEAK_RATIO = 6.0


def round2(x: float) -> float | int:
    """x rounded to 2 decimals, half up, as JavaScript's Math.round(x * 100) / 100 rounds it.

    A whole number comes back as an int, so json.dumps writes 1240 where JavaScript writes 1240 (not 1240.0).
    """
    scaled = x * 100
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100
    return int(value) if value.is_integer() else value


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def classify_unlock(text: str) -> tuple[str, bool, bool]:
    """Classify a comparable's unlock text.

    Returns (unlock, early_access, weak) where unlock is one of
    "rolling", "simultaneous", "unknown".
    """
    t = (text or "").lower()
    early_access = "early-access-contaminated" in t
    weak = "tier: weak" in t
    if ("single global unlock" in t or "single simultaneous" in t
            or "simultaneous across platforms" in t
            or "simultaneous midnight" in t):
        return "simultaneous", early_access, weak
    if "rolling" in t or "midnight local" in t:
        return "rolling", early_access, weak
    return "unknown", early_access, weak


def release_day(hourly: list, day_start: datetime) -> dict:
    """Hourly points of a launch's release day, hour 1..24 -> viewers."""
    out = {}
    for p in hourly or []:
        try:
            t = parse_ts(p["from"])
            v = p["viewers"]
        except (KeyError, TypeError, ValueError):
            continue
        delta = (t - day_start).total_seconds() / 3600
        if 0 <= delta < 24 and isinstance(v, (int, float)) and v >= 0:
            out[int(delta) + 1] = v
    return out


def is_console_only(platforms: str) -> bool:
    """True for a console-only launch (no PC), the GTA VI shape."""
    p = platforms or ""
    if "PC" in p:
        return False
    return any(k in p for k in ("PS5", "PS4", "Switch", "Xbox"))


def has_cliff(day: dict) -> bool:
    """True if the release-day curve jumps like a simultaneous unlock."""
    for h in range(2, 25):
        if h in day and h - 1 in day and day[h - 1] >= 1:
            if day[h] / max(1, day[h - 1]) > CLIFF:
                return True
    return False


def load_curves(case: dict) -> list:
    """Release-day curves of the case's comparables, cleaned.

    Each curve: {"name", "unlock", "day", "p1"}. Curves whose hour-1
    print is too thin to anchor on are dropped.
    """
    curves = []
    for comp in case.get("comparables") or []:
        try:
            day_start = parse_ts(comp["day_start"])
        except (KeyError, TypeError, ValueError):
            continue
        day = release_day(comp.get("hourly"), day_start)
        p1 = day.get(1)
        if not p1 or p1 < MIN_P1:
            continue
        unlock, early_access, weak = classify_unlock(comp.get("unlock") or "")
        curves.append({
            "name": comp.get("name") or "",
            "unlock": unlock,
            "early_access": early_access,
            "weak": weak,
            "platforms": comp.get("platforms") or "",
            "day": day,
            "p1": p1,
        })
    curves.sort(key=lambda c: c["name"])
    return curves


def select_curves(curves: list) -> list:
    """The GTA-like pool: rolling midnight launches, clean analogues.

    Falls back to wider pools if fewer than MIN_FIT curves qualify,
    so the entry always has something to fit on when any comparable
    exists at all.
    """
    clean = [c for c in curves if not c["early_access"] and not c["weak"]]
    selected = [c for c in clean if c["unlock"] == "rolling"]
    for c in clean:
        if c["unlock"] != "unknown" or c in selected:
            continue
        if not is_console_only(c["platforms"]):
            continue
        if has_cliff(c["day"]):
            continue
        if max(c["day"].values()) / c["p1"] >= MAX_PEAK_RATIO:
            continue
        selected.append(c)
    if len(selected) >= MIN_FIT:
        return selected
    if len(clean) >= MIN_FIT:
        return clean
    if clean:
        return clean
    return curves


def smooth(vals: dict) -> dict:
    """3-hour moving average over the hours present (edges: 2-point)."""
    out = {}
    for h in sorted(vals):
        ns = [vals[hh] for hh in (h - 1, h, h + 1) if hh in vals]
        out[h] = sum(ns) / len(ns)
    return out


def fit_shape(curves: list) -> tuple[dict, dict]:
    """Per-hour median and sd of log(viewers(h) / viewers(1)), smoothed."""
    med, sd = {}, {}
    for h in range(2, 25):
        lr = [math.log(max(1, c["day"][h]) / c["p1"])
              for c in curves if h in c["day"] and c["day"][h] >= 1]
        if len(lr) >= 3:
            med[h] = median(lr)
            sd[h] = pstdev(lr) if len(lr) > 1 else SIGMA_FLOOR
    return smooth(med), smooth(sd)


def fit_p1_slope(curves: list) -> tuple[float, float]:
    """Slope and centre of log peak-ratio on log hour-1 count.

    Bigger launches have flatter shapes; the slope is clamped to
    [SLOPE_MIN, SLOPE_MAX] (never positive).
    """
    xs = [math.log(c["p1"]) for c in curves]
    ys = [math.log(max(c["day"].values()) / c["p1"]) for c in curves]
    n = len(xs)
    if n < 3:
        return 0.0, sum(xs) / n if n else 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    vx = sum((a - mx) ** 2 for a in xs) / n
    if vx <= 0:
        return 0.0, mx
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / n
    slope = cov / vx
    return max(SLOPE_MIN, min(SLOPE_MAX, slope)), mx


def hour1_print(case: dict) -> float:
    """Hour 1's print, clamped to at least 1 (a print below 1 counts as 1)."""
    for k in case.get("known") or []:
        if k.get("hour") == 1:
            try:
                return max(1.0, float(k["viewers"]))
            except (KeyError, TypeError, ValueError):
                pass
    # No hour-1 print (the final then has no answer, but the entry must
    # still emit a valid file): anchor on the last history point.
    hist = case.get("history") or []
    if hist:
        try:
            return max(1.0, float(hist[-1]["viewers"]))
        except (KeyError, TypeError, ValueError):
            pass
    return 1.0


def forecast_case(case: dict, name: str) -> dict:
    levels = case.get("levels") or LEVELS
    z = [_NORM.inv_cdf(tau) for tau in levels] if levels != LEVELS else Z
    p1 = hour1_print(case)
    hours = [row["hour"] for row in case.get("forecast") or []]
    if not hours:
        hours = list(range(2, 25))

    curves = select_curves(load_curves(case))
    if curves:
        med, sd = fit_shape(curves)
        slope, centre = fit_p1_slope(curves)
        peak_med = max(med.values()) if med else 0.0
        raw_adj = slope * (math.log(p1) - centre) if slope else 0.0
        adj = max(-ADJ_CAP, min(ADJ_CAP, raw_adj))
    else:
        med, sd, adj, peak_med = {}, {}, 0.0, 0.0

    rows = []
    for h in hours:
        if curves:
            m = med.get(h, 0.0)
            s = min(SIGMA_CAP, max(SIGMA_FLOOR, sd.get(h, SIGMA_FLOOR)))
            if adj and peak_med > 0.5:
                m += adj * (m / peak_med)
        else:
            # No comparable qualifies: the house persistence forecast.
            m = 0.0
            s = BASELINE_SIGMA * math.sqrt(max(0, h - 1))
        qs = []
        prev = 0.0
        for zz in z:
            q = p1 * math.exp(m + zz * s)
            q = min(1e9, max(1.0, q))
            q = round2(q)
            if q < prev:  # rounding must not break non-decreasing order
                q = prev
            prev = q
            qs.append(q)
        rows.append({"hour": h, "q": qs})
    return {"hours": rows}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Every case file, in name order, so two runs write the same bytes.
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
