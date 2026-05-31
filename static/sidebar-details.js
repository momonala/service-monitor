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

    function buildDetailItem(className, iconName, text, title) {
        const item = document.createElement('span');
        item.className = `service-details__item ${className}`;
        item.appendChild(buildIcon(iconName));
        item.appendChild(document.createTextNode(text));
        if (title) item.title = title;
        return item;
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
                if (use) use.setAttribute('href', '#icon-circle');
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
     * Refresh sidebar details from backend.
     */
    async function load() {
        const nav = document.querySelector('.sidebar__nav');
        if (!nav) return;

        const response = await fetch('/api/services/sidebar-details');
        if (!response.ok) {
            throw new Error(`Failed to load sidebar details: ${response.status}`);
        }
        const payload = await response.json();
        const services = Array.isArray(payload.services) ? payload.services : [];

        const byName = new Map();
        for (const item of nav.querySelectorAll('.service-item[data-service-name]')) {
            byName.set(item.getAttribute('data-service-name'), item);
        }

        for (const status of services) {
            const serviceItem = byName.get(status.name);
            if (!serviceItem) continue;
            updateServiceItem(serviceItem, status);
        }
    }

    window.ServiceMonitorSidebarDetails = {
        load,
    };
})();
