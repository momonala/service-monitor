(function() {
    'use strict';

    function initAll() {
        window.ServiceMonitorUiShell?.init();
        window.ServiceMonitorServicesList?.init();
        window.ServiceMonitorLogStream?.init();
        window.ServiceMonitorSidebarDetails?.load().catch((error) => {
            console.error('⚠️ Sidebar details load failed:', error);
        });
        window.ServiceMonitorSidebarDetails?.loadAlertSettings().catch((error) => {
            console.error('⚠️ Alert settings load failed:', error);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
        return;
    }
    initAll();
})();
