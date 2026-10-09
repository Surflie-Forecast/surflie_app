// AUTO-GENERATED - DO NOT EDIT MANUALLY
// Generated from backend/rating_config.py
// To update, modify rating_config.py and run: python sync_rating_logic.py
//
// Continuous 0-100 surf score from five independent factors
// (energy x direction x wind x tide x sea state), bucketed into five labels/colors.
// See backend/rating_config.py for the full model description.

const RATING_BUCKETS = [{"name": "Fair to good", "color": "#15803d", "floor": 55}, {"name": "Fair", "color": "#22c55e", "floor": 28}, {"name": "Poor to fair", "color": "#ffce1c", "floor": 8}, {"name": "Poor", "color": "#fd9500", "floor": 5}, {"name": "Very poor", "color": "#f1496a", "floor": 0}];
const RATING_DEFAULT_PROFILE = {"swell_min": 180.0, "swell_ideal": 270.0, "swell_max": 360.0, "offshore_dir": 0.0, "offshore_width": 30.0, "size_min": 0.5, "size_ideal": 1.8, "size_max": 4.0, "tide_min": null, "tide_max": null};
const WIND_FADE_DEG = 30.0;
const WIND_CROSS_END_DEG = 120.0;
const WIND_CROSS_PENALTY = 0.15;
const WIND_ONSHORE_FULL_KT = 12.0;
const WIND_BOOST = 0.12;
const WIND_BOOST_PEAK_KT = 8.0;
const WIND_BOOST_END_KT = 15.0;
const STRONG_WIND_KT = 10.0;
const STRONG_WIND_SPAN_KT = 8.0;
const STRONG_WIND_FLOOR = 0.2;
const STORM_STEEPNESS = 0.013;
const STORM_FLOOR = 0.5;
const PERIOD_SIZE_REF_S = 12.0;
const PERIOD_SIZE_EXP = 0.4;
const PERIOD_WEIGHT = 0.8;
const STORM_END_RATIO = 3.0;
const RATING_RESERVED = [{"name": "Epic", "color": "#631ec7"}, {"name": "Good", "color": "#6751f4"}];

function _spotProfile(meta) {
    const p = Object.assign({}, RATING_DEFAULT_PROFILE);
    if (meta) {
        for (const k of Object.keys(RATING_DEFAULT_PROFILE)) {
            if (meta[k] !== undefined && meta[k] !== null) p[k] = meta[k];
        }
    }
    return p;
}

function _angDiff(a, b) {
    let d = Math.abs(((a - b) % 360 + 360) % 360);
    return d <= 180 ? d : 360 - d;
}

function _signedDiff(a, b) {
    const d = ((a - b) % 360 + 360) % 360;
    return d > 180 ? d - 360 : d;
}

function _energyFactor(height, period, p) {
    height = height * Math.pow(Math.max(period, PERIOD_SIZE_REF_S) / PERIOD_SIZE_REF_S, PERIOD_SIZE_EXP);
    let size;
    if (height <= p.size_min) {
        size = 0;
    } else if (height < p.size_ideal) {
        size = (height - p.size_min) / (p.size_ideal - p.size_min);
    } else if (height <= p.size_max) {
        const frac = (height - p.size_ideal) / Math.max(p.size_max - p.size_ideal, 1e-6);
        size = 1 - 0.35 * frac;
    } else {
        const over = (height - p.size_max) / Math.max(p.size_max, 1e-6);
        size = Math.max(0, 0.65 - over);
    }

    let per;
    if (period <= 6) per = 0.35;
    else if (period >= 14) per = 1;
    else per = 0.35 + 0.65 * (period - 6) / 8;

    const combined = Math.pow(size, 0.6) * Math.pow(per, PERIOD_WEIGHT);
    return Math.max(0, Math.min(1, combined));
}

function _directionFactor(swellDir, p) {
    const off = _angDiff(swellDir, p.swell_ideal);
    const edge = _signedDiff(swellDir, p.swell_ideal) >= 0 ? p.swell_max : p.swell_min;
    const halfWidth = Math.max(_angDiff(p.swell_ideal, edge), 1);
    const x = off / halfWidth;
    if (x <= 1) return 0.5 + 0.5 * Math.cos(x * Math.PI / 2);
    const over = Math.min((off - halfWidth) / 45, 1);
    return Math.max(0.08, 0.5 * (1 - over));
}

