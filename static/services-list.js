(function() {
    'use strict';

    const AUTO_REFRESH_INTERVAL = 30000;

    // Project colors are generated, not hand-picked: the group name hashes to a hue
    // (stable no matter how the service list changes), rendered in OKLCH so every hue
    // lands at the same perceived brightness on the dark theme. A repair pass then
    // walks the sidebar top to bottom and rotates any group whose hue sits within
    // HUE_MIN_DISTANCE of the group directly above it, so vertical neighbors always
    // read as distinct colors.
    const HUE_MIN_DISTANCE = 55;
    // Rotating by the golden angle escapes a clash in at most a couple of steps
    // without landing back near an earlier hue.
    const GOLDEN_ANGLE = 137.508;
    const PROJECT_COLOR_CHROMA = 0.12;
    const PROJECT_COLOR_LIGHTNESS = 0.72;

    /**
     * Deterministic hue (0-359) for a project group name.
     * @param {string} projectGroup
     * @returns {number}
     */
    function projectHue(projectGroup) {
        let hash = 0;
        for (let i = 0; i < projectGroup.length; i++) {
            hash = ((hash << 5) - hash) + projectGroup.charCodeAt(i);
            hash |= 0;  // coerce to 32-bit integer
        }
        return Math.abs(hash) % 360;
    }

    /**
     * Shortest angular distance between two hues.
     * @param {number} a
     * @param {number} b
     * @returns {number}
     */
    function hueDistance(a, b) {
        const d = Math.abs(a - b) % 360;
        return d > 180 ? 360 - d : d;
    }

    /**
     * Assign each group a hue, nudging any that clash with the group before it.
     * @param {string[]} orderedNames sidebar groups in display order
     * @returns {Map<string, number>}
     */
    function resolveProjectHues(orderedNames) {
        const hues = new Map();
        let prev = null;
        for (const name of orderedNames) {
            let hue = projectHue(name);
            let attempts = 0;
            while (prev !== null && hueDistance(hue, prev) < HUE_MIN_DISTANCE && attempts++ < 8) {
                hue = (hue + GOLDEN_ANGLE) % 360;
            }
            hues.set(name, hue);
            prev = hue;
        }
        return hues;
    }

    function applyProjectColors() {
        const groups = [...document.querySelectorAll('.project-group')];
        const hues = resolveProjectHues(groups.map((g) => g.dataset.projectGroup).filter(Boolean));
        document.querySelectorAll('.project-group, .website-pill').forEach((item) => {
            const projectGroup = item.dataset.projectGroup;
            if (!projectGroup) return;
            // Website pills reuse the sidebar's resolved hue so a project keeps one
            // color everywhere; a pill with no sidebar group falls back to its raw hue.
            const hue = hues.has(projectGroup) ? hues.get(projectGroup) : projectHue(projectGroup);
            item.style.setProperty(
                '--project-color',
                `oklch(${PROJECT_COLOR_LIGHTNESS} ${PROJECT_COLOR_CHROMA} ${hue.toFixed(1)})`,
            );
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
        // Clearing the field dissolves the typed text away; re-filter on the
        // spot rather than waiting for the tween to finish.
        window.SMTransitions?.setupInputClear(serviceSearch, (value) => filterServices(value));
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
