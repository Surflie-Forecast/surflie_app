// Auth + user preferences (Phase 1): magic-link login, favorite spots and a
// default spot, stored in Supabase. The browser talks to Supabase directly
// with the publishable key; row-level security keeps users to their own rows.
//
// Access is invite-gated (migration 008): signing in is open, but writes are
// refused by RLS until the account redeems a code. profiles.activated drives
// the panel — unactivated sessions get the code prompt instead of the
// favorites/alerts/sessions UI.
//
// Exposes window.surfUser = { session, profile, favorites:Set<slug> } and
// emits:
//   'auth-changed'       — after login/logout and initial session restore
//   'user-default-spot'  — detail: spot slug (app.js applies it unless the
//                          URL hash already picked a spot)
// Listens for:
//   'spot-rendered'      — detail: { name, slug } from app.js, to sync the
//                          favorite star with the displayed spot

const SUPABASE_URL = 'https://axbjsbtepbecalvaamnu.supabase.co';
const SUPABASE_PUBLISHABLE_KEY = 'sb_publishable_ctbhSSA00mkgOyWSAfvD8w_OIT7NHzB';

const sb = window.supabase.createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY);

window.surfUser = { session: null, profile: null, favorites: new Set(), alarms: [], sessions: [] };

// DOM-ready gate so events/UI never fire before app.js has registered its
// listeners (all app.js code runs at DOMContentLoaded).
const domReady = new Promise((resolve) => {
    if (document.readyState !== 'loading') resolve();
    else document.addEventListener('DOMContentLoaded', resolve);
});

// What the hero currently shows (kept fresh via 'spot-rendered' /
// 'buoy-rendered' / 'buoy-exited'). Favorites cover both kinds, stored as
// (kind, item_id): kind='spot' + slug, or kind='buoy' + CDIP id — the same
// pair Phase-2 alarms will target.
let currentSpot = { name: null, slug: null };
let currentBuoy = { id: null, name: null };
let buoyMode = false;

const favKey = (kind, id) => `${kind}:${id}`;

const NEEDS_CODE_MSG =
    'Enter your invite code in the account panel to save favorites, alerts and sessions.';

// Google's 4-colour mark, inlined — the sign-in button must not depend on a
// remote asset. Google's branding guidelines require the official mark on
// "Continue with Google".
const googleMarkSvg =
    '<svg class="google-mark" viewBox="0 0 18 18" width="16" height="16" aria-hidden="true">' +
    '<path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z"/>' +
    '<path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z"/>' +
    '<path fill="#FBBC05" d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z"/>' +
    '<path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z"/>' +
    '</svg>';

// What the star acts on right now: the buoy in buoy view, else the spot
function currentFavTarget() {
    if (buoyMode) {
        return currentBuoy.id ? { kind: 'buoy', id: currentBuoy.id } : null;
    }
    return currentSpot.slug ? { kind: 'spot', id: currentSpot.slug } : null;
}

// CDIP id -> station name, for the favorites list (buoys.json is tiny and
// served from the same static api/ the app already fetches)
let buoyNames = {};
fetch('api/buoys.json')
    .then(r => r.ok ? r.json() : [])
    .catch(() => [])
    .then(buoys => { buoys.forEach(b => { buoyNames[String(b.id)] = b.name; }); });

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

async function loadUserData() {
    const user = window.surfUser.session?.user;
    if (!user) {
        window.surfUser.profile = null;
        window.surfUser.favorites = new Set();
        return;
    }
    const [profileRes, favRes, alarmRes, sessionRes] = await Promise.all([
        sb.from('profiles').select('*').eq('id', user.id).maybeSingle(),
        sb.from('favorites').select('kind, item_id').eq('user_id', user.id),
        sb.from('alarms').select('*').eq('user_id', user.id)
            .order('created_at'),
        sb.from('session_ratings').select('*').eq('user_id', user.id)
            .order('session_at', { ascending: false }),
    ]);
    if (profileRes.error) console.error('profile load failed', profileRes.error);
    if (favRes.error) console.error('favorites load failed', favRes.error);
    if (alarmRes.error) console.error('alarms load failed', alarmRes.error);
    if (sessionRes.error) console.error('sessions load failed', sessionRes.error);
    window.surfUser.profile = profileRes.data || null;
    window.surfUser.favorites =
        new Set((favRes.data || []).map(r => favKey(r.kind, r.item_id)));
    window.surfUser.alarms = alarmRes.data || [];
    window.surfUser.sessions = sessionRes.data || [];
}

async function toggleFavorite(kind, id) {
    const user = window.surfUser.session?.user;
    if (!user || !kind || !id) return;
    if (!isActivated()) { showPanelError(NEEDS_CODE_MSG); return; }
    const favs = window.surfUser.favorites;
    const key = favKey(kind, id);
    if (favs.has(key)) {
        favs.delete(key); // optimistic
        const { error } = await sb.from('favorites')
            .delete().eq('user_id', user.id).eq('kind', kind).eq('item_id', id);
        if (error) {
            favs.add(key);
            console.error('unfavorite failed', error);
            showPanelError(`Couldn't remove favorite: ${error.message}`);
        }
    } else {
        favs.add(key);
        const { error } = await sb.from('favorites')
            .insert({ user_id: user.id, kind, item_id: id });
        if (error) {
            favs.delete(key);
            console.error('favorite failed', error);
            showPanelError(`Couldn't save favorite: ${error.message}`);
        }
    }
    updateStar();
    renderAccountPanel();
}

async function setDefault(kind, id) {
    const user = window.surfUser.session?.user;
    if (!user || !kind || !id) return;
    // profiles itself stays writable pre-activation, but a default implies a
    // favorites row, and that insert is gated.
    if (!isActivated()) { showPanelError(NEEDS_CODE_MSG); return; }
    const { error } = await sb.from('profiles')
        .update({ default_kind: kind, default_item: id }).eq('id', user.id);
    if (error) {
        console.error('set default failed', error);
        showPanelError(`Couldn't save default: ${error.message}`);
        return;
    }
    if (window.surfUser.profile) {
        window.surfUser.profile.default_kind = kind;
        window.surfUser.profile.default_item = id;
    }
    // a default is a favorite by definition — the DEFAULT badge lives on
    // its favorites row, so make sure that row exists
    const key = favKey(kind, id);
    if (!window.surfUser.favorites.has(key)) {
        window.surfUser.favorites.add(key);
        const { error: favErr } = await sb.from('favorites')
            .insert({ user_id: user.id, kind, item_id: id });
        if (favErr) console.error('auto-favorite failed', favErr);
        updateStar();
    }
    renderAccountPanel();
}

// ---------------------------------------------------------------------------
// Invite gate (migration 008): an account can authenticate but writes nothing
// until it redeems a code. Enforcement is in RLS, so this UI is only about
// telling the user why — never the gate itself.
// ---------------------------------------------------------------------------

