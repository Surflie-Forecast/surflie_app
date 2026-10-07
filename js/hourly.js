// 16-point compass name for a coming-from direction in degrees.
function compassName(deg) {
    const names = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                   'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
    return names[Math.round(((deg % 360) + 360) % 360 / 22.5) % 16];
}

// Toggle hourly details for a forecast card
async function toggleHourlyDetails(card, dateISO, spotName) {
    const detailsDiv = card.querySelector('.hourly-details');

    // If already open, close it
    if (detailsDiv.style.display === 'block') {
        detailsDiv.style.display = 'none';
        card.classList.remove('expanded');
        return;
    }

    // Show loading
    detailsDiv.style.display = 'block';
    card.classList.add('expanded');
    detailsDiv.innerHTML = '<div style="text-align:center; padding:1rem;">Loading hourly data...</div>';

    try {
        // Fetch from static JSON file organized by spot and date
        const response = await fetch(`api/hourly/${apiSpotId(spotName)}/${dateISO}.json`);
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
        html += '<div class="hourly-header"><span>Time</span><span></span><span>Surf</span><span>Wind</span></div>';

        hourlyData.forEach((hour, hourIdx) => {
            const timeObj = new Date(hour.datetime);
            const timeHour = timeObj.toLocaleTimeString('en-US', { hour: 'numeric', hour12: true }).split(' ')[0];
            const timePeriod = timeObj.toLocaleTimeString('en-US', { hour: 'numeric', hour12: true }).split(' ')[1];
            const swellHeight = formatHeight(hour.swell.height, currentUnit);
            const swellPeriod = Math.round(hour.swell.period);
            const swellDir = Math.round(hour.swell.direction);
            const windSpeed = Math.round(hour.wind.speed);
            const windDir = Math.round(hour.wind.direction);

            // Prefer the score computed by the backend (direction- and
            // spot-aware); fall back to the shared JS engine for older data.
            let color;
            if (typeof hour.color === 'string') {
                color = hour.color;
            } else {
                color = getRatingAndColor(
                    hour.swell.height, hour.swell.period, hour.wind.speed,
                    hour.swell.direction, hour.wind.direction).color;
            }

            // Swell-train breakdown (primary/secondary/tertiary, no wind
            // sea): rows with data expand on click.
            const swells = Array.isArray(hour.swells) ? hour.swells : [];
            const expandable = swells.length > 0;

            html += `
                <div class="hourly-row${expandable ? ' has-swells' : ''}" data-hour-idx="${hourIdx}">
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

            if (expandable) {
                // Surfline-style rows: height, period, arrow, compass + degrees,
                // with a "Swells" title beside the first row.
                html += `<div class="hourly-swell-breakdown" style="display:none;">`;
                html += `<span class="swell-breakdown-title">Swells</span>`;
                html += `<div class="swell-train-rows">`;
                swells.forEach(s => {
                    const h = formatHeight(s.height, currentUnit);
                    const d = Math.round(s.direction);
                    html += `
                        <div class="swell-train-row">
                            <span class="swell-train-h" data-val-m="${s.height}">${h}${getHeightUnit(currentUnit)}</span>
                            <span class="swell-train-p">${Math.round(s.period)}s</span>
                            <span class="swell-arrow-small" style="transform: rotate(${d + 90}deg)">➤</span>
                            <span class="swell-train-dir">${compassName(d)} ${d}°</span>
                        </div>`;
                });
                html += '</div></div>';
            }
        });

        html += '</div>';

        // Add Tide Chart Container
        html += `<div class="daily-tide-chart-container">
                    <div class="daily-tide-chart-header">Tide</div>
                    <div id="tide-chart-${dateISO}" class="daily-tide-chart-svg"></div>
                 </div>`;

        detailsDiv.innerHTML = html;

        // Wire the expandable rows. stopPropagation: a row click must not
        // bubble to the card's own click handler and fold the whole day.
        detailsDiv.querySelectorAll('.hourly-row.has-swells').forEach(row => {
            row.addEventListener('click', (e) => {
                e.stopPropagation();
                const breakdown = row.nextElementSibling;
                if (!breakdown) return;
                const open = breakdown.style.display !== 'none';
                breakdown.style.display = open ? 'none' : 'flex';
                row.classList.toggle('swells-open', !open);
            });
        });

        // Render the tide chart for this day
        renderDailyTideChart(spotName, dateISO, `tide-chart-${dateISO}`);

    } catch (error) {
        console.error('Error loading hourly data:', error);
        detailsDiv.innerHTML = '<div style="text-align:center; padding:1rem; color:red;">Error loading hourly data</div>';
    }
}

async function renderDailyTideChart(spotName, dateISO, containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    try {
        // We reuse the main tide file. 
        // Note: Using a global cache would be better but browser cache helps.
        const res = await fetch(`api/tide/${apiSpotId(spotName)}.json`);
        if (!res.ok) {
            container.innerHTML = '<div style="font-size:0.7rem;">Tide data unavailable</div>';
            return;
        }

        const json = await res.json();
        const fullData = json.dataset || [];
        const fullPeaks = json.peaks || [];

        // Filter for this specific day (Local Time vs UTC?) 
        // The dateISO is e.g. "2023-12-15".
        // The tide data is in local time strings "YYYY-MM-DD HH:MM:SS".
        // Simple string matching on the date part should work.

        const dayData = fullData.filter(d => d.t.startsWith(dateISO));
        const dayPeaks = fullPeaks.filter(p => p.t.startsWith(dateISO));

        if (dayData.length === 0) {
            container.innerHTML = '<div style="font-size:0.7rem;">No tide data for this day</div>';
            return;
        }

        drawDailyTideSvg(dayData, dayPeaks, container, dateISO);

    } catch (e) {
        console.warn("Error rendering daily tide:", e);
    }
}

function drawDailyTideSvg(data, peaks, container, dateISO) {
    const containerWidth = container.clientWidth;
    const height = container.clientHeight;

    if (containerWidth === 0) return; // Hidden?

    // Add horizontal margins for labels that extend beyond the chart
    const marginX = 20;
    const width = containerWidth - (marginX * 2);

    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("width", containerWidth);
    svg.setAttribute("height", height);
    svg.style.display = "block";
    svg.style.margin = "0 auto";
    svg.style.overflow = "visible";

    // Min/Max for just this day
    let minH = Infinity;
    let maxH = -Infinity;
    data.forEach(d => {
        if (d.v < minH) minH = d.v;
        if (d.v > maxH) maxH = d.v;
    });

    // Add significant padding for labels
    const range = maxH - minH;
    const safeRange = range === 0 ? 1 : range;
    const paddingY = 30; // Increased padding for top labels
    const paddingBottom = 15; // Space for X-axis labels
    const paddingBottomBuffer = 10; // Extra space below lowest value

    // Time Range: Midnight to Midnight for that day
    // Use strictly the day boundaries for X-axis to align 6am/12pm/6pm correctly
    const dateStr = data[0].t.split(' ')[0]; // YYYY-MM-DD
    const tStart = new Date(`${dateStr} 00:00:00`).getTime();
    const tEnd = new Date(`${dateStr} 23:59:59`).getTime();
    const tRange = tEnd - tStart;

    const getX = (t) => {
        const time = new Date(t).getTime();
        return ((time - tStart) / tRange) * width;
    };

    const getY = (v) => {
        const pct = (v - minH) / safeRange;
        // height - paddingBottom - paddingY_top...
        // Let's reserve paddingBottom for text, and paddingY for top clearance
        // Also reduce drawHeight by buffer, and lift the base by buffer
        const drawHeight = height - paddingBottom - paddingY - paddingBottomBuffer;
        return height - paddingBottom - paddingBottomBuffer - (pct * drawHeight);
    };

    // Path
    let d = "";
    data.forEach((pt, i) => {
        const x = getX(pt.t) + marginX;  // Add margin offset
        const y = getY(pt.v);

        // Clamp x to be safe
        const safeX = Math.max(marginX, Math.min(width + marginX, x));

        if (i === 0) d += `M ${safeX.toFixed(1)} ${y.toFixed(1)}`;
        else d += ` L ${safeX.toFixed(1)} ${y.toFixed(1)}`;
    });


    // Create gradient definition for fade effect
    const defs = document.createElementNS(ns, "defs");
    const gradient = document.createElementNS(ns, "linearGradient");
    const gradientId = `tideGradient-${dateISO}`;
    gradient.setAttribute("id", gradientId);
    gradient.setAttribute("x1", "0%");
    gradient.setAttribute("y1", "0%");
    gradient.setAttribute("x2", "0%");
    gradient.setAttribute("y2", "100%");

    const stop1 = document.createElementNS(ns, "stop");
    stop1.setAttribute("offset", "0%");
    stop1.setAttribute("stop-color", "rgb(0, 119, 190)");
    stop1.setAttribute("stop-opacity", "0.25");

    const stop2 = document.createElementNS(ns, "stop");
    stop2.setAttribute("offset", "100%");
    stop2.setAttribute("stop-color", "rgb(0, 119, 190)");
    stop2.setAttribute("stop-opacity", "0");

    gradient.appendChild(stop1);
    gradient.appendChild(stop2);
    defs.appendChild(gradient);
    svg.appendChild(defs);

    // Area
    const areaPath = document.createElementNS(ns, "path");
    areaPath.setAttribute("d", d + ` L ${width + marginX} ${height - paddingBottom} L ${marginX} ${height - paddingBottom} Z`);
    areaPath.setAttribute("fill", `url(#${gradientId})`);
    svg.appendChild(areaPath);

    // Zero Line (if visible)
    if (minH < 0 && maxH > 0) {
        const y0 = getY(0);
        const zeroLine = document.createElementNS(ns, "line");
        zeroLine.setAttribute("x1", marginX);
        zeroLine.setAttribute("y1", y0.toFixed(1));
        zeroLine.setAttribute("x2", width + marginX);
        zeroLine.setAttribute("y2", y0.toFixed(1));
        zeroLine.setAttribute("stroke", "rgba(0,0,0,0.1)");
        zeroLine.setAttribute("stroke-width", "1");
        svg.appendChild(zeroLine);
    }

    // Line
    const linePath = document.createElementNS(ns, "path");
    linePath.setAttribute("d", d);
    linePath.setAttribute("fill", "none");
    linePath.setAttribute("stroke", "#0077be");
    linePath.setAttribute("stroke-width", "2");
    svg.appendChild(linePath);

    // X-Axis Grid & Labels (6 AM, 12 PM, 6 PM)
    const axisTimes = [
        { hour: 6, label: '6am' },
        { hour: 12, label: '12pm' },
        { hour: 18, label: '6pm' }
    ];

    axisTimes.forEach(at => {
        const tVal = tStart + (at.hour * 3600 * 1000);
        const x = ((tVal - tStart) / tRange) * width + marginX;  // Add margin offset

        // Grid Line
        const line = document.createElementNS(ns, "line");
        line.setAttribute("x1", x.toFixed(1));
        line.setAttribute("y1", "0");
        line.setAttribute("x2", x.toFixed(1));
        line.setAttribute("y2", (height - paddingBottom).toFixed(1));
        line.setAttribute("stroke", "rgba(0,0,0,0.05)");
        line.setAttribute("stroke-dasharray", "2,2");
        svg.appendChild(line);

        // Label
        const txt = document.createElementNS(ns, "text");
        txt.textContent = at.label;
        txt.setAttribute("x", x.toFixed(1));
        txt.setAttribute("y", height.toFixed(1)); // Bottom
        txt.setAttribute("text-anchor", "middle");
        txt.setAttribute("font-size", "11px");
        txt.setAttribute("fill", "var(--text-secondary)");
        svg.appendChild(txt);
    });

    // Peaks
    peaks.forEach(p => {
        const px = getX(p.t) + marginX;  // Add margin offset
        const py = getY(p.v);

        // Dot
        const circle = document.createElementNS(ns, "circle");
        circle.setAttribute("cx", px.toFixed(1));
        circle.setAttribute("cy", py.toFixed(1));
        circle.setAttribute("r", "2.5");
        circle.setAttribute("fill", "#131929");
        svg.appendChild(circle);

        // Label
        const text = document.createElementNS(ns, "text");
        const valM = p.v;
        const formVal = formatHeight(valM, currentUnit || 'imperial');
        const unit = getHeightUnit(currentUnit || 'imperial');

        text.textContent = `${formVal}${unit}`;
        text.setAttribute("x", px.toFixed(1));
        text.setAttribute("text-anchor", "middle");
        text.setAttribute("font-size", "10px");
        text.setAttribute("font-weight", "bold");
        text.setAttribute("fill", "var(--text-secondary)");

        // Time
        const timeText = document.createElementNS(ns, "text");
        const dt = new Date(p.t);
        const timeStr = dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
        timeText.textContent = timeStr;
        timeText.setAttribute("x", px.toFixed(1));
        timeText.setAttribute("text-anchor", "middle");
        timeText.setAttribute("font-size", "9px");
        timeText.setAttribute("fill", "rgba(0,0,0,0.5)");

        // Place both ABOVE the point
        text.setAttribute("y", (py - 14).toFixed(1));
        timeText.setAttribute("y", (py - 5).toFixed(1));

        svg.appendChild(text);
        svg.appendChild(timeText);
    });

    container.appendChild(svg);
}
