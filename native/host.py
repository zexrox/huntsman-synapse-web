"""Forward Synapse Web feature reports to the real keyboard 1532:02a7 interface 3.

Does not build packets, does not rewrite replies, and does not set a poll rate.
"""
import datetime
import json
import os
from pathlib import Path
import struct
import sys
import threading
import time

LOG = Path(__file__).with_name('diagnose.log')
# GET commands already identified in the 0.1.4 package. Sizes are enforced.
# Commands outside these sets are still forwarded when the packet structure
# is valid: lighting, actuation and keymap writes are not listed here, and
# this host does not invent their bytes.
READ_COMMANDS = {(0x00, n) for n in (0x81, 0x82, 0x84, 0x86, 0x87)} | {
    (0x0f, 0x80), (0x0f, 0x82), (0x0f, 0x84), (0x0f, 0x90)
}
# Names and sizes copied from the 0.1.4 package. 05:8a is Get Max Profiles
# Supported, size 1. The 0.1.4 notes say profile init asks for it, then for
# profile count and the profile list. The 0.1.3 log blocked size=1 before
# the keyboard answered. This host still does not create that packet.
EXTRA_READS = {
    (0x05, 0x8a): (1, 'Get Max Profiles Supported'),
    (0x05, 0x80): (1, 'Get Number of Profiles'),
    (0x05, 0x81): (80, 'Get Profiles ID List'),
    (0x05, 0x88): (80, 'Get Profile Name With Offset'),
    (0x05, 0x8c): (4, 'Get Profile Color'),
    (0x06, 0x88): (6, 'Get Macro Data Size'),
    (0x06, 0x89): (80, 'Get Macro Memory Data'),
    (0x06, 0x8a): (6, 'Get Macro Memory Management'),
    (0x06, 0x8b): (80, 'Get Macro ID List With Offset'),
    (0x06, 0x8c): (80, 'Get Macro Name With Offset'),
    (0x02, 0x84): (80, 'Get Button ID List'),
    (0x02, 0x8c): (80, 'Get Single Button Assignment'),
    (0x02, 0x8d): (80, 'Get Single Key Assignment'),
    (0x02, 0x8e): (80, 'Get Button Assignment List'),
    (0x02, 0x8f): (80, 'Get Key Assignment List'),
    (0x02, 0x92): (80, 'Get Single Analog Key Assignment'),
    (0x02, 0x99): (80, 'Get Multi Analog Key Actuation Point'),
    (0x02, 0x9a): (80, 'Get Multi Analog Key Rapid Trigger'),
    (0x02, 0x9b): (80, 'Get Multi Mod Tap Key Assignment'),
    (0x02, 0x9e): (80, 'Get Multi Key Assignment'),
    (0x02, 0x9f): (2, 'Get Analog Key Adjustment Mode Ctrl'),
    (0x02, 0xb2): (1, 'Get Number of Keymaps'),
    (0x02, 0xb5): (80, 'Get Keymap Name With Offset'),
    (0x02, 0xb6): (2, 'Get Active Keymap'),
    (0x02, 0xb7): (1, 'Get OBM Keymap'),
    (0x06, 0x80): (2, 'Get Number of Macros'),
    (0x00, 0xda): (3, 'Get Game Mode Selection'),
    (0x06, 0x8e): (14, 'Get Macro Storage Info'),
    (0x02, 0xaf): (1, 'Get Analog V3 Key Adjustment Mode Ctrl'),
    (0x03, 0x80): (3, 'Get LED State'),
    (0x02, 0xac): (11, 'Get Single-key Snap Tap Ctrl'),
    (0x02, 0xa7): (15, 'Get Snap Tap Ctrl'),
    (0x02, 0xb1): (7, 'Get Dual Keypress Priority'),
    (0x05, 0x84): (1, 'Get Active Profile ID'),
    (0x00, 0xc0): (2, 'Get USB High Speed Polling Period'),
    (0x02, 0xa8): (2, 'Get Analog Key Bottom Dead-Zone Value'),
    (0x02, 0xa0): (2, 'Get Analog Key Continuous Rapid Trigger'),
    (0x02, 0xaa): (1, 'Get Analog Report Mode'),
    (0x00, 0xdb): (1, 'Get Tournament Mode'),
}