// Signed-out users aren't "unactivated" — they just aren't in yet.
const isActivated = () =>
    !window.surfUser.session || window.surfUser.profile?.activated === true;

// The user's own unused codes, loaded lazily: only activated users have any,
// and only the account panel shows them.
let myInviteCodes = null;

async function loadInviteCodes() {
    if (!window.surfUser.session || !isActivated()) { myInviteCodes = null; return; }
    // Select-only by design: this runs more than once per login (supabase
    // fires several auth events), so it must never mint. Codes are created
    // once, at redemption.
    const { data, error } = await sb.rpc('get_invite_codes');
    if (error) { console.error('invite codes load failed', error); return; }
    myInviteCodes = (data || []).map(r => (typeof r === 'string' ? r : r.code));
}

async function redeemInviteCode(code) {
    const { error } = await sb.rpc('redeem_invite_code', { p_code: code });
    if (error) return error.message;
    if (window.surfUser.profile) window.surfUser.profile.activated = true;
    await loadInviteCodes();
    return null;
}

// ---------------------------------------------------------------------------
// Unit preference: header toggle <-> panel toggle <-> profiles.units.
// The saved preference drives the app on login AND how alert emails format
// heights (backend/alerts.py reads profiles.units).
// ---------------------------------------------------------------------------

function applyUnits(units) {
    const imperial = units === 'imperial';
    const t = document.querySelector('.unit-toggle');
    if (t && t.checked !== imperial) {
        t.checked = imperial;
        // bubbles so the document-level save listener sees it too
        // (app.js's own handler on the toggle updates display + storage)
        t.dispatchEvent(new Event('change', { bubbles: true }));
    } else {
        localStorage.setItem('surfForecastUnit', units);
    }
}

async function saveUnits(units) {
    const user = window.surfUser.session?.user;
    if (!user) return;
    if (window.surfUser.profile?.units === units) return; // already saved
    if (window.surfUser.profile) window.surfUser.profile.units = units;
    const { error } = await sb.from('profiles')
        .update({ units }).eq('id', user.id);
    if (error) console.error('units save failed', error);
}

// Display label for a (kind, id) pair
function favLabel(kind, id) {
    return kind === 'buoy'
        ? `CDIP ${id} — ${buoyNames[id] || 'buoy'}`
        : spotNameForSlug(id);
}

// Inline error line at the bottom of the account panel (failures were
// previously console-only, which reads as an unresponsive button)
function showPanelError(msg) {
    const panel = document.getElementById('accountPanel');
    if (!panel) return;
    let line = panel.querySelector('.account-error');
    if (!line) {
        line = document.createElement('div');
        line.className = 'account-status account-error';
        panel.querySelector('.account-section')?.appendChild(line);
    }
    line.textContent = msg;
}

// slug -> display name, via the name->slug map app.js builds from spots.json
function spotNameForSlug(slug) {
    const map = window._spotSlugs || {};
    for (const [name, s] of Object.entries(map)) if (s === slug) return name;
    return slug;
}

// ---------------------------------------------------------------------------
// UI: header account button + dropdown, hero favorite star
// ---------------------------------------------------------------------------

function buildUI() {
    // Account button in the header, after the unit toggle
    const headerRight = document.querySelector('.header-right');
    const btn = document.createElement('button');
    btn.id = 'accountBtn';
    btn.className = 'account-btn';
    btn.title = 'Account';
    btn.innerHTML =
        '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>' +
        '<circle cx="12" cy="7" r="4"></circle></svg>';
    headerRight.appendChild(btn);

    // Dropdown panel, portaled to <body> like the search results
    const panel = document.createElement('div');
    panel.id = 'accountPanel';
    panel.className = 'account-panel';
    panel.style.display = 'none';
    document.body.appendChild(panel);

    btn.addEventListener('click', () => {
        const open = panel.style.display !== 'none';
        if (open) { panel.style.display = 'none'; return; }
        renderAccountPanel();
        const r = btn.getBoundingClientRect();
        panel.style.top = `${r.bottom + 8}px`;
        panel.style.right = `${Math.max(8, window.innerWidth - r.right)}px`;
        panel.style.display = 'block';
    });
    document.addEventListener('click', (e) => {
        // composedPath, not contains: panel buttons re-render the panel's
        // innerHTML on click, so by the time this bubbles up the clicked
        // element is detached and contains() would wrongly close the panel.
        const path = e.composedPath();
        if (!path.includes(panel) && !path.includes(btn)) {
            panel.style.display = 'none';
        }
    });

    // Favorite star in the hero (top-right corner; positioned by CSS)
    const hero = document.querySelector('.hero');
    const star = document.createElement('button');
    star.id = 'favStarBtn';
    star.className = 'fav-star-btn';
    star.title = 'Save as favorite';
    star.style.display = 'none';
    star.innerHTML = starSvg(false);
    hero.appendChild(star);
    star.addEventListener('click', () => {
        const t = currentFavTarget();
        if (t) toggleFavorite(t.kind, t.id);
    });
}

function starSvg(filled) {
    return '<svg width="22" height="22" viewBox="0 0 24 24" ' +
        `fill="${filled ? 'currentColor' : 'none'}" stroke="currentColor" ` +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 ' +
        '12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>';
}

function updateStar() {
    const star = document.getElementById('favStarBtn');
    if (!star) return;
    const target = window.surfUser.session ? currentFavTarget() : null;
    if (!target) { star.style.display = 'none'; return; }
    const isFav = window.surfUser.favorites.has(favKey(target.kind, target.id));
    star.style.display = 'flex';
    star.innerHTML = starSvg(isFav);
    star.classList.toggle('active', isFav);
    const noun = target.kind === 'buoy' ? 'buoy' : 'spot';
    star.title = isFav ? `Remove ${noun} from favorites` : `Save ${noun} as favorite`;
}

// ---------------------------------------------------------------------------
// Swell alarms (evaluated by backend/alerts.py on each scheduled build)
// ---------------------------------------------------------------------------

// Forecast models an alarm can watch (spots and buoys alike). full=true
// models serve period + direction; Hs-only models (ECMWF WAM) grey out
// when those conditions are set. MOP is the 1km nearshore model — full
// variables (Hs + peak period/direction) at a shorter horizon (~5-8 days),
// the sharpest local data.
const ALARM_MODELS = [
    { id: 'gfswave016', label: 'GFS Wave 16km', full: true },
    { id: 'mfwam', label: 'MFWAM 8km', full: true },
    { id: 'mop', label: 'CDIP MOP (nearshore)', full: true },
    { id: 'ecmwf_wam9', label: 'ECMWF WAM 9km', full: false },
];
const ALARM_MODELS_BUOY = ALARM_MODELS;

const M_PER_FT = 0.3048;
const isImperial = () =>
    (localStorage.getItem('surfForecastUnit') || 'imperial') === 'imperial';

