"""
RATING CONFIGURATION - SINGLE SOURCE OF TRUTH
==============================================
This module defines the surf rating logic used across the entire application.
All rating calculations (backend and frontend) derive from these rules.

To update rating logic, modify this file and run:
  python sync_rating_logic.py

This regenerates js/rating.js (the JavaScript port) from the Python here.

SCORING MODEL
-------------
The score is a continuous 0-100 number built from four INDEPENDENT factors,
each in [0, 1], multiplied together:

    overall = energy x direction x wind x tide          (then x100)

Multiplying (rather than adding) means any single dealbreaker - flat surf, a
blocked swell, howling onshore wind - drags the whole score down, which is how
surf actually works. The 0-100 result is then bucketed into the same five
labels/colors the UI has always used (Poor -> Epic).

    energy     from swell HEIGHT and PERIOD, shaped by the spot's working
               size range. Short wind-swell is penalized; long groundswell
               rewarded. Above the spot's close-out size the score falls off.
    direction  cosine falloff from the spot's swell window; ~0 when the swell
               comes from a direction the break can't see.
    wind       offshore grooms (boost), cross is neutral, onshore ruins it -
               all scaled by speed, so calm wind is neutral regardless of dir.
    tide       optional per-spot preferred band; 1.0 (no effect) when a spot
               has no tide preference, so untuned spots are never penalized.

PER-SPOT METADATA
-----------------
Each factor can be tuned per break via a SpotProfile (see build_profile()).
When a spot supplies no metadata, a profile is DERIVED from its coastline
orientation (computed from lat/lon by the caller) so new spots score sensibly
with zero manual data entry. Hand-entered values always win over derived ones.
"""
import math


# ---------------------------------------------------------------------------
# Buckets: the continuous 0-100 score maps to these five labels/colors.
# `floor` is the inclusive lower bound of the score band. Order high -> low.
# ---------------------------------------------------------------------------
BUCKETS = [
    {'name': 'Epic',      'color': '#a855f7', 'floor': 80},   # Purple
    {'name': 'Excellent', 'color': '#15803d', 'floor': 62},   # Dark Green
    {'name': 'Good',      'color': '#22c55e', 'floor': 42},   # Green
    {'name': 'Fair',      'color': '#eab308', 'floor': 22},   # Yellow
    {'name': 'Poor',      'color': '#ef4444', 'floor': 0},    # Red
]


# ---------------------------------------------------------------------------
# Global defaults for a spot that supplies no metadata and whose coastline
# orientation is unknown. Deliberately forgiving so nothing scores absurdly.
# ---------------------------------------------------------------------------
DEFAULT_PROFILE = {
    # Swell window the break receives, as (min_deg, ideal_deg, max_deg) where
    # the arc runs clockwise from min to max through ideal. 0..360, compass.
    'swell_min': 180.0, 'swell_ideal': 270.0, 'swell_max': 360.0,
    # Wind direction (compass, "from") that is offshore/grooming for the break.
    'offshore_dir': 0.0,
    # Hs (m): where it starts breaking, the sweet spot, and where it closes out.
    'size_min': 0.5, 'size_ideal': 1.8, 'size_max': 4.0,
    # Optional tide band (m) where it works best; None => tide has no effect.
    'tide_min': None, 'tide_max': None,
}


class SpotProfile:
    """Per-spot tuning for the score. Construct via build_profile()."""

    __slots__ = ('swell_min', 'swell_ideal', 'swell_max', 'offshore_dir',
                 'size_min', 'size_ideal', 'size_max', 'tide_min', 'tide_max')

    def __init__(self, **kw):
        for k, v in DEFAULT_PROFILE.items():
            setattr(self, k, kw.get(k, v))


def build_profile(meta=None, coast_normal=None):
    """Build a SpotProfile from optional hand-entered metadata, deriving any
    missing swell window / offshore direction from the coastline orientation.

    Args:
        meta: dict of any of the DEFAULT_PROFILE keys (from locations.csv).
              Present keys win over derived values.
        coast_normal: compass bearing (deg) the coastline faces the OPEN OCEAN
              at this spot (i.e. the seaward normal). Used to derive the swell
              window and offshore wind direction when meta omits them. None to
              skip derivation and fall back to DEFAULT_PROFILE.

    A break facing bearing N receives swell from an arc centered on N, is
    offshore when wind blows FROM the land (N + 180), and closes out at a size
    scaled to how exposed it is (left at the default here).
    """
    meta = dict(meta or {})

    if coast_normal is not None:
        n = coast_normal % 360.0
        meta.setdefault('swell_ideal', n)
        # A typical point/beach receives roughly +/- 70 deg around its normal.
        meta.setdefault('swell_min', (n - 70.0) % 360.0)
        meta.setdefault('swell_max', (n + 70.0) % 360.0)
        # Offshore wind comes from over the land, opposite the seaward normal.
        meta.setdefault('offshore_dir', (n + 180.0) % 360.0)

    return SpotProfile(**meta)


