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

        // Deep link (#slug[/date]/models): restore the overlay now — the
        // only point where the chart data and compare button are both ready.
        if (window._pendingModelsView) {
            window._pendingModelsView = false;
            toggleModelOverlay();
        }

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

    renderBarYAxis(chartContainer);
}

// --- Discrete Y Axis (bars view AND model view) ---
// Height labels + short ticks on BOTH sides of the plot, hanging outside
// the scroll container in the page margins, so scrolled plot content never
// runs under them. No axis line. Always at least 2 tick values.

function removeYAxis() {
    const section = document.getElementById('waveChartSection');
    if (!section) return;
    section.classList.remove('models-gutters');
    section.querySelectorAll('.chart-y-axis').forEach(el => el.remove());
}

function renderYAxisTicks(scaleMax, yScreenFor) {
    const section = document.getElementById('waveChartSection');
    if (!section) return;
    section.classList.add('models-gutters');
    section.querySelectorAll('.chart-y-axis').forEach(el => el.remove());

    // Ticks at nice values in the display unit (skip 0); pick the step
    // giving 2-3 ticks, preferring fewer but never less than 2
    const factor = currentUnit === 'imperial' ? 3.28084 : 1;
    const unit = getHeightUnit(currentUnit);
    const maxDisp = scaleMax * factor;
    const steps = [0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 20];
    const nTicks = s => Math.max(0, Math.ceil(maxDisp / s) - 1);
    const step = steps.find(s => nTicks(s) >= 2 && nTicks(s) <= 3)
        || steps.find(s => nTicks(s) <= 3) || 10;

    const sectionTop = section.getBoundingClientRect().top;
    ['left', 'right'].forEach(side => {
        const axis = document.createElement('div');
        axis.className = `chart-y-axis chart-y-axis--${side}`;
        for (let v = step; v < maxDisp; v = Math.round((v + step) * 100) / 100) {
            const tick = document.createElement('div');
            tick.className = 'y-tick';
            tick.style.top = `${(yScreenFor(v / factor) - sectionTop).toFixed(1)}px`;
            tick.innerHTML = side === 'left'
                ? `<span>${v}${unit}</span><i></i>`
                : `<i></i><span>${v}${unit}</span>`;
            axis.appendChild(tick);
        }
        section.appendChild(axis);
    });
}

function renderBarYAxis(container) {
    const w0 = container.querySelector('.chart-bar-wrapper');
    if (!w0 || !container._scaleMax) {
        removeYAxis();
        return;
    }
    const scale = container._scaleMax;
    renderYAxisTicks(scale, v => {
        const r = w0.getBoundingClientRect();
        return r.top + r.height * (1 - v / scale);
    });
    container._axisRender = () => renderBarYAxis(container);
}

function renderModelYAxis(svg, scaleMax, yForOffset) {
    renderYAxisTicks(scaleMax,
        v => svg.getBoundingClientRect().top + yForOffset(v));
}

// Redraw the axis in the new unit once app.js has updated currentUnit
document.querySelectorAll('.unit-toggle').forEach(t =>
    t.addEventListener('change', () => setTimeout(() => {
        const container = document.getElementById('waveChartContainer');
        if (container && container._axisRender) container._axisRender();
    }, 60)));

// --- Model Comparison Overlay ---
// Overlays every wave model's combined Hs as lines on the bar chart,
// sharing the bars' y-scale and time axis (bars = default model).

