(function() {
    'use strict';

    const {
        WINDOWS, ROLLUPS, cssToken, withOpacity, readLocal, writeLocal, chartTheme,
        createChoiceGroup, bindSeriesToggles, applyHiddenSeries,
        padCell, formatTooltipValue, timeScale, valueScale,
        buildTooltip, createChart, applySamples, createPoller,
    } = window.SMChartUtils;

    const DEFAULT_WINDOW = '7d';
    const DEFAULT_ROLLUP = '30s';
    const STORAGE_ROLLUP = 'servicemonitor:system-chart-rollup';
    const SHARED_Y_AXIS = 'y';
    const TOOLTIP_LABEL_W = 10;
    const TOOLTIP_VALUE_W = 6;

    // Order here drives toggles, Y-label, datasets, and tooltip rows.
    const SERIES = {
        cpu: { key: 'cpu_percent', label: 'CPU', displayLabel: 'CPU %', colorVar: '--color-series-cpu' },
        disk: { key: 'disk_used_pct', label: 'Disk', displayLabel: 'Disk %', colorVar: '--color-series-disk' },
        memory: {
            key: 'memory_used_pct',
            label: 'Memory',
            displayLabel: 'Memory %',
            colorVar: '--color-series-memory',
        },
        temperature: {
            key: 'temperature_c',
            label: 'Temp',
            displayLabel: 'Temp (°C)',
            colorVar: '--color-series-temp',
        },
    };
    const SERIES_ORDER = Object.keys(SERIES);

    let chart = null;
    let bootTimeMs = null;
    let windowGroup = { value: DEFAULT_WINDOW };
    let rollupGroup = { value: DEFAULT_ROLLUP };
    const visibleSeries = Object.fromEntries(SERIES_ORDER.map((id) => [id, true]));
    const poller = createPoller(refreshChart, 'System metrics chart');

    const rebootLinePlugin = {
        id: 'rebootLine',
        afterDraw(chartInstance) {
            if (bootTimeMs == null) return;
            const { x } = chartInstance.scales;
            if (bootTimeMs < x.min || bootTimeMs > x.max) return;

            const xPixel = x.getPixelForValue(bootTimeMs);
            const { top, bottom } = chartInstance.chartArea;
            const ctx = chartInstance.ctx;
            ctx.save();
            ctx.strokeStyle = cssToken('--color-danger') || 'red';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(xPixel, top);
            ctx.lineTo(xPixel, bottom);
            ctx.stroke();
            ctx.restore();
        },
    };

    function setBootTime(bootTimeIso) {
        const parsed = bootTimeIso ? Date.parse(bootTimeIso) : NaN;
        bootTimeMs = Number.isNaN(parsed) ? null : parsed;
        chart?.update('none');
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
            (dataset) => dataset.seriesId === seriesId && dataset.kind === 'max' && !dataset.hidden,
        );
        return maxDataset?.data?.[dataIndex]?.y ?? null;
    }

    function buildLineDataset(id, series, kind) {
        const isMax = kind === 'max';
        const color = cssToken(series.colorVar);
        return {
            id: `${id}-${kind}`,
            seriesId: id,
            kind,
            sampleKey: isMax ? `${series.key}_max` : series.key,
            label: isMax ? `${series.label} max` : series.label,
            data: [],
            parsing: false,
            borderColor: isMax ? withOpacity(color, 0.5) : color,
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

    function buildChart(canvas, theme) {
        return createChart(canvas, {
            plugins: [rebootLinePlugin],
            datasets: SERIES_ORDER.flatMap((id) => [
                buildLineDataset(id, SERIES[id], 'avg'),
                buildLineDataset(id, SERIES[id], 'max'),
            ]),
            scales: {
                x: timeScale(theme, () => windowGroup.value),
                [SHARED_Y_AXIS]: valueScale(theme, { min: 0, max: 100 }),
            },
            tooltip: buildTooltip(theme, {
                filter: (item) => item.dataset.kind === 'avg',
                itemSort: (a, b) =>
                    SERIES_ORDER.indexOf(a.dataset.seriesId) - SERIES_ORDER.indexOf(b.dataset.seriesId),
                callbacks: {
                    beforeBody: () => tooltipMetricRow('', 'avg', 'max'),
                    label(ctx) {
                        const series = SERIES[ctx.dataset.seriesId];
                        if (ctx.parsed.y == null || !series) return null;
                        return tooltipMetricRow(
                            series.displayLabel,
                            ctx.parsed.y,
                            maxValueAt(ctx.chart, ctx.dataset.seriesId, ctx.dataIndex),
                        );
                    },
                },
            }),
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
        syncYLabel();
        applyHiddenSeries(chart, visibleSeries);
    }

    async function refreshChart() {
        if (!chart) return;
        const params = new URLSearchParams({ window: windowGroup.value, rollup: rollupGroup.value });
        const res = await fetch(`/api/system-info/history?${params}`);
        if (!res.ok) throw new Error(`history ${res.status}`);
        const payload = await res.json();
        applySamples(chart, payload.samples || []);
    }

    function init() {
        const root = document.getElementById('systemChart');
        const canvas = document.getElementById('systemMetricsChart');
        if (!root || !(canvas instanceof HTMLCanvasElement)) return;

        try {
            chart = buildChart(canvas, chartTheme());
        } catch (err) {
            console.error(err);
            return;
        }

        const savedRollup = readLocal(STORAGE_ROLLUP);
        windowGroup = createChoiceGroup(
            root, '.system-chart__range', 'window', WINDOWS, DEFAULT_WINDOW,
            () => poller.refresh(),
        );
        rollupGroup = createChoiceGroup(
            root, '.system-chart__rollup', 'rollup', ROLLUPS,
            ROLLUPS.has(savedRollup) ? savedRollup : DEFAULT_ROLLUP,
            (rollup) => {
                writeLocal(STORAGE_ROLLUP, rollup);
                poller.refresh();
            },
        );
        bindSeriesToggles(root, visibleSeries, applySeriesVisibility);

        applySeriesVisibility();
        poller.start();
    }

    window.ServiceMonitorSystemMetricsChart = { init, setBootTime };
})();
