(function() {
    'use strict';

    const REFRESH_INTERVAL = 10000;

    // Usage thresholds (percent) at which a metric reads as elevated / critical.
    const WARN_PCT = 80;
    const CRIT_PCT = 90;
    // Raspberry Pi soft-throttles around 80°C; warn well before that.
    const WARN_TEMP_C = 65;
    const CRIT_TEMP_C = 80;
    // Temperature bar maps this Celsius window onto 0–100% fill.
    const TEMP_BAR_MIN_C = 40;
    const TEMP_BAR_MAX_C = 80;

    // Same order as the history chart series.
    const TILES = [
        { id: 'cpu', icon: 'cpu', label: 'CPU', bar: true },
        { id: 'disk', icon: 'disk', label: 'Disk', bar: true },
        { id: 'temperature', icon: 'thermometer', label: 'Temperature', dualBar: true },
        { id: 'memory', icon: 'memory', label: 'Memory', bar: true },
    ];

    let timer = null;

    function levelFor(value, warn, crit) {
        if (value == null) return '';
        if (value >= crit) return 'metric--crit';
        if (value >= warn) return 'metric--warn';
        return '';
    }

    function formatValue(value, suffix = '') {
        return value == null ? '—' : `${value}${suffix}`;
    }

    function tempFill(celsius) {
        if (celsius == null) return null;
        const span = TEMP_BAR_MAX_C - TEMP_BAR_MIN_C;
        return Math.min(100, Math.max(0, ((celsius - TEMP_BAR_MIN_C) / span) * 100));
    }

    function barHtmlFor(tile) {
        if (tile.dualBar) {
            return `
                <div class="metric__bar metric__bar--dual">
                    <span class="metric__bar-current" data-role="bar"></span>
                    <span class="metric__bar-avg" data-role="bar-avg" hidden></span>
                    <span class="metric__bar-max" data-role="bar-max" hidden></span>
                </div>`;
        }
        if (tile.bar) {
            return '<div class="metric__bar"><span data-role="bar"></span></div>';
        }
        return '';
    }

    /**
     * Build the metric tiles once. Subsequent updates mutate these in place so
     * values change without tearing down the DOM (no flicker, preserved focus).
     */
    function buildTiles(container) {
        container.textContent = '';
        for (const tile of TILES) {
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
                ${barHtmlFor(tile)}
            `;
            container.appendChild(el);
        }
    }

    function setBarWidth(el, fill) {
        if (!el) return;
        el.style.width = fill == null ? '0%' : `${Math.min(100, fill)}%`;
    }

    function setBarMarker(el, fill) {
        if (!el) return;
        if (fill == null) {
            el.hidden = true;
            return;
        }
        el.hidden = false;
        el.style.left = `${Math.min(100, fill)}%`;
    }

    function setTile(container, id, { value, sub, level, fill, avgFill, maxFill }) {
        const tile = container.querySelector(`.metric[data-metric="${id}"]`);
        if (!tile) return;
        tile.classList.remove('metric--warn', 'metric--crit');
        if (level) tile.classList.add(level);
        tile.querySelector('[data-role="value"]').textContent = value;
        tile.querySelector('[data-role="sub"]').textContent = sub ?? '';
        setBarWidth(tile.querySelector('[data-role="bar"]'), fill);
        setBarMarker(tile.querySelector('[data-role="bar-avg"]'), avgFill);
        setBarMarker(tile.querySelector('[data-role="bar-max"]'), maxFill);
    }

    function render(container, info) {
        if (!container.querySelector('.metric')) buildTiles(container);
        container.removeAttribute('aria-busy');

        setTile(container, 'cpu', {
            value: formatValue(info.cpu_percent, '%'),
            sub: [
                info.load_avg != null ? `load ${info.load_avg}` : null,
                info.cpu_count ? `${info.cpu_count} cores` : null,
            ].filter(Boolean).join(' · '),
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
        if (!container) return;
        refresh(container);
        timer = setInterval(() => refresh(container), REFRESH_INTERVAL);
    }

    window.ServiceMonitorSystemInfo = { init };
})();
