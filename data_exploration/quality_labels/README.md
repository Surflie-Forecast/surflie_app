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

## Step 1 implemented: spot orientation + wind rule (2026-10-07)

**Orientation.** `backend/build_spot_orientation.py` writes `data/spot_orientation.csv`,
which `spots.load_spots` reads as derived rating metadata (`locations.csv` columns
still win; a new `offshore_width` column was added for hand tuning).

- Facing = CDIP MOP shore normal at the spot's transect (the one the nearshore
  forecast uses), averaged over the consecutive transects within ~±200 m of coast,
  triangular weights. ±500 m / ±1 km averages flag a turning coast (spread > 20°).
  CDIP's `1000` marker means "no normal" and is skipped.
- 269 of 276 spots come from MOP; 7 (harbours, bays) use the centre of the open water.
- Swell window = open-water arc within ±90° of facing, rays every 2° over the Salix
  elevation grids. Only land within 30 km blocks: strict line of sight to 250 km made
  distant islands walls (88 spots facing land). Channel Islands shadowing is not modelled.
- Old Man's: facing 214°, offshore 34° (Surfline's labels imply ~30°).

**Wind.** Three zones by angle off offshore (full boost within `offshore_width` = 30°,
fading over 30°; cross-shore to 100° with a light penalty; onshore beyond), offshore
boost peaking at 8 kt and gone by 15 kt, a direction-blind penalty from 12 kt to a
0.4 floor at 20 kt, and a storm penalty from steepness Hs/L0 > 0.013. `sweep_wind.py`
(414,720 configs) pinned down only the strong-wind onset, the cross-shore penalty and
the storm steepness; the rest is surf knowledge. The swell direction factor now uses
the half-width of the side the swell comes from.

**Results.** Spearman vs Surfline (`compare_surfline.py`): **+0.90** with the app's
profile (was +0.79; the old wind rule with the correct direction gave +0.42). Storm
hours are now Poor (Surfline: POOR); clean Feb 15 hours are Good (Surfline:
FAIR_TO_GOOD); nothing reaches Excellent/Epic. Across all 276 spots on the
2026-10-07 store, Excellent+Epic days fall from 14.5% to 6.7%.

Still open: the scale/colour choice (A/B/C above) and the model-only cap.

**Bucket floors recalibrated (same day).** Epic kept for near-perfect hours:
Good 42–70, Excellent 70–92, Epic 92+ (was 62 / 80). On the 2026-10-07 store, daily
shares are Poor 41.4%, Fair 36.0%, Good 18.5%, Excellent 3.9%, Epic 0.2% (was 1.4%).
Swell windows now bridge gaps up to 8° and keep the arc the break faces (Ventura
Point: 152–264°, ideal 174°).

**Wind retuned with guards (same day).** `sweep_wind.py` v2 searches 2.6M configs on
the app's real Old Man's profile, keeping only those that pass ten surf-sense guards
(e.g. 17 kt offshore on a clean swell ≤ Fair, 12 kt offshore ≥ Good). Applied: strong-
wind penalty from 10 kt to a 0.2 floor at 18 kt, cross-shore penalty 0.15 of onshore
out to 120°, onshore at full strength by 12 kt. Spearman **+0.92** (was +0.90, which
failed three guards). The sample alone rewards ever-harsher wind penalties (only the
four storm hours have strong wind), so the guards, not the data, set how harsh.
Daily shares: Poor 41.8%, Fair 35.2%, Good 18.7%, Excellent 4.0%, Epic 0.3%.

## To pick up later (not done)

**Per-spot size range from slope + bottom type.** Every spot still uses the shared
range (0.5 / 1.8 / 4.0 m Hs). Tested 2026-10-07 at Old Man's (only spot with
Surfline data): a sweet spot >= 1.4 m keeps agreement at +0.925; "gentle = small"
(2A) drops it to +0.890 (consistently, bootstrap); "steep sand closes out sooner"
(2B) ties option 1 (+0.916, within noise) but gave Ghost Tree, a big-wave reef, the
smallest sweet spot. Plan: 2B for sand beaches only, reefs/points keep the shared
range unless hand-set; needs a `bottom_type` column (beach / reef / point).

