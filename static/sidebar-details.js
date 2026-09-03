(function() {
    'use strict';

    // Value-text thresholds for the sidebar's compact per-service mem readout
    // (this is one service's share of the whole machine, so much lower than the
    // 60/85 warn/crit used by the host-wide and per-service detail-view tiles).
    const METRIC_WARN_PCT = 5;
    const METRIC_CRIT_PCT = 10;

    const CLOUDFLARE_R2_DASHBOARD_URL = 'https://dash.cloudflare.com/7912d21c50893a42372a2187d0cbdf8b/r2/overview';

    const CI_ICONS = { success: 'check-circle', failure: 'x-circle' };

    /** Compact "Nh"/"Nd" age string for the backup badge tooltip. */
    function formatStaleAge(staleSeconds) {
        const hours = staleSeconds / 3600;
        return hours < 24 ? `${hours.toFixed(1)}h` : `${(hours / 24).toFixed(1)}d`;
    }

    function buildIcon(name, className = 'service-details__icon') {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('aria-hidden', 'true');
        svg.setAttribute('class', className);
        const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
        use.setAttribute('href', `#icon-${name}`);
        svg.appendChild(use);
        return svg;
    }

    function metricLevelClass(pct) {
        if (pct == null) return 'service-metric--empty';
        if (pct >= METRIC_CRIT_PCT) return 'service-metric--crit';
        if (pct >= METRIC_WARN_PCT) return 'service-metric--warn';
        return 'service-metric--ok';
    }

    /**
     * Build the "NNN MB N.N%" memory cell for a sidebar row (the percent is hidden on
     * mobile, where the column is too narrow to read both). Text color reflects usage
     * level (ok/warn/crit) rather than a fixed per-metric identity color.
     * @param {number | null} pct
     * @param {number | null} mb
     */
    function buildMemoryCell(pct, mb) {
        const cell = document.createElement('span');
        cell.className = `service-mem ${metricLevelClass(pct)}`;
        cell.title = 'Click to sort by this column';

        const value = document.createElement('span');
        value.className = 'service-metric__value';

        if (pct != null && mb != null) {
            const amount = document.createElement('span');
            amount.className = 'service-metric__amount';
            amount.textContent = `${mb} MB`;
            value.appendChild(amount);

            const pctEl = document.createElement('span');
            pctEl.className = 'service-metric__pct';
            pctEl.textContent = `${pct.toFixed(1)}%`;
            value.appendChild(pctEl);
        } else {
            value.textContent = pct != null ? `${pct.toFixed(1)}%` : '—';
        }
        cell.appendChild(value);
        return cell;
    }

    /**
     * Update sidebar card status indicator and detail rows.
     * @param {Element} serviceItem
     * @param {object} status
     */
    function updateServiceItem(serviceItem, status) {
        const icon = serviceItem.querySelector('.status-icon');
        if (icon) {
            icon.classList.remove('status-icon--active', 'status-icon--failed', 'status-icon--inactive');
            const use = icon.querySelector('use');
            if (status.is_active) {
                icon.classList.add('status-icon--active');
                icon.setAttribute('aria-label', 'Active');
                if (use) use.setAttribute('href', '#icon-activity');
            } else if (status.is_failed) {
                icon.classList.add('status-icon--failed');
                icon.setAttribute('aria-label', 'Failed');
                if (use) use.setAttribute('href', '#icon-alert-circle');
            } else {
                icon.classList.add('status-icon--inactive');
                icon.setAttribute('aria-label', 'Inactive');
                if (use) use.setAttribute('href', '#icon-pause-circle');
            }
        }

        const grid = serviceItem.querySelector('.service-item-grid');
        if (!grid) return;

        // Remove previous dynamic bottom-row cells (backup badge is handled separately by
        // updateBackupBadge, since it loads on its own slower fetch — leave it alone here).
        grid.querySelector('.service-details__item--ci')?.remove();
        grid.querySelector('.service-mem')?.remove();
        grid.querySelector('.service-uptime')?.remove();

        if (status.ci_status) {
            const ciIcon = CI_ICONS[status.ci_status] ?? 'alert-triangle';
            const ciLink = document.createElement('a');
            ciLink.className = `service-details__item service-details__item--ci service-details__item--ci-${status.ci_status}`;
            ciLink.href = `https://github.com/momonala/${status.project_group}/actions/workflows/ci.yml`;
            ciLink.target = '_blank';
            ciLink.rel = 'noopener';
            ciLink.title = 'View CI on GitHub';
            const ciSvg = buildIcon(ciIcon);
            ciSvg.setAttribute('aria-label', `CI ${status.ci_status}`);
            ciSvg.removeAttribute('aria-hidden');
            ciLink.appendChild(ciSvg);
            grid.appendChild(ciLink);
        }
        grid.appendChild(buildMemoryCell(status.memory_used_pct, status.memory_used_mb));

        const item = document.createElement('span');
        item.className = 'service-uptime';
        item.title = 'Click to sort by this column';
        item.textContent = status.uptime || '—';
        grid.appendChild(item);

        updateHealthRollup(serviceItem);
    }

    // States for the mobile health rollup, worst first. #icon-close is a bare X — the same
    // glyph the broken state needs, so there's no separate symbol for it.
    const HEALTH_STATES = {
        broken: { modifier: 'service-health--failed', icon: 'close', text: 'Needs attention' },
        inactive: { modifier: 'service-health--inactive', icon: 'pause-circle', text: 'Inactive' },
        ok: { modifier: null, icon: 'check', text: 'All checks pass' },
    };

    /**
     * Refresh the mobile-only health rollup from the row's own status icons: red X if anything
     * is actually broken (unit failed, CI failed, backup missing), gray pause if the unit is
     * merely stopped, green check otherwise. The alert bell is a preference rather than a health
     * signal, so it never turns the rollup red, and a stale-backup dot still counts as green.
     * @param {Element} serviceItem
     */
    function updateHealthRollup(serviceItem) {
        const grid = serviceItem.querySelector('.service-item-grid');
        if (!grid) return;

        const state = grid.querySelector(
            '.status-icon--failed, .service-details__item--ci-failure, .service-details__item--backup-red',
        )
            ? HEALTH_STATES.broken
            : grid.querySelector('.status-icon--inactive')
                ? HEALTH_STATES.inactive
                : HEALTH_STATES.ok;

        let cell = grid.querySelector('.service-health');
        if (!cell) {
            cell = document.createElement('span');
            cell.className = 'service-health';
            cell.appendChild(buildIcon('check', 'service-health__icon'));
            grid.appendChild(cell);
        }
        cell.className = ['service-health', state.modifier].filter(Boolean).join(' ');
        cell.title = state.text;
        const svg = cell.querySelector('svg');
        svg.setAttribute('aria-label', state.text);
        svg.removeAttribute('aria-hidden');
        cell.querySelector('use').setAttribute('href', `#icon-${state.icon}`);
    }

    /**
     * Render the cloud-backup badge for a service, once its status is available. Called
     * separately from updateServiceItem because backup status is fetched on its own (slower,
     * R2-backed) request and shouldn't hold up the rest of the row.
     * @param {Element} serviceItem
     * @param {object} status - { backup_status, backup_stale_seconds }
     */
    function updateBackupBadge(serviceItem, status) {
        const grid = serviceItem.querySelector('.service-item-grid');
        if (!grid) return;

        grid.querySelector('.service-details__item--backup')?.remove();
        if (!status.backup_status) return;

        const backupLink = document.createElement('a');
        backupLink.className = `service-details__item service-details__item--backup service-details__item--backup-${status.backup_status}${
            status.backup_stale ? ' service-details__item--backup-stale' : ''
        }`;
        backupLink.href = CLOUDFLARE_R2_DASHBOARD_URL;
        backupLink.target = '_blank';
        backupLink.rel = 'noopener';
        const age = formatStaleAge(status.backup_stale_seconds);
        backupLink.title = `Cloud backup: last known-good backup ${age} old`
            + (status.backup_stale ? ', source has changed since' : '');
        const backupSvg = buildIcon('cloud');
        backupSvg.setAttribute('aria-label', `Backup ${status.backup_status}`);
        backupSvg.removeAttribute('aria-hidden');
        backupLink.appendChild(backupSvg);
        grid.appendChild(backupLink);

        updateHealthRollup(serviceItem);
    }

    const ALERT_FREQUENCIES = ['hourly', 'daily', 'muted'];

    /**
     * Persist a service's alert frequency to the backend.
     * @param {string} serviceName
     * @param {string} frequency - 'hourly' | 'daily' | 'muted'
     * @returns {Promise<boolean>} whether the save succeeded
     */
    async function persistAlertFrequency(serviceName, frequency) {
        try {
            const res = await fetch('/api/alert-settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ service: serviceName, frequency }),
            });
            return res.ok;
        } catch (err) {
            console.error('Failed to update alert setting:', err);
            return false;
        }
    }

    /**
     * Render or update the alert badge in the sidebar service item. Clicking the badge
     * cycles hourly -> daily -> muted -> hourly and persists the change.
     * @param {Element} serviceItem
     * @param {string} frequency - 'hourly' | 'daily' | 'muted'
     */
    function updateAlertBadge(serviceItem, frequency) {
        const grid = serviceItem.querySelector('.service-item-grid');
        if (!grid) return;

        let badge = grid.querySelector('.service-alert-badge');
        if (!badge) {
            badge = document.createElement('span');
            badge.className = 'service-alert-badge';
            badge.setAttribute('role', 'button');
            badge.tabIndex = 0;

            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('class', 'service-alert-badge__icon');
            svg.setAttribute('aria-hidden', 'true');
            const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
            svg.appendChild(use);
            badge.appendChild(svg);

            const cycleFrequency = async (event) => {
                event.preventDefault();
                event.stopPropagation();
                const serviceName = serviceItem.getAttribute('data-service-name');
                const current = badge.dataset.frequency || 'hourly';
                const next = ALERT_FREQUENCIES[(ALERT_FREQUENCIES.indexOf(current) + 1) % ALERT_FREQUENCIES.length];
                const ok = await persistAlertFrequency(serviceName, next);
                if (!ok) return;
                updateAlertBadge(serviceItem, next);
                const control = document.getElementById('alertSettingsControl');
                const select = control?.querySelector('.alert-frequency-select');
                if (select && control.dataset.service === serviceName) {
                    select.value = next;
                    select.dataset.committed = next;
                }
            };
            badge.addEventListener('click', cycleFrequency);
            badge.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') cycleFrequency(event);
            });

            grid.appendChild(badge);
        }

        const use = badge.querySelector('use');
        badge.dataset.frequency = frequency;
        badge.classList.toggle('service-alert-badge--muted', frequency === 'muted');
        badge.setAttribute('aria-label', `Alert: ${frequency}`);
        badge.title = `Alert: ${frequency} — click to change`;
        if (use) use.setAttribute('href', frequency === 'muted' ? '#icon-bell-off' : '#icon-bell');
    }

    /**
     * Fetch alert settings and render the frequency select in the main content header.
     */
    async function loadAlertSettings() {
        const control = document.getElementById('alertSettingsControl');
        if (!control) return;

        const serviceName = control.dataset.service;
        if (!serviceName) return;

        const response = await fetch('/api/alert-settings');
        if (!response.ok) return;
        const settings = await response.json();

        const frequency = settings[serviceName] ?? 'hourly';

        const existing = control.querySelector('.alert-frequency-select');
        if (existing) {
            existing.value = frequency;
            return;
        }

        const select = document.createElement('select');
        select.className = 'alert-frequency-select t-input';
        select.setAttribute('aria-label', 'Alert frequency');

        for (const { value, label } of [
            { value: 'daily', label: 'Alert: daily' },
            { value: 'hourly', label: 'Alert: hourly' },
            { value: 'muted', label: 'Alert: muted' },
        ]) {
            const opt = document.createElement('option');
            opt.value = value;
            opt.textContent = label;
            select.appendChild(opt);
        }
        select.value = frequency;
        select.dataset.committed = frequency;

        select.addEventListener('change', async (e) => {
            const newFrequency = e.target.value;
            const prevFrequency = select.dataset.committed ?? 'hourly';
            const ok = await persistAlertFrequency(serviceName, newFrequency);
            if (!ok) {
                console.error('Failed to save alert setting, reverting');
                select.value = prevFrequency;
                window.SMTransitions?.shakeError(select);
                window.SMTransitions?.showToast('Could not save alert setting', 'error');
                return;
            }
            select.dataset.committed = newFrequency;
            window.SMTransitions?.showToast(`Alerts set to ${newFrequency}`, 'success');
            const sidebarItem = document.querySelector(`.service-item[data-service-name="${CSS.escape(serviceName)}"]`);
            if (sidebarItem) updateAlertBadge(sidebarItem, newFrequency);
        });

        control.appendChild(select);
    }

    // --- Click-to-sort: click the mem or uptime cell to sort the whole list by that
    // column (descending, missing readings last); click it again to return to the
    // default alphabetical/grouped order.

    /** Convert a formatted uptime string like "2w 3d" or "3d 15h" to minutes, for sorting. */
    function uptimeToMinutes(uptime) {
        if (!uptime || uptime === '—') return null;
        const weeks = /(\d+)w/.exec(uptime);
        const days = /(\d+)d/.exec(uptime);
        const hours = /(\d+)h/.exec(uptime);
        const minutes = /(\d+)m/.exec(uptime);
        if (!weeks && !days && !hours && !minutes) return null;
        return (
            (weeks ? Number(weeks[1]) * 7 * 24 * 60 : 0) +
            (days ? Number(days[1]) * 24 * 60 : 0) +
            (hours ? Number(hours[1]) * 60 : 0) +
            (minutes ? Number(minutes[1]) : 0)
        );
    }

    const SORT_FIELDS = {
        mem: (metrics) => metrics.memory_used_pct,
        uptime: (metrics) => uptimeToMinutes(metrics.uptime),
    };

    let sortMode = null; // null | 'mem' | 'uptime'
    let latestMetricsByName = new Map(); // service name -> { memory_used_pct, uptime }
    let originalOrder = null; // [{ group, item }], captured once from the server-rendered order
    let sortHandlersBound = false;

    function captureOriginalOrder(nav) {
        if (originalOrder) return;
        originalOrder = [];
        nav.querySelectorAll('.project-group').forEach((group) => {
            group.querySelectorAll(':scope > .service-item').forEach((item) => {
                originalOrder.push({ group, item });
            });
        });
    }

    function sortValueFor(mode, serviceName) {
        const metrics = latestMetricsByName.get(serviceName);
        if (!metrics) return null;
        return SORT_FIELDS[mode](metrics);
    }

    function applySort(nav) {
        captureOriginalOrder(nav);
        if (!originalOrder.length) return;

        if (!sortMode) {
            let lastGroup = null;
            for (const { group, item } of originalOrder) {
                if (group !== lastGroup) {
                    nav.appendChild(group);
                    lastGroup = group;
                }
                group.appendChild(item);
            }
            nav.querySelectorAll('.project-group').forEach((group) => {
                group.hidden = false;
            });
        } else {
            const sorted = [...originalOrder].sort((a, b) => {
                const va = sortValueFor(sortMode, a.item.getAttribute('data-service-name'));
                const vb = sortValueFor(sortMode, b.item.getAttribute('data-service-name'));
                if (va == null && vb == null) return 0;
                if (va == null) return 1;
                if (vb == null) return -1;
                return vb - va;
            });
            for (const { item } of sorted) {
                nav.appendChild(item);
            }
            nav.querySelectorAll('.project-group').forEach((group) => {
                group.hidden = !group.querySelector(':scope > .service-item');
            });
        }

        nav.dataset.sortMode = sortMode ?? '';
    }

    function setupSortHandlers(nav) {
        if (sortHandlersBound) return;
        sortHandlersBound = true;
        nav.addEventListener('click', (event) => {
            const cell = event.target.closest('.service-mem, .service-uptime');
            if (!cell || !nav.contains(cell)) return;
            const mode = cell.classList.contains('service-mem') ? 'mem' : 'uptime';
            sortMode = sortMode === mode ? null : mode;
            applySort(nav);
            window.SMTransitions?.showToast(
                sortMode ? `Sorted by ${sortMode}` : 'Sort cleared',
                'info',
            );
        });
    }

    /**
     * Refresh sidebar details from backend.
     */
    async function load() {
        const nav = document.querySelector('.sidebar__nav');
        if (!nav) return;

        setupSortHandlers(nav);

        const [detailsResponse, alertsResponse] = await Promise.all([
            fetch('/api/services/sidebar-details'),
            fetch('/api/alert-settings'),
        ]);

        if (!detailsResponse.ok) {
            throw new Error(`Failed to load sidebar details: ${detailsResponse.status}`);
        }
        const payload = await detailsResponse.json();
        const alertSettings = alertsResponse.ok ? await alertsResponse.json() : {};
        const services = Array.isArray(payload.services) ? payload.services : [];

        const byName = new Map();
        for (const item of nav.querySelectorAll('.service-item[data-service-name]')) {
            byName.set(item.getAttribute('data-service-name'), item);
        }

        latestMetricsByName = new Map(
            services.map((status) => [
                status.name,
                {
                    memory_used_pct: status.memory_used_pct,
                    memory_used_mb: status.memory_used_mb,
                    uptime: status.uptime,
                },
            ]),
        );

        for (const status of services) {
            const serviceItem = byName.get(status.name);
            if (!serviceItem) continue;
            updateServiceItem(serviceItem, status);
            updateAlertBadge(serviceItem, alertSettings[status.name] ?? 'hourly');
        }

        applySort(nav);

        // Backup status costs an rclone round-trip to R2 per project and can be noticeably
        // slower — fetch it separately so it never holds up the rest of the sidebar, and fill
        // in badges whenever it resolves.
        fetch('/api/services/backup-status')
            .then((res) => (res.ok ? res.json() : { services: [] }))
            .then((backupPayload) => {
                const backupStatuses = Array.isArray(backupPayload.services) ? backupPayload.services : [];
                for (const status of backupStatuses) {
                    const serviceItem = byName.get(status.name);
                    if (serviceItem) updateBackupBadge(serviceItem, status);
                }
            })
            .catch((err) => console.error('Failed to load backup status:', err));
    }

    window.ServiceMonitorSidebarDetails = {
        load,
        loadAlertSettings,
    };
})();
