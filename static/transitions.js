// transitions.dev orchestration for Service Monitor.
//
// Each block below is the reference snippet's JS adapted to this app's DOM.
// Durations are read back off the CSS custom properties so tuning
// transitions.css alone keeps JS and CSS in sync.
(function() {
    'use strict';

    const root = document.documentElement;

    /** Numeric value of a CSS custom property, with a fallback. */
    function num(name, fallback) {
        const value = parseFloat(getComputedStyle(root).getPropertyValue(name));
        return Number.isFinite(value) ? value : fallback;
    }

    /** Raw (trimmed) value of a CSS custom property, with a fallback. */
    function token(name, fallback) {
        return getComputedStyle(root).getPropertyValue(name).trim() || fallback;
    }

    const prefersReducedMotion = () =>
        window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ── Toast open / close (22) + success check (10) ──────────────────
    // Transient confirmation for actions that have no other visible
    // outcome: restart triggered, inspections run, alert setting saved.

    const TOAST_VISIBLE_MS = 4000;

    const TOAST_ICONS = {
        success: '#icon-check-circle',
        error: '#icon-x-circle',
        info: '#icon-activity',
    };

    function toastHost() {
        let host = document.getElementById('toastHost');
        if (!host) {
            host = document.createElement('div');
            host.id = 'toastHost';
            host.className = 'toast-host';
            host.setAttribute('aria-live', 'polite');
            document.body.appendChild(host);
        }
        return host;
    }

    /** The animated checkmark used by success toasts. */
    function buildSuccessCheck() {
        const wrap = document.createElement('span');
        wrap.className = 't-success-check toast__icon';
        wrap.setAttribute('data-state', 'out');
        wrap.setAttribute('aria-hidden', 'true');
        wrap.innerHTML =
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" ' +
            'stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M4 12.5L9.5 18L20 6.5"/></svg>';

        // Calibrate the stroke-draw to this path rather than the
        // snippet's placeholder dasharray.
        const path = wrap.querySelector('path');
        const length = Math.ceil(path.getTotalLength());
        path.style.strokeDasharray = String(length);
        path.style.strokeDashoffset = String(length);
        return wrap;
    }

    function buildStaticIcon(kind) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', 'toast__icon');
        svg.setAttribute('aria-hidden', 'true');
        const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
        use.setAttribute('href', TOAST_ICONS[kind] ?? TOAST_ICONS.info);
        svg.appendChild(use);
        return svg;
    }

    /**
     * Show a transient toast. Rises from below on the slower open clock and
     * leaves on the faster close clock (the asymmetry is in the CSS).
     * @param {string} message
     * @param {'success' | 'error' | 'info'} [kind]
     */
    function showToast(message, kind = 'info') {
        const host = toastHost();

        const toast = document.createElement('div');
        toast.className = `t-toast toast toast--${kind}`;
        toast.setAttribute('role', kind === 'error' ? 'alert' : 'status');

        toast.appendChild(kind === 'success' ? buildSuccessCheck() : buildStaticIcon(kind));

        const text = document.createElement('span');
        text.className = 'toast__msg';
        text.textContent = message;
        toast.appendChild(text);

        host.appendChild(toast);

        requestAnimationFrame(() => {
            toast.classList.add('is-open');
            // The check is mounted with the toast, so the plain form is
            // enough — no reflow-replay needed.
            toast.querySelector('.t-success-check')?.setAttribute('data-state', 'in');
        });

        const closeMs = num('--toast-close', 250);
        setTimeout(() => {
            toast.classList.remove('is-open');
            setTimeout(() => toast.remove(), closeMs + 50);
        }, TOAST_VISIBLE_MS);

        return toast;
    }

    // ── Accordion expand (21) ─────────────────────────────────────────
    // The chart modules own the collapse state (and persist it); this only
    // mirrors that state onto the data-open attribute CSS animates from.

    function setAccordionOpen(accordion, open) {
        if (!accordion) return;

        if (accordion.dataset.accInit === '1') {
            accordion.setAttribute('data-open', String(open));
            return;
        }
        // First write restores a persisted state — snap to it, or the panel
        // plays a collapse animation on every page load.
        accordion.dataset.accInit = '1';
        const parts = accordion.querySelectorAll('.t-acc-panel, .t-acc-panel-inner, .t-acc-chevron');
        parts.forEach((el) => { el.style.transition = 'none'; });
        accordion.setAttribute('data-open', String(open));
        void accordion.offsetWidth;  // force reflow
        parts.forEach((el) => { el.style.transition = ''; });
    }

    // ── Tabs sliding (16) ─────────────────────────────────────────────
    // The rollup / lookback segmented controls. The chart modules flip
    // .is-active via SMChartUtils.createChoiceGroup; movePill re-measures
    // and CSS tweens the pill between the two positions.

    function movePill(bar, animate = true) {
        if (!bar) return;
        const pill = bar.querySelector('.t-tabs-pill');
        const active = bar.querySelector('.t-tab.is-active') || bar.querySelector('.t-tab');
        if (!pill || !active) return;

        const write = () => {
            pill.style.transform = `translateX(${active.offsetLeft}px)`;
            pill.style.width = `${active.offsetWidth}px`;
        };

        const placed = pill.dataset.placed === '1';
        pill.dataset.placed = '1';
        if (animate && placed) {
            write();
            return;
        }
        // First paint and resize: land the pill without a transition, or it
        // animates in from translateX(0) / width: 0.
        const previous = pill.style.transition;
        pill.style.transition = 'none';
        write();
        void pill.offsetWidth;  // force reflow
        pill.style.transition = previous;
    }

    /** Re-measure every segmented control inside `scope` (default: document). */
    function syncPills(scope = document, animate = true) {
        scope.querySelectorAll('.t-tabs').forEach((bar) => movePill(bar, animate));
    }

    function setupTabs() {
        document.querySelectorAll('.system-chart__ranges, .system-chart__rollups').forEach((bar) => {
            bar.classList.add('t-tabs');
            bar.querySelectorAll('button').forEach((btn) => btn.classList.add('t-tab'));
            const pill = document.createElement('span');
            pill.className = 't-tabs-pill';
            pill.setAttribute('aria-hidden', 'true');
            bar.prepend(pill);
        });

        requestAnimationFrame(() => syncPills(document, false));

        let resizeTimer;
        window.addEventListener('resize', () => {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(() => syncPills(document, false), 150);
        });
    }

    // ── Skeleton loader and reveal (14) ───────────────────────────────
    // Metric grids mount as a pulsing placeholder and cross-fade to the
    // real tiles once the first fetch lands.

    // Tile counts match what each grid renders, so the placeholder is the
    // same shape and height as the content it cross-fades into.
    const SKELETON_TILES = {
        systemMetrics: 4,
        serviceMetrics: 2,
        r2Usage: 3,
    };

    function buildSkeleton(gridClass, tileCount) {
        const skeleton = document.createElement('div');
        skeleton.className = `t-skel-skeleton is-pulsing ${gridClass}`;
        skeleton.setAttribute('aria-hidden', 'true');
        for (let i = 0; i < tileCount; i++) {
            const tile = document.createElement('div');
            tile.className = 'metric-skel__tile';
            tile.innerHTML =
                '<div class="metric-skel__bar metric-skel__bar--wide"></div>' +
                '<div class="metric-skel__bar metric-skel__bar--value"></div>' +
                '<div class="metric-skel__bar"></div>';
            skeleton.appendChild(tile);
        }
        return skeleton;
    }

    /** Wrap a metric grid in the two-layer skeleton stack. */
    function attachSkeleton(grid, tileCount = 4) {
        if (!grid || grid.closest('.t-skel')) return;

        const wrap = document.createElement('div');
        wrap.className = 't-skel metric-skel';
        wrap.setAttribute('data-state', 'loading');
        grid.parentNode.insertBefore(wrap, grid);

        // Both layers share one grid cell (see .metric-skel in
        // transitions.css) so the taller one defines the wrap's height.
        wrap.appendChild(buildSkeleton(grid.className.split(/\s+/).filter(Boolean).join(' '), tileCount));
        grid.classList.add('t-skel-content');
        wrap.appendChild(grid);

        // Grids that render nothing until their section un-hides (R2) keep
        // their placeholder text out of the way.
        grid.querySelector('.metric-grid__placeholder')?.remove();
    }

    /** Cross-fade a metric grid's skeleton out and its content in. */
    function revealSkeleton(grid) {
        grid?.closest('.t-skel')?.classList.add('is-revealed');
    }

    function setupSkeletons() {
        Object.entries(SKELETON_TILES).forEach(([id, tileCount]) => {
            attachSkeleton(document.getElementById(id), tileCount);
        });
    }

    // ── Number pop-in (02) ────────────────────────────────────────────
    // Metric tile values re-enter from below with a blur whenever they
    // change. Unchanged values are left alone so a steady reading doesn't
    // flicker on every poll.

    function setDigits(group, text) {
        const next = String(text);
        if (group.dataset.value === next) return;
        group.dataset.value = next;

        group.classList.remove('is-animating');
        group.replaceChildren();

        const chars = next.split('');
        chars.forEach((char, i) => {
            const span = document.createElement('span');
            span.className = 't-digit';
            span.textContent = char === ' ' ? ' ' : char;
            if (i === chars.length - 2) span.dataset.stagger = '1';
            else if (i === chars.length - 1) span.dataset.stagger = '2';
            group.appendChild(span);
        });

        void group.offsetHeight;  // force reflow so the animation replays
        group.classList.add('is-animating');
    }

    // ── Error state shake (12) ────────────────────────────────────────
    // "That didn't save" feedback on a control, with an auto-revert.

    function shakeError(input, wrap = input) {
        wrap.classList.add('is-error');
        input.classList.add('is-error');

        input.classList.remove('is-shaking');
        void input.offsetWidth;  // force reflow so the shake replays
        input.classList.add('is-shaking');

        const shakeMs = num('--shake-dur-a', 80) * 2 + num('--shake-dur-b', 60) * 2;
        setTimeout(() => input.classList.remove('is-shaking'), shakeMs + 20);

        if (wrap._revertTimer) clearTimeout(wrap._revertTimer);
        wrap._revertTimer = setTimeout(() => {
            wrap._revertTimer = null;
            wrap.classList.remove('is-error');
            input.classList.remove('is-error');
        }, shakeMs + num('--revert-hold', 3000));
    }

    // ── Input clear with dissolve (13) ────────────────────────────────
    // The sidebar service search. Per-frame JS is unavoidable: the
    // streak's rise/peak/fall envelope can't be a static @keyframes.

    /** Minimal cubic-bezier(x1,y1,x2,y2) sampler so JS easing matches CSS. */
    function bezier(spec) {
        const m = String(spec).match(/cubic-bezier\(([-\d.]+),([-\d.]+),([-\d.]+),([-\d.]+)\)/);
        if (!m) return (t) => t;
        const [x1, y1, x2, y2] = m.slice(1).map(parseFloat);
        const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
        const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
        return (t) => {
            if (t <= 0) return 0;
            if (t >= 1) return 1;
            let s = t;
            for (let i = 0; i < 8; i++) {
                const dx = ((ax * s + bx) * s + cx) * s - t;
                const d = (3 * ax * s + 2 * bx) * s + cx;
                if (Math.abs(dx) < 1e-6 || d === 0) break;
                s -= dx / d;
            }
            return ((ay * s + by) * s + cy) * s;
        };
    }

    /**
     * Rebuild the sidebar search field as a .t-clear wrap (mirror,
     * placeholder, glow, clear button) around the existing input.
     * @param {(value: string) => void} onClear - run once the value is gone,
     *   so the caller can re-filter immediately rather than after the tween.
     */
    function setupInputClear(input, onClear) {
        if (!input || input.closest('.t-clear')) return;

        const wrap = document.createElement('div');
        wrap.className = 't-clear';
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);

        const mirror = document.createElement('div');
        mirror.className = 't-clear-mirror';
        mirror.setAttribute('aria-hidden', 'true');

        const placeholder = document.createElement('div');
        placeholder.className = 't-clear-placeholder';
        placeholder.setAttribute('aria-hidden', 'true');
        placeholder.textContent = input.placeholder || '';

        const glow = document.createElement('div');
        glow.className = 't-clear-glow';
        glow.setAttribute('aria-hidden', 'true');

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 't-clear-btn';
        button.setAttribute('aria-label', 'Clear search');
        button.innerHTML = '<svg aria-hidden="true"><use href="#icon-close"></use></svg>';

        wrap.append(mirror, placeholder, glow, button);

        // The fake placeholder owns the empty state, so silence the native one.
        input.placeholder = '';
        input.setAttribute('aria-label', input.getAttribute('aria-label') || 'Search services');

        const measure = document.createElement('canvas').getContext('2d');
        let clearing = false;

        const sync = () => {
            const has = input.value.length > 0;
            wrap.classList.toggle('has-value', has);
            if (has) mirror.textContent = input.value.replace(/ /g, ' ');
        };

        // Dark surface: paint white gradients and let mix-blend-mode: screen
        // lighten. Multiply over black would vanish.
        function buildGlow(text) {
            measure.font = getComputedStyle(input).font;
            const width = wrap.clientWidth || 240;
            const padLeft = parseFloat(getComputedStyle(input).paddingLeft) || 12;
            const spread = num('--glow-spread', 1.5);
            const layers = [];
            let x = 0;
            text.split(/(\s+)/).forEach((segment) => {
                const segmentWidth = measure.measureText(segment).width;
                if (segment.trim()) {
                    const cx = padLeft + x + segmentWidth / 2;
                    const hw = Math.max(segmentWidth * 0.45, 8) * spread;
                    [[0, 0.8, 7, 0.22], [hw * 0.45, 0.55, 8, 0.18],
                        [-hw * 0.4, 0.65, 6, 0.16], [hw * 0.15, 0.9, 5, 0.14]]
                        .forEach(([dx, rwm, rh, alpha]) => {
                            const lx = (((cx + dx) / width) * 100).toFixed(2);
                            layers.push(
                                `radial-gradient(ellipse ${Math.max(hw * rwm, 2).toFixed(1)}px ${rh}px at ${lx}% 100%, rgba(255,255,255,${alpha}), transparent)`,
                            );
                        });
                }
                x += segmentWidth;
            });
            return layers.join(', ');
        }

        function clearWithAnimation() {
            if (clearing || !input.value) return;
            clearing = true;
            const keepFocus = document.activeElement === input;
            mirror.textContent = input.value.replace(/ /g, ' ');

            input.value = '';
            wrap.classList.remove('has-value');
            onClear?.('');

            if (prefersReducedMotion()) {
                mirror.textContent = '';
                clearing = false;
                if (keepFocus) input.focus({ preventScroll: true });
                return;
            }

            const total = num('--clear-dur', 1000);
            const outDur = num('--clear-out-dur', 400);
            const inDur = num('--clear-in-dur', 400);
            const outFly = num('--clear-out-fly', 12);
            const inFly = num('--clear-in-fly', 12);
            const blur = num('--clear-blur', 2);
            const delay = num('--glow-delay', 50);
            const peakAt = num('--glow-peak-at', 0.15);
            const glowOpacity = num('--glow-opacity', 0.85);
            const easeOut = bezier(token('--clear-out-ease', ''));
            const easeIn = bezier(token('--clear-in-ease', ''));

            wrap.classList.add('is-clearing');
            glow.style.background = buildGlow(mirror.textContent);
            glow.style.opacity = '0';
            placeholder.style.transform = `translateY(-${inFly}px)`;
            placeholder.style.opacity = '0.9';
            placeholder.style.filter = `blur(${blur}px)`;

            const start = performance.now();
            (function tick(now) {
                const elapsed = now - start;
                const eo = easeOut(Math.min(1, elapsed / outDur));
                mirror.style.transform = `translateY(${(eo * outFly).toFixed(1)}px)`;
                mirror.style.opacity = (1 - eo).toFixed(3);
                mirror.style.filter = `blur(${(eo * blur).toFixed(1)}px)`;

                const ei = easeIn(Math.min(1, elapsed / inDur));
                placeholder.style.transform = `translateY(${(-inFly + ei * inFly).toFixed(1)}px)`;
                placeholder.style.opacity = (0.9 + ei * 0.1).toFixed(3);
                placeholder.style.filter = `blur(${(blur - ei * blur).toFixed(1)}px)`;

                let g = 0;
                if (elapsed > delay) {
                    const gp = Math.min(1, (elapsed - delay) / Math.max(1, total - delay));
                    g = gp < peakAt ? gp / peakAt : 1 - (gp - peakAt) / (1 - peakAt);
                }
                glow.style.opacity = (g * glowOpacity).toFixed(3);

                if (elapsed < total) {
                    requestAnimationFrame(tick);
                    return;
                }
                wrap.classList.remove('is-clearing');
                [mirror, placeholder].forEach((el) => { el.style.cssText = ''; });
                mirror.textContent = '';
                glow.style.opacity = '0';
                glow.style.background = '';
                clearing = false;
                if (keepFocus) requestAnimationFrame(() => input.focus({ preventScroll: true }));
            })(performance.now());
        }

        const keep = (event) => { if (document.activeElement === input) event.preventDefault(); };
        button.addEventListener('pointerdown', keep);
        button.addEventListener('mousedown', keep);
        button.addEventListener('click', clearWithAnimation);
        input.addEventListener('input', sync);
        sync();
    }

    // ── Avatar group hover (11) ───────────────────────────────────────
    // The Sites pill list: hovering one pill lifts it and combs its
    // neighbours with a distance falloff, then springs back on leave.

    function setupAvatarGroup(group) {
        if (!group) return;
        const items = Array.from(group.querySelectorAll('.t-avatar'));
        if (!items.length) return;

        function setShifts(activeIndex, phase) {
            const lift = num('--avatar-lift', -4);
            const falloff = num('--avatar-falloff', 0.45);
            const scale = num('--avatar-scale', 1.05);
            // The timing function has to be written BEFORE the variable
            // writes — the browser uses whichever one is current when the
            // property changes, which is what gives us a clean lift and a
            // bouncy return without a second class.
            const timing = phase === 'out'
                ? token('--avatar-ease-out', 'cubic-bezier(0.34, 3.85, 0.64, 1)')
                : token('--avatar-ease-in', 'cubic-bezier(0.22, 1, 0.36, 1)');

            items.forEach((el, i) => {
                el.style.transitionTimingFunction = timing;
                if (activeIndex == null) {
                    el.style.setProperty('--shift', '0px');
                    el.style.setProperty('--scale-active', '1');
                    return;
                }
                const distance = Math.abs(i - activeIndex);
                el.style.setProperty('--shift', `${(lift * Math.pow(falloff, distance)).toFixed(3)}px`);
                el.style.setProperty('--scale-active', i === activeIndex ? String(scale) : '1');
            });
        }

        items.forEach((el, i) => el.addEventListener('mouseenter', () => setShifts(i, 'in')));
        group.addEventListener('mouseleave', () => setShifts(null, 'out'));
    }

    function setupWebsitePills() {
        const group = document.querySelector('.website-pills');
        if (!group) return;
        group.querySelectorAll('.website-pill').forEach((pill) => pill.classList.add('t-avatar'));
        setupAvatarGroup(group);
    }

    // ── Checkbox check (25) ───────────────────────────────────────────
    // Log filter toggles. The native input stays the source of truth (the
    // log module reads .checked); the drawn box mirrors its state.

    function setupCheckboxes() {
        document.querySelectorAll('.log-filter-checkbox input[type="checkbox"]').forEach((input) => {
            if (input.classList.contains('t-check-native')) return;
            input.classList.add('t-check-native');

            const box = document.createElement('span');
            box.className = 't-check';
            box.setAttribute('aria-hidden', 'true');
            box.innerHTML = '<svg viewBox="0 0 10.1668 10.1668"><path d="M1 5.52L3.92 9.17L9.17 1"/></svg>';
            input.insertAdjacentElement('afterend', box);

            // Calibrate stroke-dasharray to this path so it never
            // over- or under-draws.
            const path = box.querySelector('path');
            box.style.setProperty('--check-len', String(Math.ceil(path.getTotalLength())));

            const sync = () => box.setAttribute('aria-checked', String(input.checked));
            input.addEventListener('change', sync);
            sync();
        });
    }

    // ── Tooltip (17) ──────────────────────────────────────────────────
    // Pure CSS; the class is all the wiring the collapsed-sidebar
    // service-name bubbles need.

    function setupTooltips() {
        document.querySelectorAll('.service-tooltip').forEach((tip) => {
            tip.classList.add('t-tt');
            tip.setAttribute('role', 'tooltip');
        });
    }

    // ── Post-action toasts ────────────────────────────────────────────
    // Restart and the inspections check redirect back to the index; the
    // `done` query param is the only trace of what just happened.

    const DONE_MESSAGES = {
        restart: 'Restart triggered',
        check: 'Inspections check completed',
    };

    function showPostActionToast() {
        const params = new URLSearchParams(window.location.search);
        const done = params.get('done');
        if (!done) return;

        const message = DONE_MESSAGES[done];
        if (message) showToast(message, 'success');

        // Strip the flag so a reload doesn't replay the toast.
        params.delete('done');
        const query = params.toString();
        window.history.replaceState(
            {},
            '',
            window.location.pathname + (query ? `?${query}` : '') + window.location.hash,
        );
    }

    function init() {
        setupTooltips();
        setupTabs();
        setupSkeletons();
        setupCheckboxes();
        setupWebsitePills();
        showPostActionToast();
    }

    window.SMTransitions = {
        init,
        showToast,
        setAccordionOpen,
        syncPills,
        movePill,
        attachSkeleton,
        revealSkeleton,
        setDigits,
        shakeError,
        setupInputClear,
    };
})();