# ---------------------------------------------------------------------------
# Angle helpers
# ---------------------------------------------------------------------------
def _ang_diff(a, b):
    """Smallest absolute difference between two compass bearings, in [0, 180]."""
    d = abs((a - b) % 360.0)
    return d if d <= 180.0 else 360.0 - d


# ---------------------------------------------------------------------------
# The four factors, each returning a value in [0, 1].
# ---------------------------------------------------------------------------
def _energy_factor(height, period, p):
    """Wave energy from HEIGHT (shaped by working size range) and PERIOD."""
    # --- size term: ramps 0->1 across [size_min, size_ideal], holds near 1 to
    #     size_max, then falls off as it closes out. ---
    if height <= p.size_min:
        size = 0.0
    elif height < p.size_ideal:
        size = (height - p.size_min) / (p.size_ideal - p.size_min)
    elif height <= p.size_max:
        # Gentle decline from ideal to close-out: stays strong but not perfect.
        frac = (height - p.size_ideal) / max(p.size_max - p.size_ideal, 1e-6)
        size = 1.0 - 0.35 * frac
    else:
        # Beyond close-out: rapidly unsurfable (washed out / maxed).
        over = (height - p.size_max) / max(p.size_max, 1e-6)
        size = max(0.0, 0.65 - over)

    # --- period term: quality of the swell. Short wind-swell (<6s) is weak;
    #     8s is a legit short-period swell; groundswell (>=14s) is prime. ---
    if period <= 6.0:
        per = 0.35
    elif period >= 14.0:
        per = 1.0
    else:
        per = 0.35 + 0.65 * (period - 6.0) / 8.0

    # Combine size and period as a weighted geometric mean rather than a raw
    # product, so a clean-but-modest day (good period, sub-ideal size) isn't
    # crushed the way multiplying two middling factors would. Size still leads.
    combined = (size ** 0.6) * (per ** 0.4)
    return max(0.0, min(1.0, combined))


def _direction_factor(swell_dir, p):
    """How well the swell direction lines up with the break's window.

    A single continuous cosine falloff from the ideal bearing: 1.0 dead-on,
    ~0.5 at the window edge (half-width from ideal), then decaying smoothly to
    a small floor as the swell rotates outside the window - no cliff at the
    edge, since a swell 1 deg outside the arc is barely worse than 1 deg inside.
    """
    off = _ang_diff(swell_dir, p.swell_ideal)
    half_width = max(_ang_diff(p.swell_ideal, p.swell_min),
                     _ang_diff(p.swell_ideal, p.swell_max), 1.0)
    x = off / half_width
    if x <= 1.0:
        # Inside the window: 1.0 at ideal -> 0.5 at the edge.
        return 0.5 + 0.5 * math.cos(x * math.pi / 2.0)
    # Outside: keep decaying from ~0.5 toward a wrap/refraction floor.
    over = min((off - half_width) / 45.0, 1.0)   # 45 deg past edge => floor
    return max(0.08, 0.5 * (1.0 - over))


def _wind_factor(speed, direction, p):
    """Offshore grooms, onshore ruins, cross neutral - all scaled by speed."""
    if speed < 3.0:
        return 1.0  # glass / near-calm: great regardless of direction
    # Alignment: 1 when wind is straight offshore, -1 when straight onshore.
    off = _ang_diff(direction, p.offshore_dir)          # 0 => offshore
    align = math.cos(math.radians(off))                 # +1 offshore .. -1 onshore
    # Strength ramp: light wind matters little, strong wind matters a lot.
    strength = min((speed - 3.0) / 17.0, 1.0)           # 0 at 3kt .. 1 at 20kt+
    if align >= 0:
        # Offshore: a modest boost that grows with wind, capped.
        return 1.0 + 0.12 * align * strength
    # Onshore/cross-onshore: penalty grows with both wind strength and alignment.
    return max(0.1, 1.0 - (0.9 * (-align) * strength))


