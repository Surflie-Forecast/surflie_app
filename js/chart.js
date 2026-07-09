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
            return fetch(`api/hourly/${apiSpotId(spotName)}/${day.date_iso}.json`)
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
            setupModelCompare(null);
            return;
        }

        renderChart(allHours);
        setupModelCompare(spotName);

    } catch (error) {
        console.error("Error rendering chart:", error);
        chartContainer.innerHTML = '<div style="width:100%; text-align:center; padding-top: 50px; color:red;">Failed to load chart</div>';
        setupModelCompare(null);
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

        // Click to scroll to card
        wrapper.addEventListener('click', () => {
            if (window.scrollToForecastDate) {
                window.scrollToForecastDate(dateIso);
            }
        });


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

    // Expose data the model-comparison overlay needs to share the bars'
    // exact time axis and height scale
    chartContainer._hourlyData = hourlyData;
    chartContainer._scaleMax = scaleMax;
}

// --- Model Comparison Overlay ---
// Overlays every wave model's combined Hs as lines on the bar chart,
// sharing the bars' y-scale and time axis (bars = default model).

const MODEL_COLORS = {
    gfswave025: '#131929',
    mfwam: '#e11d48',
    ecmwf_wam9: '#7c3aed',
    ecmwf_wam25: '#c084fc',
    gwam: '#10b981',
};

function setupModelCompare(spotName) {
    const btn = document.getElementById('modelCompareBtn');
    if (!btn) return;

    clearModelOverlay();

    if (!spotName) {
        btn.style.display = 'none';
        return;
    }

    btn.style.display = 'inline-flex';
    btn.dataset.spot = spotName;

    if (!btn._bound) {
        btn._bound = true;
        btn.addEventListener('click', toggleModelOverlay);
        window.addEventListener('resize', () => {
            clearTimeout(window._modelResizeTimer);
            window._modelResizeTimer = setTimeout(() => {
                const container = document.getElementById('waveChartContainer');
                if (container && container._modelData) {
                    drawModelOverlay(container._modelData);
                }
            }, 200);
        });
    }
}

function clearModelOverlay() {
    const container = document.getElementById('waveChartContainer');
    const btn = document.getElementById('modelCompareBtn');
    const legend = document.getElementById('modelLegend');

    if (container) {
        container._modelData = null;
        const svg = container.querySelector('.model-overlay-svg');
        if (svg) svg.remove();
    }
    if (btn) btn.classList.remove('active');
    if (legend) {
        legend.classList.remove('visible');
        legend.innerHTML = '';
    }
}

async function toggleModelOverlay() {
    const btn = document.getElementById('modelCompareBtn');
    const container = document.getElementById('waveChartContainer');
    if (!btn || !container) return;

    if (container._modelData) {
        clearModelOverlay();
        return;
    }

    try {
        const res = await fetch(`api/models/${apiSpotId(btn.dataset.spot)}.json`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!data.models || data.models.length === 0) throw new Error('no models');

        container._modelData = data;
        drawModelOverlay(data);
        btn.classList.add('active');
    } catch (e) {
        console.warn('Model comparison unavailable:', e);
        btn.disabled = true;
        setTimeout(() => { btn.disabled = false; }, 3000);
    }
}