Bottom-type sources to check, in order (never Surfline: Cloudflare + ToS):
1. NOAA ENC charts, seabed areas (SBDARE, NATSUR: sand / rock / gravel).
2. California Seafloor Mapping Program substrate maps (likely starts ~10 m deep).
3. OpenStreetMap (natural=reef / beach, surface=*); volunteer quality.
4. Fallback: draft from spot names (Point, Reef, Cove, Jetty…) + "coast_turns"
   flag, corrected by hand.

**Ruled out for now.**
- Breaking-height transform (Komar-Gaillard on offshore Hs): no better at ranking
  Surfline's surf heights (+0.86 vs +0.87 for Hs) and worse for the rating (+0.889
  vs +0.925; period counted twice). Salix nearshore Hs already covers sheltering.
- Iribarren wave-shape factor: at one spot it is a mirror of wave steepness
  (rank corr -1.00), so it adds nothing to the sea-state factor; every hour
  classifies as spilling. Break-zone slope from NOAA CRM SoCal (1 arc-sec) matches
  the 0-10 m average within ~30% at 9 of 11 spots (Malibu, Trestles ~1.8x steeper)
  and rests on 1.4-5.8 grid cells, so it can't resolve bars or reef ledges.

**Still open from earlier:** scale/colour choice (A / B / C), point-break swell
windows by hand (Ventura Point, Rincon, Steamer Lane, Malibu, C St), Channel Islands
shadowing, partition-aware direction (score each swell against the window).

## Review page

