"""ALSA mixer/jack, PipeWire and evdev plumbing."""
import configparser
import fcntl
import json
import os
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "applemote"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "applemote"
CONFIG_FILE = CONFIG_DIR / "config.ini"
GAIN_STATE = STATE_DIR / "saved-gains.json"
DATA_DIR = Path(__file__).resolve().parent.parent / "templates"
NODE_NAME = "applemote"

DEFAULTS = {
    "card": "0",
    "source": "",
    "jack_control": "Mic Jack",
    "boost_control": "Mic Boost",
    "capture_control": "Capture",
    "boost_db": "0",
    "capture_db": "0",
    "key_device": "uinput",
    "templates": "",
    "ncc_up": "0.85",
    "ncc_down": "0.85",
    "debug": "0",
    "classifier": "template",
}


def log(msg):
    print(f"applemote: {msg}", file=sys.stderr, flush=True)


# --- config -----------------------------------------------------------------

def load_config():
    cp = configparser.ConfigParser()
    cp["applemote"] = DEFAULTS
    cp.read(CONFIG_FILE)
    return dict(cp["applemote"])


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cp = configparser.ConfigParser()
    cp["applemote"] = cfg
    with open(CONFIG_FILE, "w") as f:
        cp.write(f)


# --- ALSA -------------------------------------------------------------------

class Mixer:
    def __init__(self, card):
        self.card = str(card)

    def amixer(self, *args):
        return subprocess.run(["amixer", "-c", self.card, *args],
                              capture_output=True, text=True).stdout

    def jack(self, name):
        return "values=on" in self.amixer("cget", f"iface=CARD,name={name}")

    def get_step(self, control):
        m = re.search(r": (?:Capture |Playback )?(\d+) \[", self.amixer("sget", control))
        return int(m.group(1)) if m else None

    def get_db(self, control):
        m = re.search(r"\[(-?[\d.]+)dB\]", self.amixer("sget", control))
        return float(m.group(1)) if m else 0.0

    def set_step(self, control, step):
        self.amixer("sset", control, str(step))

    def set_db(self, control, db):
        self.amixer("sset", control, f"{db}dB")

    def controls(self):
        return self.amixer("controls")

    def scontrols(self):
        return re.findall(r"Simple mixer control '([^']+)',0", self.amixer("scontrols"))


class Gains:
    """Pins the headset-mic gain to the detection operating point and restores
    the user's gain afterwards.  Saved values survive crashes via GAIN_STATE."""

    def __init__(self, mixer, cfg):
        self.mixer = mixer
        self.pin = [(c, float(cfg[k])) for c, k in ((cfg["boost_control"], "boost_db"),
                                                    (cfg["capture_control"], "capture_db")) if c]
        self.saved = None
        self.last_set = 0.0
        self.source = cfg.get("source", "")
        self.unmuted = False        # local patch: we lifted a user mute of the headset source
        self.recover()

    def recover(self):
        """Restore gains a previous run left pinned (crash, power loss)."""
        try:
            saved = json.loads(GAIN_STATE.read_text())
        except (OSError, ValueError):
            return
        if saved.pop("_unmuted", False) and self.source:
            set_source_mute(self.source, True)
        for control, step in saved.items():
            self.mixer.set_step(control, step)
        GAIN_STATE.unlink(missing_ok=True)
        log(f"restored gains from a previous run: {saved}")

    def borrow(self):
        if self.saved is None:
            self.saved = {c: self.mixer.get_step(c) for c, _ in self.pin}
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            if self.source and source_muted(self.source):
                set_source_mute(self.source, False)
                self.unmuted = True
            GAIN_STATE.write_text(json.dumps({**self.saved, **({"_unmuted": True} if self.unmuted else {})}))
        for control, db in self.pin:
            self.mixer.set_db(control, db)
        self.last_set = time.monotonic()

    def user_changed(self):
        """A gain changed while pinned: keep it as the user's new preference."""
        if self.saved is None or time.monotonic() - self.last_set < 1.0:
            return
        self.saved = {c: self.mixer.get_step(c) for c, _ in self.pin}
        GAIN_STATE.write_text(json.dumps({**self.saved, **({"_unmuted": True} if self.unmuted else {})}))
        self.borrow()

    def give_back(self):
        if self.saved is None:
            return
        for control, step in self.saved.items():
            if step is not None:
                self.mixer.set_step(control, step)
        if self.unmuted and self.source:
            set_source_mute(self.source, True)
        self.unmuted = False
        self.saved = None
        GAIN_STATE.unlink(missing_ok=True)

    def total_db(self):
        return sum(self.mixer.get_db(c) for c, _ in self.pin)


# --- PipeWire ---------------------------------------------------------------

def pactl_json(*args):
    out = subprocess.run(["pactl", "-f", "json", *args], capture_output=True, text=True).stdout
    try:
        return json.loads(out or "[]")
    except ValueError:
        return []


def source_muted(name):
    out = subprocess.run(["pactl", "get-source-mute", name], capture_output=True, text=True,
                         env={**os.environ, "LC_ALL": "C"}).stdout
    return "yes" in out.lower()


