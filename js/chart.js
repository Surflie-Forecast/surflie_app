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

        // The models file is fetched alongside: every model line (nearshore
        // and offshore) feeds the one shared y-scale, and the models view
        // reuses this copy instead of refetching.
        const modelsPromise = fetch(`api/models/${apiSpotId(spotName)}.json`)
            .then(res => (res.ok ? res.json() : null))
            .catch(() => null);

        const [results, modelsFile] = await Promise.all(
            [Promise.all(promises), modelsPromise]);
        chartContainer._modelsFile =
            modelsFile && modelsFile.models && modelsFile.models.length ? modelsFile : null;

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
            setupBuoyCompare(null);
            return;
        }

        renderChart(allHours, chartContainer._modelsFile);
        setupModelCompare(spotName);
        setupBuoyCompare(spotName);

        // Deep link (#slug[/date]/models or /buoys): restore the view now —
        // the only point where the chart data and buttons are both ready.
        if (window._pendingModelsView) {
            window._pendingModelsView = false;
            toggleModelOverlay();
        } else if (window._pendingBuoysView) {
            window._pendingBuoysView = false;
            toggleBuoyView();
        }

    } catch (error) {
        console.error("Error rendering chart:", error);
        chartContainer.innerHTML = '<div style="width:100%; text-align:center; padding-top: 50px; color:red;">Failed to load chart</div>';
        setupModelCompare(null);
        setupBuoyCompare(null);
    }
}

function renderChart(hourlyData, modelsFile) {
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

    // One y-scale for bars and every model line (both families) over the
    // bars' time window, so switching views never rescales anything.
    let maxVal = 0;
    hourlyData.forEach(h => {
        const val = parseFloat(h.swell.height);
        if (val > maxVal) maxVal = val;
    });
    if (modelsFile && hourlyData.length) {
        const tFirst = hourlyData[0].datetime;
        const tLast = hourlyData[hourlyData.length - 1].datetime;
        modelsFile.models.forEach(m => m.series.forEach(pt => {
            if (pt.t >= tFirst && pt.t <= tLast && pt.v > maxVal) maxVal = pt.v;
        }));
    }
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
        wrapper.dataset.dt = hour.datetime; // full timestamp for the hover crosshair
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
        if (hour.swell.direction != null) bar.dataset.dir = hour.swell.direction;

        // Add Interactions for Global Tooltip
        bar.addEventListener('mouseenter', (e) => {
            const rect = bar.getBoundingClientRect();

            // Re-fetch current unit logic
            const currentVal = formatHeight(parseFloat(bar.dataset.valM), currentUnit);
            const currentU = getHeightUnit(currentUnit);

            // Forecast only (the crosshair already reads out the time):
            // height, period, and direction as a rotated arrow (card style).
            const dir = bar.dataset.dir;
            const arrow = dir != null && dir !== ''
                ? ` &nbsp;&nbsp;<span style="display:inline-block;transform:rotate(${parseInt(dir) + 90}deg)">&#10148;</span>`
                : '';
            tooltip.innerHTML = `<span style="font-size: 0.9rem; font-weight: 700;">${currentVal}${currentU} &nbsp;${period}s${arrow}</span>`;

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
            timeLabel.style.left = '8px';
            timeLabel.style.whiteSpace = 'nowrap';
            timeLabel.style.fontSize = '0.75rem';
            timeLabel.style.fontWeight = 'bold';
            timeLabel.style.color = 'var(--text-secondary)';

            wrapper.appendChild(timeLabel);
        }
    });

    // --- Hover crosshair: vertical line at the cursor, hour label above ---
    // Bars are 3-hourly; the label interpolates within a bar's slot so the
    // readout is hourly even though the bars are coarser.
    const crosshair = document.createElement('div');
    crosshair.className = 'chart-crosshair';
    const crosshairLabel = document.createElement('div');
    crosshairLabel.className = 'chart-crosshair-label';
    chartContainer.appendChild(crosshair);
    chartContainer.appendChild(crosshairLabel);

    // Listeners live on the container and survive re-renders — drop the old
    // pair (they'd reference removed elements) before attaching.
    if (chartContainer._crosshairMove) {
        chartContainer.removeEventListener('mousemove', chartContainer._crosshairMove);
        chartContainer.removeEventListener('mouseleave', chartContainer._crosshairHide);
    }
    chartContainer._crosshairMove = (e) => {
        const wrappers = chartContainer.querySelectorAll('.chart-bar-wrapper');
        if (!wrappers.length) return;
        const rect = chartContainer.getBoundingClientRect();
        const x = e.clientX - rect.left + chartContainer.scrollLeft;
        const x0 = wrappers[0].offsetLeft;
        const stride = wrappers.length > 1
            ? wrappers[1].offsetLeft - x0 : wrappers[0].offsetWidth;
        const idx = Math.min(wrappers.length - 1,
            Math.max(0, Math.floor((x - x0) / stride)));
        const frac = Math.min(0.999, Math.max(0, (x - x0 - idx * stride) / stride));
        const dt = new Date(wrappers[idx].dataset.dt);
        const stepH = wrappers.length > 1
            ? Math.round((new Date(wrappers[1].dataset.dt) - new Date(wrappers[0].dataset.dt)) / 3.6e6)
            : 3;
        dt.setHours(dt.getHours() + Math.floor(frac * stepH));
        crosshair.style.left = `${x}px`;
        crosshairLabel.style.left = `${x}px`;
        crosshairLabel.textContent = dt.toLocaleTimeString(
            'en-US', { hour: 'numeric', hour12: true });
        crosshair.classList.add('visible');
        crosshairLabel.classList.add('visible');
    };
    chartContainer._crosshairHide = () => {
        crosshair.classList.remove('visible');
        crosshairLabel.classList.remove('visible');
    };
    chartContainer.addEventListener('mousemove', chartContainer._crosshairMove);
    chartContainer.addEventListener('mouseleave', chartContainer._crosshairHide);

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

