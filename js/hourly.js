// Toggle hourly details for a forecast card
async function toggleHourlyDetails(card, dateISO, spotName) {
    const detailsDiv = card.querySelector('.hourly-details');

    // If already open, close it
    if (detailsDiv.style.display === 'block') {
        detailsDiv.style.display = 'none';
        return;
    }

    // Show loading
    detailsDiv.style.display = 'block';
    detailsDiv.innerHTML = '<div style="text-align:center; padding:1rem;">Loading hourly data...</div>';

    try {
        // Fetch from static JSON file organized by spot and date
        const response = await fetch(`api/hourly/${encodeURIComponent(spotName)}/${dateISO}.json`);
        if (!response.ok) {
            throw new Error('Failed to load hourly data');
        }

        const hourlyData = await response.json();

        if (!hourlyData || hourlyData.length === 0) {
            detailsDiv.innerHTML = '<div style="text-align:center; padding:1rem;">No hourly data available</div>';
            return;
        }

        // Build hourly view
        let html = '<div class="hourly-container">';
        html += '<div class="hourly-header"><span>Time</span><span></span><span>Swell</span><span>Wind</span></div>';

        hourlyData.forEach(hour => {
            const timeObj = new Date(hour.datetime);
            const timeHour = timeObj.toLocaleTimeString('en-US', { hour: 'numeric', hour12: true }).split(' ')[0];
            const timePeriod = timeObj.toLocaleTimeString('en-US', { hour: 'numeric', hour12: true }).split(' ')[1];
            const swellHeight = formatHeight(hour.swell.height, currentUnit);
            const swellPeriod = Math.round(hour.swell.period);
            const swellDir = Math.round(hour.swell.direction);
            const windSpeed = Math.round(hour.wind.speed);
            const windDir = Math.round(hour.wind.direction);

            // Calculate rating color
            const height = hour.swell.height;
            const period = hour.swell.period;
            const rating_val = height * period;

            let color = '#ef4444'; // Red - Poor
            if (height > 2.5) {
                color = '#a855f7'; // Purple - Epic
            } else if (rating_val > 18) {
                color = '#15803d'; // Dark Green - Excellent
            } else if (rating_val > 12) {
                color = '#22c55e'; // Green - Good
            } else if (rating_val > 6) {
                color = '#eab308'; // Yellow - Fair
            }

            html += `
                <div class="hourly-row">
                    <div class="hourly-time">
                        <span class="time-hour">${timeHour}</span>
                        <span class="time-period">${timePeriod}</span>
                    </div>
                    <div class="hourly-rating-bar" style="background-color: ${color};"></div>
                    <div class="hourly-swell">
                        <span class="hourly-swell-val" data-val-m="${hour.swell.height}">${swellHeight}${getHeightUnit(currentUnit)}</span>
                        <span>${swellPeriod}s</span>
                        <div class="direction-indicator">
                            <span class="swell-arrow-small" style="transform: rotate(${swellDir + 90}deg)">➤</span>
                            <span class="direction-degrees">${swellDir}°</span>
                        </div>
                    </div>
                    <div class="hourly-wind">
                        <span>${windSpeed}kts</span>
                        <div class="direction-indicator">
                            <span class="wind-arrow-small" style="transform: rotate(${windDir + 90}deg)">➤</span>
                            <span class="direction-degrees">${windDir}°</span>
                        </div>
                    </div>
                </div>
            `;
        });

        html += '</div>';
        detailsDiv.innerHTML = html;

    } catch (error) {
        console.error('Error loading hourly data:', error);
        detailsDiv.innerHTML = '<div style="text-align:center; padding:1rem; color:red;">Error loading hourly data</div>';
    }
}
