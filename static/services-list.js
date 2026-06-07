(function() {
    'use strict';

    const AUTO_REFRESH_INTERVAL = 30000;

    // Fixed muted palette for project group borders — consistent and intentional
    const PROJECT_COLORS = [
        '#4A9ECC',  // blue
        '#6AAB7A',  // green
        '#CC9A4A',  // amber
        '#9A6ACC',  // purple
        '#CC6A6A',  // red
        '#4ACCB8',  // teal
    ];

    /**
     * Deterministic color from fixed palette for a project group.
     * @param {string} projectGroup
     * @returns {string}
     */
    function getProjectColor(projectGroup) {
        let hash = 0;
        for (let i = 0; i < projectGroup.length; i++) {
            hash = ((hash << 5) - hash) + projectGroup.charCodeAt(i);
            hash |= 0;  // coerce to 32-bit integer
        }
        return PROJECT_COLORS[Math.abs(hash) % PROJECT_COLORS.length];
    }

    function applyProjectColors() {
        document.querySelectorAll('.project-group, .website-pill').forEach((item) => {
            const projectGroup = item.dataset.projectGroup;
            if (!projectGroup) return;
            item.style.setProperty('--project-color', getProjectColor(projectGroup));
        });
    }

    function filterServices(query) {
        const searchTerm = query.toLowerCase().trim();
        const serviceItems = document.querySelectorAll('.service-item');

        let visibleCount = 0;
        serviceItems.forEach((item) => {
            const serviceName = item.querySelector('.service-name')?.textContent.toLowerCase() || '';
            const matches = serviceName.includes(searchTerm);
            item.classList.toggle('service-item--filtered', !matches);
            if (matches) visibleCount += 1;
        });

        window.ServiceMonitorNotifications?.announceStatus(
            `${visibleCount} service${visibleCount !== 1 ? 's' : ''} found`,
        );
    }

    function refreshServiceStatus() {
        fetch(window.location.href, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
        })
            .then((res) => {
                if (!res.ok) throw new Error(`Refresh failed: ${res.status}`);
                return res.text();
            })
            .then((html) => {
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');

                // Update status icons in-place to avoid DOM teardown flicker
                doc.querySelectorAll('.service-item[data-service-name]').forEach((newItem) => {
                    const name = newItem.getAttribute('data-service-name');
                    const currentItem = document.querySelector(`.service-item[data-service-name="${CSS.escape(name)}"]`);
                    if (!currentItem) return;

                    const newIcon = newItem.querySelector('.status-icon');
                    const currentIcon = currentItem.querySelector('.status-icon');
                    if (newIcon && currentIcon) {
                        currentIcon.className = newIcon.className;
                        const ariaLabel = newIcon.getAttribute('aria-label');
                        if (ariaLabel) currentIcon.setAttribute('aria-label', ariaLabel);
                        const newUse = newIcon.querySelector('use');
                        const currentUse = currentIcon.querySelector('use');
                        if (newUse && currentUse) {
                            currentUse.setAttribute('href', newUse.getAttribute('href'));
                        }
                    }
                });

                // Update status summary counts safely using text content only
                const newSummary = doc.querySelector('.status-summary');
                const currentSummary = document.querySelector('.status-summary');
                if (newSummary && currentSummary) {
                    newSummary.querySelectorAll('.status-summary__item').forEach((newItem, i) => {
                        const currentItem = currentSummary.querySelectorAll('.status-summary__item')[i];
                        if (currentItem) {
                            const newSpan = newItem.querySelector('span');
                            const currentSpan = currentItem.querySelector('span');
                            if (newSpan && currentSpan) {
                                currentSpan.textContent = newSpan.textContent;
                            }
                        }
                    });
                }

                window.ServiceMonitorSidebarDetails?.load();

                const searchValue = document.getElementById('serviceSearch')?.value;
                if (searchValue) {
                    filterServices(searchValue);
                }
            })
            .catch((err) => {
                console.error('Status refresh failed:', err);
            });
    }

    function startAutoRefresh() {
        setInterval(refreshServiceStatus, AUTO_REFRESH_INTERVAL);
    }

    function setupSearch() {
        const serviceSearch = document.getElementById('serviceSearch');
        if (!serviceSearch) return;
        serviceSearch.addEventListener('input', (event) => {
            filterServices(event.target.value);
        });
    }

    function showWelcomeMessage() {
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.has('service') || sessionStorage.getItem('servicemonitor:welcomed')) {
            return;
        }
        sessionStorage.setItem('servicemonitor:welcomed', 'true');
        const serviceCount = document.querySelectorAll('.service-item').length;
        window.ServiceMonitorNotifications?.announceStatus(
            `Service Monitor loaded. ${serviceCount} services available.`,
        );
    }

    function init() {
        setupSearch();
        applyProjectColors();
        startAutoRefresh();
        showWelcomeMessage();
    }

    window.ServiceMonitorServicesList = {
        init,
    };
})();