function alarmSummary(a) {
    const parts = [];
    if (a.min_height_m != null) {
        parts.push(isImperial()
            ? `waves ≥ ${(a.min_height_m / M_PER_FT).toFixed(1)} ft`
            : `waves ≥ ${a.min_height_m} m`);
    }
    if (a.min_period_s != null) parts.push(`period ≥ ${a.min_period_s} s`);
    if (a.dir_min != null) {
        const [c, m] = _dirToCenterMargin(a.dir_min, a.dir_max);
        parts.push(`swell ${c}°±${m}`);
    }
    if (a.max_wind_kts != null) parts.push(`wind ≤ ${a.max_wind_kts} kts`);
    if (a.wind_dir_min != null) {
        const [c, m] = _dirToCenterMargin(a.wind_dir_min, a.wind_dir_max);
        parts.push(`wind ${c}°±${m}`);
    }
    if (a.lead_days != null) parts.push(`within ${a.lead_days}d`);
    const models = (a.models || []).length
        ? (a.require_all_models ? 'all of: ' : '') + a.models.map(id => {
            const m = ALARM_MODELS_BUOY.find(m => m.id === id);
            return m ? m.label : id;
        }).join(', ')
        : 'default model';
    return `${parts.join(' · ')} — ${models}`;
}

async function toggleAlarm(id, enabled) {
    const { error } = await sb.from('alarms').update({ enabled }).eq('id', id);
    if (error) { showPanelError(`Couldn't update alert: ${error.message}`); return; }
    const a = window.surfUser.alarms.find(a => a.id === id);
    if (a) a.enabled = enabled;
}

async function deleteAlarm(id) {
    const a = window.surfUser.alarms.find(x => x.id === id);
    if (!confirm(`Delete the ${a ? favLabel(a.kind, a.item_id) : ''} alert?`)) return;
    const { error } = await sb.from('alarms').delete().eq('id', id);
    if (error) { showPanelError(`Couldn't delete alert: ${error.message}`); return; }
    window.surfUser.alarms = window.surfUser.alarms.filter(a => a.id !== id);
    renderAccountPanel();
}

// The create form replaces the panel body; Cancel/Save return to the main view
// Directions are entered as "from X° ± margin" (how surfers think about a
// swell window) and stored as the dir_min/dir_max range the evaluator wants.
function _dirToCenterMargin(min, max) {
    if (min == null || max == null) return [null, null];
    const span = (max - min + 360) % 360;
    const m = span / 2;
    return [Math.round((min + m) % 360), Math.round(m)];
}

function _compassHtml(id) {
    // The forecast cards' ➤ glyph. A text glyph's anchor point is not its
    // visual center, so _updateCompass measures the rendered ink (getBBox)
    // once and recenters it on the rose before rotating — otherwise the
    // arrow orbits slightly off-axis and drifts out of line with the wedge.
    return `<svg id="${id}" class="dir-compass" viewBox="0 0 64 64">` +
        '<circle cx="32" cy="32" r="26" class="dc-face"/>' +
        '<text x="32" y="9" text-anchor="middle" class="dc-n">N</text>' +
        '<path class="dc-arc" d=""/>' +
        '<text x="32" y="32" class="dc-arrow" style="display:none">➤</text></svg>';
}

// Shade the coming-from wedge [c-m, c+m]; ➤ at the center shows the travel
// direction (same +90 rotation the forecast cards apply to the glyph)
function _updateCompass(svg, c, m) {
    if (!svg) return;
    const arc = svg.querySelector('.dc-arc');
    const arrow = svg.querySelector('.dc-arrow');
    if (c == null) {
        arc.setAttribute('d', '');
        arrow.style.display = 'none';
        return;
    }
    arrow.style.display = '';
    if (arrow._center == null) {
        const b = arrow.getBBox(); // ink extents at rest position
        arrow._center = [32 - (b.x + b.width / 2), 32 - (b.y + b.height / 2)];
    }
    const [dx, dy] = arrow._center;
    // Right-to-left: center the glyph's ink on the rose, point it south
    // (inward from N), slide it out to the rim, then orbit to the heading —
    // the arrow rides the coming-from side with its tip aimed at the center.
    arrow.setAttribute('transform',
        `rotate(${c} 32 32) translate(0 -10) rotate(90 32 32) ` +
        `translate(${dx.toFixed(2)} ${dy.toFixed(2)})`);
    if (!m) {
        arc.setAttribute('d', '');
        return;
    }
    // Wedge on the DOWNSTREAM side (c+180 ± m): the arrow shows where the
    // swell/wind comes from, the shading shows where it lands — beam and
    // impact zone, per user preference.
    const t = c + 180;
    const r = 26, cx = 32, cy = 32;
    const pt = a => {
        const rad = a * Math.PI / 180;
        return `${(cx + r * Math.sin(rad)).toFixed(1)},${(cy - r * Math.cos(rad)).toFixed(1)}`;
    };
    const large = m > 90 ? 1 : 0;
    arc.setAttribute('d',
        `M${cx},${cy} L${pt(t - m)} A${r},${r} 0 ${large} 1 ${pt(t + m)} Z`);
}

