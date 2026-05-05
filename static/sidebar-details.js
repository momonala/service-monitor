(function() {
    'use strict';

    /**
     * Build a detail badge item.
     * @param {string} className
     * @param {string} text
     * @param {string} [title]
     * @returns {HTMLSpanElement}
     */
    function buildDetailItem(className, text, title) {
        const item = document.createElement('span');
        item.className = `service-details__item ${className}`;
        item.textContent = text;
        if (title) {
            item.title = title;
        }
        return item;
    }

    /**
     * Update sidebar card status indicator and detail rows.
     * @param {Element} serviceItem
     * @param {object} status
     */
    function updateServiceItem(serviceItem, status) {
        const indicator = serviceItem.querySelector('.status-indicator');
        if (indicator) {
            indicator.classList.remove('status-indicator--active', 'status-indicator--failed', 'status-indicator--inactive');
            if (status.is_active) {
                indicator.classList.add('status-indicator--active');
                indicator.setAttribute('aria-label', 'Active');
            } else if (status.is_failed) {
                indicator.classList.add('status-indicator--failed');
                indicator.setAttribute('aria-label', 'Failed');
            } else {
                indicator.classList.add('status-indicator--inactive');
                indicator.setAttribute('aria-label', 'Inactive');
            }
        }

        let details = serviceItem.querySelector('.service-details');
        const hasDetails = Boolean(
            status.uptime || status.memory || status.cpu || status.last_error || status.ci_status,
        );
        if (!hasDetails) {
            details?.remove();
            return;
        }

        if (!details) {
            details = document.createElement('div');
            details.className = 'service-details';
            serviceItem.appendChild(details);
        }
        details.innerHTML = '';

        if (status.uptime) {
            details.appendChild(buildDetailItem('service-details__item--uptime', `⏱️ ${status.uptime}`));
        }
        if (status.memory) {
            details.appendChild(buildDetailItem('service-details__item--memory', `💾 ${status.memory}`));
        }
        if (status.cpu) {
            details.appendChild(buildDetailItem('service-details__item--cpu', `⚡ ${status.cpu}`));
        }
        if (status.ci_status) {
            const ciEmoji = status.ci_status === 'success' ? '✅' : status.ci_status === 'failure' ? '❌' : '⚠️';
            details.appendChild(buildDetailItem('service-details__item--ci', `CI: ${ciEmoji}`));
        }
        if (status.last_error) {
            details.appendChild(
                buildDetailItem('service-details__item--error', `❌ ${status.last_error}`, status.last_error),
            );
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
