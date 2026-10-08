// Data is loaded from mockData.js
// State for units: 'metric' (default) or 'imperial'
let currentUnit = localStorage.getItem('surfForecastUnit') || 'imperial';

// Unit Conversion Helpers
// Unit Conversion Helpers
// Spot slugs (populated from spots.json) — slugs are the stable API file ids
window._spotSlugs = {};
window.apiSpotId = (name) => window._spotSlugs[name] || encodeURIComponent(name);
// Spot name -> preassigned closest CDIP buoy id (also from spots.json);
// the Buoys view opens this station's comparison file.
window._spotBuoys = {};

window.toFeet = (meters) => (meters * 3.28084).toFixed(1);
window.toMeters = (feet) => (feet / 3.28084).toFixed(2);
window.formatHeight = (val, unit) => unit === 'imperial' ? window.toFeet(val) : val;
window.getHeightUnit = (unit) => unit === 'imperial' ? 'ft' : 'm';

// URL hash = #slug[/date][/models|/buoys]. Update the given parts, keep the
// rest, so spot renders, date clicks and the models/buoys toggles can each
// write their own segment without clobbering the others. No-op until a spot
// is in the hash (nothing to anchor the view to yet).
window.updateUrlView = (changes) => {
    const parts = window.location.hash.slice(1).split('/');
    const state = {
        slug: parts[0] || '',
        date: parts.find(p => /^\d{4}-\d{2}-\d{2}$/.test(p)) || null,
        models: parts.includes('models'),
        buoys: parts.includes('buoys'),
        ...changes,
    };
    if (!state.slug) return;
    let hash = `#${state.slug}`;
    if (state.date) hash += `/${state.date}`;
    if (state.models) hash += '/models';
    if (state.buoys) hash += '/buoys';
    history.replaceState(null, '', hash);
};

window.scrollToForecastDate = (dateIso) => {
    const container = document.getElementById('forecastContainer');
    const card = container.querySelector(`.forecast-card[data-date="${dateIso}"]`);
    if (card) {
        // Reflect the selected day in the URL so the exact view can be
        // bookmarked/shared alongside the spot (and models view, if on).
        window.updateUrlView({ date: dateIso });
        const containerWidth = container.clientWidth;
        const cardWidth = card.offsetWidth;
        const cardLeft = card.offsetLeft;

        const targetScroll = cardLeft - (containerWidth / 2) + (cardWidth / 2);

        container.scrollTo({
            left: targetScroll,
            behavior: 'smooth'
        });
    }
};

// Function to update all displayed units in DOM without re-rendering
function updateDisplayedUnits() {
    // Collect all elements
    const swellHeights = document.querySelectorAll('.metric-value[data-type="swell-height"]');
    const tideHeights = document.querySelectorAll('.metric-value[data-type="tide-height"]');
    const hourlySwell = document.querySelectorAll('.hourly-swell-val');

    // Add updating class for soft fade effect
    [...swellHeights, ...tideHeights].forEach(el => el.classList.add('updating'));

    // Wait for soft fade, then update
    setTimeout(() => {
        // 1. Update Swell Heights (Main Cards)
        swellHeights.forEach(el => {
            const valM = parseFloat(el.dataset.valM);
            if (!isNaN(valM)) {
                el.innerHTML = `${formatHeight(valM, currentUnit)}<span class="metric-unit">${getHeightUnit(currentUnit)}</span>`;
            }
        });

        // 2. Update Tide Heights (Main Cards)
        tideHeights.forEach(el => {
            const valM = parseFloat(el.dataset.valM);
            if (!isNaN(valM)) {
                el.innerHTML = `${formatHeight(valM, currentUnit)}${getHeightUnit(currentUnit)}`;
            }
        });

        // 3. Update Hourly Swell Heights
        hourlySwell.forEach(el => {
            const valM = parseFloat(el.dataset.valM);
            if (!isNaN(valM)) {
                el.textContent = `${formatHeight(valM, currentUnit)}${getHeightUnit(currentUnit)}`;
            }
        });

        // 4. Update Chart Labels
        const chartLabels = document.querySelectorAll('.bar-label');
        chartLabels.forEach(el => {
            const valM = parseFloat(el.dataset.valM);
            const period = el.dataset.period;
            const timeStr = el.dataset.time;
            if (!isNaN(valM) && period && timeStr) {
                const formattedVal = formatHeight(valM, currentUnit);
                const unit = getHeightUnit(currentUnit);
                el.innerHTML = `<span style="font-size: 0.85rem; font-weight: 700;">${timeStr} &bull; ${formattedVal}${unit} ${period}s</span>`;
            }
        });

        // Remove updating class for fade back in
        setTimeout(() => {
            [...swellHeights, ...tideHeights].forEach(el => el.classList.remove('updating'));
        }, 50);
    }, 150);
}