function drawModelOverlay(data) {
    const container = document.getElementById('waveChartContainer');
    const legend = document.getElementById('modelLegend');
    if (!container || !container._hourlyData || !container._scaleMax) return;

    const old = container.querySelector('.model-overlay-svg');
    if (old) old.remove();

    const wrappers = container.querySelectorAll('.chart-bar-wrapper');
    if (wrappers.length === 0) return;

    // Bar timeline: timestamps (ms) and x centers, in the same offset
    // coordinate space the highlight overlay uses
    const hours = container._hourlyData;
    const times = hours.map(h => new Date(h.datetime.replace(' ', 'T')).getTime());
    const centers = Array.from(wrappers, w => w.offsetLeft + w.offsetWidth / 2);
    const n = Math.min(times.length, centers.length);

    const top = wrappers[0].offsetTop;
    const barH = wrappers[0].offsetHeight;
    const scaleMax = container._scaleMax;
    const yFor = v => Math.max(top + 1, top + barH * (1 - v / scaleMax));

    const xFor = (t) => {
        if (t < times[0] || t > times[n - 1]) return null;
        let j = times.findIndex((ts, i) => i + 1 < n && t >= ts && t <= times[i + 1]);
        if (t === times[n - 1]) j = n - 2;
        if (j < 0) return null;
        const frac = (t - times[j]) / (times[j + 1] - times[j]);
        return centers[j] + frac * (centers[j + 1] - centers[j]);
    };

    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('class', 'model-overlay-svg');
    svg.setAttribute('width', container.scrollWidth);
    svg.setAttribute('height', top + barH);

    data.models.forEach(model => {
        let d = '';
        model.series.forEach(pt => {
            const t = new Date(pt.t.replace(' ', 'T')).getTime();
            const x = xFor(t);
            if (x === null) return;
            const y = yFor(pt.v);
            d += (d ? ' L ' : 'M ') + x.toFixed(1) + ' ' + y.toFixed(1);
        });
        if (!d) return;

        const path = document.createElementNS(ns, 'path');
        path.setAttribute('d', d);
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', MODEL_COLORS[model.id] || '#64748b');
        path.setAttribute('stroke-width', model.default ? 2.5 : 1.5);
        path.setAttribute('stroke-linejoin', 'round');
        if (!model.default) path.setAttribute('opacity', '0.85');
        svg.appendChild(path);
    });

    container.appendChild(svg);

    if (legend) {
        legend.innerHTML = data.models.map(m =>
            `<span class="legend-chip"><i style="background:${MODEL_COLORS[m.id] || '#64748b'}"></i>${m.label}${m.default ? ' •' : ''}</span>`
        ).join('');
        legend.classList.add('visible');
    }
}

// --- Tide Chart Implementation ---

async function renderTideChart(spotName) {
    const container = document.getElementById('tideChartContainer');
    const waveContainer = document.getElementById('waveChartContainer');
    const section = document.getElementById('tideChartSection');

    if (!container || !waveContainer || !section) return;

    // Show section
    section.style.display = 'block';
    container.innerHTML = '<div style="width:100%; text-align:center; padding-top: 50px; color: var(--text-secondary);">Loading tide data...</div>';

    try {
        const res = await fetch(`api/tide/${apiSpotId(spotName)}.json`);
        if (!res.ok) {
            container.innerHTML = '';
            section.style.display = 'none'; // Hide if no data
            return;
        }

        const json = await res.json();
        const data = json.dataset || [];
        const peaks = json.peaks || [];

        if (!data || data.length === 0) {
            container.innerHTML = '<div style="padding:20px; text-align:center;">No tide data available</div>';
            return;
        }

        // We need to sync with the wave chart. 
        setTimeout(() => {
            drawTideChartSvg(data, peaks, container, waveContainer);
        }, 100);

        // Sync Scrolling
        if (waveContainer._syncHandler) {
            waveContainer.removeEventListener('scroll', waveContainer._syncHandler);
            container.removeEventListener('scroll', container._syncHandler);
        }

        const syncWaveToTide = () => {
            if (waveContainer._isScrolling) return;
            container._isScrolling = true;
            waveContainer.scrollLeft = container.scrollLeft;
            setTimeout(() => container._isScrolling = false, 50);
        };

        const syncTideToWave = () => {
            if (container._isScrolling) return;
            waveContainer._isScrolling = true;
            container.scrollLeft = waveContainer.scrollLeft;
            setTimeout(() => waveContainer._isScrolling = false, 50);
        };

        container.addEventListener('scroll', syncWaveToTide);
        waveContainer.addEventListener('scroll', syncTideToWave);

        container._syncHandler = syncWaveToTide;
        waveContainer._syncHandler = syncTideToWave;

        // Handle Resize
        window.addEventListener('resize', () => {
            clearTimeout(window._tideResizeTimer);
            window._tideResizeTimer = setTimeout(() => {
                drawTideChartSvg(data, peaks, container, waveContainer);
            }, 200);
        });

    } catch (e) {
        console.error("Tide render error:", e);
        container.innerHTML = '<div style="color:red; text-align:center;">Error loading tide chart</div>';
    }
}