def format_filters(filters):
    if not isinstance(filters, list) or len(filters) > 32:
        raise ValueError('Invalid filters')
    parts = []
    for item in filters:
        if not isinstance(item, dict):
            raise ValueError('Invalid filter')
        fields = []
        for key in ('vendorId', 'productId', 'usagePage', 'usage'):
            value = item.get(key, None)
            if value is None:
                fields.append(f'{key}=-')
            elif type(value) is int and 0 <= value <= 0xFFFFFFFF:
                fields.append(f'{key}=0x{value:04x}')
            else:
                raise ValueError('Invalid filter field')
        parts.append(' '.join(fields))
    if not parts:
        return 'FILTERS count=0'
    return f'FILTERS count={len(parts)} ' + ' | '.join(parts)

def log(message):
    with LOG.open('a', encoding='utf-8') as f:
        f.write(f'{datetime.datetime.now().isoformat(timespec="seconds")} {message}\n')

def validate_packet(values):
    if not isinstance(values, list) or len(values) != 90:
        raise ValueError('Expected exactly 90 report bytes')
    if any(type(v) is not int or not 0 <= v <= 255 for v in values):
        raise ValueError('Invalid byte array')
    p = bytes(values)
    command = (p[6], p[7])
    if p[0] != 0 or p[2:5] != b'\0\0\0' or p[5] > 80 or p[89] != 0:
        raise ValueError(f'Unsupported packet header actual={p[5]}')
    checksum = 0
    for v in p[2:88]:
        checksum ^= v
    if p[88] != checksum:
        raise ValueError(f'Checksum mismatch actual={p[5]}')
    if command in EXTRA_READS and p[5] != EXTRA_READS[command][0]:
        documented, name = EXTRA_READS[command]
        # Synapse sends these reads at more than one data size. The packet
        # stays Synapse's; only header and checksum were checked above.
        if command in {(0x05, 0x88), (0x02, 0x9e), (0x02, 0x9a), (0x02, 0x99)}:
            log(f'SIZE {p[6]:02x}:{p[7]:02x} {name} documented={documented} actual={p[5]}')
        else:
            raise ValueError(f'Unexpected query size for {name}: documented={documented} actual={p[5]}')
    return p

def packet_checksum(packet):
    checksum = 0
    for value in packet[2:88]:
        checksum ^= value
    return checksum

def translate_request(packet):
    """Return a firmware packet when a capture shows a different working command.

    Snap Tap on the 0498 is the 4-byte kill-switch command from snaptap.pcapng:
    GET 02:a1 and SET 02:21, payload profile, enabled, key id, key id.
    Enabled is the byte that goes 0, then 1, then 0. The web command stays
    02:a7 (GET) or 02:27 (SET), size 15: profile, pair count, enabled, then
    key id, key id, mode.
    """
    command = (packet[6], packet[7])
    if command not in {(0x02, 0xa7), (0x02, 0x27)}:
        return None
    out = bytearray(90)
    out[1] = packet[1]
    out[5] = 4
    out[6] = 0x02
    out[7] = 0xa1 if command == (0x02, 0xa7) else 0x21
    if packet[5] >= 1:
        out[8] = packet[8]
    if command == (0x02, 0x27) and packet[5] >= 3:
        out[9] = packet[10]
        if packet[5] >= 5 and packet[9] >= 1:
            out[10] = packet[11]
            out[11] = packet[12]
    out[88] = packet_checksum(out)
    return bytes(out)

def translate_reply(web, firmware):
    """Put a kill-switch reply back into the Snap Tap packet the page sent."""
    out = bytearray(90)
    out[0] = firmware[0]
    out[1] = web[1]
    size = web[5] if 6 <= web[5] <= 80 else 15
    out[5] = size
    out[6] = web[6]
    out[7] = web[7]
    profile, enabled, key1, key2 = firmware[8], firmware[9], firmware[10], firmware[11]
    out[8] = profile
    out[9] = 1 if key1 or key2 else 0
    out[10] = enabled
    out[11] = key1
    out[12] = key2
    # The firmware command has no priority byte. 0 is LAST_INPUT in the page.
    out[13] = 0
    out[88] = packet_checksum(out)
    return bytes(out)

# Status byte 1 means the keyboard is still busy. Re-read the same feature
# report until the status changes or this bound passes. Never rewrite it.
BUSY_STATUS = 1
BUSY_POLL_SECONDS = 0.5
BUSY_POLL_INTERVAL = 0.02

