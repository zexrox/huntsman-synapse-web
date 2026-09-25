"""Forward Synapse Web feature reports to the real keyboard 1532:02a7 interface 3.

Get Game Mode Selection (00:da) is answered here. Set Game Mode Selection
(00:5a) and the Gaming Mode switch (Set LED State 03:00, LED id 8) update the
same Windows lock Fn+F10 uses. Every other command is still forwarded.
"""
import datetime
import json
import os
from pathlib import Path
import queue
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

LOG_LOCK = threading.Lock()
STDOUT_LOCK = threading.Lock()
# Page parser bn(): data[0] profile, data[1] bit 0 win, bit 2 alt+tab, bit 3 alt+f4.
# Those three are the keys this lock swallows. 0x0d is all three, 0 is none.
GAME_MODE_LOCK_BITS = 0x01 | 0x04 | 0x08
SUCCESS_STATUS = 2

def log(message):
    line = f'{datetime.datetime.now().isoformat(timespec="seconds")} {message}\n'
    with LOG_LOCK:
        with LOG.open('a', encoding='utf-8') as f:
            f.write(line)

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

def write_message(payload):
    encoded = json.dumps(payload).encode('utf-8')
    with STDOUT_LOCK:
        sys.stdout.buffer.write(struct.pack('<I', len(encoded)) + encoded)
        sys.stdout.buffer.flush()

def razer_reply(query, payload):
    """Status 02 reply in the layout sendCommand already parses."""
    packet = bytearray(90)
    packet[0] = SUCCESS_STATUS
    packet[1] = query[1]
    packet[5] = len(payload)
    packet[6] = query[6]
    packet[7] = query[7]
    packet[8:8 + len(payload)] = payload
    packet[88] = packet_checksum(packet)
    return bytes(packet)

def game_mode_selection_payload(profile, enabled, extension):
    payload = bytearray([profile & 0xFF, GAME_MODE_LOCK_BITS if enabled else 0])
    if extension is not None:
        payload.append(extension & 0xFF)
    return bytes(payload)

def selection_enabled(packet):
    """True when Set Game Mode Selection turns on win, alt+tab, or alt+f4."""
    if packet[5] < 2:
        return False
    return bool(packet[9] & GAME_MODE_LOCK_BITS)

def is_game_mode_led_set(packet):
    """Set LED State 03:00 whose LED id is GameModeLED (8). Other LEDs stay forwarded."""
    return packet[6] == 0x03 and packet[7] == 0x00 and packet[5] >= 3 and packet[9] == 0x08

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
# report until the status changes or this bound passes. A report whose
# transaction id or command bytes belong to the previous command is still
# sitting in the feature report while the next command is in flight; re-read
# that too, without writing again. Never rewrite the report.
BUSY_STATUS = 1
BUSY_POLL_SECONDS = 0.5
BUSY_POLL_INTERVAL = 0.02

# Interface 1, endpoint 0x82. The 48-byte interrupt payload starts with report
# id 0x04; the next byte is 0x01 while Fn is held and 0x00 otherwise.
# hid_read may omit that report id. F10 is VK_F10 and does not change this
# byte. Interface 0 stays closed.
FN_REPORT_ID = 0x04
VK_TAB = 0x09
VK_F4 = 0x73
VK_F10 = 0x79
VK_LWIN = 0x5B
VK_RWIN = 0x5C
LLKHF_ALTDOWN = 0x20
LLKHF_UP = 0x80

def fn_held_from_report(data):
    """Return whether Fn is held, or None when this buffer is not the Fn report.

    The capture payload starts with report id 0x04 and the next byte is Fn.
    hid_read may omit that id: then the buffer starts with 0x01 or 0x00 and
    contains no 0x04, and that first byte is Fn. A missing 0x04 is not itself
    Fn down, and an interior 0x04 in some other report is ignored.
    """
    if not data:
        return None
    raw = bytes(data)
    if raw[0] == FN_REPORT_ID:
        if len(raw) < 2:
            return None
        return raw[1] == 0x01
    if FN_REPORT_ID not in raw:
        if raw[0] == 0x01:
            return True
        if raw[0] == 0x00:
            return False
        return None
    if raw[0] not in (0x00, 0x01):
        return None
    marker = raw.find(FN_REPORT_ID)
    if marker + 1 >= len(raw):
        return None
    bit = raw[marker + 1]
    if bit == 0x01:
        return True
    if bit == 0x00:
        return False
    return None

