(function(root) {
    'use strict';

    const RECONNECT_BASE_MS = 2000;
    const RECONNECT_MAX_MS = 30000;

    /** @typedef {'closed'|'connecting'|'open'|'waiting'} LogStreamState */

    function parseLogData(data) {
        try {
            return JSON.parse(data);
        } catch {
            return data;
        }
    }

    /**
     * One SSE connection to a log stream, reconnecting with capped exponential backoff.
     */
    class LogStreamConnection {
        /**
         * @param {string} url
         * @param {(line: string) => void} onLine
         * @param {{createSource?: (url: string) => EventSource, setTimer?: typeof setTimeout, clearTimer?: typeof clearTimeout}} [deps]
         */
        constructor(url, onLine, deps = {}) {
            this.url = url;
            this.onLine = onLine;
            this.createSource = deps.createSource ?? ((sourceUrl) => new EventSource(sourceUrl));
            this.setTimer = deps.setTimer ?? setTimeout;
            this.clearTimer = deps.clearTimer ?? clearTimeout;
            /** @type {LogStreamState} */
            this.state = 'closed';
            this.reconnectDelay = RECONNECT_BASE_MS;
            this.source = null;
            this.timer = null;
        }

        open() {
            this.release();
            const source = this.createSource(this.url);
            this.source = source;
            this.state = 'connecting';
            source.onopen = () => {
                if (source !== this.source) return;
                this.state = 'open';
                this.reconnectDelay = RECONNECT_BASE_MS;
            };
            source.onmessage = (evt) => {
                if (source !== this.source) return;
                this.onLine(parseLogData(evt.data));
            };
            source.onerror = () => {
                if (source !== this.source) return;
                this.onError();
            };
        }

        onError() {
            if (this.state !== 'connecting' && this.state !== 'open') return;
            this.source.close();
            this.source = null;
            this.state = 'waiting';
            this.timer = this.setTimer(() => {
                this.timer = null;
                this.reconnectDelay = Math.min(this.reconnectDelay * 2, RECONNECT_MAX_MS);
                this.open();
            }, this.reconnectDelay);
        }

        close() {
            this.release();
            this.state = 'closed';
        }

        release() {
            if (this.timer !== null) {
                this.clearTimer(this.timer);
                this.timer = null;
            }
            this.source?.close();
            this.source = null;
        }
    }

    const api = { LogStreamConnection, RECONNECT_BASE_MS, RECONNECT_MAX_MS };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    } else {
        root.ServiceMonitorLogStreamConnection = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
