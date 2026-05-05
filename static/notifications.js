(function() {
    'use strict';

    /**
     * Show toast notification.
     * @param {string} message
     * @param {'success'|'error'|'info'} type
     */
    function showToast(message, type = 'success') {
        const toastContainer = document.getElementById('toastContainer');
        if (!toastContainer) return;

        const icons = {
            success: '✓',
            error: '✕',
            info: 'ℹ',
        };

        const titles = {
            success: 'Success',
            error: 'Error',
            info: 'Info',
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
        closeBtn?.addEventListener('click', () => toast.remove());

        toastContainer.appendChild(toast);
        setTimeout(() => toast.classList.add('toast-exit'), 3000);
        setTimeout(() => toast.remove(), 3300);
    }

    /**
     * Announce status to screen readers.
     * @param {string} message
     */
    function announceStatus(message) {
        const statusAnnouncer = document.getElementById('statusAnnouncer');
        if (!statusAnnouncer) return;
        statusAnnouncer.textContent = message;
        setTimeout(() => {
            statusAnnouncer.textContent = '';
        }, 1000);
    }

    window.ServiceMonitorNotifications = {
        showToast,
        announceStatus,
    };
})();
