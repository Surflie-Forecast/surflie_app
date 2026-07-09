---
name: verify
description: Build, run, and drive the surf forecast app locally to verify changes end-to-end.
---

# Verify the surf forecast app

Static site (vanilla JS) + Python pipeline that writes `api/*.json`. No build step.

## Launch

```bash
source .venv/bin/activate
python backend/pipeline.py        # rebuilds api/ from live sources (~10 s)
python -m http.server 8000        # serve repo root; run in background
```

Backend changes require re-running the pipeline before they're visible.
Frontend files are served directly — but bump the `?v=` query on the
changed file's tag in index.html or the browser serves a cached copy.

## Drive (headless browser)

Playwright browsers are cached at `~/Library/Caches/ms-playwright/`
(chromium_headless_shell-1200). `npm i playwright-core` in the scratchpad and
launch with `executablePath` pointing at the cached shell — no browser download.

Flows worth driving:
- Default spot loads: `#waveChartContainer .chart-bar` present, bar
  `dataset.valM` non-zero (all-zero bars = a wave model's dead cell, see below).
- Spot switch via `#spotSearch` fill + click first `#searchResults` child.
- Models overlay: click `#modelCompareBtn`, expect `.model-overlay-svg`
  with one path per model in `api/models/{slug}.json` and legend chips.
- Check `page.on('pageerror'/'console')` for JS errors.

## Gotchas

- NCEP GFS Wave 25 km returns all-zero at some nearshore cells (C st. in the
  SB Channel) even with `cell_selection=sea`; builders fall back per spot to
  the next model in `WAVE_MODELS` order. Pipeline log prints which model each
  spot used ("waves from …").
- The hourly JSON `swell.height` field is combined Hs, not the swell partition.
- Inline `#` comments in pasted zsh commands break; the user's shell chokes on them.
