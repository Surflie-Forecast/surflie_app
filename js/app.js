// Data is loaded from mockData.js
// State for units: 'metric' (default) or 'imperial'
let currentUnit = localStorage.getItem('surfForecastUnit') || 'metric';

// Unit Conversion Helpers
const toFeet = (meters) => (meters * 3.28084).toFixed(1);
const toMeters = (feet) => (feet / 3.28084).toFixed(2); // Not really used but good for symmetry
const formatHeight = (val, unit) => unit === 'imperial' ? toFeet(val) : val;
const getHeightUnit = (unit) => unit === 'imperial' ? 'ft' : 'm';

// Function to update all displayed units in DOM without re-rendering
function updateDisplayedUnits() {
    // 1. Update Swell Heights (Main Cards)
    document.querySelectorAll('.metric-value[data-type="swell-height"]').forEach(el => {
        const valM = parseFloat(el.dataset.valM);
        if (!isNaN(valM)) {
            el.innerHTML = `${formatHeight(valM, currentUnit)}<span class="metric-unit">${getHeightUnit(currentUnit)}</span>`;
        }
    });

    // 2. Update Tide Heights (Main Cards)
    document.querySelectorAll('.metric-value[data-type="tide-height"]').forEach(el => {
        const valM = parseFloat(el.dataset.valM);
        if (!isNaN(valM)) {
            // Tide often has "m" in the string if we didn't strip it, but here we rebuild it
            el.innerHTML = `${formatHeight(valM, currentUnit)}${getHeightUnit(currentUnit)}`;
        }
    });

    // 3. Update Hourly Swell Heights (if any are open)
    // We need to look for elements in hourly details that we will mark
    document.querySelectorAll('.hourly-swell-val').forEach(el => {
        const valM = parseFloat(el.dataset.valM);
        if (!isNaN(valM)) {
            el.textContent = `${formatHeight(valM, currentUnit)}${getHeightUnit(currentUnit)}`;
        }
    });
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

            data.forecast.forEach((day, index) => {
                const card = document.createElement('div');
                card.className = 'forecast-card';
                // Stagger animation delay
                card.style.animationDelay = `${0.2 + (index * 0.1)}s`;
                card.style.cursor = 'pointer';

                // Store data for hourly view
                card.dataset.date = day.date_iso;  // We'll need to pass this from backend
                card.dataset.spot = data.spotName;

                card.innerHTML = `
                    <div class="card-header">
                        <span class="day-label">${day.day}</span>
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
                        <div class="metric">
                            <span class="metric-label">Tide</span>
                            <span class="metric-value" data-type="tide-height" data-val-m="${parseFloat(day.tide.height)}">
                                ${formatHeight(parseFloat(day.tide.height), currentUnit)}${getHeightUnit(currentUnit)}
                            </span>
                        </div>
                    </div>

                    <div class="rating-badge" style="background-color: ${day.color}; margin-top: auto; align-self: center; width: 100%;">
                        ${day.rating}
                    </div>
                    
                    <div class="hourly-details" style="display:none; margin-top: 1rem;"></div>
                `;

                // Add click handler
                card.addEventListener('click', function (e) {
                    // Don't toggle if clicking on the hourly details themselves
                    if (e.target.closest('.hourly-details')) return;

                    toggleHourlyDetails(card, day.date_iso, data.spotName);
                });

                container.appendChild(card);
            });
        } catch (e) {
            console.error("Forecast rendering error:", e);
            container.innerHTML = `<div style="text-align:center; width:100%; color:red; margin-top:2rem;">
                    Error loading data: ${e.message} <br><br>
                        Make sure the backend is running on port 5000.
                    </div>`;
            spotNameTitle.textContent = spotName; // revert title
        }
    }
});