class Bridge:
    def __init__(self, hid):
        self.hid = hid
        self.device = None
        self.cache = {}
        self.lock = threading.Lock()

    def read_feature_report(self, query):
        reply = bytes(self.device.get_feature_report(0, 91))
        raw = reply[1:] if len(reply) == 91 and reply[0] == 0 else reply
        if len(raw) != 90:
            raise IOError(f'Unexpected reply length: {len(reply)}')
        if raw[1] != query[1] or raw[6:8] != query[6:8]:
            raise IOError('Reply does not match query; close desktop Synapse')
        return raw

    def await_feature_reply(self, query):
        deadline = time.monotonic() + BUSY_POLL_SECONDS
        while True:
            time.sleep(BUSY_POLL_INTERVAL)
            raw = self.read_feature_report(query)
            if raw[0] != BUSY_STATUS or time.monotonic() >= deadline:
                return raw

    def devices(self):
        # Real keyboard only. Never enumerate or open 1532:02d0.
        return [d for d in self.hid.enumerate(0x1532, 0x02a7)
                if d.get('interface_number') == 3 or b'mi_03' in d['path'].lower()]

    def open(self):
        if self.device is None:
            candidates = self.devices()
            if len(candidates) != 1:
                raise RuntimeError(f'Expected exactly one interface 3, found: {len(candidates)}')
            device = self.hid.device()
            try:
                device.open_path(candidates[0]['path'])
            except Exception:
                device.close()
                raise
            self.device = device
            log('OPEN real=1532:02a7 interface=3 presented=1532:02d0')

    def handle(self, request):
        op = request.get('op')
        client = request.get('client', '')
        if not isinstance(client, str) or len(client) > 160:
            raise ValueError('Invalid client')
        if op == 'filters':
            log(format_filters(request.get('filters')))
            return True
        if op == 'note':
            text = request.get('text')
            if not isinstance(text, str) or not text or len(text) > 180 or '\n' in text or '\r' in text:
                raise ValueError('Invalid note')
            log(text)
            return True
        if op == 'status':
            count = len(self.devices())
            log(f'STATUS interface3_candidates={count}')
            return {'present': count == 1}
        if op == 'open':
            with self.lock:
                self.open()
            return True
        if op == 'close':
            with self.lock:
                self.cache.pop(client, None)
            return True
        if op == 'send':
            if request.get('reportId') != 0:
                raise ValueError('Only report ID 0 supported')
            p = validate_packet(request.get('data'))
            firmware = translate_request(p)
            sent = firmware if firmware is not None else p
            with self.lock:
                self.cache.pop(client, None)
                self.open()
                result = self.device.send_feature_report(bytes([0]) + sent)
                if result < 1:
                    raise IOError('HID query transfer failed')
                raw = self.await_feature_reply(sent)
                if firmware is not None:
                    raw = translate_reply(p, raw)
                if len(self.cache) > 64:
                    self.cache.clear()
                self.cache[client] = list(raw)
            status_name = {1: 'BUSY', 2: 'SUCCESS', 5: 'COMMAND_NOT_SUPPORTED'}.get(raw[0], 'OTHER')
            command = (p[6], p[7])
            known = command in READ_COMMANDS or command in EXTRA_READS
            name = EXTRA_READS.get(command, (None, ''))[1]
            translated = ''
            if firmware is not None:
                translated = f' translated={firmware[6]:02x}:{firmware[7]:02x} size={firmware[5]}'
            log(f'QUERY {p[6]:02x}:{p[7]:02x} size={p[5]} status={raw[0]:02x} {status_name} known={int(known)} {name}{translated}'.rstrip())
            return True
        if op == 'receive':
            with self.lock:
                if request.get('reportId') != 0 or client not in self.cache:
                    raise RuntimeError('No matching completed query')
                return list(self.cache[client])
        raise ValueError('Unsupported operation')

def read_exact(stream, size):
    data = b''
    while len(data) < size:
        part = stream.read(size - len(data))
        if not part:
            if not data:
                return None
            raise EOFError('Incomplete message')
        data += part
    return data

def main():
    if os.name == 'nt':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    import hid
    bridge = Bridge(hid)
    log('START synapse-web bridge 0.2.8')
    try:
        while True:
            header = read_exact(sys.stdin.buffer, 4)
            if header is None:
                break
            size = struct.unpack('<I', header)[0]
            if size > 16384:
                raise ValueError('Message too large')
            request = json.loads(read_exact(sys.stdin.buffer, size))
            try:
                response = {'id': request.get('id'), 'ok': True, 'result': bridge.handle(request)}
            except Exception as error:
                log(f'ERROR {type(error).__name__}: {error}')
                response = {'id': request.get('id'), 'ok': False, 'error': str(error)}
            encoded = json.dumps(response).encode('utf-8')
            sys.stdout.buffer.write(struct.pack('<I', len(encoded)) + encoded)
            sys.stdout.buffer.flush()
    finally:
        if bridge.device:
            bridge.device.close()

if __name__ == '__main__':
    main()