const MODEL_COLORS = {
    gfswave025: '#131929',
    mfwam: '#f59e0b',
    ecmwf_wam9: '#7c3aed',
    ecmwf_wam25: '#c084fc',
    gwam: '#10b981',
    mop: '#e34948', // notebook-reserved MOP red
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
        container.classList.remove('models-active');
        const svg = container.querySelector('.model-overlay-svg');
        if (svg) {
            // Fade out in step with the bars fading back in
            svg.style.opacity = '0';
            setTimeout(() => svg.remove(), 400);
        }
        renderBarYAxis(container); // back to the bars' scale
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
        if (window.updateUrlView) window.updateUrlView({ models: false });
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
        if (window.updateUrlView) window.updateUrlView({ models: true });
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

    // Open the side gutters for the y-axis BEFORE measuring bar positions:
    // the section padding narrows the scrollport and reflows the bars
    const section = document.getElementById('waveChartSection');
    if (section) section.classList.add('models-gutters');

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

    const xFor = (t) => {
        if (t < times[0] || t > times[n - 1]) return null;
        let j = times.findIndex((ts, i) => i + 1 < n && t >= ts && t <= times[i + 1]);
        if (t === times[n - 1]) j = n - 2;
        if (j < 0) return null;
        const frac = (t - times[j]) / (times[j + 1] - times[j]);
        return centers[j] + frac * (centers[j + 1] - centers[j]);
    };

    // First pass: project every model's points onto the chart's time axis
    const projected = data.models.map(model => ({
        model,
        pts: model.series.reduce((acc, pt) => {
            const t = new Date(pt.t.replace(' ', 'T'));
            const x = xFor(t.getTime());
            if (x !== null) acc.push({ x, v: pt.v, t });
            return acc;
        }, []),
    }));

    // The model view has its own y-scale: fit the highest line rather than
    // clip everything above the bars' max. The bottom band stays clear for
    // the date labels, which drop down there in model view.
    let vMax = 0;
    projected.forEach(({ pts }) => pts.forEach(p => { vMax = Math.max(vMax, p.v); }));
    const scaleMax = Math.max(vMax, 0.1) * 1.1;
    const yTop = top + 4;
    const yBottom = top + barH - 26;
    const yFor = v => yTop + (yBottom - yTop) * (1 - v / scaleMax);

    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('class', 'model-overlay-svg');
    svg.setAttribute('width', container.scrollWidth);
    svg.setAttribute('height', top + barH);

    // The wide transparent hover paths would swallow clicks meant for the
    // bars below — forward them so day selection keeps working with the
    // overlay on.
    svg.addEventListener('click', (e) => {
        const wrapper = document.elementsFromPoint(e.clientX, e.clientY)
            .find(el => el.classList && el.classList.contains('chart-bar-wrapper'));
        if (wrapper) wrapper.click();
    });

    const tooltip = document.getElementById('globalChartTooltip');
    const BAR_COLOR = '#38bdf8'; // the default model IS the bars — same color

    const toPathD = pts =>
        pts.map((p, i) => (i ? 'L ' : 'M ') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ');

    // Two-way hover link between lines and legend chips: the hot model's
    // line thickens and its chip lights up; the other lines dim.
    const refs = {};
    const setHot = (id, on) => {
        Object.entries(refs).forEach(([oid, ref]) => {
            const hot = on && oid === id;
            ref.paths.forEach(p => {
                p.setAttribute('stroke-width', ref.baseWidth + (hot ? 1.5 : 0));
                p.setAttribute('opacity',
                    on && !hot ? '0.25' : (ref.isDefault ? '1' : '0.85'));
            });
            if (ref.chip) ref.chip.classList.toggle('legend-chip--hot', hot);
        });
    };

    projected.forEach(({ model, pts }) => {
        pts.forEach(p => { p.y = yFor(p.v); });
        if (pts.length < 2) return;

        const color = model.default ? BAR_COLOR : (MODEL_COLORS[model.id] || '#64748b');
        const baseWidth = model.default ? 2.5 : 1.5;

        // The final 24 h of a model that ends inside the chart window are
        // drawn dashed: its forecast is running out. Models clipped by the
        // chart edge (they continue past it) stay solid to the edge.
        const lastMs = new Date(model.series[model.series.length - 1].t.replace(' ', 'T')).getTime();
        const endsInChart = xFor(lastMs) !== null;
        const cutoff = lastMs - 24 * 3600 * 1000;
        let solidPts = pts, dashPts = [];
        if (endsInChart) {
            solidPts = pts.filter(p => p.t.getTime() <= cutoff);
            dashPts = pts.filter(p => p.t.getTime() >= cutoff);
            if (solidPts.length) dashPts.unshift(solidPts[solidPts.length - 1]);
        }

        const paths = [];
        [[solidPts, false], [dashPts, true]].forEach(([segment, dashed]) => {
            if (segment.length < 2) return;
            const path = document.createElementNS(ns, 'path');
            path.setAttribute('d', toPathD(segment));
            path.setAttribute('fill', 'none');
            path.setAttribute('stroke', color);
            path.setAttribute('stroke-width', baseWidth);
            path.setAttribute('stroke-linejoin', 'round');
            if (dashed) path.setAttribute('stroke-dasharray', '5 4');
            if (!model.default) path.setAttribute('opacity', '0.85');
            svg.appendChild(path);
            paths.push(path);
        });
        if (paths.length === 0) return;
        refs[model.id] = { paths, baseWidth, isDefault: model.default };

        // Invisible wide twin of the line as the hover hit area
        const hit = document.createElementNS(ns, 'path');
        hit.setAttribute('d', toPathD(pts));
        hit.setAttribute('fill', 'none');
        hit.setAttribute('stroke', 'transparent');
        hit.setAttribute('stroke-width', 12);
        hit.style.pointerEvents = 'stroke';
        hit.style.cursor = 'pointer';

        const showPoint = (clientX) => {
            if (!tooltip || pts.length === 0) return;
            const rect = svg.getBoundingClientRect();
            const mx = clientX - rect.left;
            let best = pts[0];
            for (const p of pts) {
                if (Math.abs(p.x - mx) < Math.abs(best.x - mx)) best = p;
            }
            const val = formatHeight(best.v, currentUnit);
            const unit = getHeightUnit(currentUnit);
            const timeStr = best.t.toLocaleString('en-US', {
                weekday: 'short', hour: 'numeric', minute: '2-digit',
            });
            tooltip.innerHTML =
                `<span style="font-size: 0.9rem; font-weight: 700;">` +
                `<i style="display:inline-block;width:10px;height:3px;border-radius:2px;` +
                `vertical-align:middle;background:${color}"></i> ` +
                `${val}${unit} &bull; ${timeStr}</span>`;

            const cx = Math.min(Math.max(rect.left + best.x, 10), window.innerWidth - 10);
            tooltip.style.left = `${cx}px`;
            tooltip.style.top = `${rect.top + best.y}px`;
            tooltip.style.transform = 'translate(-50%, -140%)';
            tooltip.classList.add('visible');
            setHot(model.id, true);
        };

        hit.addEventListener('mousemove', e => showPoint(e.clientX));
        hit.addEventListener('touchstart', e => {
            if (e.touches.length) showPoint(e.touches[0].clientX);
        }, { passive: true });
        hit.addEventListener('mouseleave', () => {
            if (tooltip) tooltip.classList.remove('visible');
            setHot(model.id, false);
        });
        svg.appendChild(hit);
    });

    // Fade the lines in as the bars fade out (CSS transition on opacity)
    svg.style.opacity = '0';
    container.appendChild(svg);
    requestAnimationFrame(() => requestAnimationFrame(() => {
        svg.style.opacity = '1';
    }));
    container.classList.add('models-active');
    renderModelYAxis(svg, scaleMax, yFor);
    container._axisRender = () => renderModelYAxis(svg, scaleMax, yFor);

    if (legend) {
        legend.innerHTML = data.models.map(m =>
            `<span class="legend-chip" data-model-id="${m.id}"><i style="background:${m.default ? '#38bdf8' : (MODEL_COLORS[m.id] || '#64748b')}"></i>${m.label}${m.default ? ' •' : ''}</span>`
        ).join('');
        legend.classList.add('visible');
        legend.querySelectorAll('.legend-chip').forEach(chip => {
            const id = chip.dataset.modelId;
            if (refs[id]) refs[id].chip = chip;
            chip.addEventListener('mouseenter', () => setHot(id, true));
            chip.addEventListener('mouseleave', () => setHot(id, false));
        });
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