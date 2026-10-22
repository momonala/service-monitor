(function() {
    'use strict';

    const {
        levelFor, formatValue, buildTiles, setTile, showGridError, formatMegabytes, writesTile, bindBreakdowns,
    } = window.SMMetricGrid;

    const REFRESH_INTERVAL = 10000;

    // Usage thresholds (percent) at which a metric reads as elevated / critical.
    const WARN_PCT = 60;
    const CRIT_PCT = 85;
    // Raspberry Pi soft-throttles around 80°C; warn well before that.
    const WARN_TEMP_C = 60;
    const CRIT_TEMP_C = 75;
    // Temperature bar maps this Celsius window onto 0–100% fill.
    const TEMP_BAR_MIN_C = 40;
    const TEMP_BAR_MAX_C = 80;
    const WARN_WRITES_MB = 2 * 1024;
    const CRIT_WRITES_MB = 5 * 1024;

    // Same order as the history chart series.
    const TILES = [
        { id: 'writes', icon: 'activity', label: 'Disk writes', shortLabel: 'Writes', bar: true, button: true },
        { id: 'cpu', icon: 'cpu', label: 'CPU', bar: true, button: true },
        { id: 'disk', icon: 'disk', label: 'Disk', bar: true, button: true },
        { id: 'temperature', icon: 'thermometer', label: 'Temperature', shortLabel: 'Temp', dualBar: true },
        { id: 'memory', icon: 'memory', label: 'Memory', bar: true, button: true },
    ];

    const BREAKDOWNS = {
        cpu: { title: 'CPU by project, latest reading', format: (pct) => formatValue(pct, '%') },
        memory: { title: 'Memory by project, latest reading', format: formatMegabytes },
        disk: {
            title: 'Disk space by project directory',
            format: formatMegabytes,
            loadingMessage: 'Measuring project directories…',
        },
        writes: { title: 'Disk writes, last 24h', format: formatMegabytes },
    };

    const HEALTH_ICONS = { ok: 'check-circle', warn: 'alert-triangle', crit: 'x-circle', unknown: 'alert-circle' };

    let ipCopyBound = false;
    let aptCheckBound = false;
    let aptChecking = false;
    let lastHealth = null;
    let syncBreakdownSummary = () => {};

    function renderIp(ip) {
        const row = document.getElementById('sidebarIp');
        const value = document.getElementById('sidebarIpValue');
        const copyBtn = document.getElementById('sidebarIpCopy');
        if (!row || !value || !copyBtn) return;

        if (!ip) {
            row.hidden = true;
            return;
        }
        row.hidden = false;
        value.textContent = ip;

        if (!ipCopyBound) {
            ipCopyBound = true;
            copyBtn.addEventListener('click', () => {
                navigator.clipboard?.writeText(value.textContent).then(() => {
                    copyBtn.textContent = 'Copied';
                    setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1200);
                });
            });
        }
    }

    // "Last reboot" reads better with an at-a-glance age next to the timestamp.
    const RELATIVE_TIME = new Intl.RelativeTimeFormat(undefined, { numeric: 'always' });

    function relativeAge(date) {
        const minutes = Math.floor((Date.now() - date.getTime()) / 60000);
        if (minutes < 1) return 'just now';
        if (minutes < 60) return RELATIVE_TIME.format(-minutes, 'minute');
        if (minutes < 1440) return RELATIVE_TIME.format(-Math.floor(minutes / 60), 'hour');
        return RELATIVE_TIME.format(-Math.floor(minutes / 1440), 'day');
    }

    function renderReboot(bootTime) {
        const row = document.getElementById('sidebarReboot');
        const value = document.getElementById('sidebarRebootValue');
        if (!row || !value) return;

        if (!bootTime) {
            row.hidden = true;
            return;
        }
        const date = new Date(bootTime);
        if (isNaN(date.getTime())) {
            row.hidden = true;
            return;
        }
        row.hidden = false;
        const formatted = date.toLocaleString(undefined, {
            year: 'numeric', month: 'short', day: 'numeric',
            hour: 'numeric', minute: '2-digit',
        });
        value.textContent = `Last reboot: ${formatted} (${relativeAge(date)})`;
        value.title = value.textContent;
    }

    function setLine(id, text, title = '') {
        const el = document.getElementById(id);
        if (!el) return;
        el.hidden = !text;
        el.textContent = text;
        el.title = title;
    }

    function joinParts(...parts) {
        return parts.filter(Boolean).join(' · ');
    }

    function renderHost(info) {
        const card = document.getElementById('sidebarHost');
        const host = info.host;
        if (!card || !host) return;
        card.hidden = false;

        setLine('sidebarHostModel', host.model || info.hostname);
        setLine(
            'sidebarHostOs',
            joinParts(host.os_name?.replace(' GNU/Linux', ''), host.arch),
            host.os_name || '',
        );
        setLine(
            'sidebarHostPython',
            joinParts(`Kernel ${host.kernel.split('+')[0]}`, `Python ${host.python_version}`),
            joinParts(
                `Kernel ${host.kernel}`,
                `Service Monitor Python ${host.python_version}`,
                host.system_python_version && `system python3 ${host.system_python_version}`,
            ),
        );
        setLine('sidebarHostCapacity', joinParts(
            info.cpu_count && `${info.cpu_count} cores`,
            info.memory_total_mb != null && `${(info.memory_total_mb / 1024).toFixed(1)} GB RAM`,
            info.disk_total_gb != null && `${Math.round(info.disk_total_gb)} GB disk`,
        ));

        renderHealth(info.health);
    }

    function healthItems(health) {
        const items = [];
        if (health.throttled_now != null) {
            if (health.throttled_now.length) {
                items.push({ level: 'crit', text: `Now: ${health.throttled_now.join(', ')}` });
            } else if (health.throttled_since_boot.length) {
                items.push({ level: 'warn', text: `Since boot: ${health.throttled_since_boot.join(', ')}` });
            } else {
                items.push({ level: 'ok', text: 'Power and thermals OK' });
            }
        }
        items.push(health.reboot_required
            ? { level: 'warn', text: 'Reboot required' }
            : { level: 'ok', text: 'No reboot pending' });
        items.push(aptItem(health.apt_updates));
        if (health.ntp_synced != null) {
            items.push(health.ntp_synced
                ? { level: 'ok', text: 'Clock synced' }
                : { level: 'crit', text: 'Clock not synced' });
        }
        if (health.tunnel_active != null) {
            items.push(health.tunnel_active
                ? { level: 'ok', text: 'Cloudflare tunnel up' }
                : { level: 'crit', text: 'Cloudflare tunnel down' });
        }
        return items;
    }

    function aptItem(count) {
        const action = aptChecking ? 'Checking…' : 'Check';
        if (count == null) return { level: 'unknown', text: 'apt updates unknown', action };
        return count > 0
            ? { level: 'warn', text: `${count} apt update${count === 1 ? '' : 's'} pending`, action }
            : { level: 'ok', text: 'Packages up to date', action };
    }

    function bindAptCheck(list) {
        if (aptCheckBound) return;
        aptCheckBound = true;
        list.addEventListener('click', async (event) => {
            if (!event.target.closest('.sidebar-health__check') || aptChecking || !lastHealth) return;
            aptChecking = true;
            renderHealth(lastHealth);
            try {
                const res = await fetch('/api/apt-updates', { method: 'POST' });
                if (res.ok) lastHealth = { ...lastHealth, apt_updates: (await res.json()).apt_updates };
            } finally {
                aptChecking = false;
                renderHealth(lastHealth);
            }
        });
    }

    function renderHealth(health) {
        const list = document.getElementById('sidebarHealthList');
        const count = document.getElementById('sidebarHealthCount');
        if (!list || !count) return;
        lastHealth = health;
        bindAptCheck(list);

        list.hidden = !health;
        if (!health) {
            count.hidden = true;
            return;
        }

        const items = healthItems(health);
        list.replaceChildren(...items.map(({ level, text, action }) => {
            const li = document.createElement('li');
            li.className = `sidebar-health__item sidebar-health__item--${level}`;
            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('class', 'sidebar-row__icon');
            svg.setAttribute('aria-hidden', 'true');
            const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
            use.setAttribute('href', `#icon-${HEALTH_ICONS[level]}`);
            svg.appendChild(use);
            const label = document.createElement('span');
            label.textContent = text;
            li.append(svg, label);
            if (action) {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'sidebar-row-button sidebar-health__check';
                button.textContent = action;
                button.disabled = aptChecking;
                li.appendChild(button);
            }
            return li;
        }));

        const issues = items.filter((item) => item.level === 'warn' || item.level === 'crit');
        count.hidden = issues.length === 0;
        count.textContent = `${issues.length} issue${issues.length === 1 ? '' : 's'}`;
        count.classList.toggle('sidebar-health__count--crit', issues.some((item) => item.level === 'crit'));
    }

    function tempFill(celsius) {
        if (celsius == null) return null;
        const span = TEMP_BAR_MAX_C - TEMP_BAR_MIN_C;
        return Math.min(100, Math.max(0, ((celsius - TEMP_BAR_MIN_C) / span) * 100));
    }

    function render(container, info) {
        if (!container.querySelector('.metric')) {
            buildTiles(container, TILES);
            syncBreakdownSummary = bindBreakdowns(container, document.getElementById('metricBreakdown'), {
                breakdowns: BREAKDOWNS,
                endpoint: (metric) => `/api/system-info/by-project/${metric}`,
            });
        }
        container.removeAttribute('aria-busy');

        renderHost(info);
        renderIp(info.local_ip);
        renderReboot(info.boot_time);
        window.ServiceMonitorSystemMetricsChart?.setBootTime(info.boot_time);

        setTile(container, 'cpu', {
            value: formatValue(info.cpu_percent, '%'),
            sub: (info.cpu_percent != null && info.cpu_count)
                ? `${(info.cpu_percent / 100 * info.cpu_count).toFixed(1)} / ${info.cpu_count} cores`
                : '',
            level: levelFor(info.cpu_percent, WARN_PCT, CRIT_PCT),
            fill: info.cpu_percent,
        });

        setTile(container, 'disk', {
            value: formatValue(info.disk_used_pct, '%'),
            sub: (info.disk_used_gb != null && info.disk_total_gb != null)
                ? `${info.disk_used_gb} / ${info.disk_total_gb} GB`
                : '',
            level: levelFor(info.disk_used_pct, WARN_PCT, CRIT_PCT),
            fill: info.disk_used_pct,
        });

        setTile(container, 'temperature', {
            value: formatValue(info.temperature_c, '°C'),
            sub: info.temperature_avg_24h != null
                ? `avg ${formatValue(info.temperature_avg_24h, '°C')}`
                : '',
            level: levelFor(info.temperature_c, WARN_TEMP_C, CRIT_TEMP_C),
            fill: tempFill(info.temperature_c),
            avgFill: tempFill(info.temperature_avg_24h),
            maxFill: tempFill(info.temperature_max_24h),
        });

        setTile(container, 'memory', {
            value: formatValue(info.memory_used_pct, '%'),
            sub: (info.memory_used_mb != null && info.memory_total_mb != null)
                ? `${(info.memory_used_mb / 1024).toFixed(1)} / ${(info.memory_total_mb / 1024).toFixed(1)} GB`
                : '',
            level: levelFor(info.memory_used_pct, WARN_PCT, CRIT_PCT),
            fill: info.memory_used_pct,
        });

        setTile(container, 'writes', {
            ...writesTile(info),
            level: levelFor(info.disk_written_mb, WARN_WRITES_MB, CRIT_WRITES_MB),
            fill: info.disk_written_mb == null ? null : (info.disk_written_mb / CRIT_WRITES_MB) * 100,
        });
        syncBreakdownSummary();
    }

    async function refresh(container) {
        try {
            const res = await fetch('/api/system-info');
            if (!res.ok) throw new Error(`system-info ${res.status}`);
            render(container, await res.json());
        } catch (err) {
            console.error('System info refresh failed:', err);
            showGridError(container, `Could not load system info — ${err.message}.`);
        }
    }

    function init() {
        const container = document.getElementById('systemMetrics');
        if (!container) return;
        refresh(container);
        setInterval(() => refresh(container), REFRESH_INTERVAL);
    }

    window.ServiceMonitorSystemInfo = { init };
})();