def hid_path(item):
    path = item.get('path', b'')
    if isinstance(path, str):
        path = path.encode('ascii', 'ignore')
    return bytes(path)

def path_has_mi(path, marker):
    """True when path contains mi_XX as its own token. mi_01 does not match mi_010."""
    lowered = path.lower()
    token = marker.lower().encode('ascii')
    hexdigits = b'0123456789abcdef'
    start = 0
    while True:
        index = lowered.find(token, start)
        if index < 0:
            return False
        after = index + len(token)
        if after >= len(lowered) or lowered[after] not in hexdigits:
            return True
        start = index + 1

def mi_marker(path):
    """mi_ token from a device path, including a collection suffix before '#'."""
    if isinstance(path, str):
        raw = path.encode('ascii', 'ignore')
    else:
        raw = bytes(path)
    lowered = raw.lower()
    start = lowered.find(b'mi_')
    if start < 0:
        return 'mi_?'
    end = start
    while end < len(lowered) and lowered[end] not in b'#\\/?':
        end += 1
    return lowered[start:end].decode('ascii', 'ignore')

def describe_candidates(items):
    if not items:
        return 'no mi_01 path'
    parts = []
    for item in items:
        parts.append(
            f"interface_number={item.get('interface_number')} "
            f"usage_page={item.get('usage_page')} "
            f"usage={item.get('usage')}"
        )
    return '; '.join(parts)

def interface1_candidates(devices):
    """mi_01 collections only. interface_number alone is not a match.

    Windows hidapi can report interface_number 1 for every collection.
    Never open mi_00 or interface 0.
    """
    found = []
    for item in devices:
        path = hid_path(item)
        if item.get('interface_number') == 0 or path_has_mi(path, 'mi_00'):
            continue
        if not path_has_mi(path, 'mi_01'):
            continue
        found.append(item)
    return found

def game_mode_swallow(enabled, vk, flags, held):
    """Return whether this key is swallowed. No HID and no key logging.

    While the lock is on, that is Left Win, Right Win, Alt+Tab, and Alt+F4.
    The matching key-up is swallowed too, so a swallowed key cannot stick.
    """
    is_up = bool(flags & LLKHF_UP)
    if is_up:
        swallow = vk in held
        held.discard(vk)
        return swallow
    alt_down = bool(flags & LLKHF_ALTDOWN)
    swallow = bool(enabled) and (
        vk in (VK_LWIN, VK_RWIN) or (alt_down and vk in (VK_TAB, VK_F4)))
    if swallow:
        held.add(vk)
    else:
        held.discard(vk)
    return swallow

def game_mode_led_packet(transaction, enabled):
    """Set LED State 03:00, size 3. Payload 00 08 00 off, 00 08 01 on.

    Byte 08 is the game-mode LED from the gaming_modus captures.
    """
    packet = bytearray(90)
    packet[1] = transaction & 0xFF
    packet[5] = 3
    packet[6] = 0x03
    packet[7] = 0x00
    packet[8] = 0x00
    packet[9] = 0x08
    packet[10] = 0x01 if enabled else 0x00
    packet[88] = packet_checksum(packet)
    return bytes(packet)