function drawTideChartSvg(data, peaks, container, waveContainer) {
    container.innerHTML = '';

    // Get dimensions from the WAVE container to ensure alignment
    // The wave container's scrollWidth represents the full time axis length
    const fullWidth = waveContainer.scrollWidth;
    const height = container.clientHeight || 150;

    // If wave chart is empty or hidden, fallback
    if (fullWidth < 100) return;

    // Create SVG
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("width", fullWidth);
    svg.setAttribute("height", height);
    svg.style.display = "block";

    // Find Min/Max for Y-axis scaling
    let minH = Infinity;
    let maxH = -Infinity;
    data.forEach(d => {
        if (d.v < minH) minH = d.v;
        if (d.v > maxH) maxH = d.v;
    });

    // Add some padding to Y-axis
    const paddingY = 20; // more padding for labels
    const rangeY = maxH - minH;
    // Avoid divide by zero
    const safeRangeY = rangeY === 0 ? 1 : rangeY;

    // X-axis mapping
    if (data.length < 2) return;

    const startTime = new Date(data[0].t).getTime();
    const endTime = new Date(data[data.length - 1].t).getTime();
    const timeRange = endTime - startTime;

    // Helper to map X/Y
    const getX = (t) => {
        const time = new Date(t).getTime();
        return ((time - startTime) / timeRange) * fullWidth;
    };

    const getY = (v) => {
        const pctY = (v - minH) / safeRangeY;
        // Invert Y (0 is top)
        return height - paddingY - (pctY * (height - 2 * paddingY));
    };

    // Generate Path
    // M x0 y0 L x1 y1 ...
    // Fill path (close loop) for area effect

    console.log(`Rendering Tide: ${data.length} pts, W:${fullWidth}, Range:${minH.toFixed(2)} to ${maxH.toFixed(2)}`);

    // 1. Draw Line / Area
    let pathD = "";
    let firstX = 0;
    let firstY = 0;

    data.forEach((pt, i) => {
        const x = getX(pt.t);
        const y = getY(pt.v);

        if (i === 0) {
            pathD += `M ${x.toFixed(1)} ${y.toFixed(1)}`;
            firstX = x;
            firstY = y;
        } else {
            pathD += ` L ${x.toFixed(1)} ${y.toFixed(1)}`;
        }
    });

    // Area path (optional, looks nice)
    const areaPath = document.createElementNS(ns, "path");
    // Close the loop: go to bottom right, then bottom left
    const areaD = pathD + ` L ${fullWidth} ${height} L 0 ${height} Z`;
    areaPath.setAttribute("d", areaD);
    areaPath.setAttribute("fill", "rgba(0, 119, 190, 0.1)");
    areaPath.setAttribute("stroke", "none");

    svg.appendChild(areaPath);

    // Line path
    const linePath = document.createElementNS(ns, "path");
    linePath.setAttribute("d", pathD);
    linePath.setAttribute("fill", "none");
    linePath.setAttribute("stroke", "#0077be");
    linePath.setAttribute("stroke-width", "2");

    svg.appendChild(linePath);

    // 2. Draw Peaks
    peaks.forEach(peak => {
        const px = getX(peak.t);
        const py = getY(peak.v);

        // Dot
        const circle = document.createElementNS(ns, "circle");
        circle.setAttribute("cx", px.toFixed(1));
        circle.setAttribute("cy", py.toFixed(1));
        circle.setAttribute("r", "3");
        circle.setAttribute("fill", "#131929"); // Dark navy
        svg.appendChild(circle);

        // Label (H 4.5ft or just 4.5ft)
        // User asked for "peak aH and L values to it, along the date"
        // Let's show: "4.5ft" above H, below L

        const text = document.createElementNS(ns, "text");

        // Convert unit
        const valM = peak.v;
        const formattedVal = formatHeight(valM, currentUnit || 'imperial');
        const unit = getHeightUnit(currentUnit || 'imperial');

        text.textContent = `${formattedVal}${unit}`;

        text.setAttribute("x", px.toFixed(1));
        text.setAttribute("text-anchor", "middle");
        text.setAttribute("font-size", "10px");
        text.setAttribute("font-weight", "bold");
        text.setAttribute("fill", "var(--text-secondary)");

        // Offset Y
        if (peak.type === 'H') {
            text.setAttribute("y", (py - 8).toFixed(1));
        } else {
            text.setAttribute("y", (py + 15).toFixed(1));
        }

        svg.appendChild(text);

        // Time Label? "along the date"? 
        // Maybe just the time: "4:30 PM"
        const timeText = document.createElementNS(ns, "text");
        const dt = new Date(peak.t);
        const timeStr = dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

        timeText.textContent = timeStr;
        timeText.setAttribute("x", px.toFixed(1));
        timeText.setAttribute("text-anchor", "middle");
        timeText.setAttribute("font-size", "9px");
        timeText.setAttribute("fill", "rgba(0,0,0,0.4)");

        if (peak.type === 'H') {
            timeText.setAttribute("y", (py - 18).toFixed(1)); // Above value
        } else {
            timeText.setAttribute("y", (py + 25).toFixed(1)); // Below value
        }
        svg.appendChild(timeText);

    });

    container.appendChild(svg);

    // Reset scroll to match wave container
    container.scrollLeft = waveContainer.scrollLeft;
}