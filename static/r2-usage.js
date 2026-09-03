(function() {
    'use strict';

    const { levelFor, buildTiles, setTile, showGridError } = window.SMMetricGrid;

    const REFRESH_INTERVAL = 60000; // server caches for 5 min; poll faster just to catch that refresh

    // % of free-tier quota consumed at which a tile reads as elevated / critical.
    const WARN_PCT = 60;
    const CRIT_PCT = 85;

    const FREE_TIER_STORAGE_GB = 10;
    const FREE_TIER_CLASS_A_REQUESTS = 1_000_000;
    const FREE_TIER_CLASS_B_REQUESTS = 10_000_000;

    const TILES = [
        { id: 'storage', icon: 'disk', label: 'R2 Storage', bar: true },
        { id: 'class_a', icon: 'upload', label: 'R2 Class A', bar: true },
        { id: 'class_b', icon: 'download', label: 'R2 Class B', bar: true },
    ];

    function formatPct(pct) {
        return pct == null ? '—' : `${pct}%`;
    }

    function formatCompact(count) {
        return count >= 1_000_000
            ? `${(count / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
            : count.toLocaleString();
    }

    function formatCount(count, freeTierLimit) {
        return count == null ? '' : `${formatCompact(count)} / ${formatCompact(freeTierLimit)}`;
    }

    function render(container, usage) {
        if (!container.querySelector('.metric')) buildTiles(container, TILES);

        setTile(container, 'storage', {
            value: formatPct(usage.storage_pct),
            sub: usage.storage_bytes == null
                ? ''
                : `${(usage.storage_bytes / 1e9).toFixed(2)} / ${FREE_TIER_STORAGE_GB} GB`,
            level: levelFor(usage.storage_pct, WARN_PCT, CRIT_PCT),
            fill: usage.storage_pct,
        });

        setTile(container, 'class_a', {
            value: formatPct(usage.class_a_pct),
            sub: formatCount(usage.class_a_requests, FREE_TIER_CLASS_A_REQUESTS),
            level: levelFor(usage.class_a_pct, WARN_PCT, CRIT_PCT),
            fill: usage.class_a_pct,
        });

        setTile(container, 'class_b', {
            value: formatPct(usage.class_b_pct),
            sub: formatCount(usage.class_b_requests, FREE_TIER_CLASS_B_REQUESTS),
            level: levelFor(usage.class_b_pct, WARN_PCT, CRIT_PCT),
            fill: usage.class_b_pct,
        });
    }

    async function refresh(section, container) {
        try {
            const res = await fetch('/api/r2-usage');
            if (!res.ok) throw new Error(`r2-usage ${res.status}`);
            const usage = await res.json();
            if (usage.storage_pct == null) {
                // No Cloudflare token configured, or the API is unreachable — hide rather than
                // show an error for a feature that may simply not be configured on this host.
                section.hidden = true;
                return;
            }
            section.hidden = false;
            render(container, usage);
        } catch (err) {
            console.error('R2 usage refresh failed:', err);
            if (!container.querySelector('.metric')) {
                section.hidden = false;
                showGridError(container, `Could not load R2 usage — ${err.message}.`);
            }
        }
    }

    function init() {
        const section = document.getElementById('r2UsageSection');
        const container = document.getElementById('r2Usage');
        if (!section || !container) return;
        refresh(section, container);
        setInterval(() => refresh(section, container), REFRESH_INTERVAL);
    }

    window.ServiceMonitorR2Usage = { init };
})();
