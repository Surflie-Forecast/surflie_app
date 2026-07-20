"""Per-spot -> batch bridge: run an adapter's fetch() over a thread pool.

The CDIP THREDDS endpoints answer GitHub-runner IPs in ~8-9 s per request
(see CDIP_SLOWNESS_TRANSCRIPT.md), so per-spot loops that are invisible at
3 spots dominate the run at 100+. Wrapping the unchanged per-spot fetch in
a pool pays that latency concurrently — the same fix that took the buoy
build from ~53 min to seconds.

A worker failure is per-spot: it's printed and yields no rows for that spot,
never aborting the batch — matching the pipeline's per-spot error handling
for non-critical sources.
"""
from concurrent.futures import ThreadPoolExecutor

WORKERS = 8


def pooled_fetch_batch(fetch, name, spots, start_utc, end_utc,
                       workers=WORKERS, prewarm=None):
    """Rows from fetch(spot, start_utc, end_utc) across spots, concurrently.

    prewarm, if given, is called serially for every spot first — for shared
    memoized state (e.g. the spot->transect map) that isn't thread-safe to
    populate concurrently.
    """
    if prewarm is not None:
        for spot in spots:
            try:
                prewarm(spot)
            except Exception:
                pass  # the real fetch will surface the error per spot

    def one(spot):
        try:
            return spot, fetch(spot, start_utc, end_utc), None
        except Exception as e:
            return spot, [], e

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for spot, spot_rows, err in pool.map(one, spots):
            if err is not None:
                print(f"    {name}: {spot.slug} failed ({err})")
            rows.extend(spot_rows)
    return rows
