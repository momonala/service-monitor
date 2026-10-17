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

    function breakdownRow(name, value) {
        const row = document.createElement('li');
        row.className = 'metric-breakdown__row';
        const nameEl = document.createElement('span');
        nameEl.className = 'metric-breakdown__name';
        nameEl.textContent = name;
        const valueEl = document.createElement('span');
        valueEl.className = 'metric-breakdown__value';
        valueEl.textContent = value;
        row.append(nameEl, valueEl);
        return row;
    }

    function barSegment(fraction, modifier) {
        const segment = document.createElement('span');
        segment.className = `metric-breakdown__segment metric-breakdown__segment--${modifier}`;
        segment.style.width = `${fraction * 100}%`;
        return segment;
    }

    // Writes rows split into the project's own writes and the journald writes for its logs.
    function splitBar(direct, logs, largest) {
        const bar = document.createElement('span');
        bar.className = 'metric-breakdown__bar metric-breakdown__bar--split';
        bar.append(barSegment(direct / largest, 'direct'), barSegment(logs / largest, 'logs'));
        return bar;
    }

    function scaledBar(value, largest, modifier) {
        const bar = document.createElement('span');
        bar.className = modifier ? `metric-breakdown__bar metric-breakdown__bar--${modifier}` : 'metric-breakdown__bar';
        bar.style.transform = `scaleX(${value / largest})`;
        return bar;
    }

    function totalRow(name, value) {
        const row = breakdownRow(name, value);
        row.classList.add('metric-breakdown__row--total');
        return row;
    }

    function renderBreakdown(list, { projects, total, other = [], other_total: otherTotal }, format) {
        const largest = Math.max(...projects.map((p) => p.value), ...other.map((o) => o.value)) || 1;
        const projectRows = projects.map(({ project, value, direct, logs }) => {
            const row = breakdownRow(project, format(value));
            if (logs !== undefined) {
                row.title = `${format(direct)} written directly, ${format(logs)} in logs`;
                row.appendChild(splitBar(direct, logs, largest));
            } else {
                row.appendChild(scaledBar(value, largest));
            }
            return row;
        });
        const otherRows = other.map(({ name, value }) => {
            const row = breakdownRow(name, format(value));
            row.appendChild(scaledBar(value, largest, 'other'));
            return row;
        });
        list.replaceChildren(
            totalRow('All projects', format(total)),
            ...projectRows,
            ...(other.length ? [totalRow('Outside projects', format(otherTotal)), ...otherRows] : []),
        );
    }

    function showBreakdownMessage(list, message) {
        const row = document.createElement('li');
        row.className = 'metric-breakdown__message';
        row.textContent = message;
        list.replaceChildren(row);
    }

    async function loadBreakdown(list, url, { format, loadingMessage = 'Loading…' }, signal) {
        list.setAttribute('aria-busy', 'true');
        showBreakdownMessage(list, loadingMessage);
        try {
            const res = await fetch(url, { signal });
            if (!res.ok) throw new Error(`by-project ${res.status}`);
            const breakdown = await res.json();
            if (!breakdown.projects.length) {
                showBreakdownMessage(list, 'No readings recorded yet.');
                return;
            }
            renderBreakdown(list, breakdown, format);
        } catch (err) {
            if (err.name === 'AbortError') return;
            console.error('Project breakdown failed:', err);
            showBreakdownMessage(list, `Could not load the breakdown — ${err.message}.`);
        } finally {
            list.removeAttribute('aria-busy');
        }
    }

    /**
     * Make a grid's button tiles toggle a per-project breakdown panel (.metric-breakdown markup).
     * Returns a function that copies the open tile's sub-line into the panel summary; call it
     * after each tile refresh.
     */
    function bindBreakdowns(container, panel, { breakdowns, endpoint }) {
        const title = panel.querySelector('.metric-breakdown__title');
        const summary = panel.querySelector('.metric-breakdown__summary');
        const list = panel.querySelector('.metric-breakdown__list');
        const tiles = container.querySelectorAll('.metric--button');
        let openMetric = null;
        let request = null;

        function syncSummary() {
            if (!summary || !openMetric) return;
            const sub = container.querySelector(`.metric[data-metric="${openMetric}"] [data-role="sub"]`);
            summary.textContent = sub?.textContent ?? '';
        }

        for (const tile of tiles) {
            tile.setAttribute('aria-controls', panel.id);
            tile.setAttribute('aria-expanded', 'false');
        }

        container.addEventListener('click', (event) => {
            const tile = event.target.closest('.metric--button');
            if (!tile) return;
            const metric = tile.dataset.metric;
            openMetric = openMetric === metric ? null : metric;
            panel.hidden = openMetric == null;
            for (const t of tiles) t.setAttribute('aria-expanded', String(t.dataset.metric === openMetric));
            request?.abort();
            request = null;
            if (!openMetric) return;
            panel.dataset.metric = metric;
            title.textContent = breakdowns[metric].title;
            syncSummary();
            request = new AbortController();
            loadBreakdown(list, endpoint(metric), breakdowns[metric], request.signal);
        });

        return syncSummary;
    }

    window.SMMetricGrid = {
        levelFor,
        formatValue,
        buildTiles,
        setTile,
        showGridError,
        formatMegabytes,
        writesTile,
        bindBreakdowns,
    };
})();
