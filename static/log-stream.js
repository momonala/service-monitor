(function() {
    'use strict';

    const LOG_SPIKE_BUCKETS = 48;
    const MAX_LOG_ENTRIES = 10000;
    const RECONNECT_BASE_MS = 2000;
    const RECONNECT_MAX_MS = 30000;

    let activeLogSource = null;
    let reconnectDelay = RECONNECT_BASE_MS;
    let reconnectTimer = null;
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

    const RELATIVE_WINDOW_MS = {
        last5m:  5 * 60 * 1000,
        last15m: 15 * 60 * 1000,
        last30m: 30 * 60 * 1000,
        last60m: 60 * 60 * 1000,
        last1d:  24 * 60 * 60 * 1000,
    };

    /**
     * Check whether the given log entry falls within [startTs, endTs].
     * Passing null for both means "all time".
     * @param {{timestamp:number|null}} entry
     * @param {number|null} startTs
     * @param {number|null} endTs
     * @returns {boolean}
     */
    function passesTimestampFilter(entry, startTs, endTs) {
        if (startTs === null && endTs === null) return true;
        if (entry.timestamp === null) return false;
        const afterStart = startTs === null || entry.timestamp >= startTs;
        const beforeEnd  = endTs   === null || entry.timestamp <= endTs;
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
        const nowTs = Date.now();
        const offsetMs = RELATIVE_WINDOW_MS[mode] ?? null;
        const startTs = offsetMs !== null ? nowTs - offsetMs : null;
        const endTs   = offsetMs !== null ? nowTs : null;

        const textFilter = document.getElementById('logTextFilter')?.value || '';
        const caseSensitive = document.getElementById('logCaseSensitive')?.checked || false;
        const normalizedNeedle = caseSensitive ? textFilter : textFilter.toLowerCase();

        let visibleCount = 0;
        const visibleEntries = [];
        for (const entry of logEntries) {
            const visibleByTime = passesTimestampFilter(entry, startTs, endTs);
            const haystack = caseSensitive ? entry.line : entry.line.toLowerCase();
            const visibleByText = normalizedNeedle === '' || haystack.includes(normalizedNeedle);

            const isVisible = visibleByTime && visibleByText;
            entry.element.style.display = isVisible ? '' : 'none';
            if (isVisible) {
                visibleCount += 1;
                visibleEntries.push(entry);
            }
        }

        renderLogSpikeChart(visibleEntries, startTs, endTs);
    }

    /**
     * Setup event listeners for log filtering controls.
     * Uses only 'change' to avoid double-firing on checkboxes/selects.
     */
    function setupLogFilters() {
        const ids = ['logTimeFilterMode', 'logTextFilter', 'logCaseSensitive', 'logReverseDirection'];

        for (const id of ids) {
            const control = document.getElementById(id);
            if (!control) continue;
            // Use 'input' for text fields, 'change' for everything else to avoid double-firing
            const eventType = (control.type === 'text' || control.tagName === 'SELECT') ? 'input' : 'change';
            control.addEventListener(eventType, () => {
                if (id === 'logReverseDirection') syncLogDirection();
                applyLogFilters();
            });
        }

        syncLogDirection();
        applyLogFilters();
    }

    /**
     * Draw a small histogram of visible log timestamps.
     * @param {Array<{timestamp:number|null}>} visibleEntries
     * @param {number|null} startTs
     * @param {number|null} endTs
     */
    function renderLogSpikeChart(visibleEntries, startTs, endTs) {
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

        if (windowStart === null || windowEnd === null) {
            if (timestamps.length > 0) {
                windowStart = timestamps.reduce((a, b) => Math.min(a, b));
                windowEnd = timestamps.reduce((a, b) => Math.max(a, b));
            } else {
                windowEnd = nowTs;
                windowStart = nowTs - 24 * 60 * 60 * 1000;
            }
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

        const maxBucket = buckets.reduce((a, b) => Math.max(a, b), 1);
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
     * Append a single log line to the stream panel.
     * Preserves user scroll position — only auto-scrolls when already at the bottom.
     * Evicts oldest entries when MAX_LOG_ENTRIES is reached.
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

        // Evict oldest entries when cap is reached
        if (logEntries.length > MAX_LOG_ENTRIES) {
            const removed = logEntries.splice(0, logEntries.length - MAX_LOG_ENTRIES);
            for (const entry of removed) {
                entry.element.remove();
            }
        }

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
     * Closes any existing connection first. Reconnects on error with exponential backoff.
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
        if (reconnectTimer !== null) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }

        const source = new EventSource(`/logs/stream?service=${encodeURIComponent(service)}`);
        activeLogSource = source;

        source.onopen = () => {
            reconnectDelay = RECONNECT_BASE_MS;
        };

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
            source.close();
            activeLogSource = null;
            reconnectTimer = setTimeout(() => {
                reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
                setupLogStream();
            }, reconnectDelay);
        };

        window.addEventListener('beforeunload', () => {
            if (reconnectTimer !== null) clearTimeout(reconnectTimer);
            source.close();
        }, { once: true });
    }

    let resizeFilterTimer = null;

    function init() {
        setupLogFilters();
        setupLogStream();
        window.addEventListener('resize', () => {
            clearTimeout(resizeFilterTimer);
            resizeFilterTimer = setTimeout(applyLogFilters, 150);
        });
    }

    window.ServiceMonitorLogStream = {
        init,
    };
})();
