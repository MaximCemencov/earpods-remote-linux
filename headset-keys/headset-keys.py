#!/usr/bin/env python3
"""Headset remote buttons on a Conexant CX11880 laptop jack (tested: ThinkPad X1 Extreme Gen 2, 17aa:229f),
plus a bridge for the Apple USB-C to 3.5 mm adapter (05ac:110a).

The Linux kernel has no headset-button support for this codec (Realtek codecs have it, Conexant does not), but the
hardware does see the centre / "hook" button: while the mic line is shorted (EarPods centre button, the answer button of
most headsets) bit 5 of vendor register 0xa of widget 0x1c ("verb 0xca0") changes 0x81 -> 0xa1. This service reads that
register through the ALSA hwdep interface (needs root) and turns presses into media keys via a uinput keyboard:

    1 click -> Play/Pause        2 clicks -> next track        3 clicks -> previous track

Vol+ / Vol- are invisible to the codec (they do not short the line, they only nudge it). They are detected from the mic
signal by the (patched) applemote user service, which writes its keys into the same virtual keyboard.

The register is polled only while a headset with a mic is plugged in (SW_MICROPHONE_INSERT); otherwise the service
sleeps in select() and costs nothing.

Apple USB-C to 3.5 mm adapter: its buttons arrive as ordinary HID keys, but the device has no autorepeat and nobody
decodes double/triple clicks of the centre button. The service grabs its input device (EVIOCGRAB) and passes the
buttons through the same rules: Play/Pause with click counting, Vol+/Vol- with iPhone-like hold-to-repeat (after
0.45 s, ~9 steps/s while held; capped at 4 s in case a release is lost).
"""
import fcntl
import glob
import os
import re
import select
import signal
import struct
import sys
import time

DEVICE_NAME = "Headset remote keys"
CODEC_IDS = ("0x14f11f86",)           # CX11880. SN6140 (0x14f11f87) is probably identical but untested
NID_VENDOR, VERB_STATUS = 0x1C, 0xCA0  # status register: bit 5 = hook button, bit 7 = "headset type detection done"
BTN_MASK = 0x20
POLL = 0.015                           # register poll period, s
SETTLE_AFTER_PLUG = 1.5                # contacts slide while the plug goes in: do not treat that as a press
LONG_PRESS = 1.0                       # longer is not a click (Siri on an iPhone): ignored
MULTI_GAP = 0.35                       # pause after a release that ends a click series

KEY_NEXT, KEY_PLAYPAUSE, KEY_PREV, KEY_VOLDOWN, KEY_VOLUP = 163, 164, 165, 114, 115
ACTIONS = {1: KEY_PLAYPAUSE, 2: KEY_NEXT}   # 3 or more -> KEY_PREV
EV_SYN, EV_KEY, EV_SW, SW_MIC = 0, 1, 5, 4


def log(msg):
    print(msg, flush=True)


class ClickDecoder:
    """State machine: stream of (pressed?, time) -> list of (time, key code). Knows nothing about hardware."""

    def __init__(self):
        self.down = False
        self.t_down = 0.0
        self.clicks = 0
        self.t_up = 0.0
        self.long = False
        self._raw = None
        self._raw_since = 0.0

    def reset(self):
        self.down, self.clicks, self.long, self._raw = False, 0, False, None

    def feed(self, pressed, now):
        out = []
        # debounce: accept a state once it has been stable for >= 20 ms
        if pressed != self._raw:
            self._raw, self._raw_since = pressed, now
        stable = pressed if now - self._raw_since >= 0.020 else self.down
        if stable and not self.down:
            self.down, self.t_down, self.long = True, now, False
        elif not stable and self.down:
            self.down = False
            if not self.long:
                self.clicks += 1
                self.t_up = now
        if self.down and not self.long and now - self.t_down > LONG_PRESS:
            self.long, self.clicks = True, 0           # a long hold cancels the series
        if not self.down and self.clicks and now - self.t_up >= MULTI_GAP:
            out.append((now, ACTIONS.get(self.clicks, KEY_PREV)))
            self.clicks = 0
        return out


HOLD_DELAY, HOLD_PERIOD, HOLD_MAX = 0.45, 0.11, 4.0


