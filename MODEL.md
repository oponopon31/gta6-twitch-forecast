# Method

## The problem with persistence

The house baseline says every hour of release day draws as many Twitch
viewers as hour 1 did. For a rolling-midnight console launch that is
wrong in a predictable direction: hour 1 (00:00–01:00 UTC) only has New
Zealand / Australia / Japan / Europe playing — the US unlocks at 05:00Z
(East) and 08:00Z (West) — and the day peaks in the evening. GTA VI
releases 2026-11-19 with a rolling midnight unlock (New Zealand first,
2026-11-18T11:00Z), so it is a rolling launch, exactly like the four
practice cases (Marvel's Wolverine, Red Dead Redemption 2, God of War
Ragnarök, Marvel's Spider-Man 2).

## Model (`main.py`, standard library only)

1. **Pool.** From the case's comparables, take the launches whose
   unlock is a rolling midnight, that are not early-access contaminated
   and not weak analogues, with an hour-1 print ≥ 100 viewers. Add
   unknown-unlock console-only launches whose own curve has no cliff
   (no >3x consecutive-hour jump) and whose peak is < 6x hour 1.
   Simultaneous-global launches are excluded: their hour 1 is
   pre-launch and their curve is a step, not a ramp. Fallbacks widen
   the pool if fewer than 5 curves qualify; with no curve at all the
   entry falls back to the house persistence forecast, so it always
   emits a valid file.
2. **Shape.** For each selected launch, r(h) = viewers(h)/viewers(1).
   Per hour, the median and standard deviation of log r, smoothed
   with a 3-hour moving average. Quantiles are lognormal:
   q_τ(h) = p1 · exp(median(h) + z_τ · σ(h)), σ clamped to [0.35, 1.2].
   The fitted median shape dips to ~0.77x p1 at 03:00–04:00 UTC,
   ramps from 05:00Z, plateaus ~1.1x midday, peaks ~2.4x at 19:00–20:00
   UTC and decays to ~2.2x by 24:00 UTC.
3. **Scale adjustment.** Across launches, bigger hour-1 counts have
   flatter shapes (corr(log p1, log peak/p1) ≈ −0.6). A slope fitted
   on the selected comparables adjusts the median, weighted by each
   hour's share of the typical ramp, capped at ±0.35 in log terms so
   a launch bigger than anything seen (GTA VI will be) cannot bend
   the curve far. A 500k hour-1 print forecasts an evening peak of
   ~1.8x, not 2.4x.

## Validation (offline, no local grader exists for this competition)

Leave-one-out over the 9 rolling, clean comparables in the kit,
scored with the competition's own pinball loss on the log scale:

| model | pinball loss | typical median miss |
|---|---|---|
| house baseline (persistence, σ=0.15) | 0.395 | 82% |
| this entry (all 38 comparables in the case) | **0.267** | **46%** |
| this entry (rolling-clean pool only) | 0.277 | 49% |

The 0.395 baseline reproduces the ~0.387 practice loss the rules
quote for the house baseline, so the comparison is on the same scale.
The entry's own code was used for the evaluation
(`dev-gta6-forecast/test_entry.py`), not a separate copy of the model.
Outputs on the four practice cases are valid (23 rows, hours 2–24,
five non-decreasing quantiles each, all in (0, 1e9]) and byte-identical
across reruns; edge cases (no comparables, missing hour 1, hour-1
print of 0) all emit valid files.

## Honest limits

- The shape is a pooled median over ~13–16 launches; any single
  launch can deviate a lot (the worst leave-one-out case, Resident
  Evil Requiem, scores 0.96 — its hour 1 was unusually quiet for its
  peak). The intervals are wide on purpose: the 90% band at the
  evening peak runs roughly 0.9x–6.6x hour 1.
- GTA VI is bigger than every launch in the fit set. The scale
  adjustment is capped, but the true shape of a launch this size is
  an extrapolation — the intervals carry that uncertainty.
- No local grader exists for this competition (scorer and reference
  data are server-only); the practice score appears on the board
  after submission.
