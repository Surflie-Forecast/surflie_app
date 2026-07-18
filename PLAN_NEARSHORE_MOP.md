# Plan: richer nearshore sources — MOP transect nowcast + forecast

Written 2026-07-18, companion to ../surf_nearshore (see its PLAN.md/RECON.md).

## Where we are

The app's only nearshore source today is `backend/adapters/cdip_mop.py`:
CDIP's **1-km gridded forecast** (`MOP_grids/CA_0.01_forecast.nc`), total
Hs/Tp/Dp at the nearest water cell, ~4-day horizon, non-critical overlay.

CDIP publishes two better-grained siblings on the same THREDDS server, both
at the ~100 m-spaced **alongshore transects** (MOP points, e.g. `B0161`):

- `MOP_alongshore/{transect}_nowcast.nc` — driven by **measured buoy
  spectra**; the best available "what is it doing right now / the last few
  days" at a spot with no sensor. Hourly, rolling ~15-month window.
- `MOP_alongshore/{transect}_forecast.nc` — WW3-driven forecast at the same
  transect, hourly, ~7–9 days: finer in space and longer than the 1-km grid
  product we currently read.

Both carry bulk params (waveHs/waveTp/waveDp/waveTa/waveDm) directly — no
spectra processing needed for the adapter.

## What to build

### 1. Transect index + spot→transect selection

- Vendor a slim transect table into the repo (generated from CDIP's
  `CA_v1.1_transect_definitions.txt`, which surf_nearshore already has at
  `data/cdip/`): one row per transect — `label, pred_lon, pred_lat,
  shore_normal, county`. ~11.6k rows ≈ 400 KB as CSV; commit it (it's a
  static site-definition table, not CDIP model output).
- Selection rule for a spot (lat, lon):
  1. nearest transect by prediction-site distance (cos-lat scaled);
  2. reject if > ~1.5 km (spot outside CA or inland — adapter yields no
     rows, same convention as cdip_mop today);
  3. tie-breaker/sanity: the bearing from transect to spot should sit
     within ±90° of the transect's shore normal (guards against snapping
     across a headland or harbor mouth to the wrong coastline face).
- Cache the chosen transect per spot slug (they never move) in the spot
  config or a small JSON next to the adapter.

### 2. Two new adapters (same shape as cdip_mop.py)

- `cdip_mop_transect_fc.py` (`SOURCE='cdip:mop-transect-fc'`): reads
  `{transect}_forecast.nc` via OPeNDAP `.ascii` subsetting (keep the
  stdlib+requests style; the per-transect files are small and 1-D — simpler
  than the grid reads). Variables → same store keys the grid adapter uses
  (`wave_height`, `swell_period`, `swell_direction`).
- `cdip_mop_nowcast.py` (`SOURCE='cdip:mop-nowcast'`): reads
  `{transect}_nowcast.nc` for the trailing N days (default 3). This is not
  a forecast — it feeds (a) the model-comparison overlay's "observed-driven"
  reference line, and (b) future forecast-skill scoring (see 4).
- Both non-critical, cached like cdip_mop (actions/cache), never raise.
- Keep the existing 1-km grid adapter as fallback for spots that fail
  transect selection.

### 3. UI/store integration

- Overlay: nowcast line styled as "CDIP observed-driven" (it is the truth
  proxy), transect forecast replaces/augments the grid line where a
  transect is selected.
- Store: rows keyed by `source`, so no schema change — just two new source
  strings alongside `cdip:mop`.

### 4. Skill scoring (enabler for surf_nearshore switchover)

Nightly job compares, per spot and lead time: each forecast source
(cdip:mop-transect-fc, openmeteo, and later `surf_nearshore`) against the
nowcast at the matched transect. Store bias/RMSE by lead-time bucket.
This is the evidence base for promoting the surf_nearshore product (16-day,
GFS-driven, own matrices) from overlay to primary display.

### 5. surf_nearshore adapter (later, when its cron publishes)

`surf_nearshore.py` adapter reading the published per-spot artifact
(same transect labels — its coefficients are computed *at* the MOP
transects, so spot→transect selection above is shared). 16-day horizon,
hourly to +120 h then 3-hourly.

## Order of work

1. ✅ Transect table + selection helper (+ unit test with a few known spots:
   e.g. a SB channel spot → B-county transect, a Ventura spot → VE).
   `data/mop_transects.csv` (vendored), `backend/mop_transects.py`,
   `backend/tests/test_mop_transects.py`.
2. ✅ `cdip_mop_transect_fc.py`, wire into pipeline + overlay (shared reader
   in `adapters/_cdip_transect.py`; replaces the 1-km grid overlay line
   where a transect is matched, grid kept as fallback).
3. ✅ `cdip_mop_nowcast.py`, overlay reference line ("CDIP observed").
4. ✅ Skill scoring job — `backend/skill.py`, runs inside the pipeline;
   state in `data/skill/` (CI actions cache), summaries published to
   `api/skill/<slug>.json`. `backend/tests/test_skill.py`.
5. ⬜ surf_nearshore adapter once its operational cron is live.
