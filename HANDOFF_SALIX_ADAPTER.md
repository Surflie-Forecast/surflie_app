# Handoff: `surf_nearshore` adapter (PLAN_NEARSHORE_MOP.md step 5)

Written 2026-07-18 by the surflie_salix session. Everything below was
verified against the live salix pipeline and this repo's code. This is the
only unfinished step of PLAN_NEARSHORE_MOP.md — steps 1–4 (transect
selection, CDIP transect adapters, nowcast, skill scoring) are done.

## Task

Write `backend/adapters/surf_nearshore.py`: an adapter that pulls our own
MOP-style nearshore forecast (published by the private repo
`pcarbomestre/surflie_salix`) and feeds it into the store like the other
wave sources.

## The published artifact (verified)

- URL: `https://raw.githubusercontent.com/pcarbomestre/surflie_salix/forecast/out/forecast_latest.json.gz`
- Auth: header `Authorization: Bearer $SALIX_TOKEN`. The repo is private;
  `SALIX_TOKEN` is a fine-grained read-only PAT scoped to surflie_salix,
  already registered as an Actions secret in this repo (verified 2026-07-18).
- The response is the raw `.gz` bytes — `gzip.decompress(r.content)` then
  `json.loads` (guard with the `\x1f\x8b` magic check in case it ever
  arrives pre-decompressed).
- Structure:

  ```
  {
    "cycle": "open-meteo:2026-07-18T00Z",
    "times": ["2026-07-18T00:00Z", ...],   # ~208 stamps: hourly to +120 h,
                                           # then 3-hourly, ~16-day horizon.
                                           # NOTE the bare "Z" suffix — use
                                           # .replace('Z', '+00:00') before
                                           # datetime.fromisoformat.
    "sites": {
      "B0001": {"hs": [...], "tp": [...], "dp": [...]},  # arrays parallel
      ...                                                # to "times"
    }                                                    # 1788 SB sites
  }
  ```

- Site keys are the **same MOP transect labels** that
  `backend/mop_transects.py:transect_for(spot)` returns (the salix
  coefficients are computed at the MOP transects), so spot→transect
  selection is shared with the CDIP transect adapters. Units match
  (Hs in m, Tp in s, Dp in degrees coming-from).
- Refresh: force-pushed to the orphan `forecast` branch every 6 h, cron
  05:40 / 11:40 / 17:40 / 23:40 UTC. `forecast_latest.nc` sits alongside
  it (same data, richer format) if ever needed.
- Each artifact starts at its model cycle time, so elapsed hours of the
  local day are missing from a fresh download — keep the overlay's first
  day full with the same per-spot cache-merge scheme as
  `backend/adapters/cdip_mop_transect_fc.py` (`_cdip_transect.load_cache`
  / `save_cache`, live wins per timestamp).

## Recommended shape

- Module interface like the other adapters (`NAME`, `CRITICAL = False`,
  `SOURCE`, see `adapters/__init__.py` docstring). Non-critical: on missing
  token or any live failure, print and serve caches — never raise.
- Implement `fetch_batch(spots, start_utc, end_utc)` rather than per-spot
  `fetch`: one download serves every spot, and `pipeline.py` already
  dispatches batch adapters via `hasattr(a, 'fetch_batch')`. Download once,
  then loop spots → `transect_for(spot)` → look up `sites[label]` → zip
  with `times`, filter to the window.
- Map `hs`/`tp`/`dp` → `wave_height`/`swell_period`/`swell_direction` —
  reuse `_cdip_transect.rows()` by building the same
  `{ts_iso: {'h','p','d'}}` point dicts.
- Read the token with `os.environ.get('SALIX_TOKEN', '')` (house style,
  see `backend/alerts.py`).

## Wiring checklist

1. Create `backend/adapters/surf_nearshore.py`.
2. Register it in both lists in `backend/adapters/__init__.py`.
3. Add the new source tag to `WAVE_MODELS` in `backend/builders.py`
   (comparison overlay + display-pick order; slot it near the CDIP
   transect entry — owner's call on preference order).
4. Add to the "Run data pipeline" step in
   `.github/workflows/build-deploy.yml` (~line 166):

   ```yaml
   env:
     SALIX_TOKEN: ${{ secrets.SALIX_TOKEN }}
   ```

5. Nothing needed for skill scoring: `backend/skill.py` automatically
   scores every non-truth `wave_height` source against the
   `cdip:mop-nowcast` truth proxy — this adapter is the forecast that
   scoring was built to evaluate (PLAN §4).

## Salix-side backlog (not this task, tracked in surflie_salix)

Rincon-corridor 100 m nest (winter 20 s / 270° deficit), wind-sea
supplement, coastwide segments beyond Santa Barbara.