document.addEventListener('DOMContentLoaded', () => {
    // Set current date in header
    const dateElement = document.getElementById('currentDate');
    const today = new Date();

    function updateDateDisplay() {
        const isMobile = window.innerWidth <= 768;

        if (isMobile) {
            // Mobile: Stacked format
            const dayOfWeek = today.toLocaleDateString('en-US', { weekday: 'long' });
            const monthDay = today.toLocaleDateString('en-US', { month: 'long', day: 'numeric' });

            dateElement.innerHTML = `
                <div style="text-align: right; line-height: 1.3;">
                    <div style="font-size: 0.9rem; font-weight: 600;">${dayOfWeek}</div>
                    <div style="font-size: 0.8rem;">${monthDay}</div>
                </div>
            `;
        } else {
            // Desktop: Single line format
            dateElement.textContent = today.toLocaleDateString('en-US', {
                weekday: 'long',
                month: 'long',
                day: 'numeric'
            });
        }
    }

    updateDateDisplay();
    window.addEventListener('resize', updateDateDisplay);
    // Re-fit the one-line spot name when the available width changes
    window.addEventListener('resize', fitHeroTitle);

    const searchInput = document.getElementById('spotSearch');
    const searchBtn = document.getElementById('searchBtn');
    const searchResults = document.getElementById('searchResults');
    const spotNameTitle = document.getElementById('spotName');

    // Random Logo Selection
    function setRandomLogo() {
        const logos = [
            { src: 'images/logo.png', weight: 0.6 },
            { src: 'images/logo2.png', weight: 0.133 },
            { src: 'images/logo3.png', weight: 0.133 },
            { src: 'images/logo4.png', weight: 0.133 }
        ];

        let random = Math.random();
        let selectedLogo = logos[0].src;

        for (const option of logos) {
            if (random < option.weight) {
                selectedLogo = option.src;
                break;
            }
            random -= option.weight;
        }

        const brandImg = document.querySelector('.brand img');
        if (brandImg) {
            brandImg.src = selectedLogo;

            // Check if it's an alternative logo
            if (selectedLogo !== 'images/logo.png') {
                brandImg.classList.add('alt-logo');
            } else {
                brandImg.classList.remove('alt-logo');
            }
        }
    }

    setRandomLogo();

    // ---------------------------------------------------------
    // Map Logic
    // ---------------------------------------------------------
    const mapBtn = document.getElementById('mapBtn');
    const mapOverlay = document.getElementById('mapOverlay');
    const closeMapBtn = document.getElementById('closeMapBtn');
    let map = null;
    let heroMap = null;
    let markers = [];

    // Active CA buoy positions (api/buoys.json) — drawn on the hero map and
    // the full map so the Buoys view's data sources are visible in place.
    let availableBuoys = [];
    let heroBuoysAdded = false;
    const heroBuoyMarkers = {}; // buoy id -> hero-map circleMarker
    let heroBuoyFocusedId = null; // buoy the hero map is zoomed out onto
    let lastHeroView = null; // latest spot the hero map was asked to show
    let heroSpotMarker = null; // blue dot marking the current spot
    const buoysPromise = fetch('api/buoys.json')
        .then(r => r.ok ? r.json() : [])
        .catch(() => [])
        .then(buoys => {
            availableBuoys = buoys;
            addHeroBuoyMarkers(); // covers buoys arriving after the map
            return buoys;
        });

    function buoyMarker(b, radius, interactive) {
        return L.circleMarker([b.latitude, b.longitude], {
            radius,
            color: '#fff',
            weight: 1.5,
            fillColor: '#131929',
            fillOpacity: 0.95,
            interactive,
        });
    }

    function addHeroBuoyMarkers() {
        if (!heroMap || heroBuoysAdded || availableBuoys.length === 0) return;
        heroBuoysAdded = true;
        availableBuoys.forEach(b => {
            heroBuoyMarkers[b.id] = buoyMarker(b, 5, false).addTo(heroMap);
        });
    }

    // Buoy view backdrop: keep every hero element in place but swap the map
    // background — zoom out from the spot's coastline to the regional view
    // (the "View Map" overlay's scale), buoy centered and tinted red.
    const HERO_BUOY_ZOOM = 8;
    const resetHeroBuoyStyle = (id) => {
        const m = heroBuoyMarkers[id];
        if (m) {
            m.setStyle({ fillColor: '#131929', weight: 1.5 });
            m.setRadius(5);
        }
    };
    window.focusHeroBuoy = (buoyId) => {
        const b = availableBuoys.find(x => x.id === buoyId);
        if (!b || !heroMap) return;
        // Switching stations: put the previous one back to normal
        if (heroBuoyFocusedId && heroBuoyFocusedId !== buoyId) {
            resetHeroBuoyStyle(heroBuoyFocusedId);
        }
        heroBuoyFocusedId = buoyId;
        const m = heroBuoyMarkers[buoyId];
        if (m) {
            m.setStyle({ fillColor: '#e34948', weight: 2 });
            m.setRadius(7);
            m.bringToFront();
        }
        // The buoy caption adds a sub-line, so the hero (and the map with it)
        // changes height here. Re-measure before framing, exactly as the spot
        // path does, then anchor the marker the same way a spot's dot is.
        setTimeout(() => {
            if (heroBuoyFocusedId !== buoyId) return; // superseded meanwhile
            heroMap.invalidateSize({ animate: false });
            setHeroView(b.latitude, b.longitude, HERO_BUOY_ZOOM);
        }, 100);
    };
    window.blurHeroBuoy = () => {
        if (heroBuoyFocusedId == null) return;
        resetHeroBuoyStyle(heroBuoyFocusedId);
        heroBuoyFocusedId = null;
        // Back to the (latest) spot's coastline framing
        if (lastHeroView) updateHeroMap(lastHeroView.lat, lastHeroView.lon);
    };

    // Initialize Map Function
    function initMap() {
        if (map) return; // Already initialized

        // Default view (will be updated by bounds)
        map = L.map('surfMap', {
            zoomControl: false // Positioned manually or hidden needed? Standard is TopLeft
        }).setView([20, 0], 2);

        // 1. BOTTOM LAYER: Ocean Bathymetry
        L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}', {
            attribution: 'Esri, GEBCO, NOAA',
            maxZoom: 13
        }).addTo(map);

        // 2. TOP LAYER: Satellite — same near-opaque weight as the hero
        // background so both maps read identically
        L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
            attribution: 'Esri, USGS',
            opacity: 0.95,
            maxZoom: 17
        }).addTo(map);

        L.control.zoom({
            position: 'bottomright'
        }).addTo(map);
    }

    // Initialize Static Hero Map
    function initHeroMap() {
        if (heroMap) return;

        heroMap = L.map('heroStaticMap', {
            zoomControl: false,
            dragging: false,
            scrollWheelZoom: false,
            doubleClickZoom: false,
            boxZoom: false,
            keyboard: false,
            touchZoom: false,
            attributionControl: false, // Minimal UI
            zoomAnimation: false,
            fadeAnimation: false,
            inertia: false
        }).setView([20, 0], 5);

        // Ocean Base
        L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}', {
            maxZoom: 13
        }).addTo(heroMap);

        // Limit Max Zoom of map to prevent white tiles
        // Also ensure layer maxZoom matches
        // Satellite Overlay
        L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
            opacity: 0.95, // Fully opaque
            maxZoom: 17
        }).addTo(heroMap);

        addHeroBuoyMarkers(); // covers the map arriving after the buoys
    }

    // Mobile keeps the spot name on one line: start at the CSS size and step
    // down only if it would overflow the space left of the favourite star.
    // The region subtitle is sized in rem, so it's unaffected.
    const HERO_TITLE_MAX_REM = 1.8;
    const HERO_TITLE_MIN_REM = 1.0;
    // Right-hand lane the name must not enter: the star, plus room for the
    // spot dot to sit between the two. Keep in step with the h1's max-width
    // in the mobile block of styles.css.
    const HERO_TITLE_STAR_LANE = 76;

    function fitHeroTitle() {
        const el = document.getElementById('spotName');
        if (!el) return;

        // Desktop wraps as before — this is the mobile stacked layout only.
        if (window.innerWidth > 768) {
            el.style.fontSize = '';
            return;
        }

        const name = el.childNodes[0];
        if (!name || name.nodeType !== Node.TEXT_NODE) return;

        // The h1 shrink-wraps, so its own box is the text width, not the limit.
        // Derive the ceiling from the row it sits in: getComputedStyle returns
        // the max-width calc() unresolved, so compute the same value here.
        const parent = el.parentElement;
        if (!parent) return;
        const ps = getComputedStyle(parent);
        const rowWidth = parent.getBoundingClientRect().width
            - parseFloat(ps.paddingLeft) - parseFloat(ps.paddingRight);
        const available = rowWidth - HERO_TITLE_STAR_LANE;
        if (!(available > 0)) return;

        const measure = () => {
            const range = document.createRange();
            range.setStart(name, 0);
            range.setEnd(name, name.textContent.length);
            const w = range.getBoundingClientRect().width;
            range.detach();
            return w;
        };

        el.style.fontSize = `${HERO_TITLE_MAX_REM}rem`;
        // 0.05rem steps: fine enough to be invisible, few enough to stay cheap.
        for (let rem = HERO_TITLE_MAX_REM; rem >= HERO_TITLE_MIN_REM; rem -= 0.05) {
            el.style.fontSize = `${rem}rem`;
            if (measure() <= available) return;
        }
        el.style.fontSize = `${HERO_TITLE_MIN_REM}rem`;
    }
    // chart.js swaps in the buoy title and needs the same fit
    window.fitHeroTitle = fitHeroTitle;

    // Tight box around the spot name itself. Measures the leading text node
    // only: .hero-region is a block child inside the same h1, so selecting the
    // whole element would return a rect covering the subtitle line too.
    // Falls back to the element box if the range can't be measured.
    function titleTextRect(el) {
        try {
            const name = el.childNodes[0];
            const range = document.createRange();
            if (name && name.nodeType === Node.TEXT_NODE) {
                range.setStart(name, 0);
                range.setEnd(name, name.textContent.length);
            } else {
                range.selectNodeContents(el);
            }
            const r = range.getBoundingClientRect();
            range.detach();
            if (r.width && r.height) return r;
        } catch (e) { /* fall through to the element box */ }
        return el.getBoundingClientRect();
    }

    // Where the spot dot should land in the hero: the free space between the
    // title and the search box. Which axis that gap runs along depends on the
    // layout, so measure rather than assume — compact and desktop-expanded put
    // the two side by side, but under 769px they stack and the gap is vertical.
    // Returns null when there's nothing to offset by.
    function heroSpotAnchorOffset() {
        const hero = document.querySelector('.hero');
        if (!hero) return null;

        const title = document.getElementById('spotName');
        const search = hero.querySelector('.search-container');
        const mapEl = document.getElementById('heroStaticMap');
        if (!title || !search || !mapEl) return null;

        // The rendered text, not the element box: mobile pads the h1 on the
        // right to keep clear of the star, and that padding would drag the
        // midpoint off the words.
        const t = titleTextRect(title);
        const m = mapEl.getBoundingClientRect();
        if (!m.width || !m.height) return null;

        // The title's partner across the gap: on the stacked mobile layout the
        // title is left-justified and the favourite star holds the top-right,
        // so aim between those two. Everywhere else it's the search box. The
        // star only exists for a signed-in user with a favouritable target.
        const star = document.getElementById('favStarBtn');
        const starVisible = star && star.offsetParent !== null;
        const stackedMobile = search.getBoundingClientRect().left - t.right <= 0;
        const partner = (stackedMobile && starVisible) ? star : search;

        const p = partner.getBoundingClientRect();

        // Side by side when a real horizontal gap separates them; otherwise
        // they're stacked and the gap to aim at is the vertical one.
        const sideBySide = p.left - t.right > 0;

        const targetX = sideBySide
            ? (t.right + p.left) / 2
            : (Math.max(t.left, p.left) + Math.min(t.right, p.right)) / 2;
        const targetY = sideBySide
            ? (t.top + t.height / 2 + p.top + p.height / 2) / 2
            : (t.bottom + p.top) / 2;

        // setView centres the point, so shift by target-minus-centre.
        return L.point(
            targetX - m.left - m.width / 2,
            targetY - m.top - m.height / 2
        );
    }

    // setView, but landing the point on the hero's anchor instead of centre.
    function setHeroView(lat, lon, zoom) {
        const offset = heroSpotAnchorOffset();
        if (!offset) {
            heroMap.setView([lat, lon], zoom, { animate: false });
            return;
        }
        // Project at the target zoom, push the centre the opposite way so the
        // spot ends up under the anchor, then unproject back to a lat/lon.
        const point = heroMap.project([lat, lon], zoom).subtract(offset);
        heroMap.setView(heroMap.unproject(point, zoom), zoom, { animate: false });
    }

    // Update Hero Map Position
    function updateHeroMap(lat, lon) {
        if (!heroMap) initHeroMap();
        lastHeroView = { lat, lon }; // blurHeroBuoy restores this framing

        // Slight delay to ensure container size is correct
        setTimeout(() => {
            // A focused buoy owns the backdrop; don't pan back to the spot
            // out from under it (blur re-issues this view when it ends).
            if (heroBuoyFocusedId != null) return;
            heroMap.invalidateSize();

            // Spot centered like the buoy view, blue dot marking it (the
            // map symbology's surf-spot hue vs the buoys' ink/red)
            const zoomLevel = 11; // regional coastline context
            setHeroView(lat, lon, zoomLevel);
            if (!heroSpotMarker) {
                heroSpotMarker = L.circleMarker([lat, lon], {
                    radius: 7,
                    color: '#fff',
                    weight: 2,
                    fillColor: '#0078A8',
                    fillOpacity: 0.95,
                    interactive: false,
                }).addTo(heroMap);
            } else {
                heroSpotMarker.setLatLng([lat, lon]);
            }
            heroSpotMarker.bringToFront();
        }, 100);
    }

    // The hero changes height when it compacts/expands. Leaflet doesn't
    // notice, so the spot drifts off-centre — re-measure once the CSS
    // transition has settled and re-centre on whatever the map is showing.
    function refreshHeroMapFraming() {
        if (!heroMap) return;
        setTimeout(() => {
            heroMap.invalidateSize({ animate: false });
            // A focused buoy owns the framing, but it re-anchors just like a
            // spot does; otherwise fall back to the latest spot view.
            const view = heroBuoyFocusedId != null
                ? focusedHeroBuoyView()
                : lastHeroView;
            if (view) setHeroView(view.lat, view.lon, heroMap.getZoom());
        }, 450); // just past the 0.4s hero transition
    }

    // Coordinates of the buoy currently owning the hero backdrop, if any.
    function focusedHeroBuoyView() {
        const b = availableBuoys.find(x => x.id === heroBuoyFocusedId);
        return b ? { lat: b.latitude, lon: b.longitude } : null;
    }

    // Function to populate markers
    async function populateMapMarkers() {
        if (!map) return;

        // Clear existing
        markers.forEach(m => map.removeLayer(m));
        markers = [];

        // Bounds builder
        const bounds = L.latLngBounds();

        // Get today's date in ISO format
        const today = new Date();
        const todayISO = today.toISOString().split('T')[0];

        // Create markers with custom icons based on swell direction
        const markerPromises = availableSpots.map(async (spot) => {
            if (!spot.latitude || !spot.longitude) return null;

            let swellDirection = 0; // Default direction

            // Try to fetch today's hourly data to get swell direction
            try {
                const response = await fetch(`api/hourly/${apiSpotId(spot.name)}/${todayISO}.json`);
                if (response.ok) {
                    const hourlyData = await response.json();
                    if (hourlyData && hourlyData.length > 0) {
                        // Get the most recent hour's swell direction
                        const currentHour = today.getHours();
                        const relevantData = hourlyData.find(h => {
                            const hour = new Date(h.time).getHours();
                            return hour >= currentHour;
                        }) || hourlyData[0];

                        swellDirection = parseFloat(relevantData.swell.direction) || 0;
                    }
                }
            } catch (err) {
                console.warn(`Could not fetch swell direction for ${spot.name}`, err);
            }

            // Create custom arrow icon rotated to swell direction
            // Add 180 degrees to show where swell is going (not where it's coming from)
            const arrowIcon = L.divIcon({
                className: 'custom-arrow-marker',
                html: `<div style="width: 40px; height: 50px; display: flex; align-items: center; justify-content: center; position: relative;">
                    <svg width="40" height="50" viewBox="0 0 40 50" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <!-- Pin shape -->
                        <path d="M20 2C13.373 2 8 7.373 8 14C8 23 20 38 20 38C20 38 32 23 32 14C32 7.373 26.627 2 20 2Z" 
                              fill="#0078A8" stroke="white" stroke-width="2"/>
                        <!-- Arrow inside pin, rotated -->
                        <g transform="translate(20, 14) rotate(${swellDirection + 180})">
                            <path d="M0 -6L0 6M0 -6L-3 -3M0 -6L3 -3" 
                                  stroke="white" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
                        </g>
                    </svg>
                </div>`,
                iconSize: [40, 50],
                iconAnchor: [20, 40]
            });

            const marker = L.marker([spot.latitude, spot.longitude], { icon: arrowIcon })
                .addTo(map);

            // Attach metadata to marker for event handlers
            marker.spotData = spot;

            // Hover card instead of a click-popup: the 3-day summary fills
            // in lazily on first hover; clicking the pin opens the spot.
            const tooltipContent = `
                <div class="popup-wrapper">
                    <div class="popup-name" style="margin: 0 0 4px; line-height: 1.2;">${spot.name}</div>
                    <div class="popup-dynamic-content" style="min-height: 40px; display: flex; align-items: center; justify-content: center; margin: 4px 0;">
                        <!-- Content injected on first hover -->
                        <div class="loader" style="font-size: 0.8rem; color: #888;">Loading...</div>
                    </div>
                </div>
            `;

            marker.bindTooltip(tooltipContent, {
                direction: 'top',
                offset: [0, -38], // Above the pin
                className: 'spot-hover-card',
                opacity: 1,
            });

            marker.on('click', () => {
                document.dispatchEvent(new CustomEvent('map-spot-select', { detail: spot.name }));
            });

            markers.push(marker);
            bounds.extend([spot.latitude, spot.longitude]);

            return marker;
        });

        // Wait for all markers to be created
        await Promise.all(markerPromises);

        // Buoys on the full map: hover names the station, a click opens its
        // comparison view right away (bounds stay spot-driven — offshore
        // buoys shouldn't zoom us out)
        const buoys = await buoysPromise;
        buoys.forEach(b => {
            const m = buoyMarker(b, 6, true).addTo(map);
            m.bindTooltip(`${buoyNetwork(b)} ${b.id} — ${b.name}`, { direction: 'top' });
            m.on('click', () => {
                closeMap();
                if (typeof openBuoyView === 'function') openBuoyView(String(b.id));
            });
            markers.push(m);
        });

        // Fit bounds if we have spots
        if (markers.length > 0) {
            map.fitBounds(bounds, { padding: [50, 50] });
        }

        // Lazy-load the hover card's 3-day summary on first hover
        map.on('tooltipopen', async (e) => {
            const marker = e.tooltip._source;
            if (!marker || !marker.spotData) return;

            const spot = marker.spotData;
            const container = e.tooltip.getElement();
            if (!container) return;
            const dynamicContainer = container.querySelector('.popup-dynamic-content');

            if (!dynamicContainer || marker._cardLoaded) return;

            try {
                // 1. Fetch Main Forecast Summary (for ratings, colors, and valid dates)
                const forecastRes = await fetch(`api/forecast/${apiSpotId(spot.name)}.json`);
                if (!forecastRes.ok) throw new Error("Forecast unavailable");

                const forecastData = await forecastRes.json();
                const daysToShow = forecastData.forecast.slice(0, 3); // Today + Next 2 days

                if (daysToShow.length === 0) {
                    dynamicContainer.innerHTML = `<div style="font-size: 0.8rem; color: #888;">No data available</div>`;
                    return;
                }

                // 2. Fetch Hourly Data for these days in parallel
                const hourlyPromises = daysToShow.map(day =>
                    fetch(`api/hourly/${apiSpotId(spot.name)}/${day.date_iso}.json`)
                        .then(r => r.ok ? r.json() : [])
                        .catch(() => [])
                );

                const allHourlyData = await Promise.all(hourlyPromises);

                // 3. Build HTML
                let rowsHtml = '';

                daysToShow.forEach((day, index) => {
                    const hourly = allHourlyData[index];

                    // Format Date: "Dec 16"
                    const d = new Date(day.date_iso + 'T12:00:00'); // Midday
                    const dateStr = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

                    // Mini Plot
                    // Placeholder sized to match new compact dimensions
                    let plotHtml = '<div style="width: 50px; height:16px; background:rgba(0,0,0,0.05); border-radius:2px;"></div>';
                    if (hourly && hourly.length > 0) {
                        // Calc max per day for shape
                        let maxH = 0;
                        hourly.forEach(h => {
                            const val = parseFloat(h.swell.height) || 0;
                            if (val > maxH) maxH = val;
                        });
                        if (maxH < 1) maxH = 1;

                        // Generate Plot and Inject Inline Styles for Compactness
                        // Use fixed width 50px as requested/hinted by user's CSS edit
                        const originalHtml = generateMiniHourlyPlot(hourly, maxH);
                        plotHtml = originalHtml.replace('class="mini-hourly-plot"', 'class="mini-hourly-plot" style="height: 16px; width: 50px;"');
                    }

                    rowsHtml += `
                        <div style="display: flex; align-items: center; justify-content: space-between; gap: 4px; margin-bottom: 2px;">
                            <div style="font-size: 0.7rem; font-weight: 500; color: #555; width: 40px; flex-shrink: 0; text-align: left; line-height: 1;">${dateStr}</div>
                            ${plotHtml}
                            <span class="quality-label" style="background-color: ${day.color}; font-size: 0.55rem; padding: 1px 5px; min-width: 45px; text-align: center; border-radius: 4px; line-height: 1.4;">${ratingDisplayName(day.rating)}</span>
                        </div>
                    `;
                });

                dynamicContainer.innerHTML = `<div style="display:flex; flex-direction:column; width: 100%;">${rowsHtml}</div>`;
                // Persist into the bound content — tooltips re-render from it
                // on every hover, so without this each hover refetches
                const wrapper = container.querySelector('.popup-wrapper');
                if (wrapper) marker.setTooltipContent(wrapper.outerHTML);
                marker._cardLoaded = true;

            } catch (err) {
                console.warn("Failed to load map info", err);
                dynamicContainer.innerHTML = `<div style="font-size: 0.8rem; color: #888;">Error loading</div>`;
            }
        });
    }

    // Event Listener for Popup Click (delegated via CustomEvent)
    document.addEventListener('map-spot-select', (e) => {
        const spotName = e.detail;
        renderForecast(spotName);
        closeMap();
    });

    // Open/Close Handlers
    function openMap() {
        window.track('map_open');
        mapOverlay.classList.add('active');
        // Lets CSS clear the boundary strip for the site name row (the
        // compare buttons/legend drop below it while the map is open)
        document.body.classList.add('map-open');

        // Show Map Site Name Container
        const mapSiteNameContainer = document.getElementById('mapSiteNameContainer');
        if (mapSiteNameContainer) {
            let content = lastRenderedSpot;

            // Try to find full spot data for region
            const spotData = availableSpots.find(s => s.name === lastRenderedSpot);
            if (spotData && spotData.region) {
                // "Site Name, Region" with region styled differently
                content = `${spotData.name}, <span class="map-site-region">${spotData.region}</span>`;
            } else {
                // Fallback to title grabbing if needed, or just name
                const titleEl = document.getElementById('spotName');
                if (titleEl) {
                    content = titleEl.innerText; // Plain text fallback
                }
            }

            mapSiteNameContainer.innerHTML = content;
            mapSiteNameContainer.classList.add('active');
        }

        // Force hero to expand (be normal size) so map has space
        const hero = document.querySelector('.hero');
        if (hero) {
            hero.classList.remove('compact');
            refreshHeroMapFraming();
        }

        // Initialize map if needed (lazy load). Spot cards are hover-only
        // now, so nothing to auto-open — the header names the current site.
        if (!map) {
            initMap();
            // Tiny delay to ensure container has size before fitting bounds
            setTimeout(async () => {
                map.invalidateSize();
                await populateMapMarkers();
            }, 100);
        } else {
            // Just refresh size in case window changed
            setTimeout(() => {
                map.invalidateSize();
            }, 300); // Wait for transition
        }
    }

    function closeMap() {
        mapOverlay.classList.remove('active');
        document.body.classList.remove('map-open');

        // Hide Map Site Name Container
        const mapSiteNameContainer = document.getElementById('mapSiteNameContainer');
        if (mapSiteNameContainer) {
            mapSiteNameContainer.classList.remove('active');
        }

        // Close any hover card a click left open
        if (map) {
            markers.forEach(m => m.closeTooltip && m.closeTooltip());
        }

        // Restore hero state logic
        // If cards are expanded, we should return to compact hero
        const anyCardExpanded = document.querySelector('.forecast-card.expanded');
        const hero = document.querySelector('.hero');

        if (hero && anyCardExpanded) {
            hero.classList.add('compact');
            refreshHeroMapFraming();
        }
    }

    // Bind Buttons
    if (mapBtn) mapBtn.addEventListener('click', openMap);
    if (closeMapBtn) closeMapBtn.addEventListener('click', closeMap);

    // Brand → back to the landing state: drop the hash (spot/date/view) and
    // reload, so the site comes up exactly like a first visit
    const brand = document.querySelector('.brand');
    if (brand) {
        brand.addEventListener('click', () => {
            window.location.href = window.location.pathname + window.location.search;
        });
    }

    // Initial Load
    let availableSpots = []; // Array of {name, region}
    // Helper: Haversine distance
    function getDistanceFromLatLonInKm(lat1, lon1, lat2, lon2) {
        var R = 6371; // Radius of the earth in km
        var dLat = deg2rad(lat2 - lat1);
        var dLon = deg2rad(lon2 - lon1);
        var a =
            Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(deg2rad(lat1)) * Math.cos(deg2rad(lat2)) *
            Math.sin(dLon / 2) * Math.sin(dLon / 2)
            ;
        var c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
        var d = R * c; // Distance in km
        return d;
    }

    function deg2rad(deg) {
        return deg * (Math.PI / 180)
    }

    // Helper: Get IP Location
    function getIPLocation() {
        return new Promise(async (resolve) => {
            try {
                const res = await fetch('https://ipapi.co/json/');
                if (res.ok) {
                    const data = await res.json();
                    if (data.latitude && data.longitude) {
                        resolve({ lat: data.latitude, lon: data.longitude });
                        return;
                    }
                }
            } catch (err) {
                console.log("IP location failed", err);
            }
            resolve(null);
        });
    }

    // Helper: Get Geo Location
    function getGeoLocation() {
        return new Promise((resolve) => {
            if (navigator.geolocation) {
                navigator.geolocation.getCurrentPosition(
                    (position) => {
                        resolve({
                            lat: position.coords.latitude,
                            lon: position.coords.longitude
                        });
                    },
                    (error) => {
                        console.log("Geolocation permission denied or failed", error);
                        resolve(null);
                    },
                    { timeout: 10000 }
                );
            } else {
                resolve(null);
            }
        });
    }

    function findNearestSpot(userLoc, spots) {
        if (!userLoc || !spots.length) return null;

        let minDist = Infinity;
        let bestSpot = null;

        spots.forEach(spot => {
            if (spot.latitude && spot.longitude) {
                const dist = getDistanceFromLatLonInKm(userLoc.lat, userLoc.lon, spot.latitude, spot.longitude);
                if (dist < minDist) {
                    minDist = dist;
                    bestSpot = spot;
                }
            }
        });
        return bestSpot;
    }

    async function init() {
        // Setup Toggle Switches (Desktop & Mobile)
        const unitToggles = document.querySelectorAll('.unit-toggle');

        // Initial state for all
        unitToggles.forEach(toggle => {
            toggle.checked = currentUnit === 'imperial';

            toggle.addEventListener('change', (e) => {
                const newState = e.target.checked;
                currentUnit = newState ? 'imperial' : 'metric';
                localStorage.setItem('surfForecastUnit', currentUnit);

                // Sync other toggles
                unitToggles.forEach(t => t.checked = newState);

                updateDisplayedUnits();
                window.track('unit_toggle', { unit: currentUnit });
            });
        });

        try {
            // Fetch available spots from static JSON
            const spotsRes = await fetch('api/spots.json');
            if (spotsRes.ok) {
                availableSpots = await spotsRes.json();
                availableSpots.forEach(s => {
                    if (s.slug) window._spotSlugs[s.name] = s.slug;
                    if (s.closest_buoy) window._spotBuoys[s.name] = s.closest_buoy;
                });
            } else {
                console.error("Failed to fetch spots list");
            }
        } catch (e) {
            console.error("Error fetching spots:", e);
        }

        // Determine initial spot (Default)
        // We use a "confidence" level to ensure we don't downgrade.
        // 0 = Default, 1 = IP, 2 = Geo, 3 = URL slug (explicit share link)
        let currentConfidence = 0;

        // 1. Render immediately so user sees something. A #slug[/date][/models]
        // in the URL is an explicit choice (bookmark/share link) — it trumps
        // IP/Geo. Segments are recognized by shape, not position.
        let initialSpotName = availableSpots.length > 0 ? availableSpots[0].name : "Devereaux Beach";
        const hashParts = decodeURIComponent(window.location.hash.slice(1)).split('/');
        const urlSlug = hashParts[0];
        const urlSpot = urlSlug && availableSpots.find(s => s.slug === urlSlug);
        if (urlSpot) {
            initialSpotName = urlSpot.name;
            currentConfidence = 3;
            // Restore the selected day once the cards have rendered
            const urlDate = hashParts.find(p => /^\d{4}-\d{2}-\d{2}$/.test(p));
            if (urlDate) pendingScrollDate = urlDate;
            // Restore the models overlay / buoy view once the chart has
            // rendered (consumed by renderWaveHeightChart in chart.js — the
            // only point where the views' prerequisites are guaranteed).
            if (hashParts.includes('models')) window._pendingModelsView = true;
            if (hashParts.includes('buoys')) window._pendingBuoysView = true;
        }
        renderForecast(initialSpotName);

        // 2. Start Location Checks in parallel (IP first, then Geo)

        // Async IP Check
        const checkIP = async () => {
            const ipLoc = await getIPLocation();
            if (ipLoc) {
                const nearest = findNearestSpot(ipLoc, availableSpots);
                if (nearest) {
                    console.log("Auto-detected IP spot:", nearest.name);
                    // Update if we haven't already set a higher confidence spot (e.g. Geo returned super fast or user clicked)
                    if (currentConfidence < 1) {
                        currentConfidence = 1;
                        if (nearest.name !== lastRenderedSpot) {
                            renderForecast(nearest.name);
                        }
                    }
                }
            }
        };

        // Async Geo Check
        const checkGeo = async () => {
            const geoLoc = await getGeoLocation();
            if (geoLoc) {
                const nearest = findNearestSpot(geoLoc, availableSpots);
                if (nearest) {
                    console.log("Auto-detected Geo spot:", nearest.name);
                    // Geo trumps everything
                    if (currentConfidence < 2) {
                        currentConfidence = 2;
                        if (nearest.name !== lastRenderedSpot) {
                            renderForecast(nearest.name);
                        }
                    }
                }
            }
        };

        // 2.5 = user's saved default view (from auth.js): an explicit choice,
        // so it beats IP/Geo detection but not a #slug share link (3).
        // detail = { kind: 'spot'|'buoy', id: slug or CDIP id }.
        const applyUserDefault = ({ kind, id }) => {
            if (currentConfidence >= 2.5) return;
            if (kind === 'buoy') {
                if (typeof openBuoyView !== 'function') return;
                currentConfidence = 2.5;
                openBuoyView(String(id));
                return;
            }
            const spot = availableSpots.find(s => s.slug === id);
            if (!spot) return;
            currentConfidence = 2.5;
            if (spot.name !== lastRenderedSpot) renderForecast(spot.name);
        };
        document.addEventListener('user-default-spot', (e) => applyUserDefault(e.detail));
        // In case auth.js resolved the session before init() ran
        const p = window.surfUser?.profile;
        if (p?.default_item) {
            applyUserDefault({ kind: p.default_kind || 'spot', id: p.default_item });
        }

        // Fire and forget (don't await them)
        checkIP();
        checkGeo();
    }


    let lastRenderedSpot = "";
    let pendingScrollDate = null; // date from a #slug/date deep link, applied after render

    init();

    // --- Search: surf spots AND buoys in one panel ---
    const searchContainer = document.querySelector('.search-container');

    // Portal the panel to <body>: the hero's backdrop-filter creates a
    // stacking context that would trap it under the chart controls no matter
    // its z-index. Fixed-positioned under the input on open instead.
    document.body.appendChild(searchResults);

    function hideSearchResults() {
        searchResults.style.display = 'none';
    }

    function positionSearchResults() {
        const r = searchContainer.getBoundingClientRect();
        searchResults.style.left = `${r.left}px`;
        searchResults.style.top = `${r.bottom + 8}px`;
        searchResults.style.width = `${r.width}px`;
    }

    // Spots and buoys, filtered by the query — or, for an empty query, the
    // suggestion set: every spot, then buoys (current spot's closest first).
    function searchMatches(query) {
        const q = query.trim().toLowerCase();
        const spots = availableSpots
            .filter(s => !q || s.name.toLowerCase().includes(q)
                || s.region.toLowerCase().includes(q))
            .map(s => ({
                kind: 'spot',
                label: `${s.name}, ${s.region}`,
                run: () => renderForecast(s.name),
            }));
        let buoyList = availableBuoys.filter(b =>
            !q || b.name.toLowerCase().includes(q) || `${buoyNetwork(b).toLowerCase()} ${b.id}`.includes(q));
        if (!q) {
            const closest = window._spotBuoys[lastRenderedSpot];
            if (closest) {
                buoyList = [
                    ...buoyList.filter(b => b.id === closest),
                    ...buoyList.filter(b => b.id !== closest),
                ];
            }
        }
        const buoys = buoyList.map(b => ({
            kind: 'buoy',
            label: `${buoyNetwork(b)} ${b.id} — ${b.name}`,
            run: () => {
                if (typeof openBuoyView === 'function') openBuoyView(String(b.id));
            },
        }));
        return [...spots, ...buoys];
    }

    function renderSearchResults(query) {
        const matches = searchMatches(query);
        searchResults.innerHTML = '';

        if (matches.length === 0) {
            hideSearchResults();
            return;
        }

        const limit = 5;
        matches.slice(0, limit).forEach(match => {
            const item = document.createElement('div');
            item.className = 'search-result-item';
            item.innerHTML =
                `<span class="sri-dot sri-dot--${match.kind}"></span>` +
                `<span class="sri-label"></span>` +
                `<span class="sri-kind">${match.kind === 'spot' ? 'Surf spot' : 'Buoy'}</span>`;
            item.querySelector('.sri-label').textContent = match.label;
            item.onclick = () => {
                match.run();
                searchInput.value = '';
                hideSearchResults();
            };
            searchResults.appendChild(item);
        });

        // "And X more" label
        if (matches.length > limit) {
            const moreItem = document.createElement('div');
            moreItem.className = 'search-result-item';
            moreItem.style.color = 'var(--text-secondary)';
            moreItem.style.fontStyle = 'italic';
            moreItem.style.cursor = 'default';
            moreItem.textContent = `...and ${matches.length - limit} more`;
            searchResults.appendChild(moreItem);
        }

        positionSearchResults();
        searchResults.style.display = 'block';
    }

    searchInput.addEventListener('input', (e) => renderSearchResults(e.target.value));
    // Clicking into the bar unfolds the suggestion set right away
    searchInput.addEventListener('focus', () => renderSearchResults(searchInput.value));

    // Enter picks the top match (spot or buoy)
    searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            const match = searchMatches(searchInput.value)[0];
            if (match) {
                match.run();
                searchInput.value = '';
                hideSearchResults();
            }
        }
    });

    // The panel lives on <body>: fold it on any outside interaction, and on
    // scroll/resize (its fixed position would drift from the input)
    document.addEventListener('click', (e) => {
        if (!searchContainer.contains(e.target) && !searchResults.contains(e.target)) {
            hideSearchResults();
        }
    });
    window.addEventListener('resize', hideSearchResults);
    window.addEventListener('scroll', hideSearchResults, { passive: true });

    // Scroll Indicator Setup
    const forecastContainer = document.getElementById('forecastContainer');
    const scrollDotLeft = document.getElementById('scrollDotLeft');
    const scrollBar = document.getElementById('scrollBar');
    const scrollDotRight = document.getElementById('scrollDotRight');

    function updateScrollIndicator() {
        if (!forecastContainer || !scrollDotLeft || !scrollBar || !scrollDotRight) return;

        const scrollLeft = forecastContainer.scrollLeft;
        const scrollWidth = forecastContainer.scrollWidth;
        const clientWidth = forecastContainer.clientWidth;
        const maxScroll = scrollWidth - clientWidth;

        // Update Chart Highlights always
        updateChartHighlight();

        // If there's no scrollable content, hide the indicator
        if (maxScroll <= 0) {
            scrollDotLeft.classList.remove('active');
            scrollBar.classList.remove('active');
            scrollDotRight.classList.remove('active');
            return;
        }

        // Calculate scroll percentage (0 to 1)
        const scrollPercentage = scrollLeft / maxScroll;

        // Left dot: active when at the start (0-10%)
        if (scrollPercentage <= 0.1) {
            scrollDotLeft.classList.add('active');
        } else {
            scrollDotLeft.classList.remove('active');
        }

        // Middle bar: active when in the middle (10%-90%)
        if (scrollPercentage > 0.1 && scrollPercentage < 0.9) {
            scrollBar.classList.add('active');
        } else {
            scrollBar.classList.remove('active');
        }

        // Right dot: active when at the end (90-100%)
        if (scrollPercentage >= 0.9) {
            scrollDotRight.classList.add('active');
        } else {
            scrollDotRight.classList.remove('active');
        }

        // Update Chart Highlights
        updateChartHighlight();
    }

    // chart.js calls this after switching modes: the plot's vertical extent
    // changes (bars vs overlay lines), and the box is measured from it.
    // Re-measure only: a view switch must leave the chart wherever the user
    // scrolled it, not snap it back onto the forecast cards' days.
    window.updateChartHighlight = () => updateChartHighlight({ scroll: false });

    function updateChartHighlight({ scroll = true } = {}) {
        const cards = forecastContainer.querySelectorAll('.forecast-card');
        const containerRect = forecastContainer.getBoundingClientRect();

        // Defined "Active Zone" to narrow what is considered "seen" on desktop
        // This prevents cards at the very edges from inclusion, reducing fluctuation.
        // On mobile (narrow screen), we use a smaller margin or just full width?
        // User asked "for the full screen narrow what is considered".

        let marginPct = 0;
        if (window.innerWidth > 768) {
            marginPct = 0.07; // 15% margin (active zone is 70% of screen)
        } else {
            marginPct = 0.0; // on mobile we need the fallback logic mainly
        }

        const margin = containerRect.width * marginPct;
        const activeLeft = containerRect.left + margin;
        const activeRight = containerRect.right - margin;

        const visibleDates = [];
        let maxVisibleRatio = 0;
        let mostVisibleCardDate = null;

        cards.forEach(card => {
            const rect = card.getBoundingClientRect();

            // Check visibility against ACTIVE ZONE
            // Cards must be FULLY inside the active zone to count as 'seen' for the main list
            const visibleWidth = Math.min(rect.right, activeRight) - Math.max(rect.left, activeLeft);
            const cardWidth = rect.width;

            // Handle divide by zero if width is 0 (hidden)
            if (cardWidth === 0) return;

            // Calculate ratio relative to CARD width
            // If visibleWidth <= 0, it's outside active zone.
            const ratio = Math.max(0, visibleWidth) / cardWidth;

            // Strict visibility logic
            if (ratio >= 0.99) {
                visibleDates.push(card.dataset.date);
            }

            // Fallback tracking (use FULL container visibility for fallback choice)
            const fullVisibleWidth = Math.min(rect.right, containerRect.right) - Math.max(rect.left, containerRect.left);
            const fullRatio = Math.max(0, fullVisibleWidth) / cardWidth;

            if (fullRatio > maxVisibleRatio) {
                maxVisibleRatio = fullRatio;
                mostVisibleCardDate = card.dataset.date;
            }
        });

        // Fallback: If no cards are sufficiently inside the active zone
        if (visibleDates.length === 0 && mostVisibleCardDate && maxVisibleRatio > 0.1) {
            visibleDates.push(mostVisibleCardDate);
        }

        // Update Chart
        const overlay = document.getElementById('chartHighlightOverlay');
        const container = document.getElementById('waveChartContainer');

        if (!overlay || !container || visibleDates.length === 0) {
            if (overlay) overlay.style.display = 'none';
            return;
        }

        // We assume visibleDates are sorted by time as they come from DOM order
        const firstDate = visibleDates[0];
        const lastDate = visibleDates[visibleDates.length - 1];

        // Since data is sorted, first wrapper of first date is START.
        // Last wrapper of last date is END.
        // But querySelector finds the FIRST match.
        // START is easy: querySelector with firstDate.
        // END is tricky: querySelector with lastDate gets the FIRST hour of the last day. We need the LAST hour.

        // We can use querySelectorAll for the last date and take the last one.

        const startEl = container.querySelector(`.chart-bar-wrapper[data-date="${firstDate}"]`);

        const endEls = container.querySelectorAll(`.chart-bar-wrapper[data-date="${lastDate}"]`);
        const endEl = endEls.length > 0 ? endEls[endEls.length - 1] : null;

        if (startEl && endEl) {
            // Calculate range
            // offsetLeft is relative to the scrollable container (since it's relative/flex)
            // We want to cover from startEl left to endEl right

            // Adjust for margins if necessary, but offsetLeft usually includes margin if using standard box model? 
            // No, offsetLeft is to the border box. 
            // The visual gap is caused by margin. We probably want to cover the gap too.

            // Add 5px padding on each side
            const padding = 5;
            const left = startEl.offsetLeft - padding;
            const right = endEl.offsetLeft + endEl.offsetWidth + padding;
            const width = right - left;

            overlay.style.display = 'block';
            overlay.style.left = `${left}px`;
            overlay.style.width = `${width}px`;

            // Vertical span. The stylesheet's insets are right everywhere
            // except a compare view on mobile, where the taller control strip
            // pushed the plot down and left the fixed inset far too short.
            // The bars view keeps the CSS value: anchoring to the wrappers
            // there would clip the day label sitting at their top edge.
            const svg = container.querySelector(
                '.model-overlay-svg, .buoy-overlay-svg');
            if (window.innerWidth <= 768 && svg) {
                const cRect = container.getBoundingClientRect();
                const toLocal = (y) => y - cRect.top + container.scrollTop;

                // Span the whole plot band, not just the selected day's own
                // line segment: start from the svg's full drawn extent so the
                // frame is the same height wherever it slides.
                let topPx = Infinity;
                let bottomPx = -Infinity;
                // The drawn marks, not the svg element: lines and dots can
                // spill past its box and the frame should reach them.
                svg.querySelectorAll('path, circle').forEach(el => {
                    if (el.getAttribute('stroke') === 'transparent') return;
                    const q = el.getBoundingClientRect();
                    if (!q.height) return;
                    topPx = Math.min(topPx, toLocal(q.top));
                    bottomPx = Math.max(bottomPx, toLocal(q.bottom));
                });
                // Keep the bars' own column in the span too, so the frame
                // still reaches the axis baseline under the lines.
                const sRect = startEl.getBoundingClientRect();
                topPx = Math.min(topPx, toLocal(sRect.top));
                bottomPx = Math.max(bottomPx, toLocal(sRect.bottom));
                // Models view drops the date row below the plot floor: the
                // frame reaches down to take it in, as on desktop.
                const dateEl = container.classList.contains('models-active')
                    && startEl.querySelector('.axis-label');
                if (dateEl) {
                    // Final resting spot (top: 100% + 5px of the column),
                    // not the live rect: the label may still be sliding.
                    bottomPx = Math.max(bottomPx,
                        toLocal(sRect.bottom) + 5 + dateEl.offsetHeight + 4);
                }

                if (isFinite(topPx) && isFinite(bottomPx)) {
                    // Clamp the top only: the container is overflow-y visible,
                    // so marks below it are still on screen, but above it sits
                    // the strip's controls — don't ride up into those.
                    topPx = Math.max(topPx, container.scrollTop);
                    overlay.style.top = `${Math.max(0, topPx)}px`;
                    overlay.style.bottom = 'auto';
                    overlay.style.height = `${Math.max(0, bottomPx - topPx)}px`;
                }
            } else {
                // Hand the vertical placement back to the stylesheet.
                overlay.style.top = '';
                overlay.style.bottom = '';
                overlay.style.height = '';
            }

            // Auto-scroll logic
            const containerCenter = container.clientWidth / 2;
            const highlightCenter = left + (width / 2);

            // Calculate target scroll position to center the highlight
            const targetScroll = highlightCenter - containerCenter;

            // Optional: Only scroll if it's actually out of view? 
            // The user implies they want it to "scroll with it", which suggests continuous syncing.
            // Using smooth behavior might be too laggy if called on every scroll event. 
            // But let's try setting scrollLeft directly for responsiveness, or use scrollTo with behavior 'smooth' debounced.
            // Since this is called on scroll, direct assignment is better for 1:1 feel, but smooth is nicer for "catch up".
            // Let's try direct 'scrollTo' with smooth behavior first.

            if (scroll) {
                container.scrollTo({
                    left: targetScroll,
                    behavior: 'smooth'
                });
            }
        } else {
            overlay.style.display = 'none';
        }
    }

    // Listen for scroll events
    if (forecastContainer) {
        forecastContainer.addEventListener('scroll', updateScrollIndicator);
        window.addEventListener('resize', updateScrollIndicator);
        // Initial check
        setTimeout(updateChartHighlight, 500);
    }

    let currentRenderId = 0;

    async function renderForecast(spotName) {
        // Increment render ID to invalidate previous running renders
        const renderId = ++currentRenderId;

        // Update Title
        spotNameTitle.textContent = "Loading...";

        const container = document.getElementById('forecastContainer');
        container.innerHTML = '<div style="text-align:center; width:100%; margin-top:2rem;">Fetching forecast data...</div>';

        try {
            // Fetch from static JSON file
            const response = await fetch(`api/forecast/${apiSpotId(spotName)}.json`);

            // Check if superseded
            if (renderId !== currentRenderId) return;

            if (!response.ok) {
                const err = await response.json().catch(() => ({ error: "Failed to fetch data" }));
                throw new Error(err.error || "Failed to fetch data");
            }

            const data = await response.json();

            // Check if superseded
            if (renderId !== currentRenderId) return;
            window.track('spot_view', { spot_name: spotName });

            lastRenderedSpot = data.spotName;

            // Reflect the displayed spot in the URL (#slug) so it can be
            // bookmarked/shared; replaceState avoids polluting history.
            // A spot switch resets date and models view (the chart re-render
            // clears the overlay), so those segments are dropped here.
            const spotSlug = window._spotSlugs[data.spotName];
            if (spotSlug) history.replaceState(null, '', `#${spotSlug}`);

            // Update title with "Name" and "Region" separate
            spotNameTitle.innerHTML = `${data.spotName}<span class="hero-region">${data.region}</span>`;
            fitHeroTitle();

            // Let auth.js sync the favorite star with the displayed spot
            document.dispatchEvent(new CustomEvent('spot-rendered', {
                detail: { name: data.spotName, slug: spotSlug || null }
            }));

            // Update Hero Background

            // Update Hero Map Background
            // Find lat/lon from availableSpots if possible
            const spotInfo = availableSpots.find(s => s.name === data.spotName);
            if (spotInfo && spotInfo.latitude && spotInfo.longitude) {
                updateHeroMap(spotInfo.latitude, spotInfo.longitude);
            } else if (data.latitude && data.longitude) {
                // Fallback if data has it
                updateHeroMap(data.latitude, data.longitude);
            }


            container.innerHTML = ''; // Clear loading

            if (!data.forecast || data.forecast.length === 0) {
                container.innerHTML = `<div style="text-align:center; width:100%;">No wave data found for "${spotName}"</div>`;
                return;
            }

            // Render Chart
            // Render Chart
            if (window.renderWaveHeightChart) {
                renderWaveHeightChart(spotName, data.forecast).then(() => {
                    // Trigger highlight update after chart is fully rendered
                    updateChartHighlight();
                });
            }

            // Update forecast days counter
            const forecastDaysCounter = document.getElementById('forecastDaysCounter');
            if (forecastDaysCounter && data.forecast.length > 0) {
                const dayText = data.forecast.length === 1 ? 'day' : 'days';
                forecastDaysCounter.textContent = `${data.forecast.length} ${dayText}`;
                forecastDaysCounter.style.display = 'flex';
            }


            // Create a promise array to fetch all hourly data
            const hourlyPromises = data.forecast.map(async (day) => {
                try {
                    const res = await fetch(`api/hourly/${apiSpotId(spotName)}/${day.date_iso}.json`);
                    if (res.ok) {
                        return { date: day.date_iso, data: await res.json() };
                    }
                } catch (e) {
                    console.warn(`Failed to fetch hourly for ${day.date_iso}`, e);
                }
                return { date: day.date_iso, data: [] };
            });

            // Wait for all hourly data (or proceed with empty arrays)
            const allHourlyData = await Promise.all(hourlyPromises);

            // Check if superseded (Critical check before DOM manipulation)
            if (renderId !== currentRenderId) return;

            const hourlyMap = {};

            // Calculate Global Max Swell Height across all days/hours
            let globalMaxH = 0;
            allHourlyData.forEach(item => {
                hourlyMap[item.date] = item.data;
                item.data.forEach(h => {
                    const swell = parseFloat(h.swell.height) || 0;
                    if (swell > globalMaxH) globalMaxH = swell;
                });
            });

            // Set a sane minimum (e.g., 1.0m) to avoid tiny scales or divide-by-zero
            if (globalMaxH < 1.0) globalMaxH = 1.0;

            data.forecast.forEach((day, index) => {
                const card = document.createElement('div');
                card.className = 'forecast-card';
                // Stagger animation delay
                card.style.animationDelay = `${0.2 + (index * 0.1)}s`;
                card.style.cursor = 'pointer';

                // Store data for hourly view
                card.dataset.date = day.date_iso;  // We'll need to pass this from backend
                card.dataset.spot = data.spotName;

                // Dynamic Day Label Logic
                const dateParts = day.date_iso.split('-');
                const fileDate = new Date(dateParts[0], dateParts[1] - 1, dateParts[2]);
                const now = new Date();
                const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());

                const diffTime = fileDate - today;
                const diffDays = Math.round(diffTime / (1000 * 60 * 60 * 24));

                let dayLabel = day.day;
                if (diffDays === 0) {
                    dayLabel = "Today";
                } else if (diffDays === 1) {
                    dayLabel = "Tomorrow";
                } else {
                    const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
                    dayLabel = days[fileDate.getDay()];
                }

                // Generate Mini Plot HTML
                const dayHourlyData = hourlyMap[day.date_iso] || [];
                // Pass globalMaxH for consistent scaling
                const miniPlotHtml = generateMiniHourlyPlot(dayHourlyData, globalMaxH);

                card.innerHTML = `
                    <div class="card-header">
                        <span class="day-label">${dayLabel}</span>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            ${miniPlotHtml}
                            <span class="quality-label${ratingDisplayName(day.rating).length > 4 ? ' quality-label--long' : ''}" style="background-color: ${day.color};">${ratingDisplayName(day.rating)}</span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 4px;">
                            <span class="date-label">${day.date}</span>
                            <span class="expand-icon">▼</span>
                        </div>
                    </div>
                    
                    <div class="daily-summary">
                        <div class="metric-row main-row">
                            <div class="metric">
                                <span class="metric-label">Surf Height</span>
                                <span class="metric-value" data-type="swell-height" data-val-m="${day.swell.height}">
                                    ${formatHeight(day.swell.height, currentUnit)}<span class="metric-unit">${getHeightUnit(currentUnit)}</span>
                                </span>
                            </div>
                            <div class="metric">
                                <span class="metric-label">Period</span>
                                <span class="metric-value">${day.swell.period}</span>
                            </div>
                            <div class="metric" style="align-items: center;">
                                <span class="metric-label">Direction</span>
                                <div class="metric-value" style="margin-top: 0.30rem;">
                                    <span class="swell-arrow" style="transform: rotate(${parseInt(day.swell.direction) + 90}deg)">➤</span>
                                    <span class="direction-text">${day.swell.direction}°</span>
                                </div>
                            </div>
                        </div>

                        <div class="metric-row" style="padding-top: 1rem; align-items: flex-start; gap: 8px;">
                            <div class="metric" style="flex: 1; align-items: center;">
                                <span class="metric-label">Wind Speed</span>
                                <span class="metric-value">${day.wind.speed}<span class="metric-unit">${day.wind.unit}</span></span>
                            </div>
                            <div class="metric" style="flex: 1; align-items: center;">
                                <span class="metric-label">Wind Dir</span>
                                <div class="metric-value">
                                    <span class="direction-arrow wind-arrow" style="transform: rotate(${parseInt(day.wind.direction) + 90}deg)">➤</span>
                                    <span class="direction-text">${day.wind.direction}°</span>
                                </div>
                            </div>
                            <div class="metric tide-metric" style="flex: 1; display: flex; flex-direction: column; align-items: flex-end;">
                                <span class="metric-label" style="text-align: right; width: 100%; font-size: 0.75em;">Tide <span style="font-size: 0.7em;">(High/Low)</span></span>
                                <div class="tide-container" style="display: flex; flex-direction: column; gap: 2px; padding: 4px 0; align-items: flex-end;">
                                    <!-- High Tide -->
                                    <div class="tide-row" style="display: flex; align-items: baseline; gap: 4px;">
                                        <span style="color: #9ca3af; font-size: 0.8em;">▲</span>
                                        <span style="color: #000; font-weight: 700; font-size: 0.9em; line-height: 1;">${day.tide.max.time}</span>
                                        <span class="metric-value" data-type="tide-height" data-val-m="${day.tide.max.height}" style="color: #9ca3af; font-size: 0.65em; font-weight: 500;">
                                            ${formatHeight(day.tide.max.height, currentUnit)}${getHeightUnit(currentUnit)}
                                        </span>
                                    </div>
                                    <!-- Low Tide -->
                                    <div class="tide-row" style="display: flex; align-items: baseline; gap: 4px;">
                                        <span style="color: #000; font-weight: 700; font-size: 0.9em; line-height: 1;">${day.tide.min.time}</span>
                                        <span class="metric-value" data-type="tide-height" data-val-m="${day.tide.min.height}" style="color: #9ca3af; font-size: 0.65em; font-weight: 500;">
                                            ${formatHeight(day.tide.min.height, currentUnit)}${getHeightUnit(currentUnit)}
                                        </span>
                                        <span style="color: #9ca3af; font-size: 0.8em;">▼</span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    
                    <div class="hourly-details" style="display:none; margin-top: 1rem;"></div>
                `;

                // Add click handler to toggle ALL cards at once
                card.addEventListener('click', function (e) {
                    // Don't toggle if clicking on the hourly details themselves
                    if (e.target.closest('.hourly-details')) return;

                    // Get all forecast cards
                    const allCards = document.querySelectorAll('.forecast-card');
                    const heroSection = document.querySelector('.hero');

                    // Check if *this* card is already expanded
                    const isExpanded = card.classList.contains('expanded');

                    if (isExpanded) {
                        // Close all
                        allCards.forEach(c => {
                            c.classList.remove('expanded');
                            const details = c.querySelector('.hourly-details');
                            if (details) details.style.display = 'none';
                        });
                        // Expand hero back to normal
                        if (heroSection) {
                            heroSection.classList.remove('compact');
                            refreshHeroMapFraming();
                        }
                    } else {
                        // Open all
                        allCards.forEach((c) => {
                            c.classList.add('expanded');
                            const details = c.querySelector('.hourly-details');
                            const dateIso = c.dataset.date;
                            const spot = c.dataset.spot;

                            // Only toggle if it's currently hidden
                            if (details && details.style.display !== 'block' && dateIso && spot) {
                                toggleHourlyDetails(c, dateIso, spot);
                            }
                        });
                        // Compact the hero ONLY if map is NOT open
                        const mapOverlay = document.getElementById('mapOverlay');
                        const isMapOpen = mapOverlay && mapOverlay.classList.contains('active');

                        if (heroSection && !isMapOpen) {
                            heroSection.classList.add('compact');
                            refreshHeroMapFraming();
                        }
                    }
                });

                container.appendChild(card);
            });

            // Update scroll indicator after rendering
            setTimeout(() => {
                updateScrollIndicator();
                // Deep link (#slug/date): restore the selected day now that
                // the cards exist (also re-adds /date to the URL).
                if (pendingScrollDate) {
                    window.scrollToForecastDate(pendingScrollDate);
                    pendingScrollDate = null;
                }
            }, 100);
        } catch (e) {
            console.error("Forecast rendering error:", e);
            container.innerHTML = `<div style="text-align:center; width:100%; color:red; margin-top:2rem;">
                    Error loading data: ${e.message} <br><br>
                        Make sure the backend is running on port 8080.
                    </div>`;
            spotNameTitle.textContent = spotName; // revert title
            fitHeroTitle();
        }
    }
});