def _tide_factor(tide, p):
    """1.0 when the spot has no tide preference or tide is unknown."""
    if tide is None or p.tide_min is None or p.tide_max is None:
        return 1.0
    if p.tide_min <= tide <= p.tide_max:
        return 1.0
    # Outside the band: penalty scales with how far out, floored so it never zeros.
    if tide < p.tide_min:
        dist = p.tide_min - tide
    else:
        dist = tide - p.tide_max
    return max(0.55, 1.0 - dist / 2.0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def score_conditions(height, period, swell_dir, wind_speed, wind_dir,
                     tide=None, profile=None):
    """Continuous 0-100 surf score from the four factors. See module docstring."""
    p = profile or SpotProfile()
    energy = _energy_factor(height, period, p)
    direction = _direction_factor(swell_dir, p)
    wind = _wind_factor(wind_speed, wind_dir, p)
    tide_f = _tide_factor(tide, p)
    raw = energy * direction * wind * tide_f
    return max(0.0, min(100.0, raw * 100.0))


def bucket_for_score(score):
    """(name, color) for a 0-100 score."""
    for b in BUCKETS:
        if score >= b['floor']:
            return b['name'], b['color']
    return 'Poor', '#ef4444'


class RatingConfig:
    """Back-compatible facade over the scoring engine."""

    @classmethod
    def get_rating(cls, swell_height, swell_period, wind_speed=0,
                   swell_dir=None, wind_dir=None, tide=None, profile=None):
        """(rating_name, color) for the given conditions.

        swell_dir / wind_dir default to each spot's ideal/offshore bearing so
        that callers which only have height/period/speed get a sensible,
        direction-neutral score instead of a blocked/onshore one.
        """
        p = profile or SpotProfile()
        sd = p.swell_ideal if swell_dir is None else swell_dir
        wd = p.offshore_dir if wind_dir is None else wind_dir
        score = score_conditions(swell_height, swell_period, sd,
                                 wind_speed, wd, tide, p)
        return bucket_for_score(score)

    @classmethod
    def get_score(cls, swell_height, swell_period, wind_speed=0,
                  swell_dir=None, wind_dir=None, tide=None, profile=None):
        """0-100 score (rounded int) for the given conditions."""
        p = profile or SpotProfile()
        sd = p.swell_ideal if swell_dir is None else swell_dir
        wd = p.offshore_dir if wind_dir is None else wind_dir
        return round(score_conditions(swell_height, swell_period, sd,
                                     wind_speed, wd, tide, p))

    @classmethod
    def export_for_javascript(cls):
        """Emit js/rating.js: a faithful JS port of the scoring engine, with the
        buckets and default profile injected from the Python constants above."""
        import json
        buckets_js = json.dumps(BUCKETS)
        default_js = json.dumps(DEFAULT_PROFILE)
        return _JS_TEMPLATE.format(buckets=buckets_js, default=default_js)


# ---------------------------------------------------------------------------
# JavaScript port. Kept as a template so the buckets/default profile stay in
# sync with the Python constants. The math below mirrors the functions above.
# ---------------------------------------------------------------------------
_JS_TEMPLATE = r"""// AUTO-GENERATED - DO NOT EDIT MANUALLY
// Generated from backend/rating_config.py
// To update, modify rating_config.py and run: python sync_rating_logic.py
//
// Continuous 0-100 surf score from four independent factors
// (energy x direction x wind x tide), bucketed into five labels/colors.
// See backend/rating_config.py for the full model description.

const RATING_BUCKETS = {buckets};
const RATING_DEFAULT_PROFILE = {default};

function _spotProfile(meta) {{
    const p = Object.assign({{}}, RATING_DEFAULT_PROFILE);
    if (meta) {{
        for (const k of Object.keys(RATING_DEFAULT_PROFILE)) {{
            if (meta[k] !== undefined && meta[k] !== null) p[k] = meta[k];
        }}
    }}
    return p;
}}

function _angDiff(a, b) {{
    let d = Math.abs(((a - b) % 360 + 360) % 360);
    return d <= 180 ? d : 360 - d;
}}

function _energyFactor(height, period, p) {{
    let size;
    if (height <= p.size_min) {{
        size = 0;
    }} else if (height < p.size_ideal) {{
        size = (height - p.size_min) / (p.size_ideal - p.size_min);
    }} else if (height <= p.size_max) {{
        const frac = (height - p.size_ideal) / Math.max(p.size_max - p.size_ideal, 1e-6);
        size = 1 - 0.35 * frac;
    }} else {{
        const over = (height - p.size_max) / Math.max(p.size_max, 1e-6);
        size = Math.max(0, 0.65 - over);
    }}

    let per;
    if (period <= 6) per = 0.35;
    else if (period >= 14) per = 1;
    else per = 0.35 + 0.65 * (period - 6) / 8;

    const combined = Math.pow(size, 0.6) * Math.pow(per, 0.4);
    return Math.max(0, Math.min(1, combined));
}}

function _directionFactor(swellDir, p) {{
    const off = _angDiff(swellDir, p.swell_ideal);
    const halfWidth = Math.max(
        _angDiff(p.swell_ideal, p.swell_min),
        _angDiff(p.swell_ideal, p.swell_max), 1);
    const x = off / halfWidth;
    if (x <= 1) return 0.5 + 0.5 * Math.cos(x * Math.PI / 2);
    const over = Math.min((off - halfWidth) / 45, 1);
    return Math.max(0.08, 0.5 * (1 - over));
}}

function _windFactor(speed, direction, p) {{
    if (speed < 3) return 1;
    const off = _angDiff(direction, p.offshore_dir);
    const align = Math.cos(off * Math.PI / 180);
    const strength = Math.min((speed - 3) / 17, 1);
    if (align >= 0) return 1 + 0.12 * align * strength;
    return Math.max(0.1, 1 - (0.9 * (-align) * strength));
}}

function _tideFactor(tide, p) {{
    if (tide === null || tide === undefined || p.tide_min === null || p.tide_max === null) return 1;
    if (tide >= p.tide_min && tide <= p.tide_max) return 1;
    const dist = tide < p.tide_min ? (p.tide_min - tide) : (tide - p.tide_max);
    return Math.max(0.55, 1 - dist / 2);
}}

function scoreConditions(height, period, swellDir, windSpeed, windDir, tide = null, meta = null) {{
    const p = _spotProfile(meta);
    const energy = _energyFactor(height, period, p);
    const direction = _directionFactor(swellDir, p);
    const wind = _windFactor(windSpeed, windDir, p);
    const tideF = _tideFactor(tide, p);
    const raw = energy * direction * wind * tideF;
    return Math.max(0, Math.min(100, raw * 100));
}}

function bucketForScore(score) {{
    for (const b of RATING_BUCKETS) {{
        if (score >= b.floor) return {{ rating: b.name, color: b.color }};
    }}
    return {{ rating: 'Poor', color: '#ef4444' }};
}}

// Back-compatible entry point. Existing callers pass (height, period, windSpeed);
// swell/wind direction default to the profile's ideal/offshore bearing so the
// score stays direction-neutral rather than assuming a blocked/onshore setup.
// Pass extra args to make it direction-aware:
//   getRatingAndColor(h, p, windSpeed, swellDir, windDir, tide, meta)
// Returns {{ rating, color, score }} (score is the underlying 0-100 number).
function getRatingAndColor(swellHeight, swellPeriod, windSpeed = 0,
                           swellDir = null, windDir = null, tide = null, meta = null) {{
    const p = _spotProfile(meta);
    const sd = swellDir === null || swellDir === undefined ? p.swell_ideal : swellDir;
    const wd = windDir === null || windDir === undefined ? p.offshore_dir : windDir;
    const score = scoreConditions(swellHeight, swellPeriod, sd, windSpeed, wd, tide, meta);
    const b = bucketForScore(score);
    return {{ rating: b.rating, color: b.color, score: Math.round(score) }};
}}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {{
    module.exports = {{ getRatingAndColor, scoreConditions, bucketForScore }};
}}
"""


if __name__ == '__main__':
    print("Rating Configuration Test")
    print("=" * 60)

    steamer = build_profile({
        'swell_min': 200, 'swell_ideal': 285, 'swell_max': 320,
        'offshore_dir': 20, 'size_min': 0.6, 'size_ideal': 2.0, 'size_max': 4.5,
    })

    cases = [
        ("Clean W groundswell, light offshore", 1.6, 13, 285, 5, 20),
        ("Same swell but howling onshore",       1.6, 13, 285, 18, 200),
        ("Blocked S swell",                       1.6, 13, 190, 5, 20),
        ("Tiny short-period wind slop",           0.5, 6, 285, 8, 200),
        ("Big clean swell (near close-out)",      4.0, 15, 285, 4, 20),
        ("Maxed out / washed through",            6.0, 14, 285, 4, 20),
    ]
    for desc, h, per, sd, ws, wd in cases:
        score = round(score_conditions(h, per, sd, ws, wd, profile=steamer))
        name, _ = bucket_for_score(score)
        print(f"  {desc:40s}  {h}m/{per}s dir{sd}  wind{ws}kt@{wd}")
        print(f"      -> score {score:3d}  ({name})")
