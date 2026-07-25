// Shared, stateless helpers for the system and per-service metric charts.
// Kept dependency-free so both chart modules can build on the same primitives.
(function() {
    'use strict';

    function cssToken(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    function withOpacity(color, alpha) {
        const hex = color.replace('#', '');
        if (hex.length !== 6) return color;
        const r = parseInt(hex.slice(0, 2), 16);
        const g = parseInt(hex.slice(2, 4), 16);
        const b = parseInt(hex.slice(4, 6), 16);
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    function readLocal(key) {
        try {
            return localStorage.getItem(key);
        } catch {
            return null;
        }
    }

    function writeLocal(key, value) {
        try {
            localStorage.setItem(key, value);
        } catch {
            // Ignore quota / private-mode failures.
        }
    }

    function setPressed(btn, active) {
        btn.classList.toggle('is-active', active);
        btn.setAttribute('aria-pressed', String(active));
    }

    function syncChoiceGroup(root, selector, dataKey, activeValue) {
        root.querySelectorAll(selector).forEach((btn) => {
            setPressed(btn, btn.dataset[dataKey] === activeValue);
        });
    }

    function padCell(value, width, align = 'left') {
        const text = String(value);
        if (text.length >= width) return text.slice(0, width);
        const padding = ' '.repeat(width - text.length);
        return align === 'right' ? padding + text : text + padding;
    }

    function formatTooltipValue(value) {
        return value == null ? '—' : String(value);
    }

    // Compact x-axis label; wider windows show the date, narrow ones just the time.
    function formatXTick(value, window) {
        const date = new Date(value);
        if (window === '7d' || window === '24h') {
            return date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit' });
        }
        return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    }

    window.SMChartUtils = {
        cssToken,
        withOpacity,
        readLocal,
        writeLocal,
        setPressed,
        syncChoiceGroup,
        padCell,
        formatTooltipValue,
        formatXTick,
    };
})();