class Bridge:
    def __init__(self, hid):
        self.hid = hid
        self.device = None
        self.cache = {}
        self.lock = threading.Lock()
        self.led_txn = 0
        self.game_mode = None

    def game_mode_enabled(self):
        seq, enabled = self.game_mode_snapshot()
        return enabled

    def game_mode_snapshot(self):
        holder = self.game_mode
        if holder is None:
            return 0, False
        with holder.state_lock:
            return holder.mode_seq, bool(holder.game_mode)

    def remember(self, client, raw):
        if len(self.cache) > 64:
            self.cache.clear()
        self.cache[client] = list(raw)

    def notify_game_mode(self, enabled, seq):
        write_message({'op': 'game-mode', 'enabled': bool(enabled), 'seq': int(seq)})

    def close_device(self):
        device = self.device
        self.device = None
        if device is not None:
            try:
                device.close()
            except Exception:
                pass

    def read_feature_report(self):
        try:
            reply = self.device.get_feature_report(0, 91)
        except Exception:
            self.close_device()
            raise
        reply = bytes(reply)
        raw = reply[1:] if len(reply) == 91 and reply[0] == 0 else reply
        if len(raw) != 90:
            raise IOError(f'Unexpected reply length: {len(reply)}')
        return raw

    def await_feature_reply(self, query):
        deadline = time.monotonic() + BUSY_POLL_SECONDS
        logged_mismatch = False
        while True:
            time.sleep(BUSY_POLL_INTERVAL)
            raw = self.read_feature_report()
            if raw[1] != query[1] or raw[6:8] != query[6:8]:
                if not logged_mismatch:
                    log(
                        f'WAIT reply={raw[6]:02x}:{raw[7]:02x} txn={raw[1]:02x} '
                        f'expected={query[6]:02x}:{query[7]:02x} txn={query[1]:02x}'
                    )
                    logged_mismatch = True
                if time.monotonic() >= deadline:
                    raise IOError('Reply does not match query')
                continue
            if raw[0] != BUSY_STATUS or time.monotonic() >= deadline:
                return raw

    def set_game_mode_led(self, enabled):
        """Write Set LED State 03:00 under the host lock. Does not touch the page cache."""
        with self.lock:
            self.led_txn = (self.led_txn % 255) + 1
            packet = game_mode_led_packet(self.led_txn, enabled)
            self.open()
            try:
                result = self.device.send_feature_report(bytes([0]) + packet)
            except Exception:
                self.close_device()
                raise
            if result < 1:
                self.close_device()
                raise IOError('HID query transfer failed')
            self.await_feature_reply(packet)

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
            self.notify_game_mode(*self.game_mode_snapshot())
            return True
        if op == 'close':
            with self.lock:
                self.cache.pop(client, None)
            return True
        if op == 'send':
            if request.get('reportId') != 0:
                raise ValueError('Only report ID 0 supported')
            p = validate_packet(request.get('data'))
            command = (p[6], p[7])
            if command == (0x00, 0xda):
                enabled = self.game_mode_enabled()
                raw = razer_reply(p, game_mode_selection_payload(p[8], enabled, 0))
                with self.lock:
                    self.remember(client, raw)
                log(f'00:da status=02 enabled={int(enabled)}')
                return True
            if command == (0x00, 0x5a) or is_game_mode_led_set(p):
                if command == (0x00, 0x5a):
                    enabled = selection_enabled(p)
                    extension = p[10] if p[5] >= 3 else None
                    raw = razer_reply(p, game_mode_selection_payload(p[8], enabled, extension))
                else:
                    enabled = p[10] != 0
                    raw = razer_reply(p, bytes([p[8], 0x08, 0x01 if enabled else 0x00]))
                with self.lock:
                    self.remember(client, raw)
                if self.game_mode is not None:
                    self.game_mode.set_from_page(enabled)
                return True
            firmware = translate_request(p)
            sent = firmware if firmware is not None else p
            with self.lock:
                self.cache.pop(client, None)
                self.open()
                try:
                    result = self.device.send_feature_report(bytes([0]) + sent)
                except Exception:
                    self.close_device()
                    raise
                if result < 1:
                    self.close_device()
                    raise IOError('HID query transfer failed')
                raw = self.await_feature_reply(sent)
                if firmware is not None:
                    raw = translate_reply(p, raw)
                self.remember(client, raw)
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

