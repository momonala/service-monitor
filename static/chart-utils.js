// Shared pieces of the system and per-service metric charts: theming, segmented
// controls, tooltip formatting, and the Chart.js scaffolding both build on.
// Kept dependency-free apart from Chart.js itself.
(function() {
    'use strict';

    const REFRESH_INTERVAL = 30000;
    const WINDOWS = new Set(['1h', '6h', '24h', '7d']);
    const ROLLUPS = new Set(['30s', '2m', '10m', '30m']);
    const TOOLTIP_FONT = "'SF Mono', Monaco, 'Cascadia Code', Consolas, monospace";
    const AXIS_FONT_SIZE = 10;
    const AXIS_MAX_TICKS = 6;

    function cssToken(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    function withOpacity(color, alpha) {
        const hex = color.replace('#', '');
        if (hex.length !== 6) return color;
        const r = parseInt(hex.slice(0, 2), 16);
        const g = parseInt(hex.slice(2, 4), 16);
        const b = parseInt(hex.slice(4, 6), 16);
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    /** The theme tokens every chart surface reads, resolved once per chart build. */
    function chartTheme() {
        return {
            muted: cssToken('--color-text-muted'),
            panel: cssToken('--color-bg-secondary'),
            border: cssToken('--border-color'),
            textPrimary: cssToken('--color-text-primary'),
        };
    }

    function readLocal(key) {
        try {
            return localStorage.getItem(key);
        } catch {
            return null;
        }
    }

    function writeLocal(key, value) {
        try {
            localStorage.setItem(key, value);
        } catch {
            // Ignore quota / private-mode failures.
        }
    }

    function setPressed(btn, active) {
        btn.classList.toggle('is-active', active);
        btn.setAttribute('aria-pressed', String(active));
    }

    function syncChoiceGroup(root, selector, dataKey, activeValue) {
        root.querySelectorAll(selector).forEach((btn) => {
            setPressed(btn, btn.dataset[dataKey] === activeValue);
        });
        window.SMTransitions?.syncPills(root);
    }

    /**
     * Wire a segmented control (the rollup / lookback pills) to a single value.
     * Selecting a new allowed value repaints the pressed state and notifies.
     * @returns {{value: string}} live view of the current selection
     */
    function createChoiceGroup(root, selector, dataKey, allowed, initial, onChange) {
        const group = { value: initial };
        syncChoiceGroup(root, selector, dataKey, initial);
        root.querySelectorAll(selector).forEach((btn) => {
            btn.addEventListener('click', () => {
                const next = btn.dataset[dataKey];
                if (!allowed.has(next) || next === group.value) return;
                group.value = next;
                syncChoiceGroup(root, selector, dataKey, next);
                onChange(next);
            });
        });
        return group;
    }

    /** Wire the per-series show/hide toggles onto a {seriesId: boolean} map. */
    function bindSeriesToggles(root, visibleSeries, onToggle) {
        root.querySelectorAll('.system-chart__toggle').forEach((btn) => {
            btn.addEventListener('click', () => {
                const id = btn.dataset.series;
                if (!(id in visibleSeries)) return;
                visibleSeries[id] = !visibleSeries[id];
                setPressed(btn, visibleSeries[id]);
                onToggle();
            });
        });
    }

    function applyHiddenSeries(chart, visibleSeries) {
        chart.data.datasets.forEach((dataset) => {
            dataset.hidden = !visibleSeries[dataset.seriesId];
        });
        chart.update('none');
    }

    function padCell(value, width, align = 'left') {
        const text = String(value);
        if (text.length >= width) return text.slice(0, width);
        const padding = ' '.repeat(width - text.length);
        return align === 'right' ? padding + text : text + padding;
    }

    function formatTooltipValue(value) {
        return value == null ? '—' : String(value);
    }

    // Compact x-axis label; wider windows show the date, narrow ones just the time.
    function formatXTick(value, window) {
        const date = new Date(value);
        if (window === '7d' || window === '24h') {
            return date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit' });
        }
        return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    }

    /** Epoch-millisecond x axis. `getWindow` is read per tick so range changes relabel. */
    function timeScale(theme, getWindow) {
        return {
            type: 'linear',
            bounds: 'data',
            offset: false,
            ticks: {
                color: theme.muted,
                font: { size: AXIS_FONT_SIZE },
                maxTicksLimit: AXIS_MAX_TICKS,
                callback: (value) => formatXTick(value, getWindow()),
            },
            grid: { color: theme.border },
            border: { color: theme.border },
        };
    }

    /** Tick/grid styling for a y axis; callers supply the range and any label callback. */
    function valueScale(theme, options) {
        return {
            type: 'linear',
            position: 'left',
            display: true,
            grid: { color: theme.border, drawOnChartArea: true },
            border: { color: theme.border },
            ...options,
            ticks: {
                color: theme.muted,
                font: { size: AXIS_FONT_SIZE },
                maxTicksLimit: AXIS_MAX_TICKS,
                ...options.ticks,
            },
        };
    }

    /** Monospaced tooltip shared by both charts; `overrides.callbacks` merge onto the defaults. */
    function buildTooltip(theme, overrides = {}) {
        const { callbacks, ...rest } = overrides;
        return {
            backgroundColor: theme.panel,
            borderColor: theme.border,
            borderWidth: 1,
            titleColor: theme.muted,
            bodyColor: theme.textPrimary,
            displayColors: true,
            boxWidth: 10,
            boxHeight: 10,
            boxPadding: 4,
            titleFont: { family: TOOLTIP_FONT, size: 11, weight: '500' },
            bodyFont: { family: TOOLTIP_FONT, size: 11, weight: '400' },
            ...rest,
            callbacks: {
                title(items) {
                    return items.length ? new Date(items[0].parsed.x).toLocaleString() : '';
                },
                labelColor(ctx) {
                    const color = ctx.dataset.borderColor || theme.textPrimary;
                    return { borderColor: color, backgroundColor: color, borderWidth: 0 };
                },
                labelTextColor(ctx) {
                    return ctx.dataset.borderColor || theme.textPrimary;
                },
                ...callbacks,
            },
        };
    }

    function createChart(canvas, { datasets, scales, tooltip, plugins = [] }) {
        if (typeof Chart === 'undefined') {
            throw new Error('Chart.js failed to load');
        }
        return new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: { datasets },
            plugins,
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                interaction: { mode: 'nearest', axis: 'x', intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip,
                },
                scales,
            },
        });
    }

    /** Load a history payload into every dataset, keyed by each one's `sampleKey`. */
    function applySamples(chart, samples) {
        chart.data.datasets.forEach((dataset) => {
            dataset.data = samples.map((sample) => ({
                x: sample.ts * 1000,
                y: sample[dataset.sampleKey] ?? null,
            }));
        });

        // Pin x to the sample span so tick "nice" rounding can't leave empty space on the left.
        if (samples.length) {
            chart.options.scales.x.min = samples[0].ts * 1000;
            chart.options.scales.x.max = samples[samples.length - 1].ts * 1000;
        } else {
            delete chart.options.scales.x.min;
            delete chart.options.scales.x.max;
        }

        chart.update('none');
    }

    /**
     * Repeating refresh with the failure path already handled, so a rejected
     * fetch logs and skips rather than killing the interval.
     */
    function createPoller(refresh, label, intervalMs = REFRESH_INTERVAL) {
        let timer = null;
        const runOnce = () => refresh().catch((err) => console.error(`${label} refresh failed:`, err));
        return {
            refresh: runOnce,
            start() {
                this.stop();
                runOnce();
                timer = setInterval(runOnce, intervalMs);
            },
            stop() {
                if (timer === null) return;
                clearInterval(timer);
                timer = null;
            },
        };
    }

    window.SMChartUtils = {
        WINDOWS,
        ROLLUPS,
        cssToken,
        withOpacity,
        chartTheme,
        readLocal,
        writeLocal,
        createChoiceGroup,
        bindSeriesToggles,
        applyHiddenSeries,
        padCell,
        formatTooltipValue,
        timeScale,
        valueScale,
        buildTooltip,
        createChart,
        applySamples,
        createPoller,
    };
})();
