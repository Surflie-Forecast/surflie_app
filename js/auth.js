// Auth + user preferences (Phase 1): magic-link login, favorite spots and a
// default spot, stored in Supabase. The browser talks to Supabase directly
// with the publishable key; row-level security keeps users to their own rows.
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

window.surfUser = { session: null, profile: null, favorites: new Set() };

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
    const [profileRes, favRes] = await Promise.all([
        sb.from('profiles').select('*').eq('id', user.id).maybeSingle(),
        sb.from('favorites').select('kind, item_id').eq('user_id', user.id),
    ]);
    if (profileRes.error) console.error('profile load failed', profileRes.error);
    if (favRes.error) console.error('favorites load failed', favRes.error);
    window.surfUser.profile = profileRes.data || null;
    window.surfUser.favorites =
        new Set((favRes.data || []).map(r => favKey(r.kind, r.item_id)));
}

async function toggleFavorite(kind, id) {
    const user = window.surfUser.session?.user;
    if (!user || !kind || !id) return;
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
    renderAccountPanel();
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
        if (!panel.contains(e.target) && !btn.contains(e.target)) {
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

function renderAccountPanel() {
    const panel = document.getElementById('accountPanel');
    if (!panel) return;
    const session = window.surfUser.session;

    if (!session) {
        panel.innerHTML =
            '<div class="account-section">' +
            '<div class="account-title">Sign in</div>' +
            '<p class="account-hint">Get a one-time login link by email. No password needed.</p>' +
            '<form id="magicLinkForm" class="magic-link-form">' +
            '<input type="email" id="magicLinkEmail" placeholder="you@email.com" required>' +
            '<button type="submit">Send link</button>' +
            '</form>' +
            '<div id="magicLinkStatus" class="account-status"></div>' +
            '</div>';
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
        `<div class="account-email">${session.user.email}</div>` +
        '<div class="account-title">Favorites</div>' +
        `<div class="account-favs">${favItems}</div>` +
        '<div class="account-title">Default view</div>' +
        `<p class="account-hint">${defItem
            ? `Opens <b>${favLabel(defKind, defItem)}</b> when you visit.`
            : 'Not set — location detection is used.'}</p>` +
        (canSetDefault
            ? `<button id="setDefaultBtn" class="account-action">Use ${target.kind === 'buoy' ? `CDIP ${target.id}` : (currentSpot.name || 'current spot')}</button>`
            : '') +
        '<button id="signOutBtn" class="account-action account-signout">Sign out</button>' +
        '</div>';

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
    await domReady;
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