// existing = alarm row to edit (null = create for `target`)
function renderAlarmForm(panel, target, existing = null) {
    const label = favLabel(target.kind, target.id);
    const models = target.kind === 'buoy' ? ALARM_MODELS_BUOY : ALARM_MODELS;
    const imperial = isImperial();
    const hUnit = imperial ? 'ft' : 'm';
    const hMax = imperial ? 20 : 6;
    // one compact row per condition: number input + inline slider at the
    // right (+ a small decorative bar meter over the waves slider). All
    // stay in sync; typing finer decimals than the spinner step is fine
    // (the raw value is what's read on save). sliderMax caps the slider on
    // the everyday range (its midpoint = a typical value) — bigger values
    // are still typed/spun on the number input up to `max`.
    const condRow = (id, name, ph, unit, min, max, step, bars, sliderMax) =>
        `<div class="alarm-row"><span>${name}</span>` +
        `<input type="number" id="${id}" placeholder="${ph}" min="0" max="${max}" ` +
        `step="${step}" class="alarm-num"><span>${unit}</span>` +
        '<div class="alarm-slide">' +
        (bars ? `<div class="alarm-bars" id="${id}Bars">` +
            Array.from({ length: 8 }, (_, i) =>
                `<span style="height:${4 + i * 1.8}px"></span>`).join('') +
            '</div>' : '') +
        `<input type="range" id="${id}Range" class="alarm-range" ` +
        `min="${min}" max="${sliderMax || max}" step="${step}" value="${min}">` +
        '</div></div>';
    // heading has no min so the spinner can cross 0 downward (wraps to 355);
    // the margin row is indented under it — one bordered group + compass
    const dirBlock = (idp, name) =>
        '<div class="alarm-dir">' +
        '<div class="alarm-dir-fields">' +
        `<div class="alarm-row"><span>${name} from</span>` +
        `<input type="number" id="${idp}C" placeholder="270" step="5" class="alarm-num"><span>°</span></div>` +
        `<div class="alarm-row alarm-row-sub"><span>± margin</span>` +
        `<input type="number" id="${idp}M" placeholder="30" min="0" max="179" step="5" class="alarm-num"><span>°</span></div>` +
        '</div>' +
        _compassHtml(idp + 'Compass') +
        '</div>';

    panel.innerHTML =
        '<div class="account-section">' +
        `<div class="account-title">${existing ? 'Edit' : 'New'} alert — ${label}</div>` +
        '<p class="account-hint">Leave a field empty to ignore that condition. ' +
        'You get one email per matching swell window.</p>' +
        // slider midpoints: 6 ft (1.8 m) waves, 7 kts wind
        condRow('alarmHeight', 'Waves ≥', '2', hUnit, 0, hMax, 0.5, true,
            imperial ? 12 : 3.6) +
        condRow('alarmPeriod', 'Period ≥', '10', 's', 0, 22, 1) +
        dirBlock('alarmDir', 'Swell') +
        (target.kind === 'spot'
            ? condRow('alarmWind', 'Wind ≤', '10', 'kts', 0, 30, 1, false, 14) +
              dirBlock('alarmWindDir', 'Wind')
            : '') +
        condRow('alarmLead', 'Within', '16', 'days', 1, 16, 1) +
        '<div class="account-title">Models</div>' +
        '<div class="alarm-models">' +
        models.map((m, i) =>
            `<label class="alarm-model"><input type="checkbox" data-model="${m.id}" ` +
            `data-full="${m.full}"${i === 0 ? ' checked' : ''}>` +
            `<span>${m.label}</span></label>`).join('') +
        '</div>' +
        '<label class="alarm-model"><input type="checkbox" id="alarmAllModels">' +
        '<span>Require all selected models to agree</span></label>' +
        '<div class="alarm-actions">' +
        `<button id="alarmSaveBtn" class="account-action">${existing ? 'Save changes' : 'Save alert'}</button>` +
        '<button id="alarmCancelBtn" class="account-action account-signout">Cancel</button>' +
        '</div>' +
        '<div id="alarmFormStatus" class="account-status account-error"></div>' +
        '</div>';

    const el = id => panel.querySelector(`#${id}`);
    const val = id => {
        const e = el(id);
        return e && e.value !== '' ? parseFloat(e.value) : null;
    };

    // Prefill when editing
    if (existing) {
        if (existing.min_height_m != null) {
            el('alarmHeight').value = isImperial()
                ? Math.round(existing.min_height_m / M_PER_FT * 2) / 2
                : existing.min_height_m;
        }
        if (existing.min_period_s != null) el('alarmPeriod').value = existing.min_period_s;
        const [dc, dm] = _dirToCenterMargin(existing.dir_min, existing.dir_max);
        if (dc != null) { el('alarmDirC').value = dc; el('alarmDirM').value = dm; }
        if (target.kind === 'spot') {
            if (existing.max_wind_kts != null) el('alarmWind').value = existing.max_wind_kts;
            const [wc, wm] = _dirToCenterMargin(existing.wind_dir_min, existing.wind_dir_max);
            if (wc != null) { el('alarmWindDirC').value = wc; el('alarmWindDirM').value = wm; }
        }
        if (existing.lead_days != null) el('alarmLead').value = existing.lead_days;
        panel.querySelectorAll('.alarm-models input[data-model]').forEach(cb => {
            cb.checked = (existing.models || []).includes(cb.dataset.model);
        });
        el('alarmAllModels').checked = !!existing.require_all_models;
    }

    // Number <-> slider <-> bar meter sync per condition row
    const conds = ['alarmHeight', 'alarmPeriod', 'alarmLead',
        ...(target.kind === 'spot' ? ['alarmWind'] : [])];
    const syncCond = (id, from) => {
        const numEl = el(id), rangeEl = el(id + 'Range'), bars = el(id + 'Bars');
        if (from === 'range') numEl.value = rangeEl.value;
        else rangeEl.value = numEl.value === '' ? rangeEl.min : numEl.value;
        if (bars) {
            const min = +rangeEl.min, max = +rangeEl.max;
            const v = numEl.value === '' ? min : +numEl.value;
            const n = bars.children.length;
            const lit = Math.round((v - min) / (max - min) * n);
            [...bars.children].forEach((b, i) => b.classList.toggle('lit', i < lit));
        }
    };
    conds.forEach(id => {
        el(id).addEventListener('input', () => syncCond(id, 'num'));
        el(id + 'Range').addEventListener('input', () => {
            syncCond(id, 'range');
            el(id).dispatchEvent(new Event('input', { bubbles: false }));
        });
        syncCond(id, 'num');
    });

    // Live compass previews + drag-to-aim + heading wrap-around
    const syncCompass = (idp) => _updateCompass(
        el(idp + 'Compass'), val(idp + 'C'), val(idp + 'M'));
    const compasses = ['alarmDir', ...(target.kind === 'spot' ? ['alarmWindDir'] : [])];
    compasses.forEach(idp => {
        const heading = el(idp + 'C');
        // spinner below 0 or past 359 wraps around the rose
        heading.addEventListener('input', () => {
            if (heading.value !== '' && (+heading.value < 0 || +heading.value >= 360)) {
                heading.value = ((+heading.value % 360) + 360) % 360;
            }
            syncCompass(idp);
        });
        el(idp + 'M').addEventListener('input', () => syncCompass(idp));
        // drag (or click) the rose to aim the heading directly
        const svg = el(idp + 'Compass');
        const aim = (e) => {
            const r = svg.getBoundingClientRect();
            const dx = e.clientX - (r.left + r.width / 2);
            const dy = e.clientY - (r.top + r.height / 2);
            const deg = Math.round(((Math.atan2(dx, -dy) * 180 / Math.PI + 360) % 360) / 5) * 5;
            heading.value = deg % 360;
            if (el(idp + 'M').value === '') el(idp + 'M').value = 30;
            heading.dispatchEvent(new Event('input'));
        };
        svg.addEventListener('pointerdown', (e) => {
            e.preventDefault();
            svg.setPointerCapture(e.pointerId);
            aim(e);
        });
        svg.addEventListener('pointermove', (e) => {
            if (e.buttons) aim(e);
        });
        syncCompass(idp);
    });

    // Hs-only models can't serve period/direction conditions: grey them out
    // while either field has a value
    const syncModels = () => {
        const needsFull = el('alarmPeriod').value !== ''
            || el('alarmDirC').value !== '' || el('alarmDirM').value !== '';
        panel.querySelectorAll('.alarm-models input[data-model]').forEach(cb => {
            if (cb.dataset.full === 'false') {
                cb.disabled = needsFull;
                if (needsFull) cb.checked = false;
                cb.closest('.alarm-model').classList.toggle('disabled', needsFull);
            }
        });
    };
    ['alarmPeriod', 'alarmDirC', 'alarmDirM'].forEach(id =>
        el(id).addEventListener('input', syncModels));
    syncModels();

    panel.querySelector('#alarmCancelBtn').addEventListener('click',
        () => renderAccountPanel());
    panel.querySelector('#alarmSaveBtn').addEventListener('click', async () => {
        const status = panel.querySelector('#alarmFormStatus');
        let height = val('alarmHeight');
        if (height != null && isImperial()) {
            height = Math.round(height * M_PER_FT * 100) / 100;
        }
        // center ± margin -> stored range (wraps 360)
        const dirRange = (idp) => {
            const c = val(idp + 'C'), m = val(idp + 'M');
            if (c == null && m == null) return [null, null];
            if (c == null || m == null) return 'incomplete';
            return [(c - m + 720) % 360, (c + m) % 360];
        };
        const dir = dirRange('alarmDir');
        const windDir = target.kind === 'spot' ? dirRange('alarmWindDir') : [null, null];
        if (dir === 'incomplete' || windDir === 'incomplete') {
            status.textContent = 'Direction needs both a heading and a margin.';
            return;
        }
        const row = {
            min_height_m: height,
            min_period_s: val('alarmPeriod'),
            dir_min: dir[0], dir_max: dir[1],
            max_wind_kts: target.kind === 'spot' ? val('alarmWind') : null,
            wind_dir_min: windDir[0], wind_dir_max: windDir[1],
            lead_days: val('alarmLead') != null
                ? Math.round(val('alarmLead')) : null,
            models: [...panel.querySelectorAll('.alarm-models input:checked')]
                .filter(cb => cb.dataset.model).map(cb => cb.dataset.model),
            require_all_models: el('alarmAllModels').checked,
        };
        if (row.min_height_m == null && row.min_period_s == null
            && row.dir_min == null && row.max_wind_kts == null
            && row.wind_dir_min == null) {
            status.textContent = 'Set at least one condition.'; return;
        }
        if (!row.models.length) {
            status.textContent = 'Select at least one model.'; return;
        }
        status.textContent = '';
        let saved, error;
        if (existing) {
            // Changed conditions invalidate the old dedup window
            ({ data: saved, error } = await sb.from('alarms')
                .update({ ...row, snooze_until: null })
                .eq('id', existing.id).select());
        } else {
            ({ data: saved, error } = await sb.from('alarms').insert({
                ...row,
                user_id: window.surfUser.session.user.id,
                kind: target.kind,
                item_id: target.id,
            }).select());
        }
        if (error) { status.textContent = `Couldn't save: ${error.message}`; return; }
        if (existing) {
            const i = window.surfUser.alarms.findIndex(a => a.id === existing.id);
            if (i >= 0) window.surfUser.alarms[i] = saved[0];
        } else {
            window.surfUser.alarms.push(saved[0]);
        }
        renderAccountPanel();
    });
}

