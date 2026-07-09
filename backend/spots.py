"""Spot metadata loaded from data/locations.csv.

The slug is the stable identifier used for API file paths and URLs;
the display name is free to contain spaces/punctuation and to change.
"""
import csv
from dataclasses import dataclass


@dataclass(frozen=True)
class Spot:
    name: str
    slug: str
    region: str
    latitude: float
    longitude: float
    timezone: str  # IANA name, e.g. "America/Los_Angeles"


def load_spots(path):
    spots = []
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            spots.append(Spot(
                name=row['site_name'].strip(),
                slug=row['slug'].strip(),
                region=row['region'].strip(),
                latitude=float(row['latitude']),
                longitude=float(row['longitude']),
                timezone=row['timezone'].strip(),
            ))
    return spots
