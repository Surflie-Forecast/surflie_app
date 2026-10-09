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
The score is a continuous 0-100 number built from five INDEPENDENT factors,
each in [0, 1] (wind can nudge slightly above 1), multiplied together:

    overall = energy x direction x wind x tide x sea_state   (then x100)

Multiplying (rather than adding) means any single dealbreaker - flat surf, a
blocked swell, howling onshore wind - drags the whole score down, which is how
surf actually works. The 0-100 result is then bucketed into the same five
labels/colors the UI has always used (Poor -> Epic).

    energy     from swell HEIGHT and PERIOD, shaped by the spot's working
               size range. Short wind-swell is penalized; long groundswell
               rewarded. Above the spot's close-out size the score falls off.
    direction  cosine falloff from the spot's swell window (each side of the
               ideal bearing scaled to its own edge); ~0 when the swell comes
               from a direction the break can't see.
    wind       three zones by angle off straight offshore: offshore (within
               offshore_width) grooms, cross-shore is near neutral and turns
               slightly bad as it strengthens, onshore ruins it. The offshore
               boost peaks at moderate speed; above STRONG_WIND_KT every
               direction loses quality (storm wind is never clean).
    tide       optional per-spot preferred band; 1.0 (no effect) when a spot
               has no tide preference, so untuned spots are never penalized.
    sea_state  steep, short-period storm seas (Hs / deep-water wavelength
               above STORM_STEEPNESS) close out and break disorganised; the
               penalty grows evenly per second of period below the onset.

Period enters energy twice, on purpose and on separate ranges: as quality
(PERIOD_WEIGHT, ramping 6 -> 14 s) and, above PERIOD_SIZE_REF_S, as size
(long-period swell shoals more and breaks bigger, Komar-Gaughan Hb ~ T^0.4).
Retuned 2026-10-09 so no single 1 s step jumps the score: the old
steepness-linear storm ramp moved 1.5 m clean swell 49 -> 65 from 7 to 8 s.

The wind and sea-state constants were tuned against Surfline's own model
ratings for Old Man's at Tourmaline (data_exploration/quality_labels/
sweep_wind.py), restricted to settings that pass surf-sense guards (e.g.
17 kt offshore on a clean swell is at most Fair, 12 kt offshore at least
Good). The data pins the strong-wind onset (8-10 kt) and floor (0.15-0.2)
and the cross-shore penalty; the rest is surf knowledge.