`review_page/` builds the spot orientation review page (published as the Claude
artifact https://claude.ai/artifact/EvtJo9KWnUEMT15bDLxf8V): every spot's facing,
offshore wind and swell window on its local coastline, plus a live breakdown of how
each factor builds the label, using the app's own `js/rating.js`.

    python data_exploration/quality_labels/review_page/build_review_page.py

The page is a snapshot: rebuild it after changing `rating_config.py` (regenerate
`js/rating.js` first) or `data/spot_orientation.csv`, then republish to the same URL.

To preview a rating change against the current engine, pass the old engine and its
forecast build: `--now-engine old/rating.js --now-forecast-dir old/api/forecast`. The
page then shows "Now → Proposed" scores, the factor values that changed, and both
label shares (used for the 2026-10-09 retune:
https://claude.ai/artifact/HmQg5QJKDVUWSf1HRxkAah).

## Period retune (2026-10-09)

A 1 s change could swing the score by 20+ points (1.5 m clean swell: 7 s -> 8 s
moved 49 -> 65) because the storm penalty was linear in steepness Hs / (1.56 T^2),
so most of its drop landed in one or two seconds. And a small 17 s swell scored
like a 12 s one (Ventura Point 0.71 m / 17 s: 27, Poor to fair). Changes, chosen
with a sweep against smoothness guards (worst 1 s jump, 6 s wind swell stays low,
storm hours stay low, Spearman >= 0.90):

- Storm penalty grows linearly in PERIOD below the onset period T_on, full at
  T_on / 3 (`STORM_END_RATIO`). Onset and floor unchanged.
- Period-quality exponent 0.4 -> 0.8 (`PERIOD_WEIGHT`): short periods lose quality
  gradually from 14 s down to 6 s, instead of via a storm cliff.
- Long periods count as bigger surf: Hs x (T / 12 s)^0.4 above 12 s
  (`PERIOD_SIZE_*`; the T^0.4 is Komar-Gaughan's breaking-height dependence, the
  12 s reference is a calibration choice that leaves <= 12 s days unchanged).
  Earlier "ruled out" breaking-height test applied the full transform on top of
  the period term, and the Surfline sample (6-15 s, one storm) can't separate it.
- Fair to good floor 48 -> 55.

Result: worst 1 s jump 21 -> ~12 points; 1.5 m at 6/7/8/9/10 s scores
31/40/51/60/67 (was 30/49/65/74/78); Spearman +0.91 (was +0.92); exact Surfline
labels 13/24 (was 18/24). Daily labels across 276 spots x 14 days: Fair to good
23% (30% before, 34% if the floor stayed 48), Fair 44%, Poor to fair 24%. Still
open: the size curve is steep just above size_min (2 cm moved a 0.7 m hour 5%).

## Bottom type (2026-10-08, branch explore/quality-labels-2)

`backend/build_spot_bottom.py` writes `data/spot_bottom.csv` (needs network and
`pip install lerc`, local only): **point** if the coast bends seaward >= 20° within
±500 m (MOP shore normals), else **reef** if >= 40% of the transect is hard seabed in
CDFW BIOS ds3091 (10 m hard/soft raster, CC BY 4.0) or >= 25% with a mostly rocky
shoreline in CDFW BIOS ds3115 (NOAA ESI shoreline types, CC BY 4.0), else **beach**.
Result: 70 points, 42 reefs, 164 beaches; 23/24 known spots right (C Street, a cobble
point on straight coast, reads beach). 16 spots flagged for a shoreline check
(armoured shoreline, or none mapped), mostly harbour/jetty-made "points".

Ruled out as bottom-type sources: NOAA ENC charts (seabed samples km offshore),
OpenStreetMap (maps the shore, calls cobble points sand beaches). Paid Surfline
scrapers (anakin.io, parse.bot) and surflinef (direct API calls) were rejected:
outsourced Cloudflare bypass and ToS breach. Swell "cleanliness" (one dominant swell vs
crossed seas) showed no effect on the Surfline sample (+0.925 at any penalty) and
Surfline's model ignores swell mix, so it waits for user ratings.

Surfline agreement unchanged at +0.925: bottom type doesn't feed the score yet, and
Old Man's (the only spot with Surfline data) is a reef either way.

**Size by bottom type (scheme 2).** `rating_config.size_range()`: every spot keeps
size_min 0.5 m and size_ideal 1.8 m; only beaches close out sooner: size_max 4.0 m ×
clamp((1/50 ÷ slope)^0.4, 0.6, 1) × 0.85 if the shoreline is >= 50% gravel/coarse sand.
43 beaches affected (most close out ~3.4 m, steepest ~2.5 m). Reefs, points and gentle
beaches unchanged. Rejected: shrinking the whole range (raised steep beaches on small
days) and absolute ranges per type (moved 661 of 4,140 spot-days on guessed numbers).
Surfline agreement +0.925 (Old Man's is a reef). Hand override: `bottom_type` column
in locations.csv. Note: Steamer Lane, Devereaux and C Street have had hand-entered
rating values since 2026-07-11 (commit 2f4fe62); they override the generated ones.

## Label scale adopted (2026-10-08): Surfline-style, capped

The model now labels Very poor (0), Poor (5), Poor to fair (8), Fair (28) and Fair to
good (48+), capped there like Surfline's model. Fair to good was fitted at 42 and
raised to 48 on purpose, so the model's top label stays rare given the uncertainty
(18/24 exact matches instead of 20, still all within one; ~18% of days instead of ~25%). Good and Epic (`RESERVED_BUCKETS`) are
kept for people (a human forecast or observation) or, later, a model calibrated on user
session reports. Floors from Poor to Fair to good fitted on Surfline's 24 rated hours
(21 exact, all within one); Very poor is a guess. Colors: Surfline's red / orange /
yellow with our greens (#22c55e Fair, #15803d Fair to good). Pills pick dark or white
text by luminance (`ratingTextColor` in the generated js/rating.js). Replaced: Poor 0,
Fair 22, Good 42, Excellent 70, Epic 92. On the 2026-10-08 forecast: Very poor 12.5%,
Poor 4.0%, Poor to fair 34.8%, Fair 23.8%, Fair to good 24.9% of spot-days.
