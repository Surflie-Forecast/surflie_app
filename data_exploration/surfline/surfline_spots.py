#!/usr/bin/env python3
"""
surfline_spots.py -- crawl Surfline's public taxonomy tree and dump surf
spots to CSV with name + coordinates.

Surfline fronts its API with bot protection that fingerprints the TLS
handshake, so plain `requests` gets a 403. This script uses curl_cffi to
impersonate a real Chrome client, which gets through.

Setup:
    pip install curl_cffi          # required to beat the 403
    # (requests is used only as a fallback and will likely be blocked)

Usage:
    python surfline_spots.py                          # -> California spots
    python surfline_spots.py --out ca.csv
    python surfline_spots.py --path "Earth,North America,United States,Hawaii"
    python surfline_spots.py --path "" --root <id>    # crawl a subtree, no filter
    python surfline_spots.py --debug                  # show first raw response

Output columns: name, lat, lon, spot_id, path
"""

import argparse
import csv
import sys
import time

# --- HTTP client: prefer curl_cffi (beats TLS fingerprinting), else requests ---
_USE_CFFI = False
try:
    from curl_cffi import requests as http
    _USE_CFFI = True
except ImportError:
    import requests as http

BASE = "https://services.surfline.com/taxonomy"
EARTH_ID = "58f7ed51dadb30820bb38791"
DEFAULT_PATH = "Earth,North America,United States,California"
CONTAINER_TYPES = {"geoname", "region", "subregion"}

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.surfline.com",
    "Referer": "https://www.surfline.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}


def make_session():
    if _USE_CFFI:
        # impersonate a recent Chrome: matches TLS + default header order
        return http.Session(impersonate="chrome", headers=HEADERS)
    s = http.Session()
    s.headers.update(HEADERS)
    s.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
    return s


def segs(node):
    return [s.strip().lower() for s in node.get("enumeratedPath", "").split(",") if s.strip()]


def is_prefix(short, long_):
    return long_[: len(short)] == short


def fetch_node(session, node_id, debug=False, retries=4, pause=0.6):
    params = {"type": "taxonomy", "id": node_id, "maxDepth": 1}
    for attempt in range(retries):
        try:
            r = session.get(BASE, params=params, timeout=25)
            if debug:
                print(f"\n[debug] {r.url}\n[debug] status={r.status_code}\n"
                      f"[debug] body[:300]={r.text[:300]!r}", file=sys.stderr)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (403, 429, 500, 502, 503):
                time.sleep(pause * (attempt + 1) * 3)
                continue
            print(f"  ! {node_id} HTTP {r.status_code}", file=sys.stderr)
            return None
        except Exception as e:  # curl_cffi / requests raise different types
            print(f"  ! {node_id} error: {e}", file=sys.stderr)
            time.sleep(pause * (attempt + 1) * 2)
    return None


def spot_row(node):
    name = node.get("name", "")
    spot_id = node.get("spot", "")
    path = node.get("enumeratedPath", "").strip(",")
    lat = lon = None
    loc = node.get("location") or {}
    coords = loc.get("coordinates")           # GeoJSON [lon, lat]
    if isinstance(coords, list) and len(coords) == 2:
        lon, lat = coords[0], coords[1]
    else:
        lat = node.get("lat") or loc.get("lat")
        lon = node.get("lon") or loc.get("lon")
    return [name, lat, lon, spot_id, path]


def crawl(root_id, target, pause, debug):
    session = make_session()
    seen, stack, spots = set(), [root_id], []
    first = True

    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)

        data = fetch_node(session, node_id, debug=(debug and first), pause=pause)
        first = False
        time.sleep(pause)
        if not data:
            continue

        for child in data.get("contains", []) or []:
            ctype = child.get("type")
            csegs = segs(child)
            if ctype == "spot":
                if not target or is_prefix(target, csegs):
                    spots.append(spot_row(child))
            elif ctype in CONTAINER_TYPES and child.get("_id"):
                if not target or is_prefix(csegs, target) or is_prefix(target, csegs):
                    stack.append(child["_id"])

        print(f"visited {len(seen):>4} nodes | {len(spots):>5} spots",
              end="\r", file=sys.stderr)

    print(file=sys.stderr)
    return spots


def main():
    ap = argparse.ArgumentParser(description="Dump Surfline surf spots to CSV.")
    ap.add_argument("--root", default=EARTH_ID, help="Start node id (default: Earth).")
    ap.add_argument("--path", default=DEFAULT_PATH,
                    help='Comma path to prune to (default: California). "" = no filter.')
    ap.add_argument("--out", default="california_surf_spots.csv", help="Output CSV path.")
    ap.add_argument("--pause", type=float, default=0.6,
                    help="Seconds between requests (raise if you hit 429).")
    ap.add_argument("--debug", action="store_true", help="Print first raw response.")
    args = ap.parse_args()

    if not _USE_CFFI:
        print("WARNING: curl_cffi not installed -- falling back to requests, which\n"
              "         Surfline will very likely block with 403. Run: pip install curl_cffi",
              file=sys.stderr)

    target = [s.strip().lower() for s in args.path.split(",") if s.strip()]
    print(f"Target: {args.path or 'everything under root'}  "
          f"(client: {'curl_cffi/chrome' if _USE_CFFI else 'requests'})", file=sys.stderr)

    spots = crawl(args.root, target, args.pause, args.debug)

    unique = {}
    for row in spots:
        key = row[3] or (row[0], row[1], row[2])
        unique.setdefault(key, row)

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "lat", "lon", "spot_id", "path"])
        w.writerows(sorted(unique.values(), key=lambda r: (r[4], r[0])))

    print(f"Done. {len(unique)} spots -> {args.out}", file=sys.stderr)
    if not unique:
        print("Still nothing. Run again with --debug to see the raw status/body.\n"
              "If it's 403 even via curl_cffi, try impersonate='chrome124' or a\n"
              "headless browser. If it's 200 but empty, the path labels differ --\n"
              "grab the exact _id from DevTools > Network (filter 'taxonomy') and\n"
              "run: python surfline_spots.py --path \"\" --root <id>", file=sys.stderr)


if __name__ == "__main__":
    main()