// ---------------------------------------------------------------------------
// Session ratings (Phase 3): a star rating joined to a frozen snapshot of
// the conditions that produced it — the training-data seed for the future
// surf model. Snapshots are built client-side at save time from the same
// static api/ files the app renders; past files get pruned, so this is the
// only moment the pairing can be captured.
// ---------------------------------------------------------------------------

const fmtWave = (m) => isImperial()
    ? `${(m / M_PER_FT).toFixed(1)} ft` : `${m.toFixed(1)} m`;

// 5 stars, half-star steps. Click left half = n-0.5, right half = n.
function starRowHtml(rating, interactive) {
    let html = `<div class="star-row${interactive ? ' star-input' : ''}" data-rating="${rating || 0}">`;
    for (let i = 1; i <= 5; i++) {
        const fill = rating >= i ? 100 : rating >= i - 0.5 ? 50 : 0;
        html += `<span class="star" data-i="${i}">` +
            `<span class="star-fill" style="width:${fill}%">★</span>★</span>`;
    }
    return html + '</div>';
}

function wireStarInput(row, onChange) {
    row.querySelectorAll('.star').forEach(star => {
        star.addEventListener('click', (e) => {
            const half = e.offsetX < star.offsetWidth / 2;
            const val = +star.dataset.i - (half ? 0.5 : 0);
            row.dataset.rating = val;
            row.querySelectorAll('.star').forEach(s => {
                const i = +s.dataset.i;
                s.querySelector('.star-fill').style.width =
                    val >= i ? '100%' : val >= i - 0.5 ? '50%' : '0%';
            });
            if (onChange) onChange(val);
        });
    });
}

// Frozen conditions for a session hour: the hourly entry (swell/wind/score),
// tide height nearest the hour, and the closest buoy's OBSERVED state
// nearest the hour (ground truth). Missing sources = absent keys.
async function buildConditionSnapshot(slug, spotName, datetime) {
    const dateIso = datetime.slice(0, 10);
    const when = new Date(datetime.replace(' ', 'T'));
    const snap = { units: { height: 'm', wind: 'kts', tide: 'm' } };
    const getJson = (url) => fetch(url).then(r => r.ok ? r.json() : null).catch(() => null);

    const hours = await getJson(`api/hourly/${slug}/${dateIso}.json`);
    const entry = (hours || []).find(h => h.datetime === datetime);
    if (entry) {
        snap.forecast = { swell: entry.swell, wind: entry.wind,
                          score: entry.score, rating: entry.rating,
                          color: entry.color };
    }

    const tide = await getJson(`api/tide/${slug}.json`);
    if (tide?.dataset?.length) {
        let best = null, bestIdx = -1, bestDt = Infinity;
        tide.dataset.forEach((p, i) => {
            const dt = Math.abs(new Date(p.t.replace(' ', 'T')) - when);
            if (dt < bestDt) { bestDt = dt; best = p; bestIdx = i; }
        });
        if (best && bestDt <= 60 * 60000) {
            snap.tide = { t: best.t, height: best.v };
            // rising/falling from the level ~30 min later (6-min points)
            const later = tide.dataset[bestIdx + 5];
            if (later) snap.tide.trend = later.v >= best.v ? 'rising' : 'falling';
        }
    }

    const buoyId = (window._spotBuoys || {})[spotName];
    if (buoyId) {
        const buoy = await getJson(`api/buoys/${buoyId}.json`);
        if (buoy?.observed?.length) {
            let best = null, bestDt = Infinity;
            for (const p of buoy.observed) {
                const dt = Math.abs(new Date(p.t.replace(' ', 'T')) - when);
                if (dt < bestDt) { bestDt = dt; best = p; }
            }
            // ±90 min: buoys report half-hourly; beyond that it's a
            // different sea state, not ground truth for this session
            if (best && bestDt <= 90 * 60000) {
                snap.buoy = { id: buoyId, t: best.t, hs: best.v };
                const lt = buoy.latest?.time
                    && Math.abs(new Date(buoy.latest.time.replace(' ', 'T')) - when);
                if (lt != null && lt <= 90 * 60000) {
                    if (buoy.latest.tp != null) snap.buoy.tp = buoy.latest.tp;
                    if (buoy.latest.dp != null) snap.buoy.dp = buoy.latest.dp;
                }
            }
        }
    }
    return snap;
}