def set_source_mute(name, mute):
    subprocess.run(["pactl", "set-source-mute", name, "1" if mute else "0"], capture_output=True)


def source_index(name):
    for s in pactl_json("list", "sources"):
        if s.get("name") == name:
            return s.get("index")
    return None


def other_consumers(source_name):
    """Recording streams on our source that are not applemote itself."""
    idx = source_index(source_name)
    found = []
    for o in pactl_json("list", "source-outputs"):
        props = o.get("properties", {})
        if o.get("source") == idx and props.get("node.name") != NODE_NAME:
            found.append(props.get("application.name") or props.get("node.name") or "?")
    return found


def capture(source):
    """pw-record subprocess streaming raw stereo float32 at 48 kHz on stdout."""
    return subprocess.Popen(
        ["pw-record", "--raw", "--target", source, "--rate", "48000", "--channels", "2",
         "--format", "f32", "--latency", "20ms", "-P",
         f'{{ node.name = {NODE_NAME}, application.name = "Apple remote detector" }}', "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


# --- keys -------------------------------------------------------------------

EV_SYN, EV_KEY, SYN_REPORT = 0x00, 0x01, 0
KEYCODES = {"VOLUMEDOWN": 114, "VOLUMEUP": 115}


def input_devices():
    """[(event node, name, sysfs path, key-capability bitmask as int)]"""
    out = []
    for d in sorted(Path("/sys/class/input").glob("event*")):
        try:
            name = (d / "device/name").read_text().strip()
            words = (d / "device/capabilities/key").read_text().split()
        except OSError:
            continue
        bits = 0
        for w in words:
            bits = (bits << 64) | int(w, 16)
        out.append((f"/dev/input/{d.name}", name, str((d / "device").resolve()), bits))
    return out


def has_volume_keys(bits):
    return all(bits >> code & 1 for code in KEYCODES.values())


HOLD_DELAY, HOLD_PERIOD, HOLD_MAX = 0.45, 0.11, 2.5


class Hold:
    """Local patch: hold-to-repeat like on iOS. After HOLD_DELAY the key repeats every HOLD_PERIOD until the
    release is seen (or HOLD_MAX, in case the release edge was missed)."""

    def __init__(self):
        self.key = None
        self.t_next = self.t_end = 0.0

    def start(self, key, now):
        self.key, self.t_next, self.t_end = key, now + HOLD_DELAY, now + HOLD_MAX

    def stop(self, key=None):
        if key is None or key == self.key:
            self.key = None

    def due(self, now):
        if self.key is None:
            return None
        if now >= self.t_end:
            self.key = None
            return None
        if now >= self.t_next:
            self.t_next = now + HOLD_PERIOD
            return self.key
        return None


class KeyOut:
    """Momentary key presses: written into an existing input device that
    already advertises the volume keys (e.g. the ALSA headphone-jack device),
    or from a uinput virtual keyboard (key_device = uinput)."""

    def __init__(self, key_device):
        self.key_device = key_device
        self.fd = None
        self.uinput = False

    def _open(self):
        if self.key_device == "uinput":
            self._open_uinput()
            return
        for node, name, _, bits in input_devices():
            if name == self.key_device and has_volume_keys(bits):
                self.fd = os.open(node, os.O_WRONLY)
                log(f"keys go to {node} ({name})")
                return
        raise OSError(f"no writable input device {self.key_device!r} with volume keys")

    def _open_uinput(self):
        def iow(nr, size):
            return (1 << 30) | (size << 16) | (ord("U") << 8) | nr
        fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(fd, iow(100, 4), EV_KEY)                      # UI_SET_EVBIT
        for code in KEYCODES.values():
            fcntl.ioctl(fd, iow(101, 4), code)                    # UI_SET_KEYBIT
        setup = struct.pack("HHHH80sI", 0x06, 0, 0, 1, b"applemote remote buttons", 0)
        fcntl.ioctl(fd, iow(3, len(setup)), setup)                # UI_DEV_SETUP
        fcntl.ioctl(fd, (ord("U") << 8) | 1)                      # UI_DEV_CREATE
        self.fd, self.uinput = fd, True
        log("keys go to a uinput device")

    def _emit(self, typ, code, val):
        t = time.time()
        os.write(self.fd, struct.pack("qqHHi", int(t), int(t % 1 * 1e6), typ, code, val))

    def send(self, key):
        for attempt in (1, 2):
            try:
                if self.fd is None:
                    self._open()
                for val in (1, 0):
                    self._emit(EV_KEY, KEYCODES[key], val)
                    self._emit(EV_SYN, SYN_REPORT, 0)
                return
            except OSError as e:
                self.close()
                if attempt == 2:
                    log(f"cannot send KEY_{key}: {e}")

    def close(self):
        if self.fd is not None:
            if self.uinput:
                fcntl.ioctl(self.fd, (ord("U") << 8) | 2)             # UI_DEV_DESTROY
            os.close(self.fd)
            self.fd = None
