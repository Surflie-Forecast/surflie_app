// Data is loaded from mockData.js
// State for units: 'metric' (default) or 'imperial'
let currentUnit = localStorage.getItem('surfForecastUnit') || 'imperial';

// Unit Conversion Helpers
// Unit Conversion Helpers
window.toFeet = (meters) => (meters * 3.28084).toFixed(1);
window.toMeters = (feet) => (feet / 3.28084).toFixed(2);
window.formatHeight = (val, unit) => unit === 'imperial' ? window.toFeet(val) : val;
window.getHeightUnit = (unit) => unit === 'imperial' ? 'ft' : 'm';

window.scrollToForecastDate = (dateIso) => {
    const container = document.getElementById('forecastContainer');
    const card = container.querySelector(`.forecast-card[data-date="${dateIso}"]`);
    if (card) {
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
    let markers = [];

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

        // 2. TOP LAYER: Satellite (Transparent)
        L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
            attribution: 'Esri, USGS',
            opacity: 0.75,
            maxZoom: 17
        }).addTo(map);

        L.control.zoom({
            position: 'bottomright'
        }).addTo(map);
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
                const response = await fetch(`api/hourly/${encodeURIComponent(spot.name)}/${todayISO}.json`);
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

            // Add Popup with placeholders
            const safeName = spot.name.replace(/[^a-zA-Z0-9]/g, '_');
            const popupContent = `
                <div class="popup-wrapper" id="popup-${safeName}">
                    <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 2px; margin-bottom: 4px;">
                        <div class="popup-name" style="margin: 0; line-height: 1.2;">${spot.name}</div>
                        <button class="popup-btn" style="margin: 0; width: auto; padding: 4px 10px; font-size: 0.7rem;" onclick="document.dispatchEvent(new CustomEvent('map-spot-select', { detail: '${spot.name}' }))">
                            View Forecast
                        </button>
                    </div>
                    <div class="popup-dynamic-content" style="min-height: 40px; display: flex; align-items: center; justify-content: center; margin: 4px 0;">
                        <!-- Content injected on open -->
                        <div class="loader" style="font-size: 0.8rem; color: #888;">Loading...</div>
                    </div>
                </div>
            `;

            marker.bindPopup(popupContent, {
                closeButton: false,
                className: 'custom-popup',
                offset: [0, -35], // Position popup above the pin
                autoPanPadding: [30, 30] // Add margin when auto-panning to show popup
            });

            markers.push(marker);
            bounds.extend([spot.latitude, spot.longitude]);

            return marker;
        });

        // Wait for all markers to be created
        await Promise.all(markerPromises);

        // Fit bounds if we have spots
        if (markers.length > 0) {
            map.fitBounds(bounds, { padding: [50, 50] });
        }

        // Add Event Listener for Popup Open (Lazy Load Data)
        map.on('popupopen', async (e) => {
            const marker = e.popup._source;
            if (!marker || !marker.spotData) return;

            const spot = marker.spotData;
            const container = e.popup.getElement();
            // Leaflet popup content wrapper
            const dynamicContainer = container.querySelector('.popup-dynamic-content');

            if (!dynamicContainer || dynamicContainer.dataset.loaded === 'true') return;

            try {
                // 1. Fetch Main Forecast Summary (for ratings, colors, and valid dates)
                const forecastRes = await fetch(`api/forecast/${encodeURIComponent(spot.name)}.json`);
                if (!forecastRes.ok) throw new Error("Forecast unavailable");

                const forecastData = await forecastRes.json();
                const daysToShow = forecastData.forecast.slice(0, 3); // Today + Next 2 days

                if (daysToShow.length === 0) {
                    dynamicContainer.innerHTML = `<div style="font-size: 0.8rem; color: #888;">No data available</div>`;
                    return;
                }

                // 2. Fetch Hourly Data for these days in parallel
                const hourlyPromises = daysToShow.map(day =>
                    fetch(`api/hourly/${encodeURIComponent(spot.name)}/${day.date_iso}.json`)
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
                            <span class="quality-label" style="background-color: ${day.color}; font-size: 0.55rem; padding: 1px 5px; min-width: 45px; text-align: center; border-radius: 4px; line-height: 1.4;">${day.rating}</span>
                        </div>
                    `;
                });

                dynamicContainer.innerHTML = `<div style="display:flex; flex-direction:column; width: 100%;">${rowsHtml}</div>`;
                dynamicContainer.dataset.loaded = 'true';

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
        mapOverlay.classList.add('active');

        // Force hero to expand (be normal size) so map has space
        const hero = document.querySelector('.hero');
        if (hero) hero.classList.remove('compact');

        // Helper to open the specific popup
        const openCurrentSpotPopup = () => {
            if (lastRenderedSpot && markers.length > 0) {
                const targetMarker = markers.find(m => m.spotData && m.spotData.name === lastRenderedSpot);
                if (targetMarker) {
                    // Slight delay to ensure map animation/sizing is done
                    setTimeout(() => {
                        targetMarker.openPopup();
                        // Optional: Center map on marker if needed
                        // map.panTo(targetMarker.getLatLng()); 
                    }, 200);
                }
            }
        };

        // Initialize map if needed (lazy load)
        if (!map) {
            initMap();
            // Tiny delay to ensure container has size before fitting bounds
            setTimeout(async () => {
                map.invalidateSize();
                await populateMapMarkers();
                openCurrentSpotPopup();
            }, 100);
        } else {
            // Just refresh size in case window changed
            setTimeout(() => {
                map.invalidateSize();
                openCurrentSpotPopup();
            }, 300); // Wait for transition
        }
    }

    function closeMap() {
        mapOverlay.classList.remove('active');

        // Force close any open popup so that the 'popupopen' event 
        // fires reliably the next time the map is opened.
        if (map) {
            map.closePopup();
        }

        // Restore hero state logic
        // If cards are expanded, we should return to compact hero
        const anyCardExpanded = document.querySelector('.forecast-card.expanded');
        const hero = document.querySelector('.hero');

        if (hero && anyCardExpanded) {
            hero.classList.add('compact');
        }
    }

    // Bind Buttons
    if (mapBtn) mapBtn.addEventListener('click', openMap);
    if (closeMapBtn) closeMapBtn.addEventListener('click', closeMap);

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
            });
        });

        try {
            // Fetch available spots from static JSON
            const spotsRes = await fetch('api/spots.json');
            if (spotsRes.ok) {
                availableSpots = await spotsRes.json();
            } else {
                console.error("Failed to fetch spots list");
            }
        } catch (e) {
            console.error("Error fetching spots:", e);
        }

        // Determine initial spot (Default)
        // 1. Render Default immediately so user sees something
        let initialSpotName = availableSpots.length > 0 ? availableSpots[0].name : "Devereaux Beach";
        renderForecast(initialSpotName);

        // 2. Start Location Checks in parallel (IP first, then Geo)
        // We use a "confidence" level to ensure we don't downgrade.
        // 0 = Default, 1 = IP, 2 = Geo
        // 0 = Default, 1 = IP, 2 = Geo
        let currentConfidence = 0;

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

        // Fire and forget (don't await them)
        checkIP();
        checkGeo();
    }


    let lastRenderedSpot = "";

    init();

    // Search logic
    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.toLowerCase();
        searchResults.innerHTML = '';

        if (!query) {
            searchResults.style.display = 'none';
            return;
        }

        // Filter by name OR region
        const matches = availableSpots.filter(spot =>
            spot.name.toLowerCase().includes(query) ||
            spot.region.toLowerCase().includes(query)
        );

        if (matches.length > 0) {
            searchResults.style.display = 'block';

            // Show only first 5
            const limit = 5;
            const displayMatches = matches.slice(0, limit);

            displayMatches.forEach(match => {
                const item = document.createElement('div');
                item.className = 'search-result-item';
                // Display: Name, Region
                item.textContent = `${match.name}, ${match.region}`;
                item.onclick = () => {
                    renderForecast(match.name);
                    searchInput.value = '';
                    searchResults.style.display = 'none';
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
                // Prevent clicking "more" from doing anything or maybe it should act as "keep typing"?
                // User requirement: "display a label of more"
                searchResults.appendChild(moreItem);
            }

        } else {
            searchResults.style.display = 'none';
        }
    });

    // Trigger search on Enter key
    searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            const query = searchInput.value.toLowerCase();
            const match = availableSpots.find(spot =>
                spot.name.toLowerCase().includes(query) ||
                spot.region.toLowerCase().includes(query)
            );
            if (match) {
                renderForecast(match.name);
                searchInput.value = '';
                searchResults.style.display = 'none';
            }
        }
    });

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

    function updateChartHighlight() {
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

            container.scrollTo({
                left: targetScroll,
                behavior: 'smooth'
            });
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
            const response = await fetch(`api/forecast/${encodeURIComponent(spotName)}.json`);

            // Check if superseded
            if (renderId !== currentRenderId) return;

            if (!response.ok) {
                const err = await response.json().catch(() => ({ error: "Failed to fetch data" }));
                throw new Error(err.error || "Failed to fetch data");
            }

            const data = await response.json();

            // Check if superseded
            if (renderId !== currentRenderId) return;

            lastRenderedSpot = data.spotName;

            // Update title with "Name, Region"
            spotNameTitle.textContent = `${data.spotName}, ${data.region}`;

            // Update Hero Background
            const heroSection = document.querySelector('.hero');
            if (data.region && heroSection) {
                heroSection.style.backgroundImage = `url('images/${data.region}.jpg')`;
                heroSection.style.backgroundSize = 'cover';
                heroSection.style.backgroundPosition = 'center';
            }

            container.innerHTML = ''; // Clear loading

            if (!data.forecast || data.forecast.length === 0) {
                container.innerHTML = `<div style="text-align:center; width:100%;">No wave data found for "${spotName}"</div>`;
                return;
            }

            // Render Chart
            if (window.renderWaveHeightChart) {
                renderWaveHeightChart(spotName, data.forecast).then(() => {
                    // Trigger highlight update after chart is fully rendered
                    updateChartHighlight();


                });
            }

            // Create a promise array to fetch all hourly data
            const hourlyPromises = data.forecast.map(async (day) => {
                try {
                    const res = await fetch(`api/hourly/${encodeURIComponent(spotName)}/${day.date_iso}.json`);
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
                            <span class="quality-label" style="background-color: ${day.color};">${day.rating}</span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 4px;">
                            <span class="date-label">${day.date}</span>
                            <span class="expand-icon">▼</span>
                        </div>
                    </div>
                    
                    <div class="daily-summary">
                        <div class="metric-row main-row">
                            <div class="metric">
                                <span class="metric-label">Swell Height</span>
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
                        }
                    }
                });

                container.appendChild(card);
            });

            // Update scroll indicator after rendering
            setTimeout(() => {
                updateScrollIndicator();
            }, 100);
        } catch (e) {
            console.error("Forecast rendering error:", e);
            container.innerHTML = `<div style="text-align:center; width:100%; color:red; margin-top:2rem;">
                    Error loading data: ${e.message} <br><br>
                        Make sure the backend is running on port 8080.
                    </div>`;
            spotNameTitle.textContent = spotName; // revert title
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
        // Calculate Rating Color using shared logic
        const windSpd = parseFloat(h.wind.speed) || 0;
        const swellH = parseFloat(h.swell.height) || 0;
        const swellP = parseFloat(h.swell.period) || 0;
        const rating = getRatingAndColor(swellH, swellP, windSpd);

        // Height percentage (clamp at 100%)
        let pct = (swellH / maxH) * 100;
        if (pct < 10) pct = 10; // Min height
        if (pct > 100) pct = 100;

        barsHtml += `<div class="mini-bar" style="height:${pct}%; background-color:${rating.color};" title="${h.datetime}"></div>`;
    });

    return `<div class="mini-hourly-plot">${barsHtml}</div>`;
}