// The saved conditions rendered as one line in the expanded-forecast-card
// hourly style: time | score color bar | swell | wind | tide
function sessionCondRowHtml(s) {
    const c = s.conditions || {};
    const f = c.forecast;
    if (!f?.swell) return '<div class="account-session-conds">no snapshot</div>';
    const color = f.color || (typeof getRatingAndColor === 'function'
        ? getRatingAndColor(f.swell.height, f.swell.period, f.wind?.speed || 0,
            f.swell.direction, f.wind?.direction).color
        : '#94a3b8');
    const h = isImperial()
        ? `${(f.swell.height / M_PER_FT).toFixed(1)}ft`
        : `${f.swell.height.toFixed(1)}m`;
    const swellDir = Math.round(f.swell.direction);
    const windDir = f.wind ? Math.round(f.wind.direction) : null;
    const tide = c.tide ? (isImperial()
        ? `${(c.tide.height / M_PER_FT).toFixed(1)}ft`
        : `${c.tide.height.toFixed(1)}m`) : null;
    const tideArrow = c.tide?.trend === 'rising' ? '▲'
        : c.tide?.trend === 'falling' ? '▼' : '';
    // no time cell: the record's title already says when; the color bar
    // leads the row like in the cards
    return '<div class="session-cond-header">' +
        '<span></span><span>Swell</span><span>Wind</span><span>Tide</span></div>' +
        '<div class="hourly-row session-cond-row">' +
        `<div class="hourly-rating-bar" style="background-color: ${color};"></div>` +
        '<div class="hourly-swell">' +
        `<span class="hourly-swell-val">${h}</span>` +
        `<span>${Math.round(f.swell.period)}s</span>` +
        `<span class="direction-degrees">${swellDir}°</span></div>` +
        '<div class="hourly-wind">' +
        (f.wind
            ? `<span>${Math.round(f.wind.speed)}kts</span>` +
              `<span class="direction-degrees">${windDir}°</span>`
            : '<span>—</span>') +
        '</div>' +
        `<div class="session-tide">${tide
            ? `<span class="session-tide-arrow">${tideArrow}</span><span>${tide}</span>`
            : '<span>—</span>'}</div>` +
        '</div>';
}

async function deleteSession(id) {
    // sessions are training data — deletion loses an unrecoverable snapshot
    if (!confirm('Delete this session? Its saved conditions can\'t be rebuilt later.')) return;
    const { error } = await sb.from('session_ratings').delete().eq('id', id);
    if (error) { showPanelError(`Couldn't delete session: ${error.message}`); return; }
    window.surfUser.sessions = window.surfUser.sessions.filter(s => s.id !== id);
    renderAccountPanel();
}

// Rate-a-session form (today only for now: past hourly files get pruned, so
// today is what a snapshot can be built from; the timestamp column already
// supports any moment once a conditions archive exists)
async function renderSessionForm(panel, slug, spotName) {
    const today = new Date();
    const dateIso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
    let hours = [];
    try {
        const r = await fetch(`api/hourly/${slug}/${dateIso}.json`);
        if (r.ok) hours = await r.json();
    } catch (e) { /* options stay empty; save disabled below */ }

    // default to the entry closest to now
    let defIdx = 0, bestDt = Infinity;
    hours.forEach((h, i) => {
        const dt = Math.abs(new Date(h.datetime.replace(' ', 'T')) - today);
        if (dt < bestDt) { bestDt = dt; defIdx = i; }
    });
    const hourLabel = dt => new Date(dt.replace(' ', 'T'))
        .toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

    panel.innerHTML =
        '<div class="account-section">' +
        `<div class="account-title">Rate a session — ${spotName}</div>` +
        '<p class="account-hint">Today\'s conditions at that hour get saved ' +
        'with your rating.</p>' +
        '<div class="alarm-row"><span>When</span>' +
        `<select id="sessionHour" class="session-select">` +
        hours.map((h, i) =>
            `<option value="${h.datetime}"${i === defIdx ? ' selected' : ''}>${hourLabel(h.datetime)}</option>`).join('') +
        '</select></div>' +
        '<div class="alarm-row"><span>Rating</span>' + starRowHtml(0, true) + '</div>' +
        '<textarea id="sessionNote" class="session-note" rows="2" ' +
        'placeholder="Notes — board, crowd, what worked… (optional)"></textarea>' +
        '<div class="alarm-actions">' +
        '<button id="sessionSaveBtn" class="account-action">Save session</button>' +
        '<button id="sessionCancelBtn" class="account-action account-signout">Cancel</button>' +
        '</div>' +
        '<div id="sessionFormStatus" class="account-status account-error"></div>' +
        '</div>';

    wireStarInput(panel.querySelector('.star-row'));
    panel.querySelector('#sessionCancelBtn').addEventListener('click',
        () => renderAccountPanel());
    panel.querySelector('#sessionSaveBtn').addEventListener('click', async () => {
        const status = panel.querySelector('#sessionFormStatus');
        const rating = +panel.querySelector('.star-row').dataset.rating;
        const datetime = panel.querySelector('#sessionHour')?.value;
        if (!rating) { status.textContent = 'Tap the stars to rate it.'; return; }
        if (!datetime) { status.textContent = "No forecast data for today yet."; return; }
        status.textContent = 'Saving…';
        const conditions = await buildConditionSnapshot(slug, spotName, datetime);
        const { data, error } = await sb.from('session_ratings').insert({
            user_id: window.surfUser.session.user.id,
            spot_slug: slug,
            session_at: new Date(datetime.replace(' ', 'T')).toISOString(),
            rating,
            note: panel.querySelector('#sessionNote').value.trim() || null,
            conditions,
        }).select();
        if (error) { status.textContent = `Couldn't save: ${error.message}`; return; }
        window.surfUser.sessions.unshift(data[0]);
        renderAccountPanel();
    });
}