class Hold:
    """Vol+/Vol- hold: HOLD_DELAY after the press, repeat every HOLD_PERIOD until released (or HOLD_MAX)."""

    def __init__(self):
        self.key = None
        self.t_next = self.t_end = 0.0

    def start(self, key, now):
        self.key, self.t_next, self.t_end = key, now + HOLD_DELAY, now + HOLD_MAX

    def stop(self, key=None):
        if key is None or key == self.key:
            self.key = None

    def due(self, now):
        """The key to repeat right now, else None."""
        if self.key is None:
            return None
        if now >= self.t_end:
            self.key = None
            return None
        if now >= self.t_next:
            self.t_next = now + HOLD_PERIOD
            return self.key
        return None


class DongleBridge:
    """Adapter buttons: volume with hold-to-repeat, centre with click counting. uin: any object with tap(code)."""

    def __init__(self, uin):
        self.uin, self.hold, self.dec = uin, Hold(), ClickDecoder()
        self.down, self.active_until = False, 0.0

    def on_key(self, code, val, now):
        if code in (KEY_VOLUP, KEY_VOLDOWN):
            if val == 1:
                self.uin.tap(code)
                self.hold.start(code, now)
            elif val == 0:
                self.hold.stop(code)
        elif code == KEY_PLAYPAUSE and val in (0, 1):
            self.down = bool(val)
            self.active_until = now + 1.0

    def tick(self, now):
        key = self.hold.due(now)
        if key:
            self.uin.tap(key)
        for _, k in self.dec.feed(self.down, now):
            log(f"adapter: centre button -> {NAMES.get(k, k)}")
            self.uin.tap(k)

    def busy(self, now):
        return self.hold.key is not None or self.down or self.dec.clicks or self.dec.down or now < self.active_until

    def reset(self):
        self.hold.stop()
        self.down = False
        self.dec.reset()


# ---------------------------------------------------------------- hardware

def find_codec_node():
    for path in sorted(glob.glob("/proc/asound/card*/codec#*")):
        try:
            text = open(path).read()
        except OSError:
            continue
        if any(f"Vendor Id: {cid}" in text for cid in CODEC_IDS):
            card = path.split("/")[3][4:]
            dev = path.rsplit("#", 1)[1]
            return f"/dev/snd/hwC{card}D{dev}"
    return None


class Codec:
    IOCTL_VERB = 0xC0084811            # HDA_IOCTL_VERB_WRITE (_IOWR('H', 0x11, 8 bytes))

    def __init__(self, node):
        self.fd = os.open(node, os.O_RDWR)

    def read(self, nid, verb, parm=0):
        buf = bytearray(struct.pack("II", (nid << 24) | (verb << 8) | parm, 0))
        fcntl.ioctl(self.fd, self.IOCTL_VERB, buf, True)
        return struct.unpack("II", bytes(buf))[1]

    def close(self):
        os.close(self.fd)


def find_mic_switch():
    """The ALSA jack input device "... Mic" (SW_MICROPHONE_INSERT switch)."""
    for d in sorted(glob.glob("/sys/class/input/input*")):
        try:
            name = open(d + "/name").read().strip()
        except OSError:
            continue
        if name.endswith(" Mic") and open(d + "/phys").read().strip() == "ALSA":
            for e in glob.glob(d + "/event*"):
                return "/dev/input/" + os.path.basename(e)
    return None


def find_dongle_node():
    """evdev node of the Apple USB-C to 3.5 mm adapter (05ac:110a) button interface (kbd handler)."""
    try:
        blocks = open("/proc/bus/input/devices").read().split("\n\n")
    except OSError:
        return None
    for b in blocks:
        if "Vendor=05ac" in b and "Product=110a" in b and "kbd" in b:
            m = re.search(r"event(\d+)", b)
            if m:
                return f"/dev/input/event{m.group(1)}"
    return None


