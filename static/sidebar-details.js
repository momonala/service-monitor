(function() {
    'use strict';

    function buildIcon(name) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('aria-hidden', 'true');
        svg.setAttribute('class', 'service-details__icon');
        const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
        use.setAttribute('href', `#icon-${name}`);
        svg.appendChild(use);
        return svg;
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

        // Remove previous dynamic bottom-row cells
        grid.querySelector('.service-details__item--ci')?.remove();
        grid.querySelector('.service-uptime')?.remove();

        if (status.ci_status) {
            const ciIcon = status.ci_status === 'success' ? 'check-circle' : status.ci_status === 'failure' ? 'x-circle' : 'alert-triangle';
            const ciClass = `service-details__item service-details__item--ci service-details__item--ci-${status.ci_status}`;
            const ciSpan = document.createElement('span');
            ciSpan.className = ciClass;
            const ciSvg = buildIcon(ciIcon);
            ciSvg.setAttribute('aria-label', `CI ${status.ci_status}`);
            ciSvg.removeAttribute('aria-hidden');
            ciSpan.appendChild(ciSvg);
            grid.appendChild(ciSpan);
        }
        if (status.uptime) {
            const item = document.createElement('span');
            item.className = 'service-uptime';
            item.textContent = status.uptime;
            grid.appendChild(item);
        }
    }

    /**
     * Render or update the alert badge in the sidebar service item.
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

            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('class', 'service-alert-badge__icon');
            svg.setAttribute('aria-hidden', 'true');
            const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
            svg.appendChild(use);
            badge.appendChild(svg);

            grid.appendChild(badge);
        }

        const use = badge.querySelector('use');
        badge.classList.toggle('service-alert-badge--muted', frequency === 'muted');
        badge.setAttribute('aria-label', `Alert: ${frequency}`);
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

        if (control.querySelector('.alert-frequency-select')) {
            control.querySelector('.alert-frequency-select').value = frequency;
            return;
        }

        const select = document.createElement('select');
        select.className = 'alert-frequency-select';
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
            try {
                const res = await fetch('/api/alert-settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ service: serviceName, frequency: newFrequency }),
                });
                if (!res.ok) {
                    console.error('Failed to save alert setting, reverting');
                    select.value = prevFrequency;
                    return;
                }
                select.dataset.committed = newFrequency;
                const sidebarItem = document.querySelector(`.service-item[data-service-name="${CSS.escape(serviceName)}"]`);
                if (sidebarItem) updateAlertBadge(sidebarItem, newFrequency);
            } catch (err) {
                console.error('Failed to update alert setting:', err);
                select.value = prevFrequency;
            }
        });

        control.appendChild(select);
    }

    /**
     * Refresh sidebar details from backend.
     */
    async function load() {
        const nav = document.querySelector('.sidebar__nav');
        if (!nav) return;

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

        for (const status of services) {
            const serviceItem = byName.get(status.name);
            if (!serviceItem) continue;
            updateServiceItem(serviceItem, status);
            updateAlertBadge(serviceItem, alertSettings[status.name] ?? 'hourly');
        }
    }

    window.ServiceMonitorSidebarDetails = {
        load,
        loadAlertSettings,
    };
})();