// Helper to generate the mini hourly bar plot HTML
function generateMiniHourlyPlot(hourlyData, maxH = 3.0) {
    if (!hourlyData || hourlyData.length === 0) return '';

    let barsHtml = '';
    // We want 24 bars, assuming hourlyData matches hours?
    // Hourly data usually has datetime. We can just iterate array.
    hourlyData.forEach(h => {
        // Prefer the backend's direction- and spot-aware color; fall back to
        // the shared engine (now passing swell/wind direction) for older data.
        const windSpd = parseFloat(h.wind.speed) || 0;
        const swellH = parseFloat(h.swell.height) || 0;
        const swellP = parseFloat(h.swell.period) || 0;
        const swellDir = parseFloat(h.swell.direction);
        const windDir = parseFloat(h.wind.direction);
        const rating = (typeof h.color === 'string')
            ? { color: h.color }
            : getRatingAndColor(swellH, swellP, windSpd,
                isNaN(swellDir) ? null : swellDir,
                isNaN(windDir) ? null : windDir);

        // Height percentage (clamp at 100%)
        let pct = (swellH / maxH) * 100;
        if (pct < 10) pct = 10; // Min height
        if (pct > 100) pct = 100;

        barsHtml += `<div class="mini-bar" style="height:${pct}%; background-color:${rating.color};" title="${h.datetime}"></div>`;
    });

    return `<div class="mini-hourly-plot">${barsHtml}</div>`;
}
