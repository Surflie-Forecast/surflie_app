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

    // Initial Load
    let availableSpots = []; // Array of {name, region}

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

        // Determine initial spot
        const initialSpotName = availableSpots.length > 0 ? availableSpots[0].name : "Devereaux Beach";
        renderForecast(initialSpotName);
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
            matches.forEach(match => {
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

    async function renderForecast(spotName) {
        // Update Title
        spotNameTitle.textContent = "Loading...";

        const container = document.getElementById('forecastContainer');
        container.innerHTML = '<div style="text-align:center; width:100%; margin-top:2rem;">Fetching forecast data...</div>';

        try {
            // Fetch from static JSON file
            const response = await fetch(`api/forecast/${encodeURIComponent(spotName)}.json`);
            if (!response.ok) {
                const err = await response.json().catch(() => ({ error: "Failed to fetch data" }));
                throw new Error(err.error || "Failed to fetch data");
            }

            const data = await response.json();
            lastRenderedSpot = data.spotName;

            // Update title with "Name, Region"
            spotNameTitle.textContent = `${data.spotName}, ${data.region}`;

            // Update Hero Background
            const heroSection = document.querySelector('.hero');
            if (data.region && heroSection) {
                // Try to load region image, fallback handled if needed via CSS or error event? 
                // For now assumes images exist as per requirement.
                // Using encodeURIComponent to handle spaces in filenames
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

                card.innerHTML = `
                    <div class="card-header">
                        <span class="day-label">${dayLabel}</span>
                        <span class="date-label">${day.date}</span>
                    </div>
                    
                    <div class="daily-summary">
                        <div class="metric-row main-row">
                            <div class="metric">
                                <span class="metric-label">Wave Height</span>
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

                        <div class="rating-badge" style="background-color: ${day.color}; margin-top: auto; align-self: center; width: 100%;">
                            ${day.rating}
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
                        // Compact the hero
                        if (heroSection) {
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
