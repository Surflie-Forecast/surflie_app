/*
 * Wave Height Bar Chart Implementation
 * Fetches hourly data for all forecast days and renders a bar chart
 */

async function renderWaveHeightChart(spotName, forecastDays) {
    const chartSection = document.getElementById('waveChartSection');
    const chartContainer = document.getElementById('waveChartContainer');
    const labelsContainer = document.getElementById('waveChartLabels'); // Optional

    if (!chartSection || !chartContainer) return;

    // Show loading state
    chartSection.style.display = 'block';
    chartContainer.innerHTML = '<div style="width:100%; text-align:center; padding-top: 50px; color: var(--text-secondary);">Loading chart data...</div>';
    if (labelsContainer) labelsContainer.innerHTML = '';

    try {
        // Collect all hourly data promises
        const promises = forecastDays.map(day => {
            return fetch(`api/hourly/${encodeURIComponent(spotName)}/${day.date_iso}.json`)
                .then(res => {
                    if (!res.ok) return null;
                    return res.json();
                })
                .then(data => {
                    if (Array.isArray(data)) {
                        return data.map(h => ({ ...h, _sourceDate: day.date_iso }));
                    }
                    return null;
                })
                .catch(err => null);
        });

        const results = await Promise.all(promises);

        // Flatten data
        let allHours = [];
        results.forEach(dayData => {
            if (dayData && Array.isArray(dayData)) {
                allHours = allHours.concat(dayData);
            }
        });

        if (allHours.length === 0) {
            chartContainer.innerHTML = '<div style="width:100%; text-align:center; padding-top: 50px;">No hourly data available for chart</div>';
            return;
        }

        renderChart(allHours);

    } catch (error) {
        console.error("Error rendering chart:", error);
        chartContainer.innerHTML = '<div style="width:100%; text-align:center; padding-top: 50px; color:red;">Failed to load chart</div>';
    }
}

function renderChart(hourlyData) {
    const chartContainer = document.getElementById('waveChartContainer');
    chartContainer.innerHTML = '';

    // Create Highlight Overlay
    const overlay = document.createElement('div');
    overlay.id = 'chartHighlightOverlay';
    overlay.className = 'chart-highlight-overlay';
    chartContainer.appendChild(overlay);

    // Find max wave height for scaling (Scale to 1.2x max)
    let maxVal = 0;
    hourlyData.forEach(h => {
        const val = parseFloat(h.swell.height);
        if (val > maxVal) maxVal = val;
    });
    const scaleMax = (maxVal > 0 ? maxVal : 1) * 1.2;

    // Group data by day
    const days = {};
    hourlyData.forEach(hour => {
        const dateStr = hour._sourceDate || new Date(hour.datetime).toISOString().split('T')[0];
        if (!days[dateStr]) days[dateStr] = [];
        days[dateStr].push(hour);
    });

    Object.keys(days).sort().forEach((dateKey, dayIndex) => {
        const dayData = days[dateKey];
        const dateObj = new Date(dayData[0].datetime);

        // Create Day Group Container
        const dayGroup = document.createElement('div');
        dayGroup.className = 'chart-day-group';
        dayGroup.dataset.date = dateKey; // format YYYY-MM-DD

        // Add Day Label inside the group (at the top)
        const timeLabel = document.createElement('div');
        timeLabel.className = 'axis-label';
        // Format: "Mon 12"
        timeLabel.textContent = dateObj.toLocaleDateString('en-US', { weekday: 'short', day: 'numeric' });
        dayGroup.appendChild(timeLabel);

        dayData.forEach((hour, index) => {
            const valMeters = parseFloat(hour.swell.height);
            const heightPct = (valMeters / scaleMax) * 100;

            const wrapper = document.createElement('div');
            wrapper.className = 'chart-bar-wrapper';

            const bar = document.createElement('div');
            bar.className = 'chart-bar';
            bar.style.height = `${Math.max(heightPct, 5)}%`;

            const formattedVal = formatHeight(valMeters, currentUnit);
            const unit = getHeightUnit(currentUnit);
            const timeStr = new Date(hour.datetime).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true });
            const period = Math.round(parseFloat(hour.swell.period));

            const label = document.createElement('div');
            label.className = 'bar-label';
            label.dataset.valM = valMeters; // Store meters for unit conversion
            label.dataset.period = period; // Store period
            label.dataset.time = timeStr; // Store time
            label.innerHTML = `<div style="text-align:center; line-height:1.3;">
                <div style="font-size:0.65rem; opacity:0.8;">${timeStr}</div>
                <div style="font-weight:700;" class="chart-height-value">${formattedVal}${unit}  ${period}s</div>
            </div>`;

            wrapper.appendChild(label);
            wrapper.appendChild(bar);
            dayGroup.appendChild(wrapper);
        });

        chartContainer.appendChild(dayGroup);
    });

    // Ensure we start at the beginning (left)
    chartContainer.scrollLeft = 0;
}

window.renderWaveHeightChart = renderWaveHeightChart;