(function() {
    'use strict';

    const BREAKPOINT_MOBILE = 640;
    const BREAKPOINT_TABLET = 1024;
    const STORAGE_SIDEBAR_COLLAPSED = 'servicemonitor:sidebar-collapsed';
    const CSS_CLASSES = {
        SIDEBAR_OPEN: 'sidebar--open',
        SIDEBAR_CLOSED: 'sidebar--closed',
        SIDEBAR_COLLAPSED: 'sidebar--collapsed',
        OVERLAY_VISIBLE: 'sidebar-overlay--visible',
        HIDDEN: 'hidden',
    };

    const state = {
        isSidebarCollapsed: loadSidebarState(),
        isMobileSidebarOpen: false,
    };

    function getDeviceType() {
        const width = window.innerWidth;
        if (width < BREAKPOINT_MOBILE) return 'mobile';
        if (width < BREAKPOINT_TABLET) return 'tablet';
        return 'desktop';
    }

    function isMobile() {
        return getDeviceType() === 'mobile';
    }

    function loadSidebarState() {
        try {
            return localStorage.getItem(STORAGE_SIDEBAR_COLLAPSED) === 'true';
        } catch {
            return false;
        }
    }

    function saveSidebarState(collapsed) {
        try {
            localStorage.setItem(STORAGE_SIDEBAR_COLLAPSED, String(collapsed));
        } catch {
            return;
        }
    }

    function openMobileSidebar() {
        const sidebar = document.getElementById('sidebar');
        if (!sidebar) return;
        const sidebarOverlay = document.getElementById('sidebarOverlay');
        const mobileHamburger = document.getElementById('mobileHamburger');
        const sidebarClose = document.getElementById('sidebarClose');

        state.isMobileSidebarOpen = true;
        sidebar.classList.remove(CSS_CLASSES.SIDEBAR_CLOSED);
        sidebar.classList.add(CSS_CLASSES.SIDEBAR_OPEN);
        sidebarOverlay?.classList.add(CSS_CLASSES.OVERLAY_VISIBLE);
        mobileHamburger?.classList.add(CSS_CLASSES.HIDDEN);
        document.body.style.overflow = 'hidden';
        setTimeout(() => sidebarClose?.focus(), 100);
    }

    function closeMobileSidebar() {
        const sidebar = document.getElementById('sidebar');
        if (!sidebar) return;
        const sidebarOverlay = document.getElementById('sidebarOverlay');
        const mobileHamburger = document.getElementById('mobileHamburger');

        state.isMobileSidebarOpen = false;
        sidebar.classList.remove(CSS_CLASSES.SIDEBAR_OPEN);
        sidebar.classList.add(CSS_CLASSES.SIDEBAR_CLOSED);
        sidebarOverlay?.classList.remove(CSS_CLASSES.OVERLAY_VISIBLE);
        mobileHamburger?.classList.remove(CSS_CLASSES.HIDDEN);
        document.body.style.overflow = '';
        // Return focus to the hamburger so keyboard users don't lose their position
        setTimeout(() => mobileHamburger?.focus(), 0);
    }

    function toggleDesktopSidebarCollapse() {
        const sidebar = document.getElementById('sidebar');
        if (!sidebar) return;
        state.isSidebarCollapsed = !state.isSidebarCollapsed;
        sidebar.classList.toggle(CSS_CLASSES.SIDEBAR_COLLAPSED, state.isSidebarCollapsed);
        saveSidebarState(state.isSidebarCollapsed);
    }

    function initializeSidebarState() {
        const sidebar = document.getElementById('sidebar');
        if (!sidebar) return;
        const mobileHamburger = document.getElementById('mobileHamburger');
        const sidebarOverlay = document.getElementById('sidebarOverlay');

        const device = getDeviceType();
        if (device === 'mobile') {
            sidebar.classList.add(CSS_CLASSES.SIDEBAR_CLOSED);
            sidebar.classList.remove(CSS_CLASSES.SIDEBAR_OPEN, CSS_CLASSES.SIDEBAR_COLLAPSED);
            mobileHamburger?.classList.remove(CSS_CLASSES.HIDDEN);
        } else {
            sidebar.classList.remove(CSS_CLASSES.SIDEBAR_CLOSED, CSS_CLASSES.SIDEBAR_OPEN);
            mobileHamburger?.classList.add(CSS_CLASSES.HIDDEN);
            if (device === 'desktop' && state.isSidebarCollapsed) {
                sidebar.classList.add(CSS_CLASSES.SIDEBAR_COLLAPSED);
            }
        }
        sidebarOverlay?.classList.remove(CSS_CLASSES.OVERLAY_VISIBLE);
        document.body.style.overflow = '';
    }

    function setupServiceNavigation() {
        document.addEventListener('click', (event) => {
            const serviceLink = event.target.closest('.service-link');
            if (!serviceLink) return;

            const serviceItem = serviceLink.closest('.service-item');
            if (serviceItem) {
                serviceItem.style.opacity = '0.6';
                serviceItem.style.pointerEvents = 'none';
            }

            const serviceName = serviceLink.querySelector('.service-name')?.textContent;
            if (serviceName) {
                window.ServiceMonitorNotifications?.announceStatus(`Loading ${serviceName}...`);
            }
        });
    }

    function setupButtonLoadingStates() {
        document.querySelectorAll('form').forEach((form) => {
            form.addEventListener('submit', function onSubmit() {
                const btn = this.querySelector('button[type="submit"]');
                if (!btn) return;
                btn.disabled = true;
                btn.classList.add('btn--loading');
                const loadingText = btn.dataset.loadingText;
                if (loadingText) {
                    btn.dataset.originalText = btn.textContent;
                    btn.textContent = loadingText;
                }
            });
        });
    }

    function setupEventListeners() {
        document.getElementById('sidebarToggle')?.addEventListener('click', (event) => {
            event.stopPropagation();
            const device = getDeviceType();
            if (device === 'mobile') {
                openMobileSidebar();
            } else if (device === 'desktop') {
                toggleDesktopSidebarCollapse();
            }
        });

        document.getElementById('sidebarClose')?.addEventListener('click', closeMobileSidebar);
        document.getElementById('mobileHamburger')?.addEventListener('click', openMobileSidebar);
        document.getElementById('sidebarOverlay')?.addEventListener('click', closeMobileSidebar);

        document.addEventListener('click', (event) => {
            if (!isMobile() || !state.isMobileSidebarOpen) return;
            const sidebar = document.getElementById('sidebar');
            const sidebarToggle = document.getElementById('sidebarToggle');
            const mobileHamburger = document.getElementById('mobileHamburger');
            const clickedInsideSidebar = sidebar?.contains(event.target);
            const clickedToggle = sidebarToggle?.contains(event.target);
            const clickedHamburger = mobileHamburger?.contains(event.target);
            if (!clickedInsideSidebar && !clickedToggle && !clickedHamburger) {
                closeMobileSidebar();
            }
        });

        let resizeTimeout;
        window.addEventListener('resize', () => {
            clearTimeout(resizeTimeout);
            resizeTimeout = setTimeout(initializeSidebarState, 150);
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && state.isMobileSidebarOpen) {
                closeMobileSidebar();
            }
        });
    }

    function init() {
        initializeSidebarState();
        setupEventListeners();
        setupButtonLoadingStates();
        setupServiceNavigation();
    }

    window.ServiceMonitorUiShell = {
        init,
    };
})();
