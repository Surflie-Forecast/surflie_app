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

    // Find max wave height for scaling (Scale to 1.2x max)
    let maxVal = 0;
    hourlyData.forEach(h => {
        const val = parseFloat(h.swell.height);
        if (val > maxVal) maxVal = val;
    });
    const scaleMax = (maxVal > 0 ? maxVal : 1) * 1.2;

    // Track the date of the previous iteration
    let lastDateStr = null;

    hourlyData.forEach((hour, index) => {
        const valMeters = parseFloat(hour.swell.height);
        const heightPct = (valMeters / scaleMax) * 100;

        const dateObj = new Date(hour.datetime);

        // Create a string ID for the day (e.g., "12") to compare changes
        const currentDayNum = dateObj.getDate();
        const isNewDay = lastDateStr !== currentDayNum;

        // Update tracker
        lastDateStr = currentDayNum;

        const wrapper = document.createElement('div');
        wrapper.className = 'chart-bar-wrapper';
        wrapper.style.position = 'relative';

        const bar = document.createElement('div');
        bar.className = 'chart-bar';
        bar.style.height = `${Math.max(heightPct, 5)}%`;

        const formattedVal = formatHeight(valMeters, currentUnit);
        const unit = getHeightUnit(currentUnit);
        const timeStr = dateObj.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true });
        const period = Math.round(parseFloat(hour.swell.period));

        const label = document.createElement('div');
        label.className = 'bar-label';
        label.innerHTML = `<div style="text-align:center; line-height:1.3;">
            <div style="font-size:0.65rem; opacity:0.8;">${timeStr}</div>
            <div style="font-weight:700;">${formattedVal}${unit}  ${period}s</div>
        </div>`;

        wrapper.appendChild(label);
        wrapper.appendChild(bar);
        chartContainer.appendChild(wrapper);

        // --- RENDER DATE LOGIC ---
        // If the date number changed since the last loop, OR it's the very first item
        if (isNewDay || index === 0) {

            // 1. Add visual separator line
            wrapper.style.borderLeft = '1px dashed rgba(0,0,0,0.15)';

            // 2. Create the Date Label
            const timeLabel = document.createElement('div');
            timeLabel.className = 'axis-label';

            // Format: "Mon 12"
            timeLabel.textContent = dateObj.toLocaleDateString('en-US', { weekday: 'short', day: 'numeric' });

            // Position at TOP
            timeLabel.style.position = 'absolute';
            timeLabel.style.top = '0px';
            timeLabel.style.left = '6px';
            timeLabel.style.whiteSpace = 'nowrap';
            timeLabel.style.fontSize = '0.75rem';
            timeLabel.style.fontWeight = 'bold';
            timeLabel.style.color = 'var(--text-secondary)';

            wrapper.appendChild(timeLabel);
        }
    });
}

window.renderWaveHeightChart = renderWaveHeightChart;