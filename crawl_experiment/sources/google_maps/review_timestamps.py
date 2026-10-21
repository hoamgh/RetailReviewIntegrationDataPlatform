"""Same-ID network timestamp enrichment; DOM remains the review payload source."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import logging
import queue
import threading
from urllib.parse import urlsplit
from urllib.request import urlopen

logger = logging.getLogger(__name__)
SOURCE = "google_maps_network"


@dataclass(frozen=True)
class ReviewTimestamps:
    published_at: datetime | None = None
    last_modified_at: datetime | None = None
    timestamp_source: str | None = SOURCE
    timestamp_status: str = "MISSING"


def timestamp_pair(a, b, *, now=None):
    """Integer microseconds only; use integer arithmetic to retain precision."""
    now = now or datetime.now(timezone.utc)
    values = []
    malformed = False
    for raw in (a, b):
        if raw is None:
            values.append(None)
            continue
        try:
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise ValueError("Not integer microseconds")
            value = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=raw)
            if not datetime(2000, 1, 1, tzinfo=timezone.utc) <= value <= now + timedelta(minutes=5):
                raise ValueError("Timestamp outside plausible range")
            values.append(value)
        except (ValueError, OverflowError):
            malformed = True
            values.append(None)
    if malformed:
        return ReviewTimestamps(timestamp_status="PARSE_FAILED")
    if None in values:
        # Do not promote partial pairs to trusted publication/edit fields.
        return ReviewTimestamps(timestamp_status="MISSING")
    if values[0] > values[1]:
        return ReviewTimestamps(timestamp_status="AMBIGUOUS")
    return ReviewTimestamps(*values, timestamp_status="VERIFIED")


def at(value, *indices):
    for index in indices:
        if not isinstance(value, list) or index >= len(value):
            return None
        value = value[index]
    return value


def decode_timestamps(body, *, now=None):
    """Known wrb.fr/qv9Egd envelope -> payload[2] -> [core]. No recursive guessing."""
    offset = 0
    decoder = json.JSONDecoder()
    body = body.lstrip()
    if body.startswith(")]}'"):
        body = body[4:]
    result = {}
    while offset < len(body):
        while offset < len(body) and body[offset].isspace():
            offset += 1
        if offset >= len(body):
            break
        end = body.find('\n', offset, offset + 32)
        if end >= 0 and body[offset:end].strip().isdigit():
            offset = end + 1
            continue
        frame, offset = decoder.raw_decode(body, offset)
        if not isinstance(frame, list):
            continue
        for rpc in frame:
            if at(rpc, 0) != 'wrb.fr' or at(rpc, 1) != 'qv9Egd':
                continue
            payload = json.loads(at(rpc, 2))
            rows = at(payload, 2)
            if not isinstance(rows, list):
                continue
            for row in rows[:200]:
                core = at(row, 0)
                identity = at(core, 0)
                metadata = at(core, 1)
                if isinstance(identity, str) and identity.startswith(('Ch', 'Ci')) and isinstance(metadata, list):
                    result[identity] = timestamp_pair(at(metadata, 2), at(metadata, 3), now=now)
    return result


def enrich_review(review, timestamps):
    return replace(review, published_at=timestamps.published_at,
        last_modified_at=timestamps.last_modified_at, timestamp_source=timestamps.timestamp_source,
        timestamp_status=timestamps.timestamp_status)


class NetworkTimestampCollector:
    """Bounded page-CDP worker, enabled before navigation. No raw body persistence."""
    def __init__(self, driver):
        import websocket
        self.records = {}
        self.failed = False
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.pending = {}
        self.sequence = 0
        self.events = queue.Queue(maxsize=1000)
        self.body_count = 0
        self.total_bytes = 0
        address = driver.capabilities['goog:chromeOptions']['debuggerAddress']
        # Only the browser's local DevTools address is accessed here.
        host = urlsplit('http://' + address).hostname
        if host not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('DevTools address must be local')
        with urlopen('http://' + address + '/json/list', timeout=3) as response:
            targets = json.load(response)
        target_id = driver.current_window_handle.removeprefix('CDwindow-')
        target = next(t for t in targets if t.get('id') == target_id and t.get('type') == 'page')
        self.socket = websocket.create_connection(target['webSocketDebuggerUrl'], timeout=1, suppress_origin=True)
        self.reader = threading.Thread(target=self._read, daemon=True, name='timestamp-cdp-reader')
        self.worker = threading.Thread(target=self._work, daemon=True, name='timestamp-cdp-worker')
        self.reader.start()
        self.worker.start()
        try:
            self.command('Network.enable', {})
            self.command('Fetch.enable', {'patterns': [
                {'urlPattern': 'https://www.google.com/maps/*batchexecute*',
                 'requestStage': 'Response', 'resourceType': kind} for kind in ('XHR', 'Fetch')]})
        except Exception:
            self.close()
            raise

    def command(self, method, params):
        waiter = queue.Queue(maxsize=1)
        with self.lock:
            self.sequence += 1
            identity = self.sequence
            self.pending[identity] = waiter
            self.socket.send(json.dumps(dict(id=identity, method=method, params=params)))
        try:
            response = waiter.get(timeout=3)
            if 'error' in response:
                raise RuntimeError('Timestamp CDP command failed')
            return response.get('result', {})
        finally:
            with self.lock:
                self.pending.pop(identity, None)

    def _read(self):
        import websocket
        while not self.closed.is_set():
            try:
                message = json.loads(self.socket.recv())
                if 'id' in message:
                    with self.lock:
                        waiter = self.pending.get(message['id'])
                    if waiter:
                        waiter.put_nowait(message)
                elif message.get('method') == 'Fetch.requestPaused':
                    self.events.put(message['params'], timeout=1)
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                self.failed = True
                # Disable interception on transport/queue failure, never leave UI paused.
                try:
                    self.socket.send(json.dumps(dict(id=0, method='Fetch.disable', params={})))
                except Exception:
                    pass
                return

    def _work(self):
        while not self.closed.is_set():
            try:
                event = self.events.get(timeout=.1)
            except queue.Empty:
                continue
            if isinstance(event, threading.Event):
                event.set()
                continue
            body = None
            try:
                if self.body_count < 100 and self.total_bytes < 20_000_000:
                    self.body_count += 1
                    payload = self.command('Fetch.getResponseBody', {'requestId': event['requestId']})
                    body = payload.get('body', '')
                    if payload.get('base64Encoded'):
                        import base64
                        body = base64.b64decode(body).decode('utf-8')
                    self.total_bytes += len(body.encode('utf-8'))
                    if len(body) > 2_000_000:
                        body = None
            except Exception:
                self.failed = True
            finally:
                try:
                    self.command('Fetch.continueRequest', {'requestId': event['requestId']})
                except Exception:
                    self.failed = True
                    try:
                        self.command('Fetch.disable', {})
                    except Exception:
                        pass
            if body is not None:
                try:
                    decoded = decode_timestamps(body)
                    with self.lock:
                        for identity, value in decoded.items():
                            previous = self.records.get(identity)
                            # Older captured UI generations must not overwrite a newer edit.
                            if previous and previous.timestamp_status == 'VERIFIED' and (
                                value.timestamp_status != 'VERIFIED' or value.last_modified_at < previous.last_modified_at
                            ):
                                continue
                            if identity in self.records or len(self.records) < 2000:
                                self.records[identity] = value
                except Exception:
                    self.failed = True

    def drain(self):
        barrier = threading.Event()
        try:
            self.events.put(barrier, timeout=.1)
            if not barrier.wait(1):
                self.failed = True
        except queue.Full:
            self.failed = True

    def enrich(self, review):
        with self.lock:
            value = self.records.get(review.source_review_id)
        return enrich_review(review, value or ReviewTimestamps(
            timestamp_status='PARSE_FAILED' if self.failed else 'MISSING'))

    def close(self):
        if self.closed.is_set():
            return
        try:
            self.command('Fetch.disable', {})
        except Exception:
            self.failed = True
        self.closed.set()
        self.socket.close()
        self.reader.join(timeout=2)
        self.worker.join(timeout=4)


def start_timestamp_collector(driver):
    try:
        if not isinstance(getattr(driver, 'capabilities', None), dict):
            return None
        return NetworkTimestampCollector(driver)
    except Exception as exc:
        logger.warning('Network timestamps unavailable: %s', type(exc).__name__)
        return None
