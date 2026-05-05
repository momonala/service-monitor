(function() {
    'use strict';

    const LOG_SPIKE_BUCKETS = 48;
    let activeLogSource = null;
    const logEntries = [];
    let reverseDirection = false;

    /**
     * Classify a log line and return a CSS class for coloring.
     * @param {string} line
     * @returns {string}
     */
    function logLineClass(line) {
        const lower = line.toLowerCase();
        if (lower.includes('error') || lower.includes('failed') || lower.includes('fatal')) return 'error';
        if (lower.includes('warn')) return 'warning';
        return '';
    }

    /**
     * Parse a journalctl timestamp from the start of a line.
     * Expected shape: 2026-05-04T14:52:57+0200 ...
     * @param {string} line
     * @returns {number|null}
     */
    function parseLogTimestamp(line) {
        const match = line.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})([+-]\d{2})(\d{2})/);
        if (!match) return null;

        const normalized = `${match[1]}${match[2]}:${match[3]}`;
        const parsed = Date.parse(normalized);
        return Number.isNaN(parsed) ? null : parsed;
    }

    /**
     * Parse datetime-local input value to a timestamp.
     * @param {string} value
     * @returns {number|null}
     */
    function parseFilterDateValue(value) {
        if (!value) return null;
        const parsed = Date.parse(value);
        return Number.isNaN(parsed) ? null : parsed;
    }

    /**
     * Check whether the given log entry passes the active timestamp filter.
     * @param {{timestamp:number|null}} entry
     * @param {'all'|'after'|'before'|'between'} mode
     * @param {number|null} startTs
     * @param {number|null} endTs
     * @returns {boolean}
     */
    function passesTimestampFilter(entry, mode, startTs, endTs) {
        if (mode === 'all') return true;
        if (entry.timestamp === null) return false;

        if (mode === 'after') return startTs === null ? true : entry.timestamp >= startTs;
        if (mode === 'before') return endTs === null ? true : entry.timestamp <= endTs;

        const afterStart = startTs === null ? true : entry.timestamp >= startTs;
        const beforeEnd = endTs === null ? true : entry.timestamp <= endTs;
        return afterStart && beforeEnd;
    }

    /**
     * Recompute visibility for all log lines based on active filters.
     */
    function applyLogFilters() {
        const modeEl = document.getElementById('logTimeFilterMode');
        if (!modeEl) return;
        syncLogDirection();

        const mode = modeEl.value;
        const startTs = parseFilterDateValue(document.getElementById('logTimeStart')?.value || '');
        const endTs = parseFilterDateValue(document.getElementById('logTimeEnd')?.value || '');
        const textFilter = document.getElementById('logTextFilter')?.value || '';
        const caseSensitive = document.getElementById('logCaseSensitive')?.checked || false;

        const normalizedNeedle = caseSensitive ? textFilter : textFilter.toLowerCase();

        let visibleCount = 0;
        const visibleEntries = [];
        for (const entry of logEntries) {
            const visibleByTime = passesTimestampFilter(entry, mode, startTs, endTs);

            const haystack = caseSensitive ? entry.line : entry.line.toLowerCase();
            const visibleByText = normalizedNeedle === '' || haystack.includes(normalizedNeedle);

            const isVisible = visibleByTime && visibleByText;
            entry.element.style.display = isVisible ? '' : 'none';
            if (isVisible) {
                visibleCount += 1;
                visibleEntries.push(entry);
            }
        }

        updateLogCounter(visibleCount, logEntries.length);
        renderLogSpikeChart(visibleEntries, mode, startTs, endTs);
    }

    /**
     * Toggle filter date input visibility based on selected mode.
     */
    function syncLogFilterFieldVisibility() {
        const mode = document.getElementById('logTimeFilterMode')?.value;
        const startField = document.getElementById('logTimeStart')?.closest('.log-filter-field');
        const endField = document.getElementById('logTimeEnd')?.closest('.log-filter-field');
        if (!mode || !startField || !endField) return;

        if (mode === 'all') {
            startField.classList.add('hidden');
            endField.classList.add('hidden');
            return;
        }
        if (mode === 'after') {
            startField.classList.remove('hidden');
            endField.classList.add('hidden');
            return;
        }
        if (mode === 'before') {
            startField.classList.add('hidden');
            endField.classList.remove('hidden');
            return;
        }

        startField.classList.remove('hidden');
        endField.classList.remove('hidden');
    }

    /**
     * Setup event listeners for log filtering controls.
     */
    function setupLogFilters() {
        const ids = ['logTimeFilterMode', 'logTimeStart', 'logTimeEnd', 'logTextFilter', 'logCaseSensitive', 'logReverseDirection'];

        for (const id of ids) {
            const control = document.getElementById(id);
            if (!control) continue;
            control.addEventListener('input', applyLogFilters);
            control.addEventListener('change', () => {
                if (id === 'logReverseDirection') {
                    syncLogDirection();
                }
                syncLogFilterFieldVisibility();
                applyLogFilters();
            });
        }

        syncLogFilterFieldVisibility();
        syncLogDirection();
        setDefaultTimeFilterValues();
        applyLogFilters();
    }

    /**
     * Render line counter for visible vs total log lines.
     * @param {number} visibleCount
     * @param {number} totalCount
     */
    function updateLogCounter(visibleCount, totalCount) {
        const counter = document.getElementById('logLineCounter');
        if (!counter) return;
        counter.textContent = `${visibleCount}/${totalCount} lines`;
    }

    /**
     * Return a datetime-local value for yesterday at 00:00.
     * @returns {string}
     */
    function getYesterdayStartLocalValue() {
        const now = new Date();
        now.setDate(now.getDate() - 1);
        now.setHours(0, 0, 0, 0);
        return toDatetimeLocalValue(now);
    }

    /**
     * Return a datetime-local value for current local time.
     * @returns {string}
     */
    function getNowLocalValue() {
        return toDatetimeLocalValue(new Date());
    }

    /**
     * Format a Date for datetime-local input values.
     * @param {Date} date
     * @returns {string}
     */
    function toDatetimeLocalValue(date) {
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        const hours = String(date.getHours()).padStart(2, '0');
        const minutes = String(date.getMinutes()).padStart(2, '0');
        return `${year}-${month}-${day}T${hours}:${minutes}`;
    }

    /**
     * Set default time filters: yesterday 00:00 to now.
     */
    function setDefaultTimeFilterValues() {
        const start = document.getElementById('logTimeStart');
        const end = document.getElementById('logTimeEnd');
        if (!start || !end) return;

        if (!start.value) {
            start.value = getYesterdayStartLocalValue();
        }
        if (!end.value) {
            end.value = getNowLocalValue();
        }
    }

    /**
     * Draw a small histogram of visible log timestamps.
     * @param {Array<{timestamp:number|null}>} visibleEntries
     * @param {'all'|'after'|'before'|'between'} mode
     * @param {number|null} startTs
     * @param {number|null} endTs
     */
    function renderLogSpikeChart(visibleEntries, mode, startTs, endTs) {
        const canvas = document.getElementById('logSpikeChart');
        if (!(canvas instanceof HTMLCanvasElement)) return;

        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        const cssWidth = Math.max(canvas.clientWidth, 1);
        const cssHeight = Math.max(canvas.clientHeight, 1);
        const ratio = window.devicePixelRatio || 1;
        const pixelWidth = Math.floor(cssWidth * ratio);
        const pixelHeight = Math.floor(cssHeight * ratio);

        if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
            canvas.width = pixelWidth;
            canvas.height = pixelHeight;
        }

        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.clearRect(0, 0, cssWidth, cssHeight);

        const timestamps = visibleEntries
            .map((entry) => entry.timestamp)
            .filter((timestamp) => timestamp !== null);

        let windowStart = startTs;
        let windowEnd = endTs;
        const nowTs = Date.now();

        if (mode === 'all') {
            if (timestamps.length > 0) {
                windowStart = Math.min(...timestamps);
                windowEnd = Math.max(...timestamps);
            } else {
                windowEnd = nowTs;
                windowStart = nowTs - 24 * 60 * 60 * 1000;
            }
        } else if (mode === 'after') {
            windowStart = startTs ?? (nowTs - 24 * 60 * 60 * 1000);
            windowEnd = nowTs;
        } else if (mode === 'before') {
            windowEnd = endTs ?? nowTs;
            windowStart = windowEnd - 24 * 60 * 60 * 1000;
        } else {
            windowStart = startTs ?? (nowTs - 24 * 60 * 60 * 1000);
            windowEnd = endTs ?? nowTs;
        }

        if (windowStart === null || windowEnd === null || windowEnd <= windowStart) {
            windowEnd = nowTs;
            windowStart = nowTs - 24 * 60 * 60 * 1000;
        }

        const buckets = new Array(LOG_SPIKE_BUCKETS).fill(0);
        const range = windowEnd - windowStart;

        for (const ts of timestamps) {
            if (ts < windowStart || ts > windowEnd) continue;
            const idx = Math.min(
                LOG_SPIKE_BUCKETS - 1,
                Math.floor(((ts - windowStart) / range) * LOG_SPIKE_BUCKETS),
            );
            buckets[idx] += 1;
        }

        const maxBucket = Math.max(...buckets, 1);
        const gap = 1;
        const barWidth = (cssWidth - (LOG_SPIKE_BUCKETS - 1) * gap) / LOG_SPIKE_BUCKETS;
        const chartTop = 2;
        const axisY = cssHeight - 16;
        const chartHeight = Math.max(axisY - chartTop, 1);

        ctx.fillStyle = 'rgba(10, 132, 255, 0.16)';
        ctx.fillRect(0, axisY, cssWidth, 1);

        ctx.fillStyle = 'rgba(10, 132, 255, 0.75)';
        for (let i = 0; i < LOG_SPIKE_BUCKETS; i++) {
            const height = Math.max(1, (buckets[i] / maxBucket) * chartHeight);
            const x = i * (barWidth + gap);
            const y = axisY - height;
            ctx.fillRect(x, y, barWidth, height);
        }

        drawXAxisLabels(ctx, cssWidth, axisY, windowStart, windowEnd);
    }

    /**
     * Draw readable start/mid/end labels for the chart time window.
     * @param {CanvasRenderingContext2D} ctx
     * @param {number} width
     * @param {number} axisY
     * @param {number} windowStart
     * @param {number} windowEnd
     */
    function drawXAxisLabels(ctx, width, axisY, windowStart, windowEnd) {
        const mid = windowStart + ((windowEnd - windowStart) / 2);
        const labels = [
            { x: 0, align: 'left', ts: windowStart },
            { x: width / 2, align: 'center', ts: mid },
            { x: width, align: 'right', ts: windowEnd },
        ];

        ctx.fillStyle = 'rgba(161, 161, 166, 0.92)';
        ctx.font = '10px -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif';
        ctx.textBaseline = 'top';

        for (const label of labels) {
            ctx.textAlign = label.align;
            ctx.fillText(formatAxisLabel(label.ts), label.x, axisY + 3);
        }
    }

    /**
     * Format a timestamp for compact, readable x-axis labels.
     * @param {number} timestamp
     * @returns {string}
     */
    function formatAxisLabel(timestamp) {
        const date = new Date(timestamp);
        const timeText = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const dayText = date.toLocaleDateString([], { month: 'short', day: 'numeric' });
        return `${dayText} ${timeText}`;
    }

    /**
     * Update the connection status badge.
     * @param {'connecting'|'connected'|'error'} state
     * @param {string} [label]
     */
    function setLogStreamStatus(state, label) {
        const badge = document.getElementById('logStreamStatus');
        if (!badge) return;
        badge.className = `log-stream-badge log-stream-badge--${state}`;
        badge.textContent = label ?? state;
    }

    /**
     * Append a single log line to the stream panel.
     * Preserves user scroll position — only auto-scrolls when already at the bottom.
     * @param {HTMLElement} el
     * @param {string} line
     */
    function appendLogLine(el, line) {
        const pinnedToEdge = isPinnedToActiveEdge(el);

        const span = document.createElement('span');
        const cls = logLineClass(line);
        if (cls) span.className = cls;
        span.textContent = line + '\n';
        if (reverseDirection) {
            el.prepend(span);
        } else {
            el.appendChild(span);
        }

        logEntries.push({
            line,
            timestamp: parseLogTimestamp(line),
            element: span,
        });

        applyLogFilters();

        if (pinnedToEdge) {
            scrollToActiveEdge(el);
        }
    }

    /**
     * Whether log list is pinned to the active insertion edge.
     * @param {HTMLElement} el
     * @param {boolean} reverse
     * @returns {boolean}
     */
    function isPinnedToActiveEdge(el, reverse = reverseDirection) {
        if (reverse) {
            return el.scrollTop < 40;
        }
        return el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    }

    /**
     * Scroll log list to insertion edge.
     * @param {HTMLElement} el
     * @param {boolean} reverse
     */
    function scrollToActiveEdge(el, reverse = reverseDirection) {
        if (reverse) {
            el.scrollTop = 0;
            return;
        }
        el.scrollTop = el.scrollHeight;
    }

    /**
     * Re-render current log DOM order for the selected direction.
     * @param {HTMLElement} logEl
     */
    function reorderLogEntries(logEl) {
        const fragment = document.createDocumentFragment();
        const entriesInDisplayOrder = reverseDirection ? [...logEntries].reverse() : logEntries;
        for (const entry of entriesInDisplayOrder) {
            fragment.appendChild(entry.element);
        }
        logEl.appendChild(fragment);
    }

    /**
     * Sync reverse-direction toggle state with stream rendering behavior.
     */
    function syncLogDirection() {
        const reverseToggle = document.getElementById('logReverseDirection');
        const logEl = document.getElementById('logStream');
        if (!(reverseToggle instanceof HTMLInputElement) || !logEl) return;

        const shouldReverse = reverseToggle.checked;
        if (shouldReverse === reverseDirection) return;

        const wasPinnedToEdge = isPinnedToActiveEdge(logEl, reverseDirection);
        reverseDirection = shouldReverse;
        reorderLogEntries(logEl);

        if (wasPinnedToEdge) {
            scrollToActiveEdge(logEl, reverseDirection);
        }
    }

    /**
     * Open an SSE connection to /logs/stream for the current service.
     * Closes any existing connection first.
     */
    function setupLogStream() {
        const logEl = document.getElementById('logStream');
        if (!logEl) return;

        const service = logEl.dataset.service;
        if (!service) return;

        if (activeLogSource) {
            activeLogSource.close();
            activeLogSource = null;
        }

        const source = new EventSource(`/logs/stream?service=${encodeURIComponent(service)}`);
        activeLogSource = source;

        source.onopen = () => setLogStreamStatus('connected', 'live');

        source.onmessage = (evt) => {
            let line;
            try {
                line = JSON.parse(evt.data);
            } catch {
                line = evt.data;
            }
            appendLogLine(logEl, line);
        };

        source.onerror = () => {
            setLogStreamStatus('error', 'disconnected');
            source.close();
            activeLogSource = null;
        };

        window.addEventListener('beforeunload', () => {
            source.close();
        }, { once: true });
    }

    function init() {
        setupLogFilters();
        setupLogStream();
        window.addEventListener('resize', applyLogFilters);
    }

    window.ServiceMonitorLogStream = {
        init,
    };
})();
