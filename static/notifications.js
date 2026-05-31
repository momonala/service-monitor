(function() {
    'use strict';

    function announceStatus(message) {
        const statusAnnouncer = document.getElementById('statusAnnouncer');
        if (!statusAnnouncer) return;
        statusAnnouncer.textContent = message;
        setTimeout(() => {
            statusAnnouncer.textContent = '';
        }, 1000);
    }

    window.ServiceMonitorNotifications = {
        announceStatus,
    };
})();
