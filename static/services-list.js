(function() {
    'use strict';

    const AUTO_REFRESH_INTERVAL = 30000;

    /**
     * Generate consistent color for a project group using golden ratio hashing.
     * @param {string} projectGroup
     * @returns {string}
     */
    function getProjectColor(projectGroup) {
        let hash = 0;
        for (let i = 0; i < projectGroup.length; i++) {
            hash = ((hash << 5) - hash) + projectGroup.charCodeAt(i);
            hash = hash & hash;
        }

        const hue = ((Math.abs(hash) % 1000) * 0.618033988749895) % 1.0;
        const s = 0.75;
        const v = 0.95;
        const c = v * s;
        const x = c * (1 - Math.abs(((hue * 6) % 2) - 1));
        const m = v - c;

        let r;
        let g;
        let b;
        const h = hue * 6;
        if (h < 1) { r = c; g = x; b = 0; }
        else if (h < 2) { r = x; g = c; b = 0; }
        else if (h < 3) { r = 0; g = c; b = x; }
        else if (h < 4) { r = 0; g = x; b = c; }
        else if (h < 5) { r = x; g = 0; b = c; }
        else { r = c; g = 0; b = x; }

        const toHex = (n) => Math.round((n + m) * 255).toString(16).padStart(2, '0');
        return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
    }

    function applyProjectColors() {
        document.querySelectorAll('.service-item').forEach((item) => {
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
                if (!res.ok) throw new Error('Failed to refresh');
                return res.text();
            })
            .then((html) => {
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');

                const newNav = doc.querySelector('.sidebar__nav');
                const currentNav = document.querySelector('.sidebar__nav');
                if (newNav && currentNav) {
                    currentNav.innerHTML = newNav.innerHTML;
                    applyProjectColors();
                }

                const newSummary = doc.querySelector('.status-summary');
                const currentSummary = document.querySelector('.status-summary');
                if (newSummary && currentSummary) {
                    currentSummary.innerHTML = newSummary.innerHTML;
                }

                window.ServiceMonitorSidebarDetails?.load();

                const searchValue = document.getElementById('serviceSearch')?.value;
                if (searchValue) {
                    filterServices(searchValue);
                }
            })
            .catch((err) => {
                console.error('⚠️ Status refresh failed:', err);
            });
    }

    function startAutoRefresh() {
        if (window.location.search.includes('service=')) return;
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
