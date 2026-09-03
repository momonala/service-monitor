(function() {
    'use strict';

    const { levelFor, formatValue, buildTiles, setTile, showGridError } = window.SMMetricGrid;

    const REFRESH_INTERVAL = 10000;
    const WARN_PCT = 60;
    const CRIT_PCT = 85;

    const TILES = [
        { id: 'cpu', icon: 'cpu', label: 'CPU', bar: false },
        { id: 'memory', icon: 'memory', label: 'Memory', bar: false },
    ];

    let timer = null;

    function render(container, info) {
        if (!container.querySelector('.metric')) buildTiles(container, TILES);
        container.removeAttribute('aria-busy');

        setTile(container, 'cpu', {
            value: formatValue(info.cpu_percent, '%'),
            level: levelFor(info.cpu_percent, WARN_PCT, CRIT_PCT),
            fill: info.cpu_percent,
        });

        setTile(container, 'memory', {
            value: formatValue(info.memory_used_pct, '%'),
            sub: info.memory_used_mb != null ? `${info.memory_used_mb} MB` : '',
            level: levelFor(info.memory_used_pct, WARN_PCT, CRIT_PCT),
            fill: info.memory_used_pct,
        });
    }

    async function refresh(container, service) {
        try {
            const res = await fetch(`/api/services/current?service=${encodeURIComponent(service)}`);
            if (!res.ok) throw new Error(`services/current ${res.status}`);
            render(container, await res.json());
        } catch (err) {
            console.error('Service metrics refresh failed:', err);
            showGridError(container, `Could not load service metrics — ${err.message}.`);
        }
    }

    function init() {
        const container = document.getElementById('serviceMetrics');
        if (!container) return;
        const service = container.dataset.service;
        if (!service) return;
        if (timer) clearInterval(timer);
        refresh(container, service);
        timer = setInterval(() => refresh(container, service), REFRESH_INTERVAL);
    }

    window.ServiceMonitorServiceInfo = { init };
})();
