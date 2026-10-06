// Google Analytics 4 (gtag.js). Loaded in <head> so the initial page_view
// fires before the app scripts run.
//
// Exposes window.track(name, params) — a no-op when GA is off, so callers
// never need to check. GA stays off on localhost / file:// (keeps dev
// traffic out of the stats) and until GA_MEASUREMENT_ID is filled in.
//
// Custom events sent from the app: spot_view, unit_toggle, map_open,
// login, favorite_add. Signed-in visitors also carry user_id (the Supabase
// uuid, never the email) so GA can count distinct accounts across devices.

const GA_MEASUREMENT_ID = 'G-4HPKHLBBP8'; // GA4 > Admin > Data streams > Web

(function () {
    const isLocal = ['localhost', '127.0.0.1', ''].includes(location.hostname);
    const enabled = !isLocal && /^G-[A-Z0-9]+$/.test(GA_MEASUREMENT_ID)
        && GA_MEASUREMENT_ID !== 'G-XXXXXXXXXX';

    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { if (enabled) window.dataLayer.push(arguments); };
    window.track = function (name, params) { window.gtag('event', name, params || {}); };
    if (!enabled) return;

    const s = document.createElement('script');
    s.async = true;
    s.src = `https://www.googletagmanager.com/gtag/js?id=${GA_MEASUREMENT_ID}`;
    document.head.appendChild(s);

    window.gtag('js', new Date());
    window.gtag('config', GA_MEASUREMENT_ID);
})();