function renderAccountPanel() {
    const panel = document.getElementById('accountPanel');
    if (!panel) return;
    const session = window.surfUser.session;

    const unitsRow = (label) =>
        '<div class="account-email-row">' +
        (label ? `<div class="account-email">${label}</div>` : '') +
        '<div class="unit-toggle-wrapper account-units">' +
        '<span class="unit-label">M</span>' +
        `<label class="switch"><input type="checkbox" id="panelUnitToggle"${isImperial() ? ' checked' : ''}>` +
        '<span class="slider round"></span></label>' +
        '<span class="unit-label">FT</span></div>' +
        '</div>';

    if (!session) {
        panel.innerHTML =
            '<div class="account-section">' +
            unitsRow('Units') +
            '<div class="account-title">Sign in</div>' +
            '<button id="googleSignIn" class="account-action account-google">' +
            googleMarkSvg + 'Continue with Google</button>' +
            '<div class="account-or"><span>or</span></div>' +
            '<p class="account-hint">Get a one-time login link by email. No password needed.</p>' +
            '<form id="magicLinkForm" class="magic-link-form">' +
            '<input type="email" id="magicLinkEmail" placeholder="you@email.com" required>' +
            '<button type="submit">Send link</button>' +
            '</form>' +
            '<div id="magicLinkStatus" class="account-status"></div>' +
            '</div>';
        panel.querySelector('#panelUnitToggle').addEventListener('change', (e) => {
            applyUnits(e.target.checked ? 'imperial' : 'metric');
        });
        panel.querySelector('#googleSignIn').addEventListener('click', async () => {
            const status = panel.querySelector('#magicLinkStatus');
            status.textContent = 'Redirecting to Google…';
            // Same target the magic link uses, so one allow-list entry covers
            // both. Supabase handles the code exchange server-side and comes
            // back here; onAuthStateChange picks the session up from there.
            const { error } = await sb.auth.signInWithOAuth({
                provider: 'google',
                options: { redirectTo: window.location.origin + window.location.pathname },
            });
            if (error) status.textContent = `Error: ${error.message}`;
        });
        panel.querySelector('#magicLinkForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const email = panel.querySelector('#magicLinkEmail').value.trim();
            const status = panel.querySelector('#magicLinkStatus');
            status.textContent = 'Sending…';
            const { error } = await sb.auth.signInWithOtp({
                email,
                options: {
                    // Works on both localhost and GitHub Pages
                    emailRedirectTo: window.location.origin + window.location.pathname,
                },
            });
            status.textContent = error
                ? `Error: ${error.message}`
                : 'Check your email for the login link.';
        });
        return;
    }

    // Signed in but no code redeemed yet: nothing below would work (RLS
    // refuses every write), so the panel is just the code prompt.
    if (!isActivated()) {
        panel.innerHTML =
            '<div class="account-section">' +
            unitsRow(session.user.email) +
            '<div class="account-group">' +
            '<div class="account-title">Invite code</div>' +
            '<p class="account-hint">Surflie is invite-only for now. Enter your code to save favorites, set alerts and log sessions.</p>' +
            '<form id="inviteForm" class="magic-link-form">' +
            '<input type="text" id="inviteCode" placeholder="ABCD2345" autocapitalize="characters" autocomplete="off" required>' +
            '<button type="submit">Redeem</button>' +
            '</form>' +
            '<div id="inviteStatus" class="account-status"></div>' +
            '</div>' +
            '<button id="signOutBtn" class="account-action account-signout">Sign out</button>' +
            '</div>';
        panel.querySelector('#panelUnitToggle').addEventListener('change', (e) => {
            applyUnits(e.target.checked ? 'imperial' : 'metric');
        });
        panel.querySelector('#inviteForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const status = panel.querySelector('#inviteStatus');
            status.textContent = 'Checking…';
            const err = await redeemInviteCode(panel.querySelector('#inviteCode').value);
            if (err) { status.textContent = err; return; }
            // Activated: reload the data the gate was blocking, then redraw
            // into the full panel.
            await loadUserData();
            updateStar();
            renderAccountPanel();
        });
        panel.querySelector('#signOutBtn').addEventListener('click', async () => {
            panel.style.display = 'none';
            await sb.auth.signOut();
        });
        return;
    }

    // Logged in. Favorites: spots first, then buoys, with the app's map
    // symbology (blue dot = spot, ink dot = buoy).
    const favs = [...window.surfUser.favorites]
        .map(key => {
            const [kind, id] = [key.slice(0, key.indexOf(':')), key.slice(key.indexOf(':') + 1)];
            return { kind, id, label: favLabel(kind, id) };
        })
        .sort((a, b) => a.kind === b.kind
            ? a.label.localeCompare(b.label)
            : (a.kind === 'spot' ? -1 : 1));
    const defKind = window.surfUser.profile?.default_kind || 'spot';
    const defItem = window.surfUser.profile?.default_item || null;
    const favItems = favs.length
        ? favs.map(f =>
            `<div class="account-fav" data-kind="${f.kind}" data-id="${f.id}">` +
            `<span class="fav-dot fav-dot--${f.kind}"></span>` +
            `<span class="account-fav-name"></span>` +
            (f.kind === defKind && f.id === defItem
                ? '<span class="account-default-tag">default</span>' : '') +
            '</div>').join('')
        : '<p class="account-hint">No favorites yet — open a spot or buoy and tap the star.</p>';

    // Default = what the app opens on: the spot or buoy currently in the hero
    const target = currentFavTarget();
    const canSetDefault = target && !(target.kind === defKind && target.id === defItem);
    panel.innerHTML =
        '<div class="account-section">' +
        unitsRow(session.user.email) +
        // Each concern gets its own boxed group: Favorites / Alerts / Sessions
        '<div class="account-group">' +
        '<div class="account-title">Favorites</div>' +
        `<div class="account-favs">${favItems}</div>` +
        // the DEFAULT badge on the favorites row says what opens on visit;
        // this small action just moves it to the current view
        (canSetDefault
            ? `<button id="setDefaultBtn" class="account-action-small">Set ${target.kind === 'buoy' ? `CDIP ${target.id}` : (currentSpot.name || 'current spot')} as default view</button>`
            : '') +
        '</div>' +
        '<div class="account-group">' +
        '<div class="account-title">Alerts</div>' +
        (window.surfUser.alarms.length
            ? window.surfUser.alarms.map(a =>
                `<div class="account-alarm${a.enabled ? '' : ' off'}" data-id="${a.id}">` +
                '<div class="account-alarm-head">' +
                `<span class="fav-dot fav-dot--${a.kind}"></span>` +
                `<span class="account-alarm-target">${favLabel(a.kind, a.item_id)}</span>` +
                `<input type="checkbox" class="alarm-toggle" title="Enable alert"${a.enabled ? ' checked' : ''}>` +
                '<button class="alarm-del" title="Delete alert">&times;</button>' +
                '</div>' +
                `<div class="account-alarm-conds">${alarmSummary(a)}</div>` +
                '</div>').join('')
            : '<p class="account-hint">No alerts yet.</p>') +
        (target
            ? `<button id="newAlarmBtn" class="account-action">New alert for ${target.kind === 'buoy' ? `CDIP ${target.id}` : (currentSpot.name || 'this spot')}</button>`
            : '') +
        '</div>' +
        // Sessions: only when a SPOT owns the hero (buoys aren't surfed)
        (currentSpot.slug && !buoyMode ? (() => {
            const spotSessions = window.surfUser.sessions
                .filter(s => s.spot_slug === currentSpot.slug);
            const rows = spotSessions.slice(0, 10).map(s => {
                const d = new Date(s.session_at);
                const label = d.toLocaleDateString([], { month: 'short', day: 'numeric' })
                    + ' ' + d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
                return `<div class="account-session" data-id="${s.id}">` +
                    '<div class="account-session-head">' +
                    `<span class="account-session-when">${label}</span>` +
                    starRowHtml(+s.rating, false) +
                    '<button class="alarm-del session-del" title="Delete session">&times;</button>' +
                    '</div>' +
                    `<div class="account-session-detail" style="display:none">` +
                    sessionCondRowHtml(s) +
                    (s.conditions?.buoy
                        ? `<div class="account-session-conds">buoy ${s.conditions.buoy.id}: ${fmtWave(s.conditions.buoy.hs)}` +
                          (s.conditions.buoy.tp ? ` · ${s.conditions.buoy.tp}s` : '') +
                          (s.conditions.buoy.dp ? ` · ${s.conditions.buoy.dp}°` : '') + ' observed</div>'
                        : '') +
                    (s.note ? `<div class="account-session-note"></div>` : '') +
                    '</div></div>';
            }).join('');
            return '<div class="account-group">' +
                `<div class="account-title">Sessions — ${currentSpot.name || ''}</div>` +
                (rows || '<p class="account-hint">No sessions logged here yet.</p>') +
                `<button id="rateSessionBtn" class="account-action">Rate a session</button>` +
                '</div>';
        })() : '') +
        // Invites: only shown while the user still has unused codes to pass on
        (myInviteCodes && myInviteCodes.length
            ? '<div class="account-group">' +
              '<div class="account-title">Invites</div>' +
              '<p class="account-hint">Share a code to bring someone in. Each one works once.</p>' +
              myInviteCodes.map(c =>
                  `<div class="account-invite" data-code="${c}">` +
                  `<code class="account-invite-code">${c}</code>` +
                  '<button class="account-invite-copy" title="Copy code">copy</button>' +
                  '</div>').join('') +
              '</div>'
            : '') +
        '<button id="signOutBtn" class="account-action account-signout">Sign out</button>' +
        '</div>';

    panel.querySelectorAll('.account-invite').forEach(el => {
        el.querySelector('.account-invite-copy').addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            try {
                await navigator.clipboard.writeText(el.dataset.code);
                btn.textContent = 'copied';
                setTimeout(() => { btn.textContent = 'copy'; }, 1500);
            } catch { btn.textContent = 'copy failed'; }
        });
    });
    panel.querySelectorAll('.account-fav').forEach((el, i) => {
        el.querySelector('.account-fav-name').textContent = favs[i].label;
        el.addEventListener('click', () => {
            if (el.dataset.kind === 'buoy') {
                if (typeof openBuoyView === 'function') openBuoyView(el.dataset.id);
            } else {
                const name = spotNameForSlug(el.dataset.id);
                document.dispatchEvent(new CustomEvent('map-spot-select', { detail: name }));
            }
            panel.style.display = 'none';
        });
    });
    const setDefaultBtn = panel.querySelector('#setDefaultBtn');
    if (setDefaultBtn) setDefaultBtn.addEventListener('click', () => {
        const t = currentFavTarget();
        if (t) setDefault(t.kind, t.id);
    });
    const unitToggle = panel.querySelector('#panelUnitToggle');
    if (unitToggle) unitToggle.addEventListener('change', (e) => {
        applyUnits(e.target.checked ? 'imperial' : 'metric');
    });
    panel.querySelectorAll('.account-alarm').forEach(el => {
        el.querySelector('.alarm-toggle').addEventListener('change', (e) => {
            el.classList.toggle('off', !e.target.checked);
            toggleAlarm(el.dataset.id, e.target.checked);
        });
        el.querySelector('.alarm-del').addEventListener('click',
            () => deleteAlarm(el.dataset.id));
        // Clicking the row itself (not toggle/delete) opens it for editing
        el.addEventListener('click', (e) => {
            if (e.target.closest('.alarm-toggle, .alarm-del')) return;
            const a = window.surfUser.alarms.find(x => x.id === el.dataset.id);
            if (a) renderAlarmForm(panel, { kind: a.kind, id: a.item_id }, a);
        });
    });
    const newAlarmBtn = panel.querySelector('#newAlarmBtn');
    if (newAlarmBtn) newAlarmBtn.addEventListener('click', () => {
        const t = currentFavTarget();
        if (t) renderAlarmForm(panel, t);
    });
    panel.querySelectorAll('.account-session').forEach(el => {
        const s = window.surfUser.sessions.find(x => x.id === el.dataset.id);
        const note = el.querySelector('.account-session-note');
        if (note && s?.note) note.textContent = s.note; // textContent: user input
        el.querySelector('.session-del').addEventListener('click', (e) => {
            e.stopPropagation();
            deleteSession(el.dataset.id);
        });
        el.addEventListener('click', () => {
            const d = el.querySelector('.account-session-detail');
            d.style.display = d.style.display === 'none' ? '' : 'none';
        });
    });
    const rateBtn = panel.querySelector('#rateSessionBtn');
    if (rateBtn) rateBtn.addEventListener('click', () =>
        renderSessionForm(panel, currentSpot.slug, currentSpot.name));
    panel.querySelector('#signOutBtn').addEventListener('click', async () => {
        panel.style.display = 'none';
        await sb.auth.signOut();
    });
}

