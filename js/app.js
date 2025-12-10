// Data is loaded from mockData.js
// State for units: 'metric' (default) or 'imperial'
let currentUnit = localStorage.getItem('surfForecastUnit') || 'imperial';

// Unit Conversion Helpers
const toFeet = (meters) => (meters * 3.28084).toFixed(1);
const toMeters = (feet) => (feet / 3.28084).toFixed(2); // Not really used but good for symmetry
const formatHeight = (val, unit) => unit === 'imperial' ? toFeet(val) : val;
const getHeightUnit = (unit) => unit === 'imperial' ? 'ft' : 'm';

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
    dateElement.textContent = today.toLocaleDateString('en-US', {
        weekday: 'long',
        year: 'numeric',
        month: 'long',
        day: 'numeric'
    });

    const searchInput = document.getElementById('spotSearch');
    const searchBtn = document.getElementById('searchBtn');
    const searchResults = document.getElementById('searchResults');
    const spotNameTitle = document.getElementById('spotName');

    // Initial Load
    let availableSpots = []; // Array of {name, region}

    async function init() {
        // Setup Toggle Switch
        const unitToggle = document.getElementById('unitToggle');
        // If currentUnit is 'imperial', check the box
        unitToggle.checked = currentUnit === 'imperial';

        unitToggle.addEventListener('change', (e) => {
            currentUnit = e.target.checked ? 'imperial' : 'metric';
            localStorage.setItem('surfForecastUnit', currentUnit);
            updateDisplayedUnits();
        });

        try {
            // Fetch available spots from dynamic API
            const spotsRes = await fetch('/api/spots');
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

    async function renderForecast(spotName) {
        // Update Title
        spotNameTitle.textContent = "Loading...";

        const container = document.getElementById('forecastContainer');
        container.innerHTML = '<div style="text-align:center; width:100%; margin-top:2rem;">Fetching forecast data...</div>';

        try {
            // Fetch from Dynamic Backend API
            const response = await fetch(`/api/forecast?spot=${encodeURIComponent(spotName)}`);
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
                        <div class="metric">
                            <span class="metric-label">Direction</span>
                            <div class="metric-value">
                                <span class="swell-arrow" style="transform: rotate(${parseInt(day.swell.direction) + 90}deg)">➤</span>
                                <span class="direction-text">${day.swell.direction}°</span>
                            </div>
                        </div>
                    </div>

                    <div class="metric-row" style="padding-top: 1rem;">
                        <div class="metric">
                            <span class="metric-label">Wind Speed</span>
                            <span class="metric-value">${day.wind.speed}<span class="metric-unit">${day.wind.unit}</span></span>
                        </div>
                         <div class="metric">
                            <span class="metric-label">Wind Dir</span>
                             <div class="metric-value">
                                <span class="direction-arrow wind-arrow" style="transform: rotate(${parseInt(day.wind.direction) + 90}deg)">➤</span>
                                <span class="direction-text">${day.wind.direction}°</span>
                            </div>
                        </div>
                        <div class="metric tide-metric" style="flex: 1.2;">
                            <span class="metric-label">Tide (High / Low)</span>
                            <div class="tide-container" style="display: flex; flex-direction: column; gap: 6px; background: rgba(255,255,255,0.05); padding: 6px 8px; border-radius: 8px;">
                                <!-- High Tide -->
                                <div class="tide-row" style="display: flex; justify-content: space-between; align-items: center; line-height: 1;">
                                    <div style="display:flex; align-items:center; gap:4px;">
                                        <span style="color: #6ee7b7; font-size: 0.8em;">▲</span>
                                        <span class="metric-value" data-type="tide-height" data-val-m="${day.tide.max.height}" style="font-weight:600;">
                                            ${formatHeight(day.tide.max.height, currentUnit)}${getHeightUnit(currentUnit)}
                                        </span>
                                    </div>
                                    <span style="font-size: 0.85em; opacity: 0.8; font-family: monospace;">${day.tide.max.time}</span>
                                </div>
                                <!-- Divider -->
                                <div style="height:1px; background: rgba(255,255,255,0.1);"></div>
                                <!-- Low Tide -->
                                <div class="tide-row" style="display: flex; justify-content: space-between; align-items: center; line-height: 1;">
                                    <div style="display:flex; align-items:center; gap:4px;">
                                        <span style="color: #fca5a5; font-size: 0.8em;">▼</span>
                                        <span class="metric-value" data-type="tide-height" data-val-m="${day.tide.min.height}" style="font-weight:600;">
                                            ${formatHeight(day.tide.min.height, currentUnit)}${getHeightUnit(currentUnit)}
                                        </span>
                                    </div>
                                    <span style="font-size: 0.85em; opacity: 0.8; font-family: monospace;">${day.tide.min.time}</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div class="rating-badge" style="background-color: ${day.color}; margin-top: auto; align-self: center; width: 100%;">
                        ${day.rating}
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

                    // Check if any cards are currently open
                    let anyOpen = false;
                    allCards.forEach(c => {
                        const details = c.querySelector('.hourly-details');
                        if (details && details.style.display === 'block') {
                            anyOpen = true;
                        }
                    });

                    // Toggle: if any are open, close all. Otherwise, open all.
                    if (anyOpen) {
                        // Close all
                        allCards.forEach(c => {
                            const details = c.querySelector('.hourly-details');
                            if (details) {
                                details.style.display = 'none';
                            }
                        });
                        // Expand hero back to normal
                        if (heroSection) {
                            heroSection.classList.remove('compact');
                        }
                    } else {
                        // Open all
                        allCards.forEach((c, idx) => {
                            const dateIso = c.dataset.date;
                            const spot = c.dataset.spot;
                            if (dateIso && spot) {
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
