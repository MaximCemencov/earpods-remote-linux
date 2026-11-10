"""Shared helpers for the calibration tools: import the bundled applemote, load a dataset, replay it."""
import configparser, json, os, sys, types
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "applemote"))
import numpy as np
from applemote import daemon, detect, system   # noqa: E402

CONFIG = os.path.expanduser("~/.config/applemote/config.ini")


def load_config(path=CONFIG):
    cp = configparser.ConfigParser(); cp.read(path)
    return dict(cp["applemote"]) if cp.has_section("applemote") else {}


def thresholds(cfg):
    up, down = float(cfg.get("ncc_up", "0.85")), float(cfg.get("ncc_down", "0.70"))
    return {"UP_PRESS": up, "UP_RELEASE": up, "DOWN_PRESS": down, "DOWN_RELEASE": down}


def effective_config(cfg):
    """Config as the service would use it (defaults of this repo's installer)."""
    out = {"ncc_up": "0.85", "ncc_down": "0.70", "classifier": "split"}
    out.update({k: v for k, v in cfg.items() if k in ("ncc_up", "ncc_down", "classifier")})
    return out


def load_dataset(d):
    """-> (signal in unity-gain units, marks, gain_db)"""
    meta = json.load(open(os.path.join(d, "meta.json")))
    x = np.load(os.path.join(d, "audio.npy")).astype(np.float64) * 10 ** (-meta.get("gain_db", 12.0) / 20)
    return x, meta["marks"], meta.get("gain_db", 12.0)


def load_templates(path):
    return {k: v for k, v in dict(np.load(path)).items() if not k.startswith("meta")}


def replay(x, templates, cfg):
    """Run the recording through the real detector + the daemon's button state machine; -> [(t, key)] sent."""
    clock = [0.0]
    daemon.time.monotonic = lambda: clock[0]
    sent = []

    class Keys:
        def send(self, k): sent.append((clock[0], k))
    d = object.__new__(daemon.Daemon)
    d.cfg, d.verbose, d.keys, d.hold, d.hold_t, d.last_flush, d.train = {}, False, Keys(), system.Hold(), 0.0, 1e18, {}
    d.buttons = {"UP": {"key": "VOLUMEUP", "down": False, "t": 0.0, "amp": 0.0, "last_step": -9.0},
                 "DOWN": {"key": "VOLUMEDOWN", "down": False, "t": 0.0, "amp": 0.0, "last_step": -9.0}}
    d.detector, d.scale, d.pending = daemon.make_detector(templates, cfg), 1.0, b""
    log_ = daemon.log
    daemon.log = lambda *_: None
    try:
        for s0 in range(0, len(x), 960):
            blk = x[s0:s0 + 960].astype("<f4")
            st = np.repeat(blk, 2).tobytes()
            clock[0] = (s0 + len(blk)) / 48000
            d.on_audio(types.SimpleNamespace(read=lambda st=st: st), None)
            k = d.hold.due(clock[0])
            if k:
                d.keys.send(k)
    finally:
        daemon.log = log_
    return sent


EXPECT = {"QUIET": ((0, 0), (0, 0)), "UP_CLICK": ((1, 1), (0, 0)), "DOWN_CLICK": ((0, 0), (1, 1)),
          "UP_HOLD": ((3, 99), (0, 0)), "DOWN_HOLD": ((0, 0), (3, 99)), "DOWN_RAPID": ((0, 0), (4, 6)),
          "CENTRE": ((0, 0), (0, 0))}


def report(sent, marks, out=print):
    """Per labelled action: Vol+ / Vol- steps vs expectation. Returns the number of actions outside expectation."""
    bad = 0
    by = {}
    for m in marks:
        a, b = m["a"] - 0.05, m["b"] + (0.9 if m["phase"] != "QUIET" else 0.0)
        up = sum(1 for t, k in sent if k == "VOLUMEUP" and a <= t < b)
        dn = sum(1 for t, k in sent if k == "VOLUMEDOWN" and a <= t < b)
        (ulo, uhi), (dlo, dhi) = EXPECT.get(m["phase"], ((0, 99), (0, 99)))
        ok = ulo <= up <= uhi and dlo <= dn <= dhi
        bad += not ok
        by.setdefault(m["phase"], []).append(f"{up}/{dn}" + ("" if ok else "!"))
    for ph, v in by.items():
        out(f"  {ph:<11} (Vol+/Vol- steps per action)  {' '.join(v)}")
    marked = [(m["a"] - 0.05, m["b"] + 0.9) for m in marks]
    stray = [(round(t, 1), k) for t, k in sent if not any(a <= t < b for a, b in marked)]
    out(f"  steps outside labelled actions: {len(stray)} {stray[:8]}")
    out(f"  actions outside expectation (marked '!'): {bad} of {len(marks)}")
    return bad
