const test = require('node:test');
const assert = require('node:assert/strict');

const { LogStreamConnection } = require('../../static/log-stream-connection.js');

class FakeEventSource {
    constructor(url) {
        this.url = url;
        this.closed = false;
    }

    close() {
        this.closed = true;
    }
}

function harness() {
    const sources = [];
    const timers = new Map();
    const lines = [];
    let nextTimerId = 1;
    const connection = new LogStreamConnection('/logs/stream?service=foo', (line) => lines.push(line), {
        createSource: (url) => {
            const source = new FakeEventSource(url);
            sources.push(source);
            return source;
        },
        setTimer: (callback, delay) => {
            const id = nextTimerId++;
            timers.set(id, { callback, delay });
            return id;
        },
        clearTimer: (id) => timers.delete(id),
    });
    const latest = () => sources[sources.length - 1];
    const pendingDelays = () => [...timers.values()].map((timer) => timer.delay);
    const fireTimer = () => {
        assert.equal(timers.size, 1);
        const [[id, timer]] = timers;
        timers.delete(id);
        timer.callback();
    };
    const failAndRetry = () => {
        latest().onerror();
        const [delay] = pendingDelays();
        fireTimer();
        return delay;
    };
    return { connection, sources, lines, latest, pendingDelays, fireTimer, failAndRetry };
}

test('open connects to the stream url and is connecting until the source opens', () => {
    const h = harness();
    assert.equal(h.connection.state, 'closed');
    h.connection.open();
    assert.equal(h.connection.state, 'connecting');
    assert.equal(h.latest().url, '/logs/stream?service=foo');
    h.latest().onopen();
    assert.equal(h.connection.state, 'open');
});

test('messages are parsed as JSON and fall back to the raw data', () => {
    const h = harness();
    h.connection.open();
    h.latest().onmessage({ data: '"hello"' });
    h.latest().onmessage({ data: 'not json' });
    assert.deepEqual(h.lines, ['hello', 'not json']);
});

test('errors close the source and back off 2s, 4s, 8s, 16s, then cap at 30s', () => {
    const h = harness();
    h.connection.open();
    const delays = [];
    for (let attempt = 0; attempt < 7; attempt++) {
        h.latest().onerror();
        assert.equal(h.connection.state, 'waiting');
        assert.equal(h.sources[h.sources.length - 1].closed, true);
        delays.push(h.pendingDelays()[0]);
        h.fireTimer();
        assert.equal(h.connection.state, 'connecting');
    }
    assert.deepEqual(delays, [2000, 4000, 8000, 16000, 30000, 30000, 30000]);
    assert.equal(h.sources.length, 8);
});

test('a successful open resets the backoff to the base delay', () => {
    const h = harness();
    h.connection.open();
    assert.deepEqual([h.failAndRetry(), h.failAndRetry(), h.failAndRetry()], [2000, 4000, 8000]);
    h.latest().onopen();
    assert.equal(h.failAndRetry(), 2000);
});

test('an error on an open connection backs off with the current delay', () => {
    const h = harness();
    h.connection.open();
    h.latest().onopen();
    h.latest().onerror();
    assert.deepEqual(h.pendingDelays(), [2000]);
});

test('close stops the source and cancels a pending reconnect', () => {
    const h = harness();
    h.connection.open();
    h.latest().onerror();
    h.connection.close();
    assert.equal(h.connection.state, 'closed');
    assert.deepEqual(h.pendingDelays(), []);
    assert.equal(h.sources.length, 1);

    h.connection.open();
    h.connection.close();
    assert.equal(h.latest().closed, true);
});

test('reopening closes the previous source and ignores its late events', () => {
    const h = harness();
    h.connection.open();
    const first = h.latest();
    h.connection.open();
    assert.equal(first.closed, true);
    assert.equal(h.sources.length, 2);
    first.onerror();
    first.onmessage({ data: '"stale"' });
    assert.equal(h.connection.state, 'connecting');
    assert.deepEqual(h.pendingDelays(), []);
    assert.deepEqual(h.lines, []);
});
