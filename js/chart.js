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

    // Create or select Global Tooltip
    let tooltip = document.getElementById('globalChartTooltip');
    if (!tooltip) {
        tooltip = document.createElement('div');
        tooltip.id = 'globalChartTooltip';
        tooltip.className = 'global-chart-tooltip';
        document.body.appendChild(tooltip);
    }

    // Hide tooltip on scroll
    // Remove existing listener to prevent duplicates if renderChart is called multiple times
    chartContainer.removeEventListener('scroll', hideTooltip);
    chartContainer.addEventListener('scroll', hideTooltip);

    function hideTooltip() {
        if (tooltip) tooltip.classList.remove('visible');
    }

    // Create Highlight Overlay (Do this BEFORE bars so it is behind them in DOM order if z-index matches, but we set z-index high)
    const overlay = document.createElement('div');
    overlay.id = 'chartHighlightOverlay';
    overlay.className = 'chart-highlight-overlay';
    chartContainer.appendChild(overlay);

    // Determine max height for scaling
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
        // Use SourceDate if available for consistency
        const dateIso = hour._sourceDate || dateObj.toISOString().split('T')[0];

        // Create a string ID for the day (e.g., "12") to compare changes
        const currentDayNum = dateObj.getDate();
        const isNewDay = lastDateStr !== currentDayNum;

        // Update tracker
        lastDateStr = currentDayNum;

        const wrapper = document.createElement('div');
        wrapper.className = 'chart-bar-wrapper';
        wrapper.style.position = 'relative';

        // CRITICAL FOR HIGHLIGHT: Tag wrapper with date so app.js can find start/end
        wrapper.dataset.date = dateIso;
        if (isNewDay || index === 0) wrapper.classList.add('day-start');

        const bar = document.createElement('div');
        bar.className = 'chart-bar';
        bar.style.height = `${Math.max(heightPct, 5)}%`;

        const formattedVal = formatHeight(valMeters, currentUnit);
        const unit = getHeightUnit(currentUnit);
        const timeStr = dateObj.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: true });
        const period = Math.round(parseFloat(hour.swell.period));

        // Store data for tooltip
        bar.dataset.time = timeStr;
        bar.dataset.valM = valMeters;
        bar.dataset.period = period;

        // Add Interactions for Global Tooltip
        bar.addEventListener('mouseenter', (e) => {
            const rect = bar.getBoundingClientRect();

            // Re-fetch current unit logic
            const currentVal = formatHeight(parseFloat(bar.dataset.valM), currentUnit);
            const currentU = getHeightUnit(currentUnit);

            tooltip.innerHTML = `<span style="font-size: 0.9rem; font-weight: 700;">${timeStr} &bull; ${currentVal}${currentU} ${period}s</span>`;

            // Position
            const centerX = rect.left + (rect.width / 2);
            const topY = rect.top;

            tooltip.style.left = `${centerX}px`;
            tooltip.style.top = `${topY}px`;

            // Edge Detection
            const tooltipRect = tooltip.getBoundingClientRect();
            const viewportWidth = window.innerWidth;

            if (tooltipRect.left < 10) {
                tooltip.style.transform = 'translate(0, -100%)';
                tooltip.style.left = '10px';
            } else if (tooltipRect.right > viewportWidth - 10) {
                tooltip.style.transform = 'translate(-100%, -100%)';
                tooltip.style.left = `${viewportWidth - 10}px`;
            } else {
                tooltip.style.transform = 'translate(-50%, -100%)';
            }

            tooltip.classList.add('visible');
        });

        bar.addEventListener('mouseleave', () => {
            tooltip.classList.remove('visible');
        });

        // Touch support
        bar.addEventListener('touchstart', (e) => {
            const event = new MouseEvent('mouseenter');
            bar.dispatchEvent(event);
        }, { passive: true });


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

    // Ensure we start at the beginning (left)
    chartContainer.scrollLeft = 0;
}