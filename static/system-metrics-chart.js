(function() {
    'use strict';

    const CHART_REFRESH_INTERVAL = 30000;
    const DEFAULT_WINDOW = '7d';
    const DEFAULT_ROLLUP = '30s';
    const VALID_WINDOWS = new Set(['1h', '6h', '24h', '7d']);
    const VALID_ROLLUPS = new Set(['30s', '2m', '10m', '30m']);
    const STORAGE_COLLAPSED = 'servicemonitor:system-chart-collapsed';
    const STORAGE_ROLLUP = 'servicemonitor:system-chart-rollup';
    const SHARED_Y_AXIS = 'y';
    const TOOLTIP_MONO = "'SF Mono', Monaco, 'Cascadia Code', Consolas, monospace";
    const TOOLTIP_LABEL_W = 10;
    const TOOLTIP_VALUE_W = 6;

    // Order here drives toggles, Y-label, datasets, and tooltip rows.
    const SERIES = {
        cpu: {
            key: 'cpu_percent',
            label: 'CPU',
            displayLabel: 'CPU %',
            unit: '%',
            colorVar: '--color-series-cpu',
        },
        disk: {
            key: 'disk_used_pct',
            label: 'Disk',
            displayLabel: 'Disk %',
            unit: '%',
            colorVar: '--color-series-disk',
        },
        memory: {
            key: 'memory_used_pct',
            label: 'Memory',
            displayLabel: 'Memory %',
            unit: '%',
            colorVar: '--color-series-memory',
        },
        temperature: {
            key: 'temperature_c',
            label: 'Temp',
            displayLabel: 'Temp (°C)',
            unit: '°C',
            colorVar: '--color-series-temp',
        },
    };
    const SERIES_ORDER = Object.keys(SERIES);

    let chartTimer = null;
    let chart = null;
    let activeWindow = DEFAULT_WINDOW;
    let activeRollup = DEFAULT_ROLLUP;
    let isCollapsed = false;
    const visibleSeries = Object.fromEntries(SERIES_ORDER.map((id) => [id, true]));

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

    function loadCollapsedState() {
        return readLocal(STORAGE_COLLAPSED) === 'true';
    }

    function loadRollupState() {
        const saved = readLocal(STORAGE_ROLLUP);
        return saved && VALID_ROLLUPS.has(saved) ? saved : DEFAULT_ROLLUP;
    }

    function cssToken(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    function seriesColor(series) {
        return cssToken(series.colorVar);
    }

    function withOpacity(color, alpha) {
        const hex = color.replace('#', '');
        if (hex.length !== 6) return color;
        const r = parseInt(hex.slice(0, 2), 16);
        const g = parseInt(hex.slice(2, 4), 16);
        const b = parseInt(hex.slice(4, 6), 16);
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    function setPressed(btn, active) {
        btn.classList.toggle('is-active', active);
        btn.setAttribute('aria-pressed', String(active));
    }

    function syncChoiceGroup(root, selector, dataKey, activeValue) {
        root.querySelectorAll(selector).forEach((btn) => {
            setPressed(btn, btn.dataset[dataKey] === activeValue);
        });
    }

    function stopChartPolling() {
        if (chartTimer == null) return;
        clearInterval(chartTimer);
        chartTimer = null;
    }

    function refreshChartSafely() {
        return refreshChart().catch((err) => {
            console.error('System metrics chart refresh failed:', err);
        });
    }

    function startChartPolling() {
        stopChartPolling();
        chartTimer = setInterval(refreshChartSafely, CHART_REFRESH_INTERVAL);
    }

    function setCollapsed(root, collapsed) {
        isCollapsed = collapsed;
        root.classList.toggle('system-chart--collapsed', collapsed);
        const btn = root.querySelector('#systemChartCollapse');
        if (btn) {
            btn.setAttribute('aria-expanded', String(!collapsed));
            btn.title = collapsed ? 'Expand history chart' : 'Collapse history chart';
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

    function padCell(value, width, align = 'left') {
        const text = String(value);
        if (text.length >= width) return text.slice(0, width);
        const padding = ' '.repeat(width - text.length);
        return align === 'right' ? padding + text : text + padding;
    }

    function formatTooltipValue(value) {
        return value == null ? '—' : String(value);
    }

    function tooltipMetricRow(label, avgValue, maxValue) {
        return [
            padCell(label, TOOLTIP_LABEL_W),
            padCell(formatTooltipValue(avgValue), TOOLTIP_VALUE_W, 'right'),
            padCell(formatTooltipValue(maxValue), TOOLTIP_VALUE_W, 'right'),
        ].join(' ');
    }

    function maxValueAt(chartInstance, seriesId, dataIndex) {
        const maxDataset = chartInstance.data.datasets.find(
            (dataset) => (
                dataset.seriesId === seriesId
                && dataset.kind === 'max'
                && !dataset.hidden
            )
        );
        return maxDataset?.data?.[dataIndex]?.y ?? null;
    }

    function buildLineDataset(id, series, kind) {
        const isMax = kind === 'max';
        return {
            id: `${id}-${kind}`,
            seriesId: id,
            kind,
            sampleKey: isMax ? `${series.key}_max` : series.key,
            label: isMax ? `${series.label} max` : series.label,
            data: [],
            parsing: false,
            borderColor: isMax ? withOpacity(seriesColor(series), 0.5) : seriesColor(series),
            backgroundColor: 'transparent',
            borderDash: isMax ? [4, 4] : [],
            yAxisID: SHARED_Y_AXIS,
            tension: 0.35,
            cubicInterpolationMode: 'monotone',
            borderWidth: isMax ? 1.5 : 1.75,
            pointRadius: 0,
            pointHoverRadius: 3,
            spanGaps: true,
            hidden: !visibleSeries[id],
        };
    }

    function buildDatasets() {
        return SERIES_ORDER.flatMap((id) => {
            const series = SERIES[id];
            return [
                buildLineDataset(id, series, 'avg'),
                buildLineDataset(id, series, 'max'),
            ];
        });
    }

    function formatXTick(value) {
        const date = new Date(value);
        if (activeWindow === '7d' || activeWindow === '24h') {
            return date.toLocaleString(undefined, {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
            });
        }
        return date.toLocaleTimeString(undefined, {
            hour: '2-digit',
            minute: '2-digit',
        });
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
                    callback: formatXTick,
                },
                grid: { color: border },
                border: { color: border },
            },
            [SHARED_Y_AXIS]: {
                type: 'linear',
                position: 'left',
                min: 0,
                max: 100,
                display: true,
                ticks: {
                    color: muted,
                    font: { size: 10 },
                    maxTicksLimit: 6,
                },
                grid: {
                    color: border,
                    drawOnChartArea: true,
                },
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
            filter(item) {
                return item.dataset.kind === 'avg';
            },
            itemSort(a, b) {
                return (
                    SERIES_ORDER.indexOf(a.dataset.seriesId)
                    - SERIES_ORDER.indexOf(b.dataset.seriesId)
                );
            },
            callbacks: {
                title(items) {
                    if (!items.length) return '';
                    return new Date(items[0].parsed.x).toLocaleString();
                },
                beforeBody() {
                    return tooltipMetricRow('', 'avg', 'max');
                },
                label(ctx) {
                    const series = SERIES[ctx.dataset.seriesId];
                    if (ctx.parsed.y == null || !series) return null;
                    return tooltipMetricRow(
                        series.displayLabel,
                        ctx.parsed.y,
                        maxValueAt(ctx.chart, ctx.dataset.seriesId, ctx.dataIndex),
                    );
                },
                labelColor(ctx) {
                    const color = ctx.dataset.borderColor || textPrimary;
                    return {
                        borderColor: color,
                        backgroundColor: color,
                        borderWidth: 0,
                    };
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
                interaction: {
                    mode: 'nearest',
                    axis: 'x',
                    intersect: false,
                },
                plugins: {
                    legend: { display: false },
                    tooltip: buildTooltip(muted, panel, border, textPrimary),
                },
                scales: buildScales(muted, border),
            },
        });
    }

    function syncYLabel() {
        const root = document.getElementById('systemChartYLabel');
        if (!root) return;

        if (!root.childElementCount) {
            SERIES_ORDER.forEach((id) => {
                const part = document.createElement('span');
                part.className = 'system-chart__y-label-part';
                part.dataset.series = id;
                part.textContent = SERIES[id].displayLabel;
                root.appendChild(part);
            });
        }

        root.querySelectorAll('[data-series]').forEach((part) => {
            part.classList.toggle('is-hidden', !visibleSeries[part.dataset.series]);
        });
    }

    function applySeriesVisibility() {
        if (!chart) return;
        chart.data.datasets.forEach((dataset) => {
            dataset.hidden = !visibleSeries[dataset.seriesId];
        });
        syncYLabel();
        chart.update('none');
    }

    async function refreshChart() {
        if (!chart) return;
        const params = new URLSearchParams({
            window: activeWindow,
            rollup: activeRollup,
        });
        const res = await fetch(`/api/system-info/history?${params}`);
        if (!res.ok) throw new Error(`history ${res.status}`);
        const payload = await res.json();
        const samples = payload.samples || [];

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

    function bindChartControls(root) {
        root.querySelector('#systemChartCollapse')?.addEventListener('click', () => {
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
                if (!nextWindow || !VALID_WINDOWS.has(nextWindow) || nextWindow === activeWindow) {
                    return;
                }
                activeWindow = nextWindow;
                syncChoiceGroup(root, '.system-chart__range', 'window', activeWindow);
                refreshChartSafely();
            });
        });

        root.querySelectorAll('.system-chart__rollup').forEach((btn) => {
            btn.addEventListener('click', () => {
                const nextRollup = btn.dataset.rollup;
                if (!nextRollup || !VALID_ROLLUPS.has(nextRollup) || nextRollup === activeRollup) {
                    return;
                }
                activeRollup = nextRollup;
                writeLocal(STORAGE_ROLLUP, activeRollup);
                syncChoiceGroup(root, '.system-chart__rollup', 'rollup', activeRollup);
                refreshChartSafely();
            });
        });
    }

    function init() {
        const root = document.getElementById('systemChart');
        const canvas = document.getElementById('systemMetricsChart');
        if (!root || !(canvas instanceof HTMLCanvasElement)) return;

        try {
            chart = buildChart(canvas);
        } catch (err) {
            console.error(err);
            return;
        }

        activeRollup = loadRollupState();
        syncChoiceGroup(root, '.system-chart__rollup', 'rollup', activeRollup);
        syncChoiceGroup(root, '.system-chart__range', 'window', activeWindow);
        bindChartControls(root);
        applySeriesVisibility();
        setCollapsed(root, loadCollapsedState());
    }

    window.ServiceMonitorSystemMetricsChart = { init };
})();