PER-SPOT METADATA
-----------------
Each factor can be tuned per break via a SpotProfile (see build_profile()).
When a spot supplies no metadata, a profile is DERIVED from its coastline
orientation so new spots score sensibly with zero manual data entry; for
mapped spots the caller passes the generated orientation (spots.py,
data/spot_orientation.csv). Hand-entered values always win over derived ones.
"""
import math


# ---------------------------------------------------------------------------
# Buckets: the continuous 0-100 score maps to these labels/colors. `floor` is
# the inclusive lower bound of the score band. Order high -> low.
#
# Surfline-style steps, capped like Surfline's model: the model's top label
# is "Fair to good"; Good and Epic (RESERVED_BUCKETS) need a person - a human
# forecaster's forecast or observation - or, later, a model calibrated on
# user session reports. Floors fitted on Surfline's own ratings for Old Man's
# (data_exploration/quality_labels: all 24 hours within one label); Very
# poor's floor is a guess (the sample has none). Fair to good was fitted at
# 42 and deliberately raised to 48 so the model's top label stays rare given
# the uncertainty. Raised again to 55 on 2026-10-09 with the period retune
# (period boost, gradual period quality, even storm ramp): Fair to good on
# ~23% of spot-days instead of ~34% at 48, at the cost of Surfline exact
# matches (13/24, was 18/24 with the old engine at 48). Colors follow Surfline's
# red -> orange -> yellow progression with our greens for Fair / Fair to good.
# Replaced 2026-10-08: Poor 0 / Fair 22 / Good 42 / Excellent 70 / Epic 92.
# ---------------------------------------------------------------------------
BUCKETS = [
    {'name': 'Fair to good', 'color': '#15803d', 'floor': 55},   # Dark green
    {'name': 'Fair',         'color': '#22c55e', 'floor': 28},   # Green
    {'name': 'Poor to fair', 'color': '#ffce1c', 'floor': 8},    # Yellow
    {'name': 'Poor',         'color': '#fd9500', 'floor': 5},    # Orange
    {'name': 'Very poor',    'color': '#f1496a', 'floor': 0},    # Red
]

# Not assigned by the model. Kept so the UI can show them for human-rated or,
# once calibrated, user-report-backed hours.
RESERVED_BUCKETS = [
    {'name': 'Epic', 'color': '#631ec7'},   # Purple
    {'name': 'Good', 'color': '#6751f4'},   # Indigo
]


# ---------------------------------------------------------------------------
# Global defaults for a spot that supplies no metadata and whose coastline
# orientation is unknown. Deliberately forgiving so nothing scores absurdly.
# ---------------------------------------------------------------------------
DEFAULT_PROFILE = {
    # Swell window the break receives, as (min_deg, ideal_deg, max_deg) where
    # the arc runs clockwise from min to max through ideal. 0..360, compass.
    'swell_min': 180.0, 'swell_ideal': 270.0, 'swell_max': 360.0,
    # Wind direction (compass, "from") that is offshore/grooming for the break,
    # and how many degrees either side of it still count as fully offshore
    # (the boost then fades out over WIND_FADE_DEG). Widen for cliff-backed or
    # sheltered breaks that stay clean in quartering wind.
    'offshore_dir': 0.0, 'offshore_width': 30.0,
    # Hs (m): where it starts breaking, the sweet spot, and where it closes out.
    'size_min': 0.5, 'size_ideal': 1.8, 'size_max': 4.0,
    # Optional tide band (m) where it works best; None => tide has no effect.
    'tide_min': None, 'tide_max': None,
}


class SpotProfile:
    """Per-spot tuning for the score. Construct via build_profile()."""

    __slots__ = ('swell_min', 'swell_ideal', 'swell_max', 'offshore_dir',
                 'offshore_width', 'size_min', 'size_ideal', 'size_max',
                 'tide_min', 'tide_max')

    def __init__(self, **kw):
        for k, v in DEFAULT_PROFILE.items():
            setattr(self, k, kw.get(k, v))


# ---------------------------------------------------------------------------
# Working size range by bottom type (see size_range()).
# ---------------------------------------------------------------------------
BEACH_SLOPE_REF = 1 / 50.0      # beaches steeper than this close out sooner
BEACH_SLOPE_EXP = 0.4
BEACH_MIN_SCALE = 0.6           # the steepest beach closes out at 60% of size_max
GRAVEL_SHARE = 0.5              # gravel/coarse-sand shoreline at least this...
GRAVEL_SCALE = 0.85             # ...closes out a further 15% sooner


def size_range(bottom_type, slope=None, gravel_share=None):
    """(size_min, size_ideal, size_max) Hs in m for a spot's bottom type.

    Every spot keeps the shared size_min and size_ideal, so normal-size days
    score the same everywhere. Only the close-out size moves, and only for
    beaches: a steep beach (nearshore slope above BEACH_SLOPE_REF) dumps and
    closes out sooner, and a gravel / coarse-sand beach face is steeper
    still. Reefs, points and gentle beaches keep the shared size_max.
    Shrinking the whole range instead was rejected: it raised steep beaches
    on small days (a smaller sweet spot) while barely touching big ones.
    Checked against Surfline only at Old Man's (a reef, so unchanged); the
    beach scaling rests on surf sense until user ratings can calibrate it.
    """
    base = (DEFAULT_PROFILE['size_min'], DEFAULT_PROFILE['size_ideal'],
            DEFAULT_PROFILE['size_max'])
    if bottom_type != 'beach' or not slope:
        return base
    scale = min(1.0, max(BEACH_MIN_SCALE, (BEACH_SLOPE_REF / slope) ** BEACH_SLOPE_EXP))
    if gravel_share is not None and gravel_share >= GRAVEL_SHARE:
        scale *= GRAVEL_SCALE
    return base[0], base[1], round(base[2] * scale, 2)


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
# Wind and sea-state constants (see module docstring for provenance).
# ---------------------------------------------------------------------------
WIND_FADE_DEG = 30.0        # offshore boost fades to 0 over this many deg
WIND_CROSS_END_DEG = 120.0  # beyond this off offshore, the onshore ramp starts
WIND_CROSS_PENALTY = 0.15   # cross-shore penalty, as a fraction of onshore's
WIND_ONSHORE_FULL_KT = 12.0  # cross/onshore penalty reaches full strength here
WIND_BOOST = 0.12           # max offshore grooming boost
WIND_BOOST_PEAK_KT = 8.0    # offshore boost peaks here...
WIND_BOOST_END_KT = 15.0    # ...and is gone by here
STRONG_WIND_KT = 10.0       # above this, every direction loses quality...
STRONG_WIND_SPAN_KT = 8.0   # ...reaching the floor this many kt later
STRONG_WIND_FLOOR = 0.2
STORM_STEEPNESS = 0.013     # Hs / L0 where the storm penalty starts
STORM_FLOOR = 0.5           # reached at twice that steepness
PERIOD_SIZE_REF_S = 12.0    # longer periods count as bigger surf...
PERIOD_SIZE_EXP = 0.4       # ...by (T / ref) ** exp (Komar-Gaughan Hb ~ T^0.4)
PERIOD_WEIGHT = 0.8         # exponent on the period-quality term (was 0.4):
                            # short periods lose quality gradually, 6 -> 14 s
STORM_END_RATIO = 3.0       # storm penalty is full at T_on / this (see below)


# ---------------------------------------------------------------------------
# Angle helpers
# ---------------------------------------------------------------------------
def _ang_diff(a, b):
    """Smallest absolute difference between two compass bearings, in [0, 180]."""
    d = abs((a - b) % 360.0)
    return d if d <= 180.0 else 360.0 - d


def _signed_diff(a, b):
    """Signed difference a - b in (-180, 180]; > 0 when a is clockwise of b."""
    d = (a - b) % 360.0
    return d - 360.0 if d > 180.0 else d


# ---------------------------------------------------------------------------
# The four factors, each returning a value in [0, 1].
# ---------------------------------------------------------------------------
def _energy_factor(height, period, p):
    """Wave energy from HEIGHT (shaped by working size range) and PERIOD."""
    # Long-period swell shoals more and breaks bigger than its Hs: scale the
    # height by (T / ref) ** exp above ref (Komar-Gaughan Hb ~ T^0.4).
    height = height * (max(period, PERIOD_SIZE_REF_S) / PERIOD_SIZE_REF_S) ** PERIOD_SIZE_EXP
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
    combined = (size ** 0.6) * (per ** PERIOD_WEIGHT)
    return max(0.0, min(1.0, combined))


def _direction_factor(swell_dir, p):
    """How well the swell direction lines up with the break's window.

    A single continuous cosine falloff from the ideal bearing: 1.0 dead-on,
    ~0.5 at the window edge (half-width from ideal), then decaying smoothly to
    a small floor as the swell rotates outside the window - no cliff at the
    edge, since a swell 1 deg outside the arc is barely worse than 1 deg inside.
    The half-width is that of the side the swell comes from, so a window
    blocked by a headland on one side is narrower on that side only.
    """
    off = _ang_diff(swell_dir, p.swell_ideal)
    edge = p.swell_max if _signed_diff(swell_dir, p.swell_ideal) >= 0 else p.swell_min
    half_width = max(_ang_diff(p.swell_ideal, edge), 1.0)
    x = off / half_width
    if x <= 1.0:
        # Inside the window: 1.0 at ideal -> 0.5 at the edge.
        return 0.5 + 0.5 * math.cos(x * math.pi / 2.0)
    # Outside: keep decaying from ~0.5 toward a wrap/refraction floor.
    over = min((off - half_width) / 45.0, 1.0)   # 45 deg past edge => floor
    return max(0.08, 0.5 * (1.0 - over))


def _wind_factor(speed, direction, p):
    """Three zones by angle off straight offshore, scaled by speed:

        0 .. width            offshore: grooming boost
        width .. +FADE        boost fades out
        .. CROSS_END          cross-shore: penalty grows to CROSS_PENALTY
        .. 180                onshore: penalty grows to full at straight on

    plus a direction-blind strong-wind penalty above STRONG_WIND_KT.
    """
    if speed < 3.0:
        return 1.0  # glass / near-calm: great regardless of direction
    off = _ang_diff(direction, p.offshore_dir)          # 0 => straight offshore
    zero = p.offshore_width + WIND_FADE_DEG
    if off <= zero:
        # Offshore: boost peaks at moderate wind, gone once it blows hard.
        a = 1.0 if off <= p.offshore_width else (zero - off) / WIND_FADE_DEG
        if speed <= WIND_BOOST_PEAK_KT:
            bump = (speed - 3.0) / (WIND_BOOST_PEAK_KT - 3.0)
        else:
            bump = max(0.0, (WIND_BOOST_END_KT - speed)
                       / (WIND_BOOST_END_KT - WIND_BOOST_PEAK_KT))
        f = 1.0 + WIND_BOOST * a * bump
    else:
        # Cross-shore then onshore: penalty grows with angle and strength.
        cross_end = max(WIND_CROSS_END_DEG, zero + 1.0)
        if off <= cross_end:
            a = WIND_CROSS_PENALTY * (off - zero) / (cross_end - zero)
        else:
            a = WIND_CROSS_PENALTY + (1.0 - WIND_CROSS_PENALTY) * (off - cross_end) / (180.0 - cross_end)
        strength = min((speed - 3.0) / (WIND_ONSHORE_FULL_KT - 3.0), 1.0) ** 0.7
        f = max(0.1, 1.0 - 0.9 * a * strength)
    if speed > STRONG_WIND_KT:
        f *= max(STRONG_WIND_FLOOR,
                 1.0 - (1.0 - STRONG_WIND_FLOOR) * (speed - STRONG_WIND_KT) / STRONG_WIND_SPAN_KT)
    return f


def _sea_state_factor(height, period):
    """Storm penalty for steep, short-period seas: 1.0 for organised swell,
    falling to STORM_FLOOR as short-period storm seas steepen.

    The onset is deep-water steepness Hs / L0 = STORM_STEEPNESS
    (L0 = 1.56 T^2 m), i.e. the period T_on = sqrt(Hs / (1.56 * onset)).
    Below T_on the penalty grows LINEARLY IN PERIOD, reaching the floor at
    T_on / STORM_END_RATIO, so each second costs about the same. A ramp
    linear in steepness (1 / T^2) packed most of the drop into one or two
    seconds (1.5 m: 7 s -> 8 s moved the score 49 -> 65).
    """
    t_on = math.sqrt(max(height, 0.0) / (1.56 * STORM_STEEPNESS))
    if period >= t_on:
        return 1.0
    x = (t_on - period) / (t_on - t_on / STORM_END_RATIO)
    return 1.0 - (1.0 - STORM_FLOOR) * min(1.0, x)


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
    """Continuous 0-100 surf score from the five factors. See module docstring."""
    p = profile or SpotProfile()
    energy = _energy_factor(height, period, p)
    direction = _direction_factor(swell_dir, p)
    wind = _wind_factor(wind_speed, wind_dir, p)
    tide_f = _tide_factor(tide, p)
    sea = _sea_state_factor(height, period)
    raw = energy * direction * wind * tide_f * sea
    return max(0.0, min(100.0, raw * 100.0))


def bucket_for_score(score):
    """(name, color) for a 0-100 score."""
    for b in BUCKETS:
        if score >= b['floor']:
            return b['name'], b['color']
    return BUCKETS[-1]['name'], BUCKETS[-1]['color']


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
        consts = {k: v for k, v in globals().items()
                  if k.startswith(('WIND_', 'STRONG_WIND_', 'STORM_', 'PERIOD_'))}
        consts_js = '\n'.join(f'const {k} = {json.dumps(v)};' for k, v in consts.items())
        consts_js += f'\nconst RATING_RESERVED = {json.dumps(RESERVED_BUCKETS)};'
        return _JS_TEMPLATE.format(buckets=buckets_js, default=default_js,
                                   consts=consts_js)


# ---------------------------------------------------------------------------
# JavaScript port. Kept as a template so the buckets/default profile stay in
# sync with the Python constants. The math below mirrors the functions above.
# ---------------------------------------------------------------------------
_JS_TEMPLATE = r"""// AUTO-GENERATED - DO NOT EDIT MANUALLY
// Generated from backend/rating_config.py
// To update, modify rating_config.py and run: python sync_rating_logic.py
//
// Continuous 0-100 surf score from five independent factors
// (energy x direction x wind x tide x sea state), bucketed into five labels/colors.
// See backend/rating_config.py for the full model description.