function renderYAxisTicks(scaleMax, yScreenFor, withZero = false, append = false) {
    const section = document.getElementById('waveChartSection');
    if (!section) return;
    section.classList.add('models-gutters');
    // append: add another band's ticks (stacked buoy view) instead of
    // replacing the axis
    if (!append) section.querySelectorAll('.chart-y-axis').forEach(el => el.remove());

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
        for (let v = withZero ? 0 : step; v < maxDisp; v = Math.round((v + step) * 100) / 100) {
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

function renderModelYAxis(svg, scaleMax, yForOffset, withZero = false) {
    renderYAxisTicks(scaleMax,
        v => svg.getBoundingClientRect().top + yForOffset(v), withZero);
}

// Redraw the axis (and, in buoy view, the latest-observation cards) in the
// new unit once app.js has updated currentUnit
document.querySelectorAll('.unit-toggle').forEach(t =>
    t.addEventListener('change', () => setTimeout(() => {
        const container = document.getElementById('waveChartContainer');
        if (container && container._buoyData && container._buoyData.stack) {
            drawBuoyChart(container._buoyData); // band headers carry units
            return;
        }
        if (container && container._axisRender) container._axisRender();
        if (container && container._buoyData) renderBuoyStats(container._buoyData);
    }, 60)));

// --- Model Comparison Overlay ---
// Overlays every wave model's combined Hs as lines on the bar chart,
// sharing the bars' y-scale and time axis (bars = default model).

const MODEL_COLORS = {
    // Salix is built from GFS Wave (its offshore input), so GFS takes the
    // bars' blue that Salix wears as the default line in the nearshore view.
    gfswave016: '#38bdf8',
    mfwam: '#f59e0b',
    ecmwf_wam9: '#7c3aed',
    mop: '#e34948', // notebook-reserved MOP red
    // Transect (~100 m) forecast: same model family as the grid, so a
    // darker shade of the MOP red — kin to the grid line, but separable.
    'mop-transect-fc': '#9f1239',
    // Observed-driven nowcast: the truth-proxy reference line.
    'mop-nowcast': '#0f172a',
    // Our own MOP-style forecast (surflie_salix): a blue apart from the
    // MOP-red family so the two transect-scale lines stay separable.
    nearshore: '#0ea5e9',
    // NOAA NWPS per-WFO SWAN nest: green, clear of both the MOP reds and
    // the salix blue.
    cg1: '#15803d',
};

// The CDIP MOP products (1 km grid, ~100 m transect, observed nowcast) model
// the water AT the spot, post-refraction; the rest are global/offshore wave
// models. The Nearshore/Offshore toggle shows one family at a time.
const NEARSHORE_IDS = new Set(
    ['mop', 'mop-transect-fc', 'mop-nowcast', 'nearshore', 'cg1']);
const isNearshore = m => NEARSHORE_IDS.has(m.id);

// Current family, remembered across redraws (resize, unit toggle). Nearshore
// first — it's the reason the toggle exists. The default model's bars stay
// regardless; the toggle only gates the overlay lines.
let modelFilterKey = 'nearshore';

// Mobile breakpoint, matching the max-width: 768px block in styles.css. On
// mobile the legend chips are the only way to pin a model: the plot's lines
// are too thin to tap accurately, and a stray tap there would fight the
// day-selection underneath.
const isMobileView = () => window.innerWidth <= 768;

// Highlighted series get a wash under the line: the series colour at the top
// fading to nothing at the baseline. One <linearGradient> per series, and an
// area path that is only shown while that series is hot/pinned.
let areaGradientSeq = 0;
function addSeriesArea(svg, ns, pts, color, baselineY, fadeFromX) {
    if (!pts || pts.length < 2) return null;

    let defs = svg.querySelector('defs');
    if (!defs) {
        defs = document.createElementNS(ns, 'defs');
        svg.insertBefore(defs, svg.firstChild);
    }
    const uid = ++areaGradientSeq;
    const gradId = `series-area-${uid}`;
    const grad = document.createElementNS(ns, 'linearGradient');
    grad.setAttribute('id', gradId);
    // Vertical in the element's own box, so it tracks the area's height.
    grad.setAttribute('x1', '0'); grad.setAttribute('y1', '0');
    grad.setAttribute('x2', '0'); grad.setAttribute('y2', '1');
    [['0%', 0.35], ['100%', 0]].forEach(([offset, op]) => {
        const stop = document.createElementNS(ns, 'stop');
        stop.setAttribute('offset', offset);
        stop.setAttribute('stop-color', color);
        stop.setAttribute('stop-opacity', String(op));
        grad.appendChild(stop);
    });
    defs.appendChild(grad);

    const x0 = pts[0].x;
    const x1 = pts[pts.length - 1].x;

    // Where the line turns dashed the forecast is running out, so the wash
    // fades out horizontally across that tail too — full strength up to the
    // dash, gone by the end. userSpaceOnUse so the stops are plot x values.
    let maskId = null;
    if (fadeFromX != null && x1 > fadeFromX && x1 > x0) {
        maskId = `series-fade-${uid}`;
        const mgrad = document.createElementNS(ns, 'linearGradient');
        mgrad.setAttribute('id', `${maskId}-g`);
        mgrad.setAttribute('gradientUnits', 'userSpaceOnUse');
        mgrad.setAttribute('x1', String(fadeFromX)); mgrad.setAttribute('y1', '0');
        mgrad.setAttribute('x2', String(x1)); mgrad.setAttribute('y2', '0');
        [['0%', 1], ['100%', 0]].forEach(([offset, op]) => {
            const s = document.createElementNS(ns, 'stop');
            s.setAttribute('offset', offset);
            s.setAttribute('stop-color', '#fff');
            s.setAttribute('stop-opacity', String(op));
            mgrad.appendChild(s);
        });
        defs.appendChild(mgrad);

        const mask = document.createElementNS(ns, 'mask');
        mask.setAttribute('id', maskId);
        mask.setAttribute('maskUnits', 'userSpaceOnUse');
        // Opaque white before the dash keeps that stretch at full strength.
        const solid = document.createElementNS(ns, 'rect');
        solid.setAttribute('x', String(x0));
        solid.setAttribute('y', '0');
        solid.setAttribute('width', String(Math.max(0, fadeFromX - x0)));
        solid.setAttribute('height', String(baselineY));
        solid.setAttribute('fill', '#fff');
        mask.appendChild(solid);
        const fade = document.createElementNS(ns, 'rect');
        fade.setAttribute('x', String(fadeFromX));
        fade.setAttribute('y', '0');
        fade.setAttribute('width', String(x1 - fadeFromX));
        fade.setAttribute('height', String(baselineY));
        fade.setAttribute('fill', `url(#${maskId}-g)`);
        mask.appendChild(fade);
        defs.appendChild(mask);
    }

    const d = pts.map((p, i) => (i ? 'L ' : 'M ') +
        p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ') +
        ` L ${x1.toFixed(1)} ${baselineY.toFixed(1)}` +
        ` L ${x0.toFixed(1)} ${baselineY.toFixed(1)} Z`;

    const area = document.createElementNS(ns, 'path');
    area.setAttribute('d', d);
    area.setAttribute('fill', `url(#${gradId})`);
    area.setAttribute('stroke', 'none');
    if (maskId) area.setAttribute('mask', `url(#${maskId})`);
    area.style.pointerEvents = 'none';
    area.style.opacity = '0';
    area.style.transition = 'opacity 0.2s';
    // Behind the lines: insert right after <defs> so every stroke draws over.
    svg.insertBefore(area, defs.nextSibling);
    return area;
}

function filteredModels(models) {
    const test = modelFilterKey === 'offshore'
        ? m => !isNearshore(m) : isNearshore;
    // Never blank the plot: if the chosen family is empty at this spot, show
    // the other one rather than an empty overlay.
    const kept = models.filter(test);
    return kept.length ? kept : models;
}

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
                if (container && container._buoyData) {
                    drawBuoyChart(container._buoyData);
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
            // Re-measure the day box only once the svg is really gone, or it
            // would still be counted in the plot's extent.
            setTimeout(() => { svg.remove(); refreshDayHighlight(); }, 400);
        }
        renderBarYAxis(container); // back to the bars' scale
        refreshDayHighlight();
    }
    if (btn) btn.classList.remove('active');
    // Model view off: the Buoys button leaves the strip and goes back to
    // being the plain "Buoys" toggle (toggleBuoyView re-labels it if we're
    // switching straight into the buoy view).
    document.body.classList.remove('models-mode');
    const buoyBtn = document.getElementById('buoyCompareBtn');
    if (buoyBtn && !buoyBtn.classList.contains('active')) {
        buoyBtn.textContent = 'Buoys';
    }
    if (legend) {
        legend.classList.remove('visible');
        legend.innerHTML = '';
        publishLegendHeight();
    }
    const filter = document.getElementById('modelFilter');
    if (filter) filter.style.display = 'none';
    modelFilterKey = 'nearshore';   // next open starts on the nearshore family
}

async function toggleModelOverlay(evt) {
    const btn = document.getElementById('modelCompareBtn');
    const container = document.getElementById('waveChartContainer');
    if (!btn || !container) return;
    // Opened by a click straight from the bars view (not the step back from
    // the buoy view, nor a deep-link restore): land with Salix pinned.
    const fromBars = evt instanceof Event && !container._buoyData;

    if (container._modelData) {
        clearModelOverlay();
        if (window.updateUrlView) window.updateUrlView({ models: false });
        return;
    }

    try {
        let data = container._modelsFile;
        if (!data) {
            const res = await fetch(`api/models/${apiSpotId(btn.dataset.spot)}.json`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            data = await res.json();
        }
        if (!data.models || data.models.length === 0) throw new Error('no models');

        clearBuoyView();
        // Model view on: the Buoys button drops into the strip alongside
        // (body.models-mode) and narrows its offer to the spot's station.
        document.body.classList.add('models-mode');
        const buoyBtn = document.getElementById('buoyCompareBtn');
        if (buoyBtn) buoyBtn.textContent = 'Closest buoy';

        container._modelData = data;
        drawModelOverlay(data, fromBars ? 'nearshore' : null);
        btn.classList.add('active');
        if (window.updateUrlView) window.updateUrlView({ models: true, buoys: false });
    } catch (e) {
        console.warn('Model comparison unavailable:', e);
        btn.disabled = true;
        setTimeout(() => { btn.disabled = false; }, 3000);
    }
}

// Period + direction-arrow tail for an overlay tooltip, matching the bars'
// readout; empty for the parts a source doesn't publish.
function overlayPdSuffix(pt) {
    let s = '';
    if (pt.p != null) s += `&nbsp; ${Math.round(pt.p)}s`;
    if (pt.d != null) s += ` &nbsp;&nbsp;<span style="display:inline-block;` +
        `transform:rotate(${Math.round(pt.d) + 90}deg)">&#10148;</span>`;
    return s;
}

// Render one overlay series as points rather than a line — used for the
// observed-driven MOP nowcast, echoing the buoy view's "truth as dots".
// Registers a dots-based ref so setHot can highlight/dim it like the lines.
function drawNowcastDots(svg, ns, pts, color, model, refs, tooltip, setHot,
                         togglePin) {
    const dots = pts.map(p => {
        const c = document.createElementNS(ns, 'circle');
        c.setAttribute('cx', p.x.toFixed(1));
        c.setAttribute('cy', p.y.toFixed(1));
        c.setAttribute('r', '1.9');
        c.setAttribute('fill', color);
        c.setAttribute('opacity', '0.85');
        // Inert on mobile, like the overlay lines — the legend chips are the
        // only handle on the series there.
        c.style.pointerEvents = isMobileView() ? 'none' : 'all';
        c.style.cursor = 'pointer';
        svg.appendChild(c);
        return c;
    });
    if (!dots.length) return;
    refs[model.id] = { dots };

    const showPoint = (clientX) => {
        if (!tooltip) return;
        const rect = svg.getBoundingClientRect();
        const mx = clientX - rect.left;
        let best = pts[0];
        for (const p of pts) {
            if (Math.abs(p.x - mx) < Math.abs(best.x - mx)) best = p;
        }
        const val = formatHeight(best.v, currentUnit);
        const unit = getHeightUnit(currentUnit);
        tooltip.innerHTML =
            `<span style="font-size: 0.9rem; font-weight: 700;">` +
            `<i style="display:inline-block;width:8px;height:8px;border-radius:50%;` +
            `vertical-align:middle;background:${color}"></i> ` +
            `${val}${unit}${overlayPdSuffix(best)}</span>`;
        const cx = Math.min(Math.max(rect.left + best.x, 10), window.innerWidth - 10);
        tooltip.style.left = `${cx}px`;
        tooltip.style.top = `${rect.top + best.y}px`;
        tooltip.style.transform = 'translate(-50%, -140%)';
        tooltip.classList.add('visible');
        setHot(model.id, true);
    };
    dots.forEach(c => {
        c.addEventListener('mousemove', e => showPoint(e.clientX));
        c.addEventListener('touchstart', e => {
            if (e.touches.length) showPoint(e.touches[0].clientX);
        }, { passive: true });
        c.addEventListener('mouseleave', () => {
            if (tooltip) tooltip.classList.remove('visible');
            setHot(model.id, false);
        });
        if (togglePin) c.addEventListener('click', e => {
            if (isMobileView()) return;   // legend chips own pinning on mobile
            e.stopPropagation();
            togglePin(model.id);
        });
    });
}

// pinId: optionally open with that model pinned (if it's drawn).
function drawModelOverlay(data, pinId = null) {
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

    // Apply the nearshore/offshore filter before projecting: everything
    // downstream (y-scale, lines, legend chips) works off this subset.
    const shownModels = filteredModels(data.models);

    // First pass: project every model's points onto the chart's time axis
    const projected = shownModels.map(model => ({
        model,
        pts: model.series.reduce((acc, pt) => {
            const t = new Date(pt.t.replace(' ', 'T'));
            const x = xFor(t.getTime());
            if (x !== null) acc.push({ x, v: pt.v, p: pt.p, d: pt.d, t });
            return acc;
        }, []),
    }));

    // The bars' y-scale and geometry: that scale already spans every model
    // line in both families (renderChart), so toggling views or families
    // never rescales and the default line lands on the bar tops. The max()
    // only guards a models file that failed to load with the bars.
    let vMax = 0;
    projected.forEach(({ pts }) => pts.forEach(p => { vMax = Math.max(vMax, p.v); }));
    const scaleMax = Math.max(container._scaleMax || 0, vMax * 1.2, 0.1);
    const yTop = top;
    const yBottom = top + barH;
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
    // line thickens and its chip lights up; the others dim. Dot series (the
    // observed-driven nowcast) enlarge their dots instead of thickening.
    const refs = {};
    // Click-to-pin: clicking a line/chip locks the highlight on that model
    // and mutes the other lines' hit areas, so hovering along the chart
    // reads only the pinned model. Click again (or the background) to unpin.
    let pinned = null;
    const setHot = (id, on) => {
        // Hovering another chip still previews that model over a pin;
        // leaving it falls back to the pinned one.
        if (pinned && !on) { id = pinned; on = true; }
        Object.entries(refs).forEach(([oid, ref]) => {
            const hot = on && oid === id;
            const dim = on && !hot;
            (ref.paths || []).forEach(p => {
                p.setAttribute('stroke-width', ref.baseWidth + (hot ? 1.5 : 0));
                p.setAttribute('opacity', dim ? '0.25' : (ref.isDefault ? '1' : '0.85'));
            });
            (ref.dots || []).forEach(c => {
                c.setAttribute('r', hot ? '2.8' : '1.9');
                c.setAttribute('opacity', dim ? '0.25' : '0.85');
            });
            // Wash under the highlighted series only
            if (ref.area) ref.area.style.opacity = hot ? '1' : '0';
            if (ref.chip) ref.chip.classList.toggle('legend-chip--hot', hot);
        });
    };
    const togglePin = (id) => {
        pinned = pinned === id ? null : id;
        Object.entries(refs).forEach(([oid, ref]) => {
            // Mobile keeps every line inert whatever the pin state — restoring
            // 'stroke'/'all' here would hand the plot back its tap handling.
            const mute = isMobileView() || (pinned && oid !== pinned);
            if (ref.hit) ref.hit.style.pointerEvents = mute ? 'none' : 'stroke';
            (ref.dots || []).forEach(c => {
                c.style.pointerEvents = mute ? 'none' : 'all';
            });
            // Mobile shows only the pinned model's name (CSS drives the
            // reveal; the class just marks which chip is pinned).
            if (ref.chip) ref.chip.classList.toggle('legend-chip--pinned',
                pinned === oid);
        });
        setHot(pinned, !!pinned);
        publishLegendHeight();  // the revealed name can rewrap the legend
    };
    // Background click unpins — desktop only, so on mobile a tap in the plot
    // can't clear a pin the legend set (and day selection keeps the tap).
    svg.addEventListener('click', () => {
        if (isMobileView()) return;
        if (pinned) togglePin(pinned);
    });

    // The observed-driven nowcast is drawn as points, not a line — the same
    // "ground truth as dots" language the buoy comparison view uses. It's
    // never the default (bars), so this only affects the overlay reference.
    projected.forEach(({ model, pts }) => {
        pts.forEach(p => { p.y = yFor(p.v); });
        if (pts.length < 1) return;

        const color = model.default ? BAR_COLOR : (MODEL_COLORS[model.id] || '#64748b');

        if (model.id === 'mop-nowcast') {
            drawNowcastDots(svg, ns, pts, color, model, refs, tooltip, setHot,
                            togglePin);
            return;
        }
        if (pts.length < 2) return;

        // The observed-driven nowcast is the reference line — draw it a
        // touch heavier than the forecast models.
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
        // Fade the wash out across the dashed tail, if this line has one.
        const dashStartX = dashPts.length >= 2 ? dashPts[0].x : null;
        const area = addSeriesArea(svg, ns, pts, color, yBottom, dashStartX);
        refs[model.id] = { paths, baseWidth, isDefault: model.default, area };

        // Invisible wide twin of the line as the hover hit area
        const hit = document.createElementNS(ns, 'path');
        hit.setAttribute('d', toPathD(pts));
        hit.setAttribute('fill', 'none');
        hit.setAttribute('stroke', 'transparent');
        hit.setAttribute('stroke-width', 12);
        // Mobile drives the overlay entirely from the legend chips: the lines
        // take no taps at all, so nothing hovers, highlights, or steals the
        // tap from the day selection underneath.
        hit.style.pointerEvents = isMobileView() ? 'none' : 'stroke';
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
            tooltip.innerHTML =
                `<span style="font-size: 0.9rem; font-weight: 700;">` +
                `<i style="display:inline-block;width:10px;height:3px;border-radius:2px;` +
                `vertical-align:middle;background:${color}"></i> ` +
                `${val}${unit}${overlayPdSuffix(best)}</span>`;

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
        hit.addEventListener('click', e => {
            if (isMobileView()) return;   // legend chips own pinning on mobile
            e.stopPropagation();
            togglePin(model.id);
        });
        svg.appendChild(hit);
        refs[model.id].hit = hit;
    });

    // Fade the lines in as the bars fade out (CSS transition on opacity)
    svg.style.opacity = '0';
    container.appendChild(svg);
    requestAnimationFrame(() => requestAnimationFrame(() => {
        svg.style.opacity = '1';
    }));
    container.classList.add('models-active');
    // Lines float free of a bar floor, so this view labels its zero
    renderModelYAxis(svg, scaleMax, yFor, true);
    container._axisRender = () => renderModelYAxis(svg, scaleMax, yFor, true);

    // The Nearshore/Offshore toggle (static markup beside the Models button):
    // shown only when the spot actually has both families to switch between.
    syncModelFilter(data);

    if (legend) {
        // Leading count: mobile hides the chip names, so without this the row
        // is just anonymous swatches. CSS shows it on mobile only.
        legend.innerHTML = legendCountChip(shownModels.length) +
            shownModels.map(m => {
            const dot = m.id === 'mop-nowcast' ? ' legend-chip--dot' : '';
            // Name in its own span: mobile collapses the chips to their
            // colour swatch alone and reveals the name of the pinned one.
            return `<span class="legend-chip${dot}" data-model-id="${m.id}"><i style="background:${m.default ? '#38bdf8' : (MODEL_COLORS[m.id] || '#64748b')}"></i><span class="legend-chip-name">${m.label}${m.default ? ' •' : ''}</span></span>`;
        }).join('');
        legend.classList.add('visible');
        legend.querySelectorAll('.legend-chip[data-model-id]').forEach(chip => {
            const id = chip.dataset.modelId;
            if (refs[id]) refs[id].chip = chip;
            // Hover highlighting is desktop-only: a tap on mobile synthesises
            // mouseenter with no matching mouseleave, which would re-light a
            // chip the same tap had just unpinned.
            chip.addEventListener('mouseenter', () => {
                if (!isMobileView()) setHot(id, true);
            });
            chip.addEventListener('mouseleave', () => {
                if (!isMobileView()) setHot(id, false);
            });
            chip.addEventListener('click', () => togglePin(id));
            chip.style.cursor = 'pointer';
        });
        publishLegendHeight();
    }
    if (pinId && refs[pinId]) togglePin(pinId);
    // The overlay lines change the plot's vertical extent, and the day box is
    // measured from it — re-run once this frame's layout has settled.
    refreshDayHighlight();
}

// Leading "N sources" label for the legend. Mobile collapses the chips to
// bare colour swatches, so this says what the row is a list of; CSS keeps it
// hidden on desktop, where every chip already carries its name.
function legendCountChip(n) {
    if (!n) return '';
    return `<span class="legend-count">${n} source${n === 1 ? '' : 's'}:</span>`;
}

// Re-measure the day-selection box against the plot's current geometry.
// Runs twice: once for this frame's layout, then again past the 0.4s
// bars/overlay cross-fade, when the marks have settled at final size.
function refreshDayHighlight() {
    const run = () => {
        if (window.updateChartHighlight) window.updateChartHighlight();
    };
    requestAnimationFrame(run);
    setTimeout(run, 450);
}

// The boundary strip reserves room for the legend so the chart starts below
// it (mobile). Its height isn't knowable in CSS — the chips wrap to one or
// two rows depending on width and model count — so measure and publish it.
function publishLegendHeight() {
    const legend = document.getElementById('modelLegend');
    if (!legend) return;
    // Measure after layout so a wrapped legend reports its true height.
    requestAnimationFrame(() => {
        const h = legend.classList.contains('visible')
            ? Math.round(legend.getBoundingClientRect().height) : 0;
        document.documentElement.style.setProperty('--legend-h', `${h}px`);
    });
}

// Reflect modelFilterKey onto the standalone toggle and (once) wire its
// clicks. Hidden unless the spot has both a nearshore and an offshore family.
function syncModelFilter(data) {
    const filter = document.getElementById('modelFilter');
    if (!filter) return;

    const hasBoth = data.models.some(isNearshore)
        && data.models.some(m => !isNearshore(m));
    filter.style.display = hasBoth ? '' : 'none';

    filter.querySelectorAll('.model-filter-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.filter === modelFilterKey);
        if (!btn._bound) {
            btn._bound = true;
            btn.addEventListener('click', () => {
                modelFilterKey = btn.dataset.filter;
                const container = document.getElementById('waveChartContainer');
                if (container && container._modelData) {
                    drawModelOverlay(container._modelData);
                }
            });
        }
    });
}

// --- Buoy Comparison View ---
// The notebook's "apples-to-apples at the buoys" panel: observed Hs (dots),
// CDIP's MOP forecast at the buoy site (red), and the global models
// re-fetched at the buoy's coordinates. Unlike the model overlay this view
// has its own time axis spanning the full data extent — past observations on
// the left (checkable against the forecast lines), each model drawn out to
// its own horizon on the right.

// Fallback when a spot has no preassigned closest buoy yet (spots.json's
// closest_buoy is filled by the pipeline; see window._spotBuoys in app.js).
const DEFAULT_BUOY = '071'; // Harvest

// No fixed window: the buoy view spans the full extent of whatever data the
// file holds — earliest observation on the left, latest forecast point on the
// right — so every series is drawn to where its own data actually runs out.
const BUOY_INK = '#0b0b0b'; // observations are ink, not a series hue

// Same hues the site's models view shows (GFS Wave in the bars' blue, clear
// of the ink observation dots). MOP keeps its reserved red.
const BUOY_MODEL_COLORS = MODEL_COLORS;

function setupBuoyCompare(spotName) {
    const btn = document.getElementById('buoyCompareBtn');
    if (!btn) return;

    clearBuoyView();

    if (!spotName) {
        btn.style.display = 'none';
        return;
    }

    btn.style.display = 'inline-flex';
    btn.dataset.spot = spotName; // resolves the spot's closest buoy on click

    if (!btn._bound) {
        btn._bound = true;
        btn.addEventListener('click', toggleBuoyView);
    }
}

function clearBuoyView() {
    const container = document.getElementById('waveChartContainer');
    const btn = document.getElementById('buoyCompareBtn');
    const legend = document.getElementById('modelLegend');

    // Restore the normal page layout (forecast cards back, chart back to
    // its default height) and hide the latest-observation cards.
    document.body.classList.remove('buoys-mode');
    document.body.classList.remove('buoys-stack');
    const stats = document.getElementById('buoyStats');
    if (stats) {
        stats.style.display = 'none';
        stats.innerHTML = '';
    }
    const heroLabel = document.getElementById('buoyHeroLabel');
    if (heroLabel) heroLabel.textContent = '';
    // Hero backdrop back to the spot's coastline (no-op unless focused)
    if (window.blurHeroBuoy) window.blurHeroBuoy();
    // Give the hero title back to the spot — but only if it still shows our
    // buoy caption (a spot switch re-renders the title before this runs,
    // and that fresh title must not be clobbered with the stale one).
    const titleEl = document.getElementById('spotName');
    if (titleEl && titleEl._spotTitleHTML != null) {
        if (titleEl.innerHTML === titleEl._buoyTitleHTML) {
            titleEl.innerHTML = titleEl._spotTitleHTML;
            if (window.fitHeroTitle) window.fitHeroTitle();
        }
        titleEl._spotTitleHTML = titleEl._buoyTitleHTML = null;
    }
    // Hero is a spot again — auth.js re-shows the favorite star (a spot
    // switch re-dispatches 'spot-rendered' on its own; this covers toggling
    // the buoy view off while staying on the same spot).
    document.dispatchEvent(new CustomEvent('buoy-exited'));

    if (container) {
        const wasActive = !!container._buoyData;
        container._buoyData = null;
        container.classList.remove('buoys-active');
        if (wasActive) {
            // Snap the chart straight back from buoys-mode's taller height
            // instead of transitioning: the models overlay (often redrawn
            // right after, e.g. "Closest buoy" -> back to Models) measures
            // the bars to set its y-scale, and mid-shrink they'd read tall,
            // dropping the lines below the plot. Same trick drawBuoyChart
            // uses on the way in.
            const prevTransition = container.style.transition;
            container.style.transition = 'none';
            // eslint-disable-next-line no-unused-expressions
            container.offsetHeight; // force reflow at the final height
            container.style.transition = prevTransition;
        }
        // Drop this view's crosshair; the bars view rebuilds its own.
        if (container._buoyCrossMove) {
            container.removeEventListener('mousemove', container._buoyCrossMove);
            container.removeEventListener('mouseleave', container._buoyCrossHide);
            container._buoyCrossMove = container._buoyCrossHide = null;
            // Hand the shared crosshair back to the bar chart's handler.
            if (container._crosshairMove) {
                container.addEventListener('mousemove', container._crosshairMove);
                container.addEventListener('mouseleave', container._crosshairHide);
            }
            container.querySelectorAll('.chart-crosshair, .chart-crosshair-label')
                .forEach(el => el.classList.remove('visible'));
        }
        const svg = container.querySelector('.buoy-overlay-svg');
        if (svg) {
            svg.style.opacity = '0';
            setTimeout(() => { svg.remove(); refreshDayHighlight(); }, 400);
        }
        if (wasActive) {
            container.scrollLeft = 0; // bars start at the left edge again
            renderBarYAxis(container); // back to the bars' scale
            if (legend) {
                legend.classList.remove('visible');
                legend.innerHTML = '';
                publishLegendHeight();
            }
        }
        refreshDayHighlight();
    }
    if (btn) {
        btn.classList.remove('active');
        // Back to the generic label unless the model view still owns the
        // strip (there the button keeps offering the closest buoy).
        if (!document.body.classList.contains('models-mode')) {
            btn.textContent = 'Buoys';
        }
    }
    // Leaving a picked-station view: un-press the inert Models button and
    // bring the corner button back out
    if (document.body.classList.contains('buoy-solo')) {
        document.body.classList.remove('buoy-solo');
        if (btn) btn.style.display = 'inline-flex';
        const modelBtn = document.getElementById('modelCompareBtn');
        if (modelBtn && !(container && container._modelData)) {
            modelBtn.classList.remove('active');
        }
    }
}

async function toggleBuoyView() {
    const btn = document.getElementById('buoyCompareBtn');
    const container = document.getElementById('waveChartContainer');
    if (!btn || !container) return;

    if (container._buoyData) {
        // Leaving the buoy view steps back UP to the models view (the level
        // it hangs off), not all the way out to the bars. Enter models-mode
        // BEFORE clearing so both buttons hold their strip positions through
        // the hand-off instead of bouncing up to the hero and back.
        document.body.classList.add('models-mode');
        clearBuoyView();
        if (window.updateUrlView) window.updateUrlView({ buoys: false });
        await toggleModelOverlay();
        if (!container._modelData) {
            // Models view failed to come back: fall out to the default view
            document.body.classList.remove('models-mode');
            btn.textContent = 'Buoys';
        }
        return;
    }

    // The spot's preassigned closest buoy (from spots.json via app.js)
    openBuoyView(window._spotBuoys?.[btn.dataset.spot] || DEFAULT_BUOY, true);
}

// Open the comparison view for a specific station — the button's toggle
// lands on the spot's closest buoy (viaToggle), the map's buoy dots and the
// search results on themselves. Re-entrant: called while another buoy is
// showing, it just swaps stations.
async function openBuoyView(buoyId, viaToggle = false) {
    const btn = document.getElementById('buoyCompareBtn');
    const container = document.getElementById('waveChartContainer');
    if (!btn || !container) return;

    try {
        // The button's toggle from a spot: its NEARBY_BUOYS closest stations,
        // stacked. Falls back to the single preassigned one if none load.
        let data = viaToggle ? await loadNearbyBuoys(btn.dataset.spot) : null;
        if (!data) {
            const res = await fetch(`api/buoys/${buoyId}.json`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            data = await res.json();
            if (!data.models || data.models.length === 0) throw new Error('no series');
        }

        // Mutually exclusive with the model overlay — but the Models button
        // stays in the strip (body.buoys-mode positions it), one click from
        // switching back.
        clearModelOverlay();

        container._buoyData = data;
        drawBuoyChart(data);
        const modelBtn = document.getElementById('modelCompareBtn');
        if (viaToggle) {
            // The button's own toggle flow: it sits active in the corner,
            // Models beside it ready to switch back.
            document.body.classList.remove('buoy-solo');
            btn.style.display = 'inline-flex';
            btn.classList.add('active');
            btn.textContent = 'Closest buoy';
        } else {
            // Station picked off the map or the search — even if it happens
            // to be the closest one: the toggle button stands down and only
            // Models shows, pressed but inert (body.buoy-solo pins it in
            // the corner, unclickable).
            document.body.classList.add('buoy-solo');
            btn.classList.remove('active');
            btn.style.display = 'none';
            if (modelBtn) modelBtn.classList.add('active');
        }
        // The hero title becomes the station: "CDIP {id}" where the spot
        // name goes, the buoy's name in the region slot (same h1 +
        // .hero-region structure as a spot title, so every hero state —
        // desktop / mobile / compact — styles it for free). "the closest
        // buoy to {spot}" bottom-left (inside the title block on small
        // screens, via .buoy-hero-sub) — only in the button's own flow;
        // a station picked off the map or the search shows no phrase.
        const esc = s => s.replace(/[&<>"]/g, c => (
            { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
        const titleEl = document.getElementById('spotName');
        if (data.stack) {
            // Several stations: the hero stays the spot (title, favorite
            // star), captioned with what the plots below are, and its map
            // zooms out onto the stations, tinted red.
            if (titleEl && titleEl._spotTitleHTML != null) {
                titleEl.innerHTML = titleEl._spotTitleHTML;
                titleEl._spotTitleHTML = titleEl._buoyTitleHTML = null;
                if (window.fitHeroTitle) window.fitHeroTitle();
            }
            document.dispatchEvent(new CustomEvent('buoy-exited'));
            const heroLabel = document.getElementById('buoyHeroLabel');
            if (heroLabel) heroLabel.textContent =
                `the ${data.stack.length} closest buoys to ${btn.dataset.spot}`;
            const ids = data.stack.map(st => String(st.id));
            if (window.focusHeroBuoys) window.focusHeroBuoys(ids);
            else if (window.focusHeroBuoy) window.focusHeroBuoy(ids[0]);
            if (window.updateUrlView) window.updateUrlView({ buoys: true, models: false });
            return;
        }
        const subText = viaToggle ? `the closest buoy to ${btn.dataset.spot}` : '';
        if (titleEl) {
            if (titleEl._spotTitleHTML == null) {
                titleEl._spotTitleHTML = titleEl.innerHTML;
            }
            const buoyName = (data.label || '').split('—').pop().trim();
            titleEl._buoyTitleHTML =
                `${esc((data.label || 'CDIP').split(' ')[0])} ${esc(String(data.id))}` +
                `<span class="hero-region">${esc(buoyName)}</span>` +
                (subText ? `<span class="buoy-hero-sub">${esc(subText)}</span>` : '');
            titleEl.innerHTML = titleEl._buoyTitleHTML;
            // Same one-line fit the spot title gets on mobile
            if (window.fitHeroTitle) window.fitHeroTitle();
        }
        // Tell auth.js a station owns the hero: the favorite star switches
        // to this buoy (favorites store kind='buoy' + CDIP id).
        {
            const buoyName = (data.label || '').split('—').pop().trim();
            document.dispatchEvent(new CustomEvent('buoy-rendered', {
                detail: { id: String(data.id), name: buoyName }
            }));
        }
        const heroLabel = document.getElementById('buoyHeroLabel');
        if (heroLabel) heroLabel.textContent = subText;
        // Hero backdrop: zoom the map out onto the buoy (red, centered)
        if (window.focusHeroBuoy) window.focusHeroBuoy(String(data.id));
        if (window.updateUrlView) window.updateUrlView({ buoys: true, models: false });
    } catch (e) {
        console.warn('Buoy comparison unavailable:', e);
        btn.disabled = true;
        setTimeout(() => { btn.disabled = false; }, 3000);
    }
}

// The "Closest buoy" toggle shows this many stations, nearest first.
const NEARBY_BUOYS = 3;

// Great-circle km — ranks stations around a spot.
function haversineKm(lat1, lon1, lat2, lon2) {
    const r = Math.PI / 180;
    const a = Math.sin((lat2 - lat1) * r / 2) ** 2
        + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin((lon2 - lon1) * r / 2) ** 2;
    return 2 * 6371 * Math.asin(Math.sqrt(a));
}

// The spot's NEARBY_BUOYS closest stations that are reporting (a stale one
// has no observed point to show; they only fill in when fewer are fresh),
// always including at least one CDIP station — its MOP forecast is the
// site-exact line the global models are judged against — loaded and sorted
// by distance -> { stack: [station data + _km] }, or null.
async function loadNearbyBuoys(spotName) {
    const ll = window._spotLatLon && window._spotLatLon[spotName];
    const roster = window._buoyRoster || [];
    if (!ll || roster.length === 0) return null;
    const ranked = roster
        .map(b => ({ id: String(b.id), stale: !!b.stale, source: b.source,
                     km: haversineKm(ll[0], ll[1], b.latitude, b.longitude) }))
        .sort((a, b) => a.km - b.km);
    const byPreference = [...ranked.filter(b => !b.stale), ...ranked.filter(b => b.stale)];
    const picks = byPreference.slice(0, NEARBY_BUOYS);
    if (!picks.some(b => b.source === 'cdip')) {
        // No CDIP among the nearest: the closest one (reporting first) takes
        // the last slot, then back into distance order.
        const cdip = byPreference.find(b => b.source === 'cdip');
        if (cdip) {
            picks[picks.length - 1] = cdip;
            picks.sort((a, b) => a.km - b.km);
        }
    }
    const loaded = await Promise.all(picks.map(async b => {
        try {
            const res = await fetch(`api/buoys/${b.id}.json`);
            if (!res.ok) return null;
            const d = await res.json();
            if (!d.models || d.models.length === 0) return null;
            d._km = b.km;
            return d;
        } catch (e) {
            return null;
        }
    }));
    const stack = loaded.filter(Boolean);
    return stack.length ? { stack } : null;
}

// "X min / h / d ago" for a station's latest reading
function buoyAgo(time) {
    const readAt = new Date(time.replace(' ', 'T'));
    const agoMin = Math.max(0, Math.round((Date.now() - readAt.getTime()) / 60000));
    return agoMin < 60 ? `${agoMin} min ago`
        : agoMin < 1440 ? `${Math.round(agoMin / 60)} h ago`
            : `${Math.round(agoMin / 1440)} d ago`;
}

function drawBuoyChart(data) {
    const container = document.getElementById('waveChartContainer');
    const legend = document.getElementById('modelLegend');
    if (!container) return;

    const old = container.querySelector('.buoy-overlay-svg');
    if (old) old.remove();

    // Enter buoy mode (taller chart, forecast cards hidden) and open the side
    // gutters BEFORE measuring — both reflow the container, and the chart's
    // new height must be in effect so the lines fill it. Unfolded cards are
    // folded first so the hero isn't left compact over the buoy view (and
    // the forecast comes back folded when it's closed).
    if (window.foldForecastCards) window.foldForecastCards();
    document.body.classList.add('buoys-mode');
    // Stacked view (the spot's closest stations): one band per station, all
    // on one shared time axis, each with its own height scale and a header
    // naming it with its latest reading. A single station is a one-band
    // stack without the header.
    const stations = data.stack || [data];
    const stacked = !!data.stack;
    document.body.classList.toggle('buoys-stack', stacked);
    const section = document.getElementById('waveChartSection');
    if (section) section.classList.add('models-gutters');

    const parseT = s => new Date(s.replace(' ', 'T')).getTime();
    const now = Date.now();
    const PADX = 6;

    // No fixed window: span the FULL extent of the data. tMin is the earliest
    // observation, tMax the latest forecast point across every series (and
    // station) — so all the past observations stay and each model is drawn
    // to wherever its own horizon ends (GFS Wave ~16 d, MFWAM ~10, MOP ~8).
    const series = stations.map(st => ({
        st,
        obs: (st.observed || []).map(p => ({ t: parseT(p.t), v: p.v })),
        // Keep period/direction: the tooltip reports them like the spot view.
        modelPts: (st.models || []).map(model => ({
            model,
            pts: model.series.map(p => ({ t: parseT(p.t), v: p.v, p: p.p, d: p.d })),
        })),
    }));
    let tMin = Infinity, tMax = -Infinity;
    series.forEach(({ obs, modelPts }) => {
        obs.forEach(p => { tMin = Math.min(tMin, p.t); tMax = Math.max(tMax, p.t); });
        modelPts.forEach(m => m.pts.forEach(p => {
            tMin = Math.min(tMin, p.t); tMax = Math.max(tMax, p.t);
        }));
    });
    if (!isFinite(tMin)) { tMin = now - 3 * 86400e3; tMax = now + 8 * 86400e3; }

    // Measure the drawing area. buoys-mode grows the chart via a CSS height
    // transition, so suppress it for this frame so the container reports its
    // FINAL height, measure, then restore it. The timeline always fits the
    // visible width: the whole series in one view, no horizontal scroll (day
    // labels thin out when they get tight).
    const prevTransition = container.style.transition;
    container.style.transition = 'none';
    // eslint-disable-next-line no-unused-expressions
    container.offsetHeight; // force reflow at the final height
    const cs = getComputedStyle(container);
    const padTop = parseFloat(cs.paddingTop) || 0;
    const padBottom = parseFloat(cs.paddingBottom) || 0;
    const top = padTop;
    const barH = Math.max(container.clientHeight - padTop - padBottom, 100);
    container.style.transition = prevTransition;

    const W = container.clientWidth;
    const xFor = t => PADX + ((t - tMin) / (tMax - tMin)) * (W - 2 * PADX);

    // Now collapse the bars and switch on the buoy layout
    container.classList.add('buoys-active');

    // Bands top to bottom, the date row under the last. Stacked bands get a
    // header row and a gap below.
    const DATE_ROW = 26;
    // Narrow (phone): the header's latest reading drops under the name
    const narrowHead = W < 560;
    const HEAD = stacked ? (narrowHead ? 34 : 22) : 0;
    const GAP = stacked ? 10 : 0;
    const bandH = (barH - DATE_ROW) / stations.length;
    const bands = series.map(({ st, obs, modelPts }, i) => {
        const bTop = top + i * bandH;
        const yTop = bTop + 4 + HEAD;
        const yBottom = bTop + bandH - GAP;
        const projected = modelPts.map(({ model, pts }) => ({
            model,
            pts: pts.map(p => ({ x: xFor(p.t), v: p.v, t: new Date(p.t), p: p.p, d: p.d })),
        }));
        let vMax = 0;
        obs.forEach(p => { vMax = Math.max(vMax, p.v); });
        projected.forEach(({ pts }) => pts.forEach(p => { vMax = Math.max(vMax, p.v); }));
        const scaleMax = Math.max(vMax, 0.1) * 1.1;
        const yFor = v => yTop + (yBottom - yTop) * (1 - v / scaleMax);
        return { st, obs, projected, bTop, yTop, yBottom, scaleMax, yFor };
    });
    const plotTop = bands[0].yTop;
    const plotBottom = bands[bands.length - 1].yBottom;

    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('class', 'model-overlay-svg buoy-overlay-svg');
    svg.setAttribute('width', W);
    svg.setAttribute('height', top + barH);
    const svgText = (x, y, text, attrs) => {
        const el = document.createElementNS(ns, 'text');
        el.textContent = text;
        el.setAttribute('x', x);
        el.setAttribute('y', y);
        Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
        svg.appendChild(el);
        return el;
    };

    // Day separators + date labels: the whole series fits the width, so
    // label every Nth day — as few as keep a "12 Mon" label (~46px) clear
    // of the next one (every day on desktop, every 3rd on a phone).
    const nDays = (tMax - tMin) / 86400e3;
    const DAY_LABEL_PX = 46;
    const labelEvery = Math.max(1, Math.ceil(DAY_LABEL_PX / (W / nDays)));
    const dayStart = new Date(tMin);
    dayStart.setHours(0, 0, 0, 0);
    dayStart.setDate(dayStart.getDate() + 1);
    let dayIdx = 0;
    for (let d = new Date(dayStart); d.getTime() <= tMax; d.setDate(d.getDate() + 1), dayIdx++) {
        const x = xFor(d.getTime());
        bands.forEach(bd => {
            const line = document.createElementNS(ns, 'line');
            line.setAttribute('x1', x.toFixed(1));
            line.setAttribute('x2', x.toFixed(1));
            line.setAttribute('y1', bd.yTop);
            line.setAttribute('y2', bd === bands[bands.length - 1] ? bd.yBottom + 6 : bd.yBottom);
            line.setAttribute('stroke', 'rgba(0,0,0,0.09)');
            line.setAttribute('stroke-dasharray', '2 3');
            svg.appendChild(line);
        });
        if (dayIdx % labelEvery === 0) {
            svgText((x + 4).toFixed(1), top + barH - 8,
                d.toLocaleDateString('en-US', { weekday: 'short', day: 'numeric' }),
                { 'font-size': '11', 'font-weight': '600', fill: '#475569' });
        }
    }

    // Station headers (stacked): name + distance on the left, its latest
    // reading on the right — the updated observed point, in words.
    if (stacked) {
        const hsUnit = getHeightUnit(currentUnit);
        bands.forEach((bd, i) => {
            const y = bd.bTop + 15;
            const fs = narrowHead ? '11' : '12';
            // "1. " + station name + " · 18 km": only the name is a link (to
            // that station's own view), growing a touch on hover
            const head = svgText(PADX, y, `${i + 1}. `,
                { 'font-size': fs, 'font-weight': '700', fill: '#131929' });
            const name = document.createElementNS(ns, 'tspan');
            name.textContent = bd.st.label;
            name.classList.add('buoy-band-name');
            name.style.pointerEvents = 'all'; // the svg itself is click-through
            const tip = document.createElementNS(ns, 'title');
            tip.textContent = `Open ${bd.st.label}`;
            name.appendChild(tip);
            name.addEventListener('click', (e) => {
                e.stopPropagation();
                openBuoyView(String(bd.st.id));
            });
            head.appendChild(name);
            const dist = document.createElementNS(ns, 'tspan');
            dist.textContent = bd.st._km != null ? ` · ${Math.round(bd.st._km)} km` : '';
            dist.setAttribute('font-weight', '500');
            dist.setAttribute('fill', '#64748b');
            head.appendChild(dist);
            const o = bd.st.latest;
            const latest = o
                ? `Latest ${formatHeight(o.hs, currentUnit)}${hsUnit}` +
                  (o.tp != null ? ` · ${o.tp}s` : '') +
                  (o.dp != null ? ` · ${o.dp}°` : '') +
                  ` · ${buoyAgo(o.time)}`
                : 'No recent observations';
            svgText(narrowHead ? PADX : W - PADX, narrowHead ? y + 14 : y, latest, {
                'font-size': fs, 'font-weight': '600', fill: '#475569',
                'text-anchor': narrowHead ? 'start' : 'end',
            });
            // Hairline under each band but the last (the date row follows)
            if (i < bands.length - 1) {
                const sep = document.createElementNS(ns, 'line');
                sep.setAttribute('x1', 0);
                sep.setAttribute('x2', W);
                sep.setAttribute('y1', (bd.bTop + bandH - GAP / 2).toFixed(1));
                sep.setAttribute('y2', (bd.bTop + bandH - GAP / 2).toFixed(1));
                sep.setAttribute('stroke', 'rgba(0,0,0,0.08)');
                svg.appendChild(sep);
            }
        });
    }

    // "Now" line, through every band: everything to its left is checkable
    // against the dots
    bands.forEach(bd => {
        const nowLine = document.createElementNS(ns, 'line');
        nowLine.setAttribute('x1', xFor(now).toFixed(1));
        nowLine.setAttribute('x2', xFor(now).toFixed(1));
        nowLine.setAttribute('y1', bd.yTop - 2);
        nowLine.setAttribute('y2', bd.yBottom);
        nowLine.setAttribute('stroke', '#52514e');
        nowLine.setAttribute('stroke-dasharray', '5 4');
        nowLine.setAttribute('opacity', '0.6');
        svg.appendChild(nowLine);
    });

    const tooltip = document.getElementById('globalChartTooltip');
    const toPathD = pts =>
        pts.map((p, i) => (i ? 'L ' : 'M ') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ');

    // Two-way hover link between series and legend chips, plus click-to-pin —
    // the same interaction the models overlay uses. A series id spans every
    // band, so highlighting a model lights its line at all three stations.
    const refs = {};
    const refFor = (id) => refs[id] || (refs[id] =
        { paths: [], areas: [], hits: [], dots: [], baseWidth: 1.5, baseOpacity: '0.85' });
    let pinned = null;
    const setHot = (id, on) => {
        // Hovering another chip still previews it over a pin; leaving it
        // falls back to the pinned one.
        if (pinned && !on) { id = pinned; on = true; }
        Object.entries(refs).forEach(([oid, ref]) => {
            const hot = on && oid === id;
            ref.paths.forEach(p => {
                p.setAttribute('stroke-width', ref.baseWidth + (hot ? 1.5 : 0));
                p.setAttribute('opacity', on && !hot ? '0.25' : ref.baseOpacity);
            });
            ref.dots.forEach(c => {
                c.setAttribute('opacity', on && !hot ? '0.2' : '0.75');
                c.setAttribute('r', hot ? 3.2 : 2.5);
            });
            // Wash under the highlighted series only
            ref.areas.forEach(a => { a.style.opacity = hot ? '1' : '0'; });
            if (ref.chip) ref.chip.classList.toggle('legend-chip--hot', hot);
        });
    };
    const togglePin = (id) => {
        pinned = pinned === id ? null : id;
        Object.entries(refs).forEach(([oid, ref]) => {
            // Mobile keeps the plot inert whatever the pin state: the legend
            // chips are the only handle on a series there.
            const mute = isMobileView() || (pinned && oid !== pinned);
            ref.hits.forEach(h => { h.style.pointerEvents = mute ? 'none' : 'stroke'; });
            ref.dots.forEach(c => { c.style.pointerEvents = mute ? 'none' : 'all'; });
            if (ref.chip) ref.chip.classList.toggle('legend-chip--pinned', pinned === oid);
        });
        setHot(pinned, !!pinned);
        publishLegendHeight();  // the revealed name can rewrap the legend
    };
    // Background click unpins — desktop only, as in the models overlay.
    svg.addEventListener('click', () => {
        if (isMobileView()) return;
        if (pinned) togglePin(pinned);
    });

    const showTooltip = (clientX, pts, color, id) => {
        if (!tooltip || pts.length === 0) return;
        const rect = svg.getBoundingClientRect();
        const mx = clientX - rect.left;
        let best = pts[0];
        for (const p of pts) {
            if (Math.abs(p.x - mx) < Math.abs(best.x - mx)) best = p;
        }
        const val = formatHeight(best.v, currentUnit);
        const unit = getHeightUnit(currentUnit);
        // No time here: the crosshair label above the cursor already gives it.
        tooltip.innerHTML =
            `<span style="font-size: 0.9rem; font-weight: 700;">` +
            `<i style="display:inline-block;width:10px;height:3px;border-radius:2px;` +
            `vertical-align:middle;background:${color}"></i> ` +
            `${val}${unit}${overlayPdSuffix(best)}</span>`;
        const cx = Math.min(Math.max(rect.left + best.x, 10), window.innerWidth - 10);
        tooltip.style.left = `${cx}px`;
        tooltip.style.top = `${rect.top + best.y}px`;
        tooltip.style.transform = 'translate(-50%, -140%)';
        tooltip.classList.add('visible');
        setHot(id, true);
    };
    const hideTooltip = (id) => {
        if (tooltip) tooltip.classList.remove('visible');
        setHot(id, false);
    };

    // Hour crosshair, as in the spot view. The bar-based version can't serve
    // here (this view collapses the bar wrappers to zero width), so drive it
    // off this chart's own time scale: invert xFor to read the hovered time.
    {
        // Reuse the pair the bar chart already put in this container — making
        // our own would leave two stacked crosshairs after a view toggle.
        let cross = container.querySelector('.chart-crosshair');
        let label = container.querySelector('.chart-crosshair-label');
        if (!cross) {
            cross = document.createElement('div');
            cross.className = 'chart-crosshair';
            container.appendChild(cross);
        }
        if (!label) {
            label = document.createElement('div');
            label.className = 'chart-crosshair-label';
            container.appendChild(label);
        }

        if (container._buoyCrossMove) {
            container.removeEventListener('mousemove', container._buoyCrossMove);
            container.removeEventListener('mouseleave', container._buoyCrossHide);
        }
        // The bar chart's handler reads the (now collapsed) wrappers and drives
        // the same elements — stand it down while this view owns them. The next
        // bar render re-attaches it.
        if (container._crosshairMove) {
            container.removeEventListener('mousemove', container._crosshairMove);
            container.removeEventListener('mouseleave', container._crosshairHide);
        }
        container._buoyCrossMove = (e) => {
            const rect = container.getBoundingClientRect();
            const x = e.clientX - rect.left + container.scrollLeft;
            // xFor is linear in t, so invert it across the plotted span.
            const frac = (x - PADX) / Math.max(1, W - 2 * PADX);
            if (frac < 0 || frac > 1) { container._buoyCrossHide(); return; }
            const t = new Date(tMin + frac * (tMax - tMin));
            cross.style.left = `${x}px`;
            label.style.left = `${x}px`;
            label.textContent = t.toLocaleTimeString(
                'en-US', { hour: 'numeric', hour12: true });
            cross.classList.add('visible');
            label.classList.add('visible');
        };
        container._buoyCrossHide = () => {
            cross.classList.remove('visible');
            label.classList.remove('visible');
        };
        container.addEventListener('mousemove', container._buoyCrossMove);
        container.addEventListener('mouseleave', container._buoyCrossHide);
    }

    bands.forEach(({ obs, projected, yFor, yBottom }) => {
        // Forecast lines: MOP keeps its reserved red and reads heavier — it
        // is the site-exact forecast the others are being judged against
        projected.forEach(({ model, pts }) => {
            pts.forEach(p => { p.y = yFor(p.v); });
            if (pts.length < 2) return;

            const color = BUOY_MODEL_COLORS[model.id] || '#64748b';
            const isMop = model.id === 'mop';
            const ref = refFor(model.id);
            ref.baseWidth = isMop ? 2.2 : 1.5;
            ref.baseOpacity = isMop ? '1' : '0.85';

            // The final 24 h of every line is drawn dashed — the forecast is
            // running out — exactly like the site's models view.
            const cutoff = pts[pts.length - 1].t.getTime() - 24 * 3600 * 1000;
            const solidPts = pts.filter(p => p.t.getTime() <= cutoff);
            const dashPts = pts.filter(p => p.t.getTime() >= cutoff);
            if (solidPts.length) dashPts.unshift(solidPts[solidPts.length - 1]);

            [[solidPts, false], [dashPts, true]].forEach(([segment, dashed]) => {
                if (segment.length < 2) return;
                const path = document.createElementNS(ns, 'path');
                path.setAttribute('d', toPathD(segment));
                path.setAttribute('fill', 'none');
                path.setAttribute('stroke', color);
                path.setAttribute('stroke-width', ref.baseWidth);
                path.setAttribute('stroke-linejoin', 'round');
                path.setAttribute('opacity', ref.baseOpacity);
                if (dashed) path.setAttribute('stroke-dasharray', '5 4');
                svg.appendChild(path);
                ref.paths.push(path);
            });
            // Fade the wash out across the dashed tail (every buoy line has one).
            const dashStartX = dashPts.length >= 2 ? dashPts[0].x : null;
            const area = addSeriesArea(svg, ns, pts, color, yBottom, dashStartX);
            if (area) ref.areas.push(area);

            // Invisible wide twin of the whole line as the hover hit area
            const hit = document.createElementNS(ns, 'path');
            hit.setAttribute('d', toPathD(pts));
            hit.setAttribute('fill', 'none');
            hit.setAttribute('stroke', 'transparent');
            hit.setAttribute('stroke-width', 12);
            // Inert on mobile — nothing hovers, highlights, or steals the tap.
            hit.style.pointerEvents = isMobileView() ? 'none' : 'stroke';
            hit.style.cursor = 'pointer';
            hit.addEventListener('mousemove', e => showTooltip(e.clientX, pts, color, model.id));
            hit.addEventListener('touchstart', e => {
                if (e.touches.length) showTooltip(e.touches[0].clientX, pts, color, model.id);
            }, { passive: true });
            hit.addEventListener('mouseleave', () => hideTooltip(model.id));
            hit.addEventListener('click', e => {
                if (isMobileView()) return;   // legend chips own pinning on mobile
                e.stopPropagation();
                togglePin(model.id);
            });
            svg.appendChild(hit);
            ref.hits.push(hit);
        });

        // Observed Hs on top: ink dots, the ground truth
        const obsPts = obs.map(p => ({ x: xFor(p.t), y: yFor(p.v), v: p.v, t: new Date(p.t) }));
        const ref = refFor('observed');
        obsPts.forEach(p => {
            const c = document.createElementNS(ns, 'circle');
            c.setAttribute('cx', p.x.toFixed(1));
            c.setAttribute('cy', p.y.toFixed(1));
            c.setAttribute('r', 2.5);
            c.setAttribute('fill', BUOY_INK);
            c.setAttribute('opacity', '0.75');
            c.style.pointerEvents = isMobileView() ? 'none' : 'all';
            c.style.cursor = 'pointer';
            c.addEventListener('mousemove', e => showTooltip(e.clientX, obsPts, BUOY_INK, 'observed'));
            c.addEventListener('touchstart', e => {
                if (e.touches.length) showTooltip(e.touches[0].clientX, obsPts, BUOY_INK, 'observed');
            }, { passive: true });
            c.addEventListener('mouseleave', () => hideTooltip('observed'));
            c.addEventListener('click', e => {
                if (isMobileView()) return;
                e.stopPropagation();
                togglePin('observed');
            });
            svg.appendChild(c);
            ref.dots.push(c);
        });
        // The latest observation, ringed: where the truth stops and the
        // forecasts carry on alone
        if (obsPts.length) {
            const last = obsPts[obsPts.length - 1];
            const ring = document.createElementNS(ns, 'circle');
            ring.setAttribute('cx', last.x.toFixed(1));
            ring.setAttribute('cy', last.y.toFixed(1));
            ring.setAttribute('r', 5);
            ring.setAttribute('fill', 'none');
            ring.setAttribute('stroke', BUOY_INK);
            ring.setAttribute('stroke-width', 1.5);
            ring.style.pointerEvents = 'none';
            svg.appendChild(ring);
        }
    });
    if (refs.observed && refs.observed.dots.length === 0) delete refs.observed;

    // Fade in over the bars fading out (shared CSS transition). The timeline
    // fits the view, so there's nothing to scroll — pin it at the left edge.
    svg.style.opacity = '0';
    container.appendChild(svg);
    container.scrollLeft = 0;
    requestAnimationFrame(() => requestAnimationFrame(() => {
        svg.style.opacity = '1';
    }));

    // One y-axis per band, each on its own station's scale
    const axisRender = () => bands.forEach((bd, i) => renderYAxisTicks(bd.scaleMax,
        v => svg.getBoundingClientRect().top + bd.yFor(v), false, i > 0));
    axisRender();
    container._axisRender = axisRender;

    if (legend) {
        // No title chip: the hero caption names the station(s). Every model
        // any station carries, first-seen order.
        const models = [];
        stations.forEach(st => (st.models || []).forEach(m => {
            if (!models.some(x => x.id === m.id)) models.push(m);
        }));
        const chips = [
            // Dot series, so the swatch is a dot — matches how it's plotted.
            `<span class="legend-chip legend-chip--dot" data-series-id="observed">` +
            `<i style="background:${BUOY_INK};width:6px;height:6px;border-radius:50%"></i>` +
            `<span class="legend-chip-name">observed Hs</span></span>`,
            ...models.map(m =>
                `<span class="legend-chip" data-series-id="${m.id}">` +
                `<i style="background:${BUOY_MODEL_COLORS[m.id] || '#64748b'}"></i>` +
                `<span class="legend-chip-name">${m.label}</span></span>`),
        ];
        legend.innerHTML = legendCountChip(chips.length) + chips.join('');
        legend.classList.add('visible');
        legend.querySelectorAll('.legend-chip[data-series-id]').forEach(chip => {
            const id = chip.dataset.seriesId;
            if (refs[id]) refs[id].chip = chip;
            // Hover is desktop-only: a tap synthesises mouseenter with no
            // matching mouseleave, which would re-light a just-unpinned chip.
            chip.addEventListener('mouseenter', () => {
                if (!isMobileView()) setHot(id, true);
            });
            chip.addEventListener('mouseleave', () => {
                if (!isMobileView()) setHot(id, false);
            });
            chip.addEventListener('click', () => togglePin(id));
            chip.style.cursor = 'pointer';
        });
        publishLegendHeight();
    }
    refreshDayHighlight();

    renderBuoyStats(data);
}

// Latest-observation cards — the values CDIP's station page headlines
// (cdip.ucsd.edu/m/products/?stn=071p1): significant wave height, peak
// period, peak direction, water temp, and how long ago the buoy reported.
function renderBuoyStats(data) {
    const el = document.getElementById('buoyStats');
    if (!el) return;
    // Stacked view: each band's header carries its station's latest reading
    const o = data.stack ? null : data.latest;
    if (!o) {
        el.style.display = 'none';
        el.innerHTML = '';
        return;
    }

    const ago = buoyAgo(o.time);

    const hsVal = formatHeight(o.hs, currentUnit);
    const hsUnit = getHeightUnit(currentUnit);
    const cards = [
        { label: 'Wave Height', value: hsVal, unit: hsUnit },
        o.tp != null && { label: 'Peak Period', value: o.tp, unit: 's' },
        o.dp != null && {
            label: 'Direction', value: `${o.dp}°`, unit: '',
            arrow: o.dp,
        },
        o.sst != null && {
            label: 'Water Temp',
            value: currentUnit === 'imperial' ? Math.round(o.sst * 9 / 5 + 32) : o.sst,
            unit: currentUnit === 'imperial' ? '°F' : '°C',
        },
    ].filter(Boolean);

    el.innerHTML =
        `<div class="buoy-stats-head">` +
        `<span class="buoy-stats-title">${data.label}</span>` +
        `<span class="buoy-stats-sub">Latest observation &bull; ${ago}</span>` +
        `</div>` +
        `<div class="buoy-stats-grid">` +
        cards.map(c =>
            `<div class="buoy-stat-card">` +
            `<span class="buoy-stat-label">${c.label}</span>` +
            `<span class="buoy-stat-value">` +
            (c.arrow != null
                ? `<span class="swell-arrow" style="transform: rotate(${c.arrow + 90}deg)">➤</span> `
                : '') +
            `${c.value}${c.unit ? `<span class="buoy-stat-unit">${c.unit}</span>` : ''}` +
            `</span></div>`).join('') +
        `</div>`;
    el.style.display = 'block';
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