// ---------------------------------------------------------------------------
// Session lifecycle
// ---------------------------------------------------------------------------

async function onSession(session) {
    window.surfUser.session = session;
    await loadUserData();
    await loadInviteCodes();
    await domReady;
    // the saved unit preference wins over this device's localStorage
    if (window.surfUser.profile?.units) applyUnits(window.surfUser.profile.units);
    updateStar();
    renderAccountPanel();
    document.dispatchEvent(new CustomEvent('auth-changed', { detail: { session } }));
    const p = window.surfUser.profile;
    if (p?.default_item) {
        document.dispatchEvent(new CustomEvent('user-default-spot', {
            detail: { kind: p.default_kind || 'spot', id: p.default_item }
        }));
    }
}

domReady.then(() => {
    buildUI();
    // header M/FT toggle changes persist to the profile when logged in
    // (covers the panel toggle too — applyUnits routes through the header)
    document.addEventListener('change', (e) => {
        if (e.target.classList && e.target.classList.contains('unit-toggle')) {
            saveUnits(e.target.checked ? 'imperial' : 'metric');
        }
    });
    document.addEventListener('spot-rendered', (e) => {
        currentSpot = e.detail;
        buoyMode = false;
        updateStar();
    });
    document.addEventListener('buoy-rendered', (e) => {
        buoyMode = true;
        currentBuoy = { id: String(e.detail?.id || ''), name: e.detail?.name || null };
        updateStar();
    });
    document.addEventListener('buoy-exited', () => {
        buoyMode = false;
        updateStar();
    });
});

// Fires with INITIAL_SESSION on load (session restored from storage or the
// magic-link redirect), then SIGNED_IN / SIGNED_OUT afterwards.
let lastUserId; // dedupe: supabase fires several events for one login
sb.auth.onAuthStateChange((event, session) => {
    const userId = session?.user?.id || null;
    if (event === 'INITIAL_SESSION' || userId !== lastUserId) {
        lastUserId = userId;
        onSession(session);
    }
});