const RATING_BUCKETS = {buckets};
const RATING_DEFAULT_PROFILE = {default};
{consts}

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

function _signedDiff(a, b) {{
    const d = ((a - b) % 360 + 360) % 360;
    return d > 180 ? d - 360 : d;
}}

function _energyFactor(height, period, p) {{
    height = height * Math.pow(Math.max(period, PERIOD_SIZE_REF_S) / PERIOD_SIZE_REF_S, PERIOD_SIZE_EXP);
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

    const combined = Math.pow(size, 0.6) * Math.pow(per, PERIOD_WEIGHT);
    return Math.max(0, Math.min(1, combined));
}}

function _directionFactor(swellDir, p) {{
    const off = _angDiff(swellDir, p.swell_ideal);
    const edge = _signedDiff(swellDir, p.swell_ideal) >= 0 ? p.swell_max : p.swell_min;
    const halfWidth = Math.max(_angDiff(p.swell_ideal, edge), 1);
    const x = off / halfWidth;
    if (x <= 1) return 0.5 + 0.5 * Math.cos(x * Math.PI / 2);
    const over = Math.min((off - halfWidth) / 45, 1);
    return Math.max(0.08, 0.5 * (1 - over));
}}

function _windFactor(speed, direction, p) {{
    if (speed < 3) return 1;
    const off = _angDiff(direction, p.offshore_dir);
    const zero = p.offshore_width + WIND_FADE_DEG;
    let f;
    if (off <= zero) {{
        const a = off <= p.offshore_width ? 1 : (zero - off) / WIND_FADE_DEG;
        const bump = speed <= WIND_BOOST_PEAK_KT
            ? (speed - 3) / (WIND_BOOST_PEAK_KT - 3)
            : Math.max(0, (WIND_BOOST_END_KT - speed) / (WIND_BOOST_END_KT - WIND_BOOST_PEAK_KT));
        f = 1 + WIND_BOOST * a * bump;
    }} else {{
        const crossEnd = Math.max(WIND_CROSS_END_DEG, zero + 1);
        const a = off <= crossEnd
            ? WIND_CROSS_PENALTY * (off - zero) / (crossEnd - zero)
            : WIND_CROSS_PENALTY + (1 - WIND_CROSS_PENALTY) * (off - crossEnd) / (180 - crossEnd);
        const strength = Math.pow(Math.min((speed - 3) / (WIND_ONSHORE_FULL_KT - 3), 1), 0.7);
        f = Math.max(0.1, 1 - 0.9 * a * strength);
    }}
    if (speed > STRONG_WIND_KT) {{
        f *= Math.max(STRONG_WIND_FLOOR,
            1 - (1 - STRONG_WIND_FLOOR) * (speed - STRONG_WIND_KT) / STRONG_WIND_SPAN_KT);
    }}
    return f;
}}

function _seaStateFactor(height, period) {{
    const tOn = Math.sqrt(Math.max(height, 0) / (1.56 * STORM_STEEPNESS));
    if (period >= tOn) return 1;
    return 1 - (1 - STORM_FLOOR) * Math.min(1, (tOn - period) / (tOn - tOn / STORM_END_RATIO));
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
    const sea = _seaStateFactor(height, period);
    const raw = energy * direction * wind * tideF * sea;
    return Math.max(0, Math.min(100, raw * 100));
}}

function bucketForScore(score) {{
    for (const b of RATING_BUCKETS) {{
        if (score >= b.floor) return {{ rating: b.name, color: b.color }};
    }}
    const last = RATING_BUCKETS[RATING_BUCKETS.length - 1];
    return {{ rating: last.name, color: last.color }};
}}

// Compact form of a label for pills: "Poor to fair" -> "Poor/fair",
// "Fair to good" -> "Fair/good". Display only; the API keeps the full name.
function ratingDisplayName(name) {{
    return String(name || '').replace(' to ', '/');
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
    module.exports = {{ getRatingAndColor, scoreConditions, bucketForScore, ratingDisplayName }};
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
