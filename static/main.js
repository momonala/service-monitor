(function() {
    'use strict';

    function initAll() {
        window.ServiceMonitorApp?.init();
        window.ServiceMonitorLogStream?.init();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
        return;
    }
    initAll();
})();
