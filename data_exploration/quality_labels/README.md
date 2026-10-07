# Quality labels: how Surfline does it vs. us

Exploration branch `explore/quality-labels`. Nothing in the app is changed yet.

## Our current system

`backend/rating_config.py` (which generates `js/rating.js`):

- score = energy × direction × wind × tide, scaled to 0–100.
- Five buckets: **Poor** `#ef4444` (0), **Fair** `#eab308` (22), **Good** `#22c55e` (42),
  **Excellent** `#15803d` (62), **Epic** `#a855f7` (80).
- Fully automatic. Any hour can be labelled Epic if the model inputs line up.
- Every spot in `locations.csv` has empty rating columns, so each profile is derived
  from `Spot.coast_normal = 270`. That gives an offshore wind direction of 90°.

## Surfline's system

Sources: help center articles 36277684017819 (Surf Ratings & Colors) and
35995874238491 (Observation Clarity), 4410127028251, and 40670765597467, read through
the Zendesk API. The live site and `services.surfline.com` return a Cloudflare
challenge (HTTP 403) to scripts, so they were not scraped.

| API `rating.key` | value | bars | color (sampled from their image) | assigned by |
|---|---|---|---|---|
| VERY_POOR    | 0 | 1 | `#f1496a` pink-red  | model or forecaster |
| POOR         | 1 | 2 | `#fd9500` orange    | model or forecaster |
| POOR_TO_FAIR | 2 | 3 | `#ffce1c` yellow    | model or forecaster |
| FAIR         | 3 | 4 | `#09d572` green     | model or forecaster |
| FAIR_TO_GOOD | 4 | 5 | `#009371` teal      | model or forecaster (the model's ceiling) |
| GOOD         | 5 | 5 | `#6751f4` indigo    | **forecaster only**, shown with an animation |
| EPIC         | 6 | 5 | `#631ec7` purple    | **forecaster only** |

What Surfline says about how it works:

- **The model's ceiling is FAIR_TO_GOOD.** Only a forecaster who has watched the
  surf can assign GOOD or EPIC. That is why the scale has 7 ratings but only 5 bars.
- **Model inputs are breaking wave height, wind speed and wind direction only.**
  Tide and wave shape are not included, and neither is swell analysis such as
  spectra, energy or consistency.
- **Ratings are per hour and relative to each spot's potential.** For example,
  "Fair" at Pipeline is not the same as "Fair" at a beachbreak. Ratings are for
  comparing days at one spot, not for comparing spots.
- **Each spot has a configuration**, described as "a matrix of directions and periods
  that we can adjust". Some spots are dialled in and others use basic logic. The API
  exposes this as `optimalScore` (0/1/2) on the surf, on each swell, and on the wind,
  and as a `directionType` (Onshore / Cross-shore / Offshore) for the wind.
- **The model is trained on observations.** It is an ML model trained on 35 years of
  forecaster observations, and forecaster corrections feed back into it.
- **The UI shows where each rating came from** ("LOTUS model" vs. "Forecaster",
  marked with an Observed badge).
- **Users can switch color themes**: "Traffic Light" (the table above) or "Classic".
  The Classic palette is only shown in screenshots, so we don't have its colors.
- **The API returns a continuous `value`** (e.g. 2.67, 2.25) alongside the bucket key.
  Like ours, it is a continuous score that gets bucketed.

## Experiment: our engine on Surfline's own inputs

`compare_surfline.py` takes 24 three-hourly Surfline API responses for Old Man's at
Tourmaline (Feb 13–15 2023, a public sample in `TGOlson/surfline`) and scores the same
hours with our engine. This spot is also in our `locations.csv`.

```
python data_exploration/quality_labels/compare_surfline.py
```

Spearman rank correlation with Surfline's `value`:

- **+0.79** with the profile the app uses today (offshore = 90°).
- **+0.42** when the profile uses offshore = 30°, the bearing Surfline's own
  `directionType` labels imply. Using the more accurate wind direction makes the
  agreement worse, for the reason in finding 1.

Findings (n = 24, one spot, one swell event, so these are indicative only):

1. **Strong wind is bad even when it's offshore.** In the Feb 14 storm (3.3–3.9 m Hs,
   17–24 kt), Surfline rates **POOR**, including at 24 kt "Offshore". Our `_wind_factor`
   gives offshore wind a boost that grows with speed, so with the corrected profile we
   call those hours Excellent. A speed penalty is needed that applies in every
   direction above roughly 15 kt, along with closeout handling for storm surf.
2. **Our scale tops out too high.** On the clean offshore hours of Feb 15, Surfline's
   model says FAIR_TO_GOOD and we say Excellent or Epic. Surfline deliberately keeps
   its top two ratings for human observers. We have no observers, so a model-only
   Epic will overclaim.
3. **Light onshore wind on small days is penalised more by Surfline.** At about 1 m Hs,
   7 s and 7–10 kt onshore, Surfline says POOR_TO_FAIR while we often say Good.
4. **`coast_normal = 270` is wrong for Tourmaline.** Surfline's labels put offshore at
   about 30°, and we use 90°. Every spot currently shares the same derived profile.

## Possible directions (not implemented)

- **Palette and scale.** Move to a 7-step scale (or 5 bars plus two reserved labels)
  following Surfline's progression: red → orange → yellow → green → teal, with purple
  reserved for the top. Cap model-only output at the equivalent of "Fair to Good",
  unless we decide our model has earned higher.
- **Wind.** Penalise strong wind in every direction and make the offshore boost peak
  at moderate speeds.
- **Per-spot offshore and swell window.** Fill these in `locations.csv`, perhaps
  starting from a coastline dataset.
- **Show the source of each rating** ("model") in the UI, the way Surfline does.
- **Calibrate against more data.** One spot over three days is not enough. If Surfline
  data can be collected legitimately (for example, logged manually from the app), fit
  our score → bucket thresholds across several spots and seasons.