class GameMode:
    """Fn+F10 toggles a Windows-side lock. Starts off. The hook does not touch HID."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.fn_down = False
        self.game_mode = False
        self.mode_seq = 0
        self.state_lock = threading.Lock()
        self.f10_down = False
        self.held = set()
        self.queue = queue.Queue()
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.reader = None
        self.worker = None
        self.hook_thread = None
        self._reader_device = None
        self._tracked = []
        self._tracked_lock = threading.Lock()
        self._thread_id = None
        self._hook = None
        self._proc = None

    def start(self):
        self.worker = threading.Thread(target=self._led_loop, name='game-mode-led', daemon=True)
        self.worker.start()
        self.reader = threading.Thread(target=self._read_fn, name='game-mode-fn', daemon=True)
        self.reader.start()
        if os.name == 'nt':
            self.hook_thread = threading.Thread(target=self._hook_loop, name='game-mode-hook', daemon=True)
            self.hook_thread.start()

    def _track(self, device):
        with self._tracked_lock:
            self._tracked.append(device)

    def _drop_tracked(self, device):
        with self._tracked_lock:
            try:
                self._tracked.remove(device)
            except ValueError:
                pass
        try:
            device.close()
        except Exception:
            pass

    def close(self):
        self.stop.set()
        with self._tracked_lock:
            devices = list(self._tracked)
        for device in devices:
            try:
                device.close()
            except Exception:
                pass
        if self.hook_thread is not None:
            self.ready.wait(timeout=2)
            if self._thread_id and os.name == 'nt':
                import ctypes
                from ctypes import wintypes
                user32 = ctypes.WinDLL('user32', use_last_error=True)
                user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
                user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
            self.hook_thread.join(timeout=2)
        if self.reader is not None:
            self.reader.join(timeout=2)
        self.queue.put(None)
        if self.worker is not None:
            self.worker.join(timeout=2)

    def on_key(self, vk, flags):
        """Return True to swallow. Queue a toggle only; do not touch HID."""
        is_up = bool(flags & LLKHF_UP)
        if vk == VK_F10:
            if is_up:
                self.f10_down = False
            elif not self.f10_down:
                self.f10_down = True
                if self.fn_down:
                    with self.state_lock:
                        self.game_mode = not self.game_mode
                        self.mode_seq += 1
                        self.queue.put(('fn', self.game_mode, self.mode_seq))
            return False
        with self.state_lock:
            enabled = self.game_mode
        return game_mode_swallow(enabled, vk, flags, self.held)

    def set_from_page(self, enabled):
        """Apply a Synapse set to the same lock Fn+F10 toggles."""
        enabled = bool(enabled)
        with self.state_lock:
            self.game_mode = enabled
            self.mode_seq += 1
            self.queue.put(('set', enabled, self.mode_seq))

    def _led_loop(self):
        while True:
            item = self.queue.get()
            if item is None:
                return
            source, enabled, seq = item
            if source == 'fn':
                log('FN+F10')
                log('GAME MODE on' if enabled else 'GAME MODE off')
            log('SWITCH game mode on' if enabled else 'SWITCH game mode off')
            try:
                self.bridge.set_game_mode_led(enabled)
            except Exception as error:
                log(f'ERROR {type(error).__name__}: {error}')
            self.bridge.notify_game_mode(enabled, seq)

    def _read_fn(self):
        device = None
        try:
            selected = self._acquire_fn_reader()
        except Exception as error:
            if not self.stop.is_set():
                log(f'ERROR {type(error).__name__}: interface 1 not opened: {error}')
            return
        if selected is None:
            return
        device, marker = selected
        self._reader_device = device
        log(f'FN watch ready {marker}')
        try:
            while not self.stop.is_set():
                try:
                    data = device.read(64, 200)
                except Exception:
                    if self.stop.is_set():
                        break
                    raise
                if not data:
                    continue
                held = fn_held_from_report(data)
                if held is not None:
                    self.fn_down = held
        except Exception as error:
            if not self.stop.is_set():
                log(f'ERROR {type(error).__name__}: {error}')
        finally:
            self._reader_device = None
            if device is not None:
                self._drop_tracked(device)

    def _acquire_fn_reader(self):
        """Open every mi_01 collection and keep the one that delivers Fn.

        One collection is enough. Several stay open only until a buffer is
        report 0x04 or starts with the Fn bit; the others are closed.
        """
        enumerated = self.bridge.hid.enumerate(0x1532, 0x02a7)
        candidates = interface1_candidates(enumerated)
        if not candidates:
            raise RuntimeError(describe_candidates(candidates))
        opened = []
        for item in candidates:
            if self.stop.is_set():
                return None
            device = self.bridge.hid.device()
            try:
                device.open_path(item['path'])
            except Exception:
                try:
                    device.close()
                except Exception:
                    pass
                continue
            self._track(device)
            opened.append((device, item))
        if not opened:
            raise RuntimeError(describe_candidates(candidates))
        while not self.stop.is_set():
            if len(opened) == 1:
                device, item = opened[0]
                return device, mi_marker(item['path'])
            still = []
            for device, item in opened:
                if self.stop.is_set():
                    return None
                try:
                    data = device.read(64, 200)
                except Exception:
                    if self.stop.is_set():
                        return None
                    self._drop_tracked(device)
                    continue
                if not data:
                    still.append((device, item))
                    continue
                if fn_held_from_report(data) is not None:
                    self.fn_down = fn_held_from_report(data)
                    for other, _item in opened:
                        if other is not device:
                            self._drop_tracked(other)
                    return device, mi_marker(item['path'])
                self._drop_tracked(device)
            opened = still
            if not opened:
                raise RuntimeError(describe_candidates(candidates))
        return None

    def _hook_loop(self):
        user32 = None
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            LRESULT = ctypes.c_ssize_t

            class KBDLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [
                    ('vkCode', ctypes.c_uint32),
                    ('scanCode', ctypes.c_uint32),
                    ('flags', ctypes.c_uint32),
                    ('time', ctypes.c_uint32),
                    ('dwExtraInfo', ctypes.c_size_t),
                ]

            HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
            user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
            user32.CallNextHookEx.restype = LRESULT
            user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
            user32.SetWindowsHookExW.restype = wintypes.HHOOK
            user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
            user32.UnhookWindowsHookEx.restype = wintypes.BOOL
            user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            user32.GetMessageW.restype = ctypes.c_int
            user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
            user32.PeekMessageW.restype = wintypes.BOOL
            user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            kernel32.GetCurrentThreadId.restype = wintypes.DWORD

            def callback(nCode, wParam, lParam):
                try:
                    if nCode == 0:
                        info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                        if self.on_key(info.vkCode, info.flags):
                            return 1
                except Exception:
                    pass
                return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

            self._proc = HOOKPROC(callback)
            msg = wintypes.MSG()
            # Own this thread's message queue before the hook exists. The
            # native-messaging thread stays blocked in ReadFile and must not
            # be the thread that installs WH_KEYBOARD_LL.
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            self._thread_id = kernel32.GetCurrentThreadId()
            kernel32.Sleep.argtypes = [wintypes.DWORD]
            self._hook = user32.SetWindowsHookExW(13, self._proc, None, 0)
            if not self._hook:
                log(f'ERROR OSError: keyboard hook not installed ({ctypes.get_last_error()})')
                return
            self.ready.set()
            while not self.stop.is_set():
                rc = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if rc == 0 or msg.message == 0x0012:
                    break
                if rc < 0:
                    # A failed GetMessage must not unhook. Peek keeps F10
                    # arriving on this thread.
                    if not user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                        kernel32.Sleep(10)
                        continue
                    if msg.message == 0x0012:
                        break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as error:
            log(f'ERROR {type(error).__name__}: keyboard hook not installed: {error}')
        finally:
            self.ready.set()
            hook = self._hook
            self._hook = None
            if hook and user32 is not None:
                try:
                    user32.UnhookWindowsHookEx(hook)
                except Exception:
                    pass

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
    log('START synapse-web bridge 0.2.9')
    game_mode = GameMode(bridge)
    bridge.game_mode = game_mode
    try:
        game_mode.start()
    except Exception as error:
        log(f'ERROR {type(error).__name__}: keyboard hook not installed: {error}')
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
            write_message(response)
    finally:
        game_mode.close()
        if bridge.device:
            bridge.device.close()

if __name__ == '__main__':
    main()