function _windFactor(speed, direction, p) {
    if (speed < 3) return 1;
    const off = _angDiff(direction, p.offshore_dir);
    const zero = p.offshore_width + WIND_FADE_DEG;
    let f;
    if (off <= zero) {
        const a = off <= p.offshore_width ? 1 : (zero - off) / WIND_FADE_DEG;
        const bump = speed <= WIND_BOOST_PEAK_KT
            ? (speed - 3) / (WIND_BOOST_PEAK_KT - 3)
            : Math.max(0, (WIND_BOOST_END_KT - speed) / (WIND_BOOST_END_KT - WIND_BOOST_PEAK_KT));
        f = 1 + WIND_BOOST * a * bump;
    } else {
        const crossEnd = Math.max(WIND_CROSS_END_DEG, zero + 1);
        const a = off <= crossEnd
            ? WIND_CROSS_PENALTY * (off - zero) / (crossEnd - zero)
            : WIND_CROSS_PENALTY + (1 - WIND_CROSS_PENALTY) * (off - crossEnd) / (180 - crossEnd);
        const strength = Math.pow(Math.min((speed - 3) / (WIND_ONSHORE_FULL_KT - 3), 1), 0.7);
        f = Math.max(0.1, 1 - 0.9 * a * strength);
    }
    if (speed > STRONG_WIND_KT) {
        f *= Math.max(STRONG_WIND_FLOOR,
            1 - (1 - STRONG_WIND_FLOOR) * (speed - STRONG_WIND_KT) / STRONG_WIND_SPAN_KT);
    }
    return f;
}

function _seaStateFactor(height, period) {
    const tOn = Math.sqrt(Math.max(height, 0) / (1.56 * STORM_STEEPNESS));
    if (period >= tOn) return 1;
    return 1 - (1 - STORM_FLOOR) * Math.min(1, (tOn - period) / (tOn - tOn / STORM_END_RATIO));
}

function _tideFactor(tide, p) {
    if (tide === null || tide === undefined || p.tide_min === null || p.tide_max === null) return 1;
    if (tide >= p.tide_min && tide <= p.tide_max) return 1;
    const dist = tide < p.tide_min ? (p.tide_min - tide) : (tide - p.tide_max);
    return Math.max(0.55, 1 - dist / 2);
}

function scoreConditions(height, period, swellDir, windSpeed, windDir, tide = null, meta = null) {
    const p = _spotProfile(meta);
    const energy = _energyFactor(height, period, p);
    const direction = _directionFactor(swellDir, p);
    const wind = _windFactor(windSpeed, windDir, p);
    const tideF = _tideFactor(tide, p);
    const sea = _seaStateFactor(height, period);
    const raw = energy * direction * wind * tideF * sea;
    return Math.max(0, Math.min(100, raw * 100));
}

function bucketForScore(score) {
    for (const b of RATING_BUCKETS) {
        if (score >= b.floor) return { rating: b.name, color: b.color };
    }
    const last = RATING_BUCKETS[RATING_BUCKETS.length - 1];
    return { rating: last.name, color: last.color };
}

// Compact form of a label for pills: "Poor to fair" -> "Poor/fair",
// "Fair to good" -> "Fair/good". Display only; the API keeps the full name.
function ratingDisplayName(name) {
    return String(name || '').replace(' to ', '/');
}

// Back-compatible entry point. Existing callers pass (height, period, windSpeed);
// swell/wind direction default to the profile's ideal/offshore bearing so the
// score stays direction-neutral rather than assuming a blocked/onshore setup.
// Pass extra args to make it direction-aware:
//   getRatingAndColor(h, p, windSpeed, swellDir, windDir, tide, meta)
// Returns { rating, color, score } (score is the underlying 0-100 number).
function getRatingAndColor(swellHeight, swellPeriod, windSpeed = 0,
                           swellDir = null, windDir = null, tide = null, meta = null) {
    const p = _spotProfile(meta);
    const sd = swellDir === null || swellDir === undefined ? p.swell_ideal : swellDir;
    const wd = windDir === null || windDir === undefined ? p.offshore_dir : windDir;
    const score = scoreConditions(swellHeight, swellPeriod, sd, windSpeed, wd, tide, meta);
    const b = bucketForScore(score);
    return { rating: b.rating, color: b.color, score: Math.round(score) };
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { getRatingAndColor, scoreConditions, bucketForScore, ratingDisplayName };
}
