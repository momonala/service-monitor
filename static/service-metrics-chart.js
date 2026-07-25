// Per-service RAM/CPU history chart, shown in the service-detail view between the status
// header and the live logs. Reuses the .system-chart* styles and SMChartUtils helpers.
// Both series are a percent of the whole host, so they share one left axis: memory is
// MemoryCurrent over total RAM, CPU is CPUUsageNSec over all cores. A single service usually
// sits in the low single digits of the host, so the axis auto-scales from 0 to the data
// rather than locking to 0-100 like the host chart.
(function() {
    'use strict';

    const {
        cssToken, withOpacity, readLocal, writeLocal,
        setPressed, syncChoiceGroup, padCell, formatTooltipValue, formatXTick,
    } = window.SMChartUtils;

    const CHART_REFRESH_INTERVAL = 30000;
    const DEFAULT_WINDOW = '24h';
    const DEFAULT_ROLLUP = '2m';
    const VALID_WINDOWS = new Set(['1h', '6h', '24h', '7d']);
    const VALID_ROLLUPS = new Set(['30s', '2m', '10m', '30m']);
    const STORAGE_COLLAPSED = 'servicemonitor:service-chart-collapsed';
    const STORAGE_ROLLUP = 'servicemonitor:service-chart-rollup';
    const SHARED_Y_AXIS = 'y';
    const TOOLTIP_MONO = "'SF Mono', Monaco, 'Cascadia Code', Consolas, monospace";
    const TOOLTIP_LABEL_W = 8;
    const TOOLTIP_VALUE_W = 8;
    // Floor for the auto-scaled y axis, so a flat idle service doesn't render as noise.
    const Y_AXIS_SUGGESTED_MAX = 1;

    // Order drives toggles, datasets, and tooltip rows.
    const SERIES = {
        cpu: {
            key: 'cpu_percent',
            label: 'CPU',
            displayLabel: 'CPU %',
            unit: '%',
            colorVar: '--color-series-cpu',
        },
        memory: {
            key: 'memory_used_pct',
            label: 'Memory',
            displayLabel: 'Memory %',
            unit: '%',
            colorVar: '--color-series-memory',
        },
    };
    const SERIES_ORDER = Object.keys(SERIES);

    let chartTimer = null;
    let chart = null;
    let service = null;
    let activeWindow = DEFAULT_WINDOW;
    let activeRollup = DEFAULT_ROLLUP;
    let isCollapsed = false;
    const visibleSeries = Object.fromEntries(SERIES_ORDER.map((id) => [id, true]));

    function loadCollapsedState() {
        return readLocal(STORAGE_COLLAPSED) === 'true';
    }

    function loadRollupState() {
        const saved = readLocal(STORAGE_ROLLUP);
        return saved && VALID_ROLLUPS.has(saved) ? saved : DEFAULT_ROLLUP;
    }

    function seriesColor(series) {
        return cssToken(series.colorVar);
    }

    function stopChartPolling() {
        if (chartTimer == null) return;
        clearInterval(chartTimer);
        chartTimer = null;
    }

    function refreshChartSafely() {
        return refreshChart().catch((err) => {
            console.error('Service metrics chart refresh failed:', err);
        });
    }

    function startChartPolling() {
        stopChartPolling();
        chartTimer = setInterval(refreshChartSafely, CHART_REFRESH_INTERVAL);
    }

    function setCollapsed(root, collapsed) {
        isCollapsed = collapsed;
        root.classList.toggle('system-chart--collapsed', collapsed);
        const btn = root.querySelector('#serviceChartCollapse');
        if (btn) {
            btn.setAttribute('aria-expanded', String(!collapsed));
            btn.title = collapsed ? 'Expand service history chart' : 'Collapse service history chart';
        }
        writeLocal(STORAGE_COLLAPSED, String(collapsed));

        if (collapsed) {
            stopChartPolling();
            return;
        }
        if (!chart) return;
        chart.resize();
        refreshChartSafely();
        startChartPolling();
    }

    function tooltipMetricRow(label, value) {
        return [
            padCell(label, TOOLTIP_LABEL_W),
            padCell(formatTooltipValue(value), TOOLTIP_VALUE_W, 'right'),
        ].join(' ');
    }

    function buildDataset(id, series) {
        const color = seriesColor(series);
        return {
            id,
            seriesId: id,
            sampleKey: series.key,
            label: series.displayLabel,
            data: [],
            parsing: false,
            borderColor: color,
            backgroundColor: withOpacity(color, 0.08),
            yAxisID: SHARED_Y_AXIS,
            tension: 0.35,
            cubicInterpolationMode: 'monotone',
            borderWidth: 1.75,
            pointRadius: 0,
            pointHoverRadius: 3,
            spanGaps: true,
            fill: false,
            hidden: !visibleSeries[id],
        };
    }

    function buildDatasets() {
        return SERIES_ORDER.map((id) => buildDataset(id, SERIES[id]));
    }

    function buildScales(muted, border) {
        return {
            x: {
                type: 'linear',
                bounds: 'data',
                offset: false,
                ticks: {
                    color: muted,
                    font: { size: 10 },
                    maxTicksLimit: 6,
                    callback: (value) => formatXTick(value, activeWindow),
                },
                grid: { color: border },
                border: { color: border },
            },
            [SHARED_Y_AXIS]: {
                type: 'linear',
                position: 'left',
                min: 0,
                suggestedMax: Y_AXIS_SUGGESTED_MAX,
                display: true,
                ticks: {
                    color: muted,
                    font: { size: 10 },
                    maxTicksLimit: 6,
                    callback(value) {
                        return `${this.getLabelForValue(value)}%`;
                    },
                },
                grid: { color: border, drawOnChartArea: true },
                border: { color: border },
            },
        };
    }

    function buildTooltip(muted, panel, border, textPrimary) {
        return {
            backgroundColor: panel,
            borderColor: border,
            borderWidth: 1,
            titleColor: muted,
            bodyColor: textPrimary,
            displayColors: true,
            boxWidth: 10,
            boxHeight: 10,
            boxPadding: 4,
            titleFont: { family: TOOLTIP_MONO, size: 11, weight: '500' },
            bodyFont: { family: TOOLTIP_MONO, size: 11, weight: '400' },
            itemSort(a, b) {
                return SERIES_ORDER.indexOf(a.dataset.seriesId) - SERIES_ORDER.indexOf(b.dataset.seriesId);
            },
            callbacks: {
                title(items) {
                    if (!items.length) return '';
                    return new Date(items[0].parsed.x).toLocaleString();
                },
                label(ctx) {
                    const series = SERIES[ctx.dataset.seriesId];
                    if (ctx.parsed.y == null || !series) return null;
                    return tooltipMetricRow(series.displayLabel, ctx.parsed.y);
                },
                labelColor(ctx) {
                    const color = ctx.dataset.borderColor || textPrimary;
                    return { borderColor: color, backgroundColor: color, borderWidth: 0 };
                },
                labelTextColor(ctx) {
                    return ctx.dataset.borderColor || textPrimary;
                },
            },
        };
    }

    function buildChart(canvas) {
        if (typeof Chart === 'undefined') {
            throw new Error('Chart.js failed to load');
        }
        const muted = cssToken('--color-text-muted');
        const panel = cssToken('--color-bg-secondary');
        const border = cssToken('--border-color');
        const textPrimary = cssToken('--color-text-primary');

        return new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: { datasets: buildDatasets() },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                interaction: { mode: 'nearest', axis: 'x', intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: buildTooltip(muted, panel, border, textPrimary),
                },
                scales: buildScales(muted, border),
            },
        });
    }

    function applySeriesVisibility() {
        if (!chart) return;
        chart.data.datasets.forEach((dataset) => {
            dataset.hidden = !visibleSeries[dataset.seriesId];
        });
        chart.update('none');
    }

    async function refreshChart() {
        if (!chart || !service) return;
        const params = new URLSearchParams({ service, window: activeWindow, rollup: activeRollup });
        const res = await fetch(`/api/services/history?${params}`);
        if (!res.ok) throw new Error(`service history ${res.status}`);
        const payload = await res.json();
        const samples = payload.samples || [];

        chart.data.datasets.forEach((dataset) => {
            dataset.data = samples.map((sample) => ({
                x: sample.ts * 1000,
                y: sample[dataset.sampleKey] ?? null,
            }));
        });

        if (samples.length) {
            chart.options.scales.x.min = samples[0].ts * 1000;
            chart.options.scales.x.max = samples[samples.length - 1].ts * 1000;
        } else {
            delete chart.options.scales.x.min;
            delete chart.options.scales.x.max;
        }
        chart.update('none');
    }

    function bindChartControls(root) {
        root.querySelector('#serviceChartCollapse')?.addEventListener('click', () => {
            setCollapsed(root, !isCollapsed);
        });

        root.querySelectorAll('.system-chart__toggle').forEach((btn) => {
            btn.addEventListener('click', () => {
                const id = btn.dataset.series;
                if (!(id in visibleSeries)) return;
                visibleSeries[id] = !visibleSeries[id];
                setPressed(btn, visibleSeries[id]);
                applySeriesVisibility();
            });
        });

        root.querySelectorAll('.system-chart__range').forEach((btn) => {
            btn.addEventListener('click', () => {
                const nextWindow = btn.dataset.window;
                if (!nextWindow || !VALID_WINDOWS.has(nextWindow) || nextWindow === activeWindow) return;
                activeWindow = nextWindow;
                syncChoiceGroup(root, '.system-chart__range', 'window', activeWindow);
                refreshChartSafely();
            });
        });

        root.querySelectorAll('.system-chart__rollup').forEach((btn) => {
            btn.addEventListener('click', () => {
                const nextRollup = btn.dataset.rollup;
                if (!nextRollup || !VALID_ROLLUPS.has(nextRollup) || nextRollup === activeRollup) return;
                activeRollup = nextRollup;
                writeLocal(STORAGE_ROLLUP, activeRollup);
                syncChoiceGroup(root, '.system-chart__rollup', 'rollup', activeRollup);
                refreshChartSafely();
            });
        });
    }

    function init() {
        const root = document.getElementById('serviceChart');
        const canvas = document.getElementById('serviceMetricsChart');
        if (!root || !(canvas instanceof HTMLCanvasElement)) return;
        service = root.dataset.service || null;
        if (!service) return;

        try {
            chart = buildChart(canvas);
        } catch (err) {
            console.error(err);
            return;
        }

        activeWindow = DEFAULT_WINDOW;
        activeRollup = loadRollupState();
        syncChoiceGroup(root, '.system-chart__rollup', 'rollup', activeRollup);
        syncChoiceGroup(root, '.system-chart__range', 'window', activeWindow);
        bindChartControls(root);
        applySeriesVisibility();
        setCollapsed(root, loadCollapsedState());
    }

    window.ServiceMonitorServiceMetricsChart = { init };
})();
