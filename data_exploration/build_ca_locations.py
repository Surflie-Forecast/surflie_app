#!/usr/bin/env python3
"""
Build locations.csv FROM SCRATCH from every California surf spot on Surfline.

Walks Surfline's taxonomy tree down to California, collects all spots, and writes
a CSV in the same schema as the original locations.csv:

    site_name, slug, region, latitude, longitude, timezone, closest_buoy, resolved_cells

  - site_name       : Surfline's exact spot name
  - slug            : generated from the name (lowercase, hyphenated)
  - region          : California county from Surfline's path (e.g. "Santa Cruz", "Ventura")
  - latitude/longitude : Surfline's own coordinates
  - timezone        : America/Los_Angeles (all of CA)
  - closest_buoy / resolved_cells : left blank (your pipeline fills these)

Run locally (Surfline's API is not reachable from the assistant sandbox):

    python3 build_ca_locations.py

Writes: surfline_ca_locations.csv   (rename to locations.csv if you like)
Standard library only.
"""

import csv, json, re, time, urllib.request

TAXO = "https://services.surfline.com/taxonomy?type=taxonomy&id={id}&maxDepth={d}"
EARTH_ID = "58f7ed51dadb30820bb38782"     # Surfline "Earth" root (stable)
FAST_CA_ID = "58f7ed51dadb30820bb3880e"   # likely California; verified before use
HEADERS = {"User-Agent": "Mozilla/5.0 (ca-surf-spot-catalog; personal use)"}
OUT = "surfline_ca_locations.csv"
TZ = "America/Los_Angeles"


def get_json(url, tries=3):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise last


def name_of(n):
    return (n.get("name") or "").strip()


def child_by_name(node, target):
    t = target.lower()
    for c in node.get("contains", []) or []:
        if name_of(c).lower() == t:
            return c
    for c in node.get("contains", []) or []:
        if t in name_of(c).lower():
            return c
    return None


def find_california():
    try:
        node = get_json(TAXO.format(id=FAST_CA_ID, d=0))
        if name_of(node).lower() == "california":
            return FAST_CA_ID
    except Exception:
        pass
    earth = get_json(TAXO.format(id=EARTH_ID, d=1))
    na = get_json(TAXO.format(id=child_by_name(earth, "North America")["_id"], d=1))
    us = get_json(TAXO.format(id=child_by_name(na, "United States")["_id"], d=1))
    ca = child_by_name(us, "California")
    if not ca:
        raise SystemExit("Could not locate California in Surfline taxonomy.")
    return ca["_id"]


def coords(n):
    loc = n.get("location") or {}
    c = loc.get("coordinates")
    if isinstance(c, list) and len(c) >= 2:
        return c[1], c[0]          # [lon, lat] -> (lat, lon)
    return None, None


def region_from_path(path):
    """Path like ',Earth,North America,United States,California,Santa Cruz County,Santa Cruz,'.
    Return the county segment after 'California', with ' County' stripped."""
    parts = [p for p in (path or "").split(",") if p.strip()]
    for i, p in enumerate(parts):
        if p.strip().lower() == "california" and i + 1 < len(parts):
            return re.sub(r"\s+County$", "", parts[i + 1].strip())
    return ""


def collect_spots(root_id):
    # key -> (priority, row). priority 2 = region from Surfline subregion (preferred),
    # 1 = fallback to county from the geographic path.
    best = {}
    expanded = set()

    def add(c, subregion_name):
        lat, lon = coords(c)
        key = c.get("_id") or (name_of(c), lat, lon)
        if subregion_name:
            region, pr = subregion_name, 2
        else:
            region, pr = region_from_path(c.get("enumeratedPath")), 1
        prev = best.get(key)
        if prev is None or pr > prev[0]:
            best[key] = (pr, {"name": name_of(c), "lat": lat, "lon": lon, "region": region})

    def walk(node, subregion_name):
        for c in node.get("contains", []) or []:
            cid = c.get("_id")
            ctype = (c.get("type") or "").lower()
            if ctype == "spot":
                add(c, subregion_name)
            else:
                # entering a subregion node sets the forecast region for its spots
                sub = name_of(c) if ctype == "subregion" else subregion_name
                if not c.get("contains") and cid and cid not in expanded:
                    expanded.add(cid)
                    try:
                        c = get_json(TAXO.format(id=cid, d=2))
                        time.sleep(0.25)
                    except Exception:
                        continue
                walk(c, sub)

    walk(get_json(TAXO.format(id=root_id, d=6)), None)
    return [row for _, row in best.values()]


def slugify(name):
    s = name.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def main():
    print("Locating California in Surfline taxonomy ...")
    ca_id = find_california()
    print(f"California id: {ca_id}\nCollecting spots (takes a minute) ...")
    spots = collect_spots(ca_id)

    # drop coordinate-less entries, sort north->south
    spots = [s for s in spots if s["lat"] is not None and s["lon"] is not None]
    spots.sort(key=lambda s: s["lat"], reverse=True)

    seen_slugs = {}
    rows = []
    for s in spots:
        slug = slugify(s["name"]) or "spot"
        if slug in seen_slugs:
            seen_slugs[slug] += 1
            slug = f"{slug}-{seen_slugs[slug]}"
        else:
            seen_slugs[slug] = 1
        rows.append([s["name"], slug, s["region"],
                     f"{s['lat']:.6f}", f"{s['lon']:.6f}", TZ, "", ""])

    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["site_name", "slug", "region", "latitude", "longitude",
                    "timezone", "closest_buoy", "resolved_cells"])
        w.writerows(rows)

    print(f"Wrote {OUT} with {len(rows)} California spots.")


if __name__ == "__main__":
    main()
