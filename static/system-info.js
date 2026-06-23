(function() {
    'use strict';

    const REFRESH_INTERVAL = 10000;

    // Usage thresholds (percent) at which a metric reads as elevated / critical.
    const WARN_PCT = 80;
    const CRIT_PCT = 90;
    // Raspberry Pi soft-throttles around 80°C; warn well before that.
    const WARN_TEMP_C = 65;
    const CRIT_TEMP_C = 80;

    let timer = null;

    function levelFor(value, warn, crit) {
        if (value == null) return '';
        if (value >= crit) return 'metric--crit';
        if (value >= warn) return 'metric--warn';
        return '';
    }

    function fmt(value, suffix = '', dash = '—') {
        return value == null ? dash : `${value}${suffix}`;
    }

    /**
     * Build the metric tiles once. Subsequent updates mutate these in place so
     * values change without tearing down the DOM (no flicker, preserved focus).
     */
    function buildTiles(container) {
        const tiles = [
            { id: 'temperature', icon: 'thermometer', label: 'Temperature', bar: false },
            { id: 'cpu', icon: 'cpu', label: 'CPU', bar: true },
            { id: 'memory', icon: 'memory', label: 'Memory', bar: true },
            { id: 'disk', icon: 'disk', label: 'Disk', bar: true },
        ];

        container.textContent = '';
        for (const tile of tiles) {
            const el = document.createElement('div');
            el.className = 'metric';
            el.dataset.metric = tile.id;
            el.innerHTML = `
                <div class="metric__head">
                    <svg class="metric__icon" aria-hidden="true"><use href="#icon-${tile.icon}"></use></svg>
                    <span class="metric__label">${tile.label}</span>
                </div>
                <div class="metric__value" data-role="value">—</div>
                <div class="metric__sub" data-role="sub"></div>
                ${tile.bar ? '<div class="metric__bar"><span data-role="bar"></span></div>' : ''}
            `;
            container.appendChild(el);
        }
    }

    function setTile(container, id, { value, sub, level, fill }) {
        const tile = container.querySelector(`.metric[data-metric="${id}"]`);
        if (!tile) return;
        tile.classList.remove('metric--warn', 'metric--crit');
        if (level) tile.classList.add(level);
        tile.querySelector('[data-role="value"]').textContent = value;
        tile.querySelector('[data-role="sub"]').textContent = sub ?? '';
        const bar = tile.querySelector('[data-role="bar"]');
        if (bar) bar.style.width = fill == null ? '0%' : `${Math.min(100, fill)}%`;
    }

    function render(container, info) {
        if (!container.querySelector('.metric')) buildTiles(container);
        container.removeAttribute('aria-busy');

        setTile(container, 'temperature', {
            value: fmt(info.temperature_c, '°C'),
            sub: '',
            level: levelFor(info.temperature_c, WARN_TEMP_C, CRIT_TEMP_C),
        });

        const loadSub = [
            info.load_avg != null ? `load ${info.load_avg}` : null,
            info.cpu_count ? `${info.cpu_count} cores` : null,
        ].filter(Boolean).join(' · ');
        setTile(container, 'cpu', {
            value: fmt(info.cpu_percent, '%'),
            sub: loadSub,
            level: levelFor(info.cpu_percent, WARN_PCT, CRIT_PCT),
            fill: info.cpu_percent,
        });

        const memSub = (info.memory_used_mb != null && info.memory_total_mb != null)
            ? `${(info.memory_used_mb / 1024).toFixed(1)} / ${(info.memory_total_mb / 1024).toFixed(1)} GB`
            : '';
        setTile(container, 'memory', {
            value: fmt(info.memory_used_pct, '%'),
            sub: memSub,
            level: levelFor(info.memory_used_pct, WARN_PCT, CRIT_PCT),
            fill: info.memory_used_pct,
        });

        const diskSub = (info.disk_used_gb != null && info.disk_total_gb != null)
            ? `${info.disk_used_gb} / ${info.disk_total_gb} GB`
            : '';
        setTile(container, 'disk', {
            value: fmt(info.disk_used_pct, '%'),
            sub: diskSub,
            level: levelFor(info.disk_used_pct, WARN_PCT, CRIT_PCT),
            fill: info.disk_used_pct,
        });

        const meta = document.getElementById('systemMeta');
        if (meta) {
            const bits = [info.hostname, info.uptime ? `up ${info.uptime}` : null].filter(Boolean);
            meta.textContent = bits.join(' · ');
        }
    }

    function showError(container, message) {
        // Only replace the placeholder while no tiles exist; once tiles are built we
        // keep the last-known values on a transient failure rather than wiping them.
        if (container.querySelector('.metric')) return;
        container.removeAttribute('aria-busy');
        const p = container.querySelector('.metric-grid__placeholder') || document.createElement('p');
        p.className = 'metric-grid__placeholder';
        p.textContent = message;
        if (!p.isConnected) container.appendChild(p);
    }

    async function refresh(container) {
        try {
            const res = await fetch('/api/system-info');
            if (!res.ok) {
                showError(container, `Failed to load system info (HTTP ${res.status}).`);
                throw new Error(`system-info ${res.status}`);
            }
            render(container, await res.json());
        } catch (err) {
            console.error('System info refresh failed:', err);
            showError(container, `Could not load system info — ${err.message}.`);
        }
    }

    function init() {
        const container = document.getElementById('systemMetrics');
        if (!container) return;  // not on the dashboard home view
        refresh(container);
        timer = setInterval(() => refresh(container), REFRESH_INTERVAL);
    }

    window.ServiceMonitorSystemInfo = { init };
})();
