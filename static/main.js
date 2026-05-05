(function() {
    'use strict';

    function initAll() {
        window.ServiceMonitorApp?.init();
        window.ServiceMonitorLogStream?.init();
        window.ServiceMonitorSidebarDetails?.load().catch((error) => {
            console.error('⚠️ Sidebar details load failed:', error);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
        return;
    }
    initAll();
})();
