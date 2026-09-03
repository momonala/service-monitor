// Per-service RAM/CPU history chart, shown in the service-detail view between the status
// header and the live logs. Reuses the .system-chart* styles and SMChartUtils helpers.
// Both series are a percent of the whole host, so they share one left axis: memory is
// MemoryCurrent over total RAM, CPU is CPUUsageNSec over all cores. A single service usually
// sits in the low single digits of the host, so the axis auto-scales from 0 to the data
// rather than locking to 0-100 like the host chart.
(function() {
    'use strict';

    const {
        WINDOWS, ROLLUPS, cssToken, withOpacity, readLocal, writeLocal, chartTheme,
        createChoiceGroup, bindSeriesToggles, applyHiddenSeries,
        padCell, formatTooltipValue, timeScale, valueScale,
        buildTooltip, createChart, applySamples, createPoller,
    } = window.SMChartUtils;

    const DEFAULT_WINDOW = '24h';
    const DEFAULT_ROLLUP = '2m';
    const STORAGE_COLLAPSED = 'servicemonitor:service-chart-collapsed';
    const STORAGE_ROLLUP = 'servicemonitor:service-chart-rollup';
    const SHARED_Y_AXIS = 'y';
    const TOOLTIP_LABEL_W = 8;
    const TOOLTIP_VALUE_W = 8;
    // Floor for the auto-scaled y axis, so a flat idle service doesn't render as noise.
    const Y_AXIS_SUGGESTED_MAX = 50;

    // Order drives toggles, datasets, and tooltip rows.
    const SERIES = {
        cpu: {
            key: 'cpu_percent',
            displayLabel: 'CPU %',
            unit: '%',
            colorVar: '--color-series-cpu',
        },
        memory: {
            key: 'memory_used_mb',
            displayLabel: 'Memory',
            unit: ' MB',
            colorVar: '--color-series-memory',
        },
    };
    const SERIES_ORDER = Object.keys(SERIES);

    let chart = null;
    let service = null;
    let isCollapsed = false;
    let windowGroup = { value: DEFAULT_WINDOW };
    let rollupGroup = { value: DEFAULT_ROLLUP };
    const visibleSeries = Object.fromEntries(SERIES_ORDER.map((id) => [id, true]));
    const poller = createPoller(refreshChart, 'Service metrics chart');

    function setCollapsed(root, collapsed) {
        isCollapsed = collapsed;
        root.classList.toggle('system-chart--collapsed', collapsed);
        window.SMTransitions?.setAccordionOpen(root, !collapsed);
        const btn = root.querySelector('#serviceChartCollapse');
        if (btn) {
            btn.setAttribute('aria-expanded', String(!collapsed));
            btn.title = collapsed ? 'Expand service history chart' : 'Collapse service history chart';
        }
        writeLocal(STORAGE_COLLAPSED, String(collapsed));

        if (collapsed) {
            poller.stop();
            return;
        }
        // The pill can't measure itself while the panel is collapsed
        // (offsetWidth is 0), so re-land it once the panel is open again.
        window.SMTransitions?.syncPills(root, false);
        if (!chart) return;
        chart.resize();
        poller.start();
    }

    function tooltipMetricRow(label, value) {
        return [
            padCell(label, TOOLTIP_LABEL_W),
            padCell(formatTooltipValue(value), TOOLTIP_VALUE_W, 'right'),
        ].join(' ');
    }

    function buildDataset(id, series) {
        const color = cssToken(series.colorVar);
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

    function buildChart(canvas, theme) {
        return createChart(canvas, {
            datasets: SERIES_ORDER.map((id) => buildDataset(id, SERIES[id])),
            scales: {
                x: timeScale(theme, () => windowGroup.value),
                [SHARED_Y_AXIS]: valueScale(theme, {
                    min: 0,
                    suggestedMax: Y_AXIS_SUGGESTED_MAX,
                    ticks: {
                        callback(value) {
                            return `${this.getLabelForValue(value)} MB`;
                        },
                    },
                }),
            },
            tooltip: buildTooltip(theme, {
                itemSort: (a, b) =>
                    SERIES_ORDER.indexOf(a.dataset.seriesId) - SERIES_ORDER.indexOf(b.dataset.seriesId),
                callbacks: {
                    label(ctx) {
                        const series = SERIES[ctx.dataset.seriesId];
                        if (ctx.parsed.y == null || !series) return null;
                        return tooltipMetricRow(series.displayLabel, `${ctx.parsed.y}${series.unit}`);
                    },
                },
            }),
        });
    }

    async function refreshChart() {
        if (!chart || !service) return;
        const params = new URLSearchParams({
            service,
            window: windowGroup.value,
            rollup: rollupGroup.value,
        });
        const res = await fetch(`/api/services/history?${params}`);
        if (!res.ok) throw new Error(`service history ${res.status}`);
        const payload = await res.json();
        applySamples(chart, payload.samples || []);
    }

    function init() {
        const root = document.getElementById('serviceChart');
        const canvas = document.getElementById('serviceMetricsChart');
        if (!root || !(canvas instanceof HTMLCanvasElement)) return;
        service = root.dataset.service || null;
        if (!service) return;

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
        bindSeriesToggles(root, visibleSeries, () => applyHiddenSeries(chart, visibleSeries));
        root.querySelector('#serviceChartCollapse')?.addEventListener('click', () => {
            setCollapsed(root, !isCollapsed);
        });

        applyHiddenSeries(chart, visibleSeries);
        setCollapsed(root, readLocal(STORAGE_COLLAPSED) === 'true');
    }

    window.ServiceMonitorServiceMetricsChart = { init };
})();