def mic_inserted(fd):
    buf = bytearray(8)
    fcntl.ioctl(fd, (2 << 30) | (len(buf) << 16) | (ord("E") << 8) | 0x1B, buf, True)   # EVIOCGSW
    return bool(buf[SW_MIC // 8] >> (SW_MIC % 8) & 1)


class UInput:
    def __init__(self):
        def iow(nr, size):
            return (1 << 30) | (size << 16) | (ord("U") << 8) | nr
        fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(fd, iow(100, 4), EV_KEY)                         # UI_SET_EVBIT
        for code in (KEY_NEXT, KEY_PLAYPAUSE, KEY_PREV, KEY_VOLDOWN, KEY_VOLUP):
            fcntl.ioctl(fd, iow(101, 4), code)                       # UI_SET_KEYBIT
        setup = struct.pack("HHHH80sI", 0x06, 0x1209, 0x0001, 1, DEVICE_NAME.encode(), 0)
        fcntl.ioctl(fd, iow(3, len(setup)), setup)                   # UI_DEV_SETUP
        fcntl.ioctl(fd, (ord("U") << 8) | 1)                         # UI_DEV_CREATE
        self.fd = fd

    def _emit(self, typ, code, val):
        t = time.time()
        os.write(self.fd, struct.pack("qqHHi", int(t), int(t % 1 * 1e6), typ, code, val))

    def tap(self, code):
        for val in (1, 0):
            self._emit(EV_KEY, code, val)
            self._emit(EV_SYN, 0, 0)
            time.sleep(0.01)

    def close(self):
        try:
            fcntl.ioctl(self.fd, (ord("U") << 8) | 2)                # UI_DEV_DESTROY
        finally:
            os.close(self.fd)


NAMES = {KEY_PLAYPAUSE: "Play/Pause", KEY_NEXT: "next track", KEY_PREV: "previous track"}
EVIOCGRAB = 0x40044590


def main():
    uin = UInput()
    log(f"virtual keyboard '{DEVICE_NAME}' created")
    stop = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.append(1))
    dec = ClickDecoder()                 # centre button on the laptop jack
    bridge = DongleBridge(uin)           # Apple adapter buttons
    codec = sw = dg = None
    plugged, t_plug = False, 0.0
    next_hw_scan = next_dg_scan = 0.0
    try:
        while not stop:
            now = time.monotonic()
            # --- laptop jack: while the hardware is missing, look for it every 2 s
            if (codec is None or sw is None) and now >= next_hw_scan:
                next_hw_scan = now + 2.0
                if codec is None:
                    node = find_codec_node()
                    try:
                        codec = Codec(node) if node else None
                    except OSError as e:
                        log(f"cannot open {node}: {e}")
                    if codec:
                        log(f"codec: {node}")
                if sw is None:
                    node = find_mic_switch()
                    if node:
                        sw = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
                        plugged = mic_inserted(sw)
                        t_plug = time.monotonic() if plugged else 0.0
                        log(f"mic switch: {node}, headset {'plugged in' if plugged else 'not plugged in'}")
            # --- Apple adapter: grab its button device so its keys go through our rules
            if dg is None and now >= next_dg_scan:
                next_dg_scan = now + 1.5
                node = find_dongle_node()
                if node:
                    try:
                        dg = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
                        fcntl.ioctl(dg, EVIOCGRAB, 1)
                        bridge.reset()
                        log(f"Apple adapter: {node} grabbed, its buttons go through the service")
                    except OSError as e:
                        log(f"adapter {node}: cannot grab: {e}")
                        if dg is not None:
                            os.close(dg)
                        dg = None
            hw_polling = plugged and codec is not None
            timeout = POLL if (hw_polling or (dg is not None and bridge.busy(now))) else 1.0
            fds = [fd for fd in (sw, dg) if fd is not None]
            r, _, _ = select.select(fds, [], [], timeout)
            now = time.monotonic()
            for fd in r:
                try:
                    data = os.read(fd, 4096)
                except BlockingIOError:
                    continue
                except OSError:
                    if fd == dg:
                        os.close(dg); dg = None
                        bridge.reset()
                        log("Apple adapter unplugged")
                    else:
                        os.close(sw); sw = None
                        plugged = False
                        dec.reset()
                    continue
                for i in range(0, len(data) - 23, 24):
                    _, _, typ, code, val = struct.unpack("llHHi", data[i:i + 24])
                    if fd == sw:
                        if typ == EV_SW and code == SW_MIC:
                            plugged = bool(val)
                            t_plug = now
                            dec.reset()
                            log("headset plugged in" if plugged else "headset unplugged")
                    elif typ == EV_KEY:
                        bridge.on_key(code, val, now)
            if dg is not None:
                bridge.tick(now)
            if not (plugged and codec is not None) or now - t_plug < SETTLE_AFTER_PLUG:
                continue
            try:
                status = codec.read(NID_VENDOR, VERB_STATUS)
            except OSError as e:
                log(f"codec read error: {e}; reopening")
                codec.close(); codec = None
                continue
            for _, key in dec.feed(bool(status & BTN_MASK), now):
                log(f"centre button -> {NAMES.get(key, key)}")
                uin.tap(key)
    finally:
        uin.close()
        log("stopped")


if __name__ == "__main__":
    sys.exit(main())
