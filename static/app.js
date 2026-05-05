/**
 * Service Monitor - Main JavaScript
 * 
 * Handles sidebar navigation, responsive behavior, and user interactions.
 */

(function() {
    'use strict';

    // ============================================
    // Configuration
    // ============================================
    const CONFIG = {
        // Breakpoints (must match CSS)
        BREAKPOINT_MOBILE: 640,   // Below this: mobile (iPhone)
        BREAKPOINT_TABLET: 1024,  // Below this: tablet (iPad), above: desktop
        
        // Animation timing (ms)
        TRANSITION_DURATION: 300,
        
        // Local storage keys
        STORAGE_SIDEBAR_COLLAPSED: 'servicemonitor:sidebar-collapsed',
        
        // Auto-refresh interval (30 seconds)
        AUTO_REFRESH_INTERVAL: 30000,
        
        // Toast duration (ms)
        TOAST_DURATION: 3000,
    };

    const CSS_CLASSES = {
        SIDEBAR_OPEN: 'sidebar--open',
        SIDEBAR_CLOSED: 'sidebar--closed',
        SIDEBAR_COLLAPSED: 'sidebar--collapsed',
        OVERLAY_VISIBLE: 'sidebar-overlay--visible',
        HIDDEN: 'hidden',
    };

    // ============================================
    // DOM Elements
    // ============================================
    const elements = {
        sidebar: document.getElementById('sidebar'),
        sidebarToggle: document.getElementById('sidebarToggle'),
        sidebarClose: document.getElementById('sidebarClose'),
        mobileHamburger: document.getElementById('mobileHamburger'),
        sidebarOverlay: document.getElementById('sidebarOverlay'),
        serviceSearch: document.getElementById('serviceSearch'),
        toastContainer: document.getElementById('toastContainer'),
        statusAnnouncer: document.getElementById('statusAnnouncer'),
    };

    // ============================================
    // State
    // ============================================
    const state = {
        isSidebarCollapsed: loadSidebarState(),
        isMobileSidebarOpen: false,
    };

    // ============================================
    // Utility Functions
    // ============================================
    
    /**
     * Generate consistent color for a project group using golden ratio hashing
     * @param {string} projectGroup - The project group name
     * @returns {string} Hex color code
     */
    function getProjectColor(projectGroup) {
        // Simple hash function
        let hash = 0;
        for (let i = 0; i < projectGroup.length; i++) {
            hash = ((hash << 5) - hash) + projectGroup.charCodeAt(i);
            hash = hash & hash; // Convert to 32-bit integer
        }
        
        // Use golden ratio for hue distribution
        const hue = ((Math.abs(hash) % 1000) * 0.618033988749895) % 1.0;
        
        // Convert HSV to RGB (S=0.75, V=0.95)
        const s = 0.75;
        const v = 0.95;
        const c = v * s;
        const x = c * (1 - Math.abs(((hue * 6) % 2) - 1));
        const m = v - c;
        
        let r, g, b;
        const h = hue * 6;
        if (h < 1) { r = c; g = x; b = 0; }
        else if (h < 2) { r = x; g = c; b = 0; }
        else if (h < 3) { r = 0; g = c; b = x; }
        else if (h < 4) { r = 0; g = x; b = c; }
        else if (h < 5) { r = x; g = 0; b = c; }
        else { r = c; g = 0; b = x; }
        
        const toHex = (n) => {
            const val = Math.round((n + m) * 255);
            return val.toString(16).padStart(2, '0');
        };
        
        return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
    }
    
    /**
     * Get current device type based on viewport width
     * @returns {'mobile'|'tablet'|'desktop'}
     */
    function getDeviceType() {
        const width = window.innerWidth;
        if (width < CONFIG.BREAKPOINT_MOBILE) return 'mobile';
        if (width < CONFIG.BREAKPOINT_TABLET) return 'tablet';
        return 'desktop';
    }

    /**
     * Check if current view is mobile
     * @returns {boolean}
     */
    function isMobile() {
        return getDeviceType() === 'mobile';
    }

    /**
     * Load sidebar collapsed state from localStorage
     * @returns {boolean}
     */
    function loadSidebarState() {
        try {
            return localStorage.getItem(CONFIG.STORAGE_SIDEBAR_COLLAPSED) === 'true';
        } catch {
            return false;
        }
    }

    /**
     * Save sidebar collapsed state to localStorage
     * @param {boolean} collapsed
     */
    function saveSidebarState(collapsed) {
        try {
            localStorage.setItem(CONFIG.STORAGE_SIDEBAR_COLLAPSED, String(collapsed));
        } catch {
            // localStorage not available
        }
    }

    /**
     * Show toast notification
     * @param {string} message
     * @param {'success'|'error'|'info'} type
     */
    function showToast(message, type = 'success') {
        if (!elements.toastContainer) return;
        
        const icons = {
            success: '✓',
            error: '✕',
            info: 'ℹ'
        };
        
        const titles = {
            success: 'Success',
            error: 'Error',
            info: 'Info'
        };
        
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.innerHTML = `
            <span class="toast-icon">${icons[type]}</span>
            <div class="toast-content">
                <div class="toast-title">${titles[type]}</div>
                <div class="toast-message">${message}</div>
            </div>
            <button class="toast-close" aria-label="Close notification">×</button>
        `;
        
        const closeBtn = toast.querySelector('.toast-close');
        closeBtn.addEventListener('click', () => toast.remove());
        
        elements.toastContainer.appendChild(toast);
        
        setTimeout(() => toast.classList.add('toast-exit'), CONFIG.TOAST_DURATION);
        setTimeout(() => toast.remove(), CONFIG.TOAST_DURATION + 300);
    }

    /**
     * Announce status to screen readers
     * @param {string} message
     */
    function announceStatus(message) {
        if (!elements.statusAnnouncer) return;
        elements.statusAnnouncer.textContent = message;
        setTimeout(() => elements.statusAnnouncer.textContent = '', 1000);
    }

    // ============================================
    // Sidebar Functions
    // ============================================

    /**
     * Open mobile sidebar (slide in)
     */
    function openMobileSidebar() {
        if (!elements.sidebar) return;
        
        state.isMobileSidebarOpen = true;
        elements.sidebar.classList.remove(CSS_CLASSES.SIDEBAR_CLOSED);
        elements.sidebar.classList.add(CSS_CLASSES.SIDEBAR_OPEN);
        
        // Show overlay
        if (elements.sidebarOverlay) {
            elements.sidebarOverlay.classList.add(CSS_CLASSES.OVERLAY_VISIBLE);
        }
        
        // Hide hamburger
        if (elements.mobileHamburger) {
            elements.mobileHamburger.classList.add(CSS_CLASSES.HIDDEN);
        }
        
        // Prevent body scroll on mobile
        document.body.style.overflow = 'hidden';
        
        // Focus close button for accessibility
        setTimeout(() => elements.sidebarClose?.focus(), 100);
    }

    /**
     * Close mobile sidebar (slide out)
     */
    function closeMobileSidebar() {
        if (!elements.sidebar) return;
        
        state.isMobileSidebarOpen = false;
        elements.sidebar.classList.remove(CSS_CLASSES.SIDEBAR_OPEN);
        elements.sidebar.classList.add(CSS_CLASSES.SIDEBAR_CLOSED);
        
        // Hide overlay
        if (elements.sidebarOverlay) {
            elements.sidebarOverlay.classList.remove(CSS_CLASSES.OVERLAY_VISIBLE);
        }
        
        // Show hamburger
        if (elements.mobileHamburger) {
            elements.mobileHamburger.classList.remove(CSS_CLASSES.HIDDEN);
        }
        
        // Restore body scroll
        document.body.style.overflow = '';
    }

    /**
     * Toggle desktop sidebar collapse
     */
    function toggleDesktopSidebarCollapse() {
        if (!elements.sidebar) return;
        
        state.isSidebarCollapsed = !state.isSidebarCollapsed;
        elements.sidebar.classList.toggle(CSS_CLASSES.SIDEBAR_COLLAPSED, state.isSidebarCollapsed);
        saveSidebarState(state.isSidebarCollapsed);
    }

    /**
     * Apply initial sidebar state based on device type
     */
    function initializeSidebarState() {
        if (!elements.sidebar) return;
        
        const device = getDeviceType();
        
        if (device === 'mobile') {
            // Mobile: sidebar starts closed
            elements.sidebar.classList.add(CSS_CLASSES.SIDEBAR_CLOSED);
            elements.sidebar.classList.remove(CSS_CLASSES.SIDEBAR_OPEN, CSS_CLASSES.SIDEBAR_COLLAPSED);
            if (elements.mobileHamburger) {
                elements.mobileHamburger.classList.remove(CSS_CLASSES.HIDDEN);
            }
        } else {
            // Tablet/Desktop: sidebar always visible
            elements.sidebar.classList.remove(CSS_CLASSES.SIDEBAR_CLOSED, CSS_CLASSES.SIDEBAR_OPEN);
            if (elements.mobileHamburger) {
                elements.mobileHamburger.classList.add(CSS_CLASSES.HIDDEN);
            }
            
            // Desktop: restore collapsed state
            if (device === 'desktop' && state.isSidebarCollapsed) {
                elements.sidebar.classList.add(CSS_CLASSES.SIDEBAR_COLLAPSED);
            }
        }
        
        // Ensure overlay is hidden
        if (elements.sidebarOverlay) {
            elements.sidebarOverlay.classList.remove(CSS_CLASSES.OVERLAY_VISIBLE);
        }
        
        // Restore body scroll
        document.body.style.overflow = '';
    }

    // ============================================
    // Auto-Refresh Functions
    // ============================================

    /**
     * Refresh service status without page reload
     */
    function refreshServiceStatus() {
        fetch(window.location.href, { 
            headers: { 'X-Requested-With': 'XMLHttpRequest' } 
        })
        .then(res => {
            if (!res.ok) throw new Error('Failed to refresh');
            return res.text();
        })
        .then(html => {
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');
            
            // Update sidebar navigation
            const newNav = doc.querySelector('.sidebar__nav');
            const currentNav = document.querySelector('.sidebar__nav');
            if (newNav && currentNav) {
                currentNav.innerHTML = newNav.innerHTML;
                applyProjectColors(); // Reapply colors after refresh
            }
            
            // Update status summary
            const newSummary = doc.querySelector('.status-summary');
            const currentSummary = document.querySelector('.status-summary');
            if (newSummary && currentSummary) {
                currentSummary.innerHTML = newSummary.innerHTML;
            }

            window.ServiceMonitorSidebarDetails?.load();
            
            // Reapply search filter if active
            const searchValue = elements.serviceSearch?.value;
            if (searchValue) {
                filterServices(searchValue);
            }
        })
        .catch(err => {
            console.error('⚠️ Status refresh failed:', err);
        });
    }

    /**
     * Start auto-refresh if on dashboard
     */
    function startAutoRefresh() {
        // Only auto-refresh on dashboard (no service query param)
        if (window.location.search.includes('service=')) return;
        
        setInterval(refreshServiceStatus, CONFIG.AUTO_REFRESH_INTERVAL);
    }

    // ============================================
    // Search/Filter Functions
    // ============================================

    /**
     * Filter services based on search query
     * @param {string} query
     */
    function filterServices(query) {
        const searchTerm = query.toLowerCase().trim();
        const serviceItems = document.querySelectorAll('.service-item');
        
        let visibleCount = 0;
        serviceItems.forEach(item => {
            const serviceName = item.querySelector('.service-name')?.textContent.toLowerCase() || '';
            const matches = serviceName.includes(searchTerm);
            
            item.classList.toggle('service-item--filtered', !matches);
            if (matches) visibleCount++;
        });
        
        announceStatus(`${visibleCount} service${visibleCount !== 1 ? 's' : ''} found`);
    }

    /**
     * Setup search input handler
     */
    function setupSearch() {
        if (!elements.serviceSearch) return;
        
        elements.serviceSearch.addEventListener('input', function(e) {
            filterServices(e.target.value);
        });
    }

    // ============================================
    // Navigation Loading States
    // ============================================

    /**
     * Show loading state when navigating to service details
     */
    function setupServiceNavigation() {
        document.addEventListener('click', function(e) {
            const serviceLink = e.target.closest('.service-link');
            if (!serviceLink) return;
            
            // Add loading class to clicked service
            const serviceItem = serviceLink.closest('.service-item');
            if (serviceItem) {
                serviceItem.style.opacity = '0.6';
                serviceItem.style.pointerEvents = 'none';
            }
            
            // Show loading announcement
            const serviceName = serviceLink.querySelector('.service-name')?.textContent;
            if (serviceName) {
                announceStatus(`Loading ${serviceName}...`);
            }
        });
    }

    // ============================================
    // Button Loading States
    // ============================================

    /**
     * Setup loading states for form buttons
     */
    function setupButtonLoadingStates() {
        document.querySelectorAll('form').forEach(form => {
            form.addEventListener('submit', function() {
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

    // ============================================
    // Event Handlers
    // ============================================

    /**
     * Handle sidebar toggle button click
     * @param {Event} event
     */
    function handleSidebarToggleClick(event) {
        event.stopPropagation();
        
        const device = getDeviceType();
        
        if (device === 'mobile') {
            openMobileSidebar();
        } else if (device === 'desktop') {
            toggleDesktopSidebarCollapse();
        }
        // Tablet: no toggle action (sidebar always visible and expanded)
    }

    /**
     * Handle click outside sidebar (mobile only)
     * @param {Event} event
     */
    function handleOutsideClick(event) {
        if (!isMobile() || !state.isMobileSidebarOpen) return;
        
        const clickedInsideSidebar = elements.sidebar?.contains(event.target);
        const clickedToggle = elements.sidebarToggle?.contains(event.target);
        const clickedHamburger = elements.mobileHamburger?.contains(event.target);
        
        if (!clickedInsideSidebar && !clickedToggle && !clickedHamburger) {
            closeMobileSidebar();
        }
    }

    /**
     * Handle window resize
     */
    function handleResize() {
        // Re-initialize sidebar state when crossing breakpoints
        initializeSidebarState();
    }

    /**
     * Handle overlay click
     */
    function handleOverlayClick() {
        closeMobileSidebar();
    }

    // ============================================
    // Event Listener Setup
    // ============================================

    function setupEventListeners() {
        // Sidebar toggle button (hamburger inside sidebar on desktop)
        if (elements.sidebarToggle) {
            elements.sidebarToggle.addEventListener('click', handleSidebarToggleClick);
        }
        
        // Close button (mobile only, inside sidebar)
        if (elements.sidebarClose) {
            elements.sidebarClose.addEventListener('click', closeMobileSidebar);
        }
        
        // Mobile hamburger button (fixed position)
        if (elements.mobileHamburger) {
            elements.mobileHamburger.addEventListener('click', openMobileSidebar);
        }
        
        // Overlay click
        if (elements.sidebarOverlay) {
            elements.sidebarOverlay.addEventListener('click', handleOverlayClick);
        }
        
        // Click outside sidebar
        document.addEventListener('click', handleOutsideClick);
        
        // Window resize (debounced)
        let resizeTimeout;
        window.addEventListener('resize', function() {
            clearTimeout(resizeTimeout);
            resizeTimeout = setTimeout(handleResize, 150);
        });
        
        // Handle escape key to close mobile sidebar
        document.addEventListener('keydown', function(event) {
            if (event.key === 'Escape' && state.isMobileSidebarOpen) {
                closeMobileSidebar();
            }
        });
    }

    // ============================================
    // Project Colors
    // ============================================

    /**
     * Apply project colors to all service items
     */
    function applyProjectColors() {
        document.querySelectorAll('.service-item').forEach(item => {
            const projectGroup = item.dataset.projectGroup;
            if (projectGroup) {
                const color = getProjectColor(projectGroup);
                item.style.setProperty('--project-color', color);
            }
        });
    }

    // ============================================
    // Initialization
    // ============================================

    function init() {
        initializeSidebarState();
        setupEventListeners();
        setupSearch();
        setupButtonLoadingStates();
        setupServiceNavigation();
        applyProjectColors();
        startAutoRefresh();
        
        // Show welcome message on first load
        const urlParams = new URLSearchParams(window.location.search);
        if (!urlParams.has('service') && !sessionStorage.getItem('servicemonitor:welcomed')) {
            sessionStorage.setItem('servicemonitor:welcomed', 'true');
            announceStatus('Service Monitor loaded. ' + document.querySelectorAll('.service-item').length + ' services available.');
        }
    }

    window.ServiceMonitorApp = {
        init,
    };

})();
