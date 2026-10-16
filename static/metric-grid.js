// Tile-grid rendering shared by the system-info, service-info, and R2 panels
// (metric-grid / .metric markup in app.css). Each panel polls an endpoint on an
// interval and renders CPU/memory-style tiles; this owns the parts that don't
// vary: tile scaffolding, warn/crit thresholds, and error display.
(function() {
    'use strict';

    function levelFor(value, warn, crit) {
        if (value == null) return '';
        if (value >= crit) return 'metric--crit';
        if (value >= warn) return 'metric--warn';
        return '';
    }

    function formatValue(value, suffix = '') {
        return value == null ? '—' : `${value}${suffix}`;
    }

    function barHtmlFor(tile) {
        if (tile.dualBar) {
            return `
                <span class="metric__bar metric__bar--dual">
                    <span class="metric__bar-current" data-role="bar"></span>
                    <span class="metric__bar-avg" data-role="bar-avg" hidden></span>
                    <span class="metric__bar-max" data-role="bar-max" hidden></span>
                </span>`;
        }
        if (tile.bar) {
            return '<span class="metric__bar"><span data-role="bar"></span></span>';
        }
        return '';
    }

    function labelHtmlFor(tile) {
        if (!tile.shortLabel) return tile.label;
        return `<span class="metric__label-full">${tile.label}</span>`
            + `<span class="metric__label-short">${tile.shortLabel}</span>`;
    }

    /**
     * Build the metric tiles once. Subsequent updates mutate these in place so
     * values change without tearing down the DOM (no flicker, preserved focus).
     */
    function buildTiles(container, tiles) {
        container.textContent = '';
        for (const tile of tiles) {
            const el = document.createElement(tile.button ? 'button' : 'div');
            el.className = tile.button ? 'metric metric--button' : 'metric';
            if (tile.button) el.type = 'button';
            el.dataset.metric = tile.id;
            el.innerHTML = `
                <span class="metric__head">
                    <svg class="metric__icon" aria-hidden="true"><use href="#icon-${tile.icon}"></use></svg>
                    <span class="metric__label">${labelHtmlFor(tile)}</span>
                </span>
                <span class="metric__value t-digit-group" data-role="value">—</span>
                ${tile.sub !== false ? '<span class="metric__sub" data-role="sub"></span>' : ''}
                ${barHtmlFor(tile)}
            `;
            container.appendChild(el);
        }
    }

    function setBarFill(el, fill) {
        if (!el) return;
        el.style.transform = `scaleX(${fill == null ? 0 : Math.min(100, fill) / 100})`;
    }

    function setBarMarker(el, fill) {
        if (!el) return;
        if (fill == null) {
            el.hidden = true;
            return;
        }
        el.hidden = false;
        el.style.transform = `translateX(${Math.min(100, fill)}%)`;
    }

    function setTile(container, id, { value, sub, level, fill, avgFill, maxFill }) {
        const tile = container.querySelector(`.metric[data-metric="${id}"]`);
        if (!tile) return;
        tile.classList.remove('metric--warn', 'metric--crit');
        if (level) tile.classList.add(level);

        const valueEl = tile.querySelector('[data-role="value"]');
        if (window.SMTransitions) {
            window.SMTransitions.setDigits(valueEl, value);
        } else {
            valueEl.textContent = value;
        }

        const subEl = tile.querySelector('[data-role="sub"]');
        if (subEl) subEl.textContent = sub ?? '';
        setBarFill(tile.querySelector('[data-role="bar"]'), fill);
        setBarMarker(tile.querySelector('[data-role="bar-avg"]'), avgFill);
        setBarMarker(tile.querySelector('[data-role="bar-max"]'), maxFill);

        // Idempotent: the first tile written after a fetch lands is what
        // cross-fades the pulsing skeleton out.
        window.SMTransitions?.revealSkeleton(container);
    }

    function formatMegabytes(mb) {
        if (mb == null) return '—';
        return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
    }

    function coveredLabel(hours) {
        if (hours == null) return '';
        if (hours < 1) return `last ${Math.max(1, Math.round(hours * 60))}m`;
        return `last ${hours >= 23.5 ? 24 : Math.round(hours)}h`;
    }

    function writesTile(info) {
        return { value: formatMegabytes(info.disk_written_mb), sub: coveredLabel(info.disk_written_hours) };
    }

    // Only replace the placeholder while no tiles exist; once tiles are built we
    // keep the last-known values on a transient failure rather than wiping them.
    function showGridError(container, message) {
        if (container.querySelector('.metric')) return;
        container.removeAttribute('aria-busy');
        const p = container.querySelector('.metric-grid__placeholder') || document.createElement('p');
        p.className = 'metric-grid__placeholder';
        p.textContent = message;
        if (!p.isConnected) container.appendChild(p);
        window.SMTransitions?.revealSkeleton(container);
    }

    window.SMMetricGrid = {
        levelFor,
        formatValue,
        buildTiles,
        setTile,
        showGridError,
        formatMegabytes,
        writesTile,
    };
})();
