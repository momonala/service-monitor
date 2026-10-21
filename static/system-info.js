(function() {
    'use strict';

    const {
        levelFor, formatValue, buildTiles, setTile, showGridError, formatMegabytes, writesTile, bindBreakdowns,
    } = window.SMMetricGrid;

    const REFRESH_INTERVAL = 10000;

    // Usage thresholds (percent) at which a metric reads as elevated / critical.
    const WARN_PCT = 60;
    const CRIT_PCT = 85;
    // Raspberry Pi soft-throttles around 80°C; warn well before that.
    const WARN_TEMP_C = 60;
    const CRIT_TEMP_C = 75;
    // Temperature bar maps this Celsius window onto 0–100% fill.
    const TEMP_BAR_MIN_C = 40;
    const TEMP_BAR_MAX_C = 80;
    const WARN_WRITES_MB = 2 * 1024;
    const CRIT_WRITES_MB = 5 * 1024;

    // Same order as the history chart series.
    const TILES = [
        { id: 'writes', icon: 'activity', label: 'Disk writes', shortLabel: 'Writes', bar: true, button: true },
        { id: 'cpu', icon: 'cpu', label: 'CPU', bar: true, button: true },
        { id: 'disk', icon: 'disk', label: 'Disk', bar: true, button: true },
        { id: 'temperature', icon: 'thermometer', label: 'Temperature', shortLabel: 'Temp', dualBar: true },
        { id: 'memory', icon: 'memory', label: 'Memory', bar: true, button: true },
    ];

    const BREAKDOWNS = {
        cpu: { title: 'CPU by project, latest reading', format: (pct) => formatValue(pct, '%') },
        memory: { title: 'Memory by project, latest reading', format: formatMegabytes },
        disk: {
            title: 'Disk space by project directory',
            format: formatMegabytes,
            loadingMessage: 'Measuring project directories…',
        },
        writes: { title: 'Disk writes, last 24h', format: formatMegabytes },
    };

    let ipCopyBound = false;
    let syncBreakdownSummary = () => {};

    function renderIp(ip) {
        const row = document.getElementById('sidebarIp');
        const value = document.getElementById('sidebarIpValue');
        const copyBtn = document.getElementById('sidebarIpCopy');
        if (!row || !value || !copyBtn) return;

        if (!ip) {
            row.hidden = true;
            return;
        }
        row.hidden = false;
        value.textContent = ip;

        if (!ipCopyBound) {
            ipCopyBound = true;
            copyBtn.addEventListener('click', () => {
                navigator.clipboard?.writeText(value.textContent).then(() => {
                    copyBtn.textContent = 'Copied';
                    setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1200);
                });
            });
        }
    }

    // "Last reboot" reads better with an at-a-glance age next to the timestamp.
    const RELATIVE_TIME = new Intl.RelativeTimeFormat(undefined, { numeric: 'always' });

    function relativeAge(date) {
        const minutes = Math.floor((Date.now() - date.getTime()) / 60000);
        if (minutes < 1) return 'just now';
        if (minutes < 60) return RELATIVE_TIME.format(-minutes, 'minute');
        if (minutes < 1440) return RELATIVE_TIME.format(-Math.floor(minutes / 60), 'hour');
        return RELATIVE_TIME.format(-Math.floor(minutes / 1440), 'day');
    }

    function renderReboot(bootTime) {
        const row = document.getElementById('sidebarReboot');
        const value = document.getElementById('sidebarRebootValue');
        if (!row || !value) return;

        if (!bootTime) {
            row.hidden = true;
            return;
        }
        const date = new Date(bootTime);
        if (isNaN(date.getTime())) {
            row.hidden = true;
            return;
        }
        row.hidden = false;
        const formatted = date.toLocaleString(undefined, {
            year: 'numeric', month: 'short', day: 'numeric',
            hour: 'numeric', minute: '2-digit',
        });
        value.textContent = `Last reboot: ${formatted} (${relativeAge(date)})`;
    }

    function tempFill(celsius) {
        if (celsius == null) return null;
        const span = TEMP_BAR_MAX_C - TEMP_BAR_MIN_C;
        return Math.min(100, Math.max(0, ((celsius - TEMP_BAR_MIN_C) / span) * 100));
    }

    function render(container, info) {
        if (!container.querySelector('.metric')) {
            buildTiles(container, TILES);
            syncBreakdownSummary = bindBreakdowns(container, document.getElementById('metricBreakdown'), {
                breakdowns: BREAKDOWNS,
                endpoint: (metric) => `/api/system-info/by-project/${metric}`,
            });
        }
        container.removeAttribute('aria-busy');

        renderIp(info.local_ip);
        renderReboot(info.boot_time);
        window.ServiceMonitorSystemMetricsChart?.setBootTime(info.boot_time);

        setTile(container, 'cpu', {
            value: formatValue(info.cpu_percent, '%'),
            sub: (info.cpu_percent != null && info.cpu_count)
                ? `${(info.cpu_percent / 100 * info.cpu_count).toFixed(1)} / ${info.cpu_count} cores`
                : '',
            level: levelFor(info.cpu_percent, WARN_PCT, CRIT_PCT),
            fill: info.cpu_percent,
        });

        setTile(container, 'disk', {
            value: formatValue(info.disk_used_pct, '%'),
            sub: (info.disk_used_gb != null && info.disk_total_gb != null)
                ? `${info.disk_used_gb} / ${info.disk_total_gb} GB`
                : '',
            level: levelFor(info.disk_used_pct, WARN_PCT, CRIT_PCT),
            fill: info.disk_used_pct,
        });

        setTile(container, 'temperature', {
            value: formatValue(info.temperature_c, '°C'),
            sub: info.temperature_avg_24h != null
                ? `avg ${formatValue(info.temperature_avg_24h, '°C')}`
                : '',
            level: levelFor(info.temperature_c, WARN_TEMP_C, CRIT_TEMP_C),
            fill: tempFill(info.temperature_c),
            avgFill: tempFill(info.temperature_avg_24h),
            maxFill: tempFill(info.temperature_max_24h),
        });

        setTile(container, 'memory', {
            value: formatValue(info.memory_used_pct, '%'),
            sub: (info.memory_used_mb != null && info.memory_total_mb != null)
                ? `${(info.memory_used_mb / 1024).toFixed(1)} / ${(info.memory_total_mb / 1024).toFixed(1)} GB`
                : '',
            level: levelFor(info.memory_used_pct, WARN_PCT, CRIT_PCT),
            fill: info.memory_used_pct,
        });

        setTile(container, 'writes', {
            ...writesTile(info),
            level: levelFor(info.disk_written_mb, WARN_WRITES_MB, CRIT_WRITES_MB),
            fill: info.disk_written_mb == null ? null : (info.disk_written_mb / CRIT_WRITES_MB) * 100,
        });
        syncBreakdownSummary();
    }

    async function refresh(container) {
        try {
            const res = await fetch('/api/system-info');
            if (!res.ok) throw new Error(`system-info ${res.status}`);
            render(container, await res.json());
        } catch (err) {
            console.error('System info refresh failed:', err);
            showGridError(container, `Could not load system info — ${err.message}.`);
        }
    }

    function init() {
        const container = document.getElementById('systemMetrics');
        if (!container) return;
        refresh(container);
        setInterval(() => refresh(container), REFRESH_INTERVAL);
    }

    window.ServiceMonitorSystemInfo = { init };
})();
