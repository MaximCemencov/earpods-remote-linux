"""applemote run: listen on the headset mic while it is free, emit volume keys.

States:
  unplugged          nothing captured, user gains untouched
  plugged, mic free  gains pinned to the detection operating point, listening
  plugged, mic busy  another app records from the headset mic: user gains are
                     given back and detection pauses (a clipped signal can
                     produce wrong-direction keys, which is worse than none)
"""
import fcntl
import os
import selectors
import signal
import subprocess
import sys
import time

import numpy as np

from . import detect, system
from .system import log


ACT_RELEASE = 3e-4      # line activity that ends a hold (held: <1e-4, any release: >3e-3)


def make_detector(templates, cfg):
    """The detector exactly as the service runs it (also used by the calibration tools)."""
    up, down = float(cfg.get("ncc_up", 0.85)), float(cfg.get("ncc_down", 0.85))
    det = detect.Detector(templates, ncc_min={"UP_PRESS": up, "UP_RELEASE": up, "DOWN_PRESS": down, "DOWN_RELEASE": down},
                          scale=(0.12, 4.0), classifier=cfg.get("classifier", "template"))
    det.debug = cfg.get("debug", "0") == "1"
    return det


def merge_edges(edges, gap=0.04):
    """One physical edge can fire several onsets within a few ms: keep the best-matching one of each burst."""
    out = []
    for e in sorted(edges, key=lambda e: e["t"]):
        if out and e["t"] - out[-1]["t"] < gap:
            if e["ncc"] > out[-1]["ncc"]:
                out[-1] = e
        else:
            out.append(e)
    return out


class Daemon:
    def __init__(self, cfg, verbose=False):
        self.cfg, self.verbose = cfg, verbose
        self.mixer = system.Mixer(cfg["card"])
        self.gains = system.Gains(self.mixer, cfg)
        self.keys = system.KeyOut(cfg["key_device"])
        self.templates = detect.load_templates(cfg["templates"])
        self.sel = selectors.DefaultSelector()
        self.cap = None
        self.detector = None
        self.scale = 1.0
        self.pending = b""
        self.recheck_at = 0.0
        self.hold = system.Hold()      # unused for the 3.5 mm jack: the remote repeats pulses itself while a button is held
        self.hold_t = 0.0
        self.train = {}
        self.buttons = {"UP": {"key": "VOLUMEUP", "down": False, "t": 0.0, "amp": 0.0, "last_step": -9.0},
                        "DOWN": {"key": "VOLUMEDOWN", "down": False, "t": 0.0, "amp": 0.0, "last_step": -9.0}}
        self.last_flush = time.monotonic()
        self.watch(["stdbuf", "-oL", "amixer", "-c", cfg["card"], "events"], self.on_mixer_event)
        self.watch(["stdbuf", "-oL", "pactl", "subscribe"], self.on_pulse_event)

    def watch(self, cmd, handler):
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        os.set_blocking(p.stdout.fileno(), False)
        self.sel.register(p.stdout, selectors.EVENT_READ, (handler, [b""]))

    # --- state ----------------------------------------------------------------
    def evaluate(self):
        plugged = self.mixer.jack(self.cfg["jack_control"])
        busy = system.other_consumers(self.cfg["source"]) if plugged else []
        if plugged and not busy:
            self.start()
        else:
            self.stop("headset mic in use by " + ", ".join(busy) if busy else
                      "headset unplugged")

    def start(self):
        if self.cap:
            return
        self.gains.borrow()
        self.scale = 10 ** (-self.gains.total_db() / 20)
        self.detector = make_detector(self.templates, self.cfg)
        self.pending = b""
        self.cap = system.capture(self.cfg["source"])
        try:
            fcntl.fcntl(self.cap.stdout.fileno(), 1031, 1 << 20)   # F_SETPIPE_SZ: ~2.7 s of audio instead of 0.17 s
        except OSError:
            pass
        os.set_blocking(self.cap.stdout.fileno(), False)
        self.sel.register(self.cap.stdout, selectors.EVENT_READ, (self.on_audio, None))
        log(f"listening (capture gain {self.gains.total_db():+.1f} dB)")

    def stop(self, why):
        if not self.cap:
            return
        self.sel.unregister(self.cap.stdout)
        self.cap.terminate()
        self.cap.wait()
        self.cap = None
        self.flush_debug(force=True)
        self.hold.stop()
        for b in self.buttons.values():
            b['down'] = False
        self.gains.give_back()
        log(f"paused: {why}")

    # --- inputs ---------------------------------------------------------------
    def on_audio(self, f, _):
        data = f.read()
        if not data:
            log("capture ended; restarting")
            self.stop("capture ended")
            self.recheck_at = time.monotonic() + 1
            return
        data = self.pending + data
        n = len(data) // 8 * 8
        self.pending = data[n:]
        x = np.frombuffer(data[:n], dtype="<f4")[0::2] * self.scale
        self.detector.process(x)
        edges, self.detector.edges = self.detector.edges, []
        for e in merge_edges(edges):
            self.on_edge(e)
        act, self.detector.activity = self.detector.activity, []
        for fam, b in self.buttons.items():
            if not b["down"]:
                continue
            hit = next((t for t, v in act if t > b["t"] + 0.10 and v >= ACT_RELEASE), None)
            if hit is not None:
                b["down"] = False
                self.hold.stop(b["key"])
                if self.verbose:
                    log(f"release {fam} by line activity after {hit - b['t']:.2f}s")
        if self.verbose:
            for ev in self.detector.near_misses:
                log(f"near miss {ev['cls']} ncc={ev['ncc']:.2f} scale={ev['scale']:.2f}")
        self.detector.near_misses.clear()
        self.flush_debug()

    def flush_debug(self, force=False):
        log_ = self.detector.edge_log if self.detector else []
        if not log_ or (not force and time.monotonic() - self.last_flush < 30 and len(log_) < 2000):
            return
        import numpy as _np, datetime, threading
        d = system.STATE_DIR / "debug"
        d.mkdir(parents=True, exist_ok=True)
        wall = time.time()                     # wall clock of the last edge, to match user-reported times
        t_last = log_[-1][0]
        path = d / f"edges-{datetime.datetime.now():%Y%m%d-%H%M%S}.npz"
        def save(log_=log_):                   # in a thread: compressing must not stall the audio path
            _np.savez_compressed(path, t=_np.array([e[0] for e in log_]), step=_np.array([e[1] for e in log_]),
                                 win=_np.stack([e[2] for e in log_]), cls=_np.array([e[3] for e in log_]),
                                 ncc=_np.array([e[4] for e in log_]), scale=_np.array([e[5] for e in log_]),
                                 wall_ref=_np.array([wall, t_last]))
            for f in sorted(d.glob("edges-*.npz"))[:-400]:
                f.unlink(missing_ok=True)
        threading.Thread(target=save, daemon=True).start()
        self.detector.edge_log = []
        self.last_flush = time.monotonic()

    def on_edge(self, e):
        """Button state machine (local patch).  After a press, the next edge of the same button (99% of the time
        within 2 s, median 0.14 s) is its release, whatever the classifier thinks: Vol- press and release look alike.
        So: a click is exactly one step, fast clicks all count, and a hold (no edge for HOLD_DELAY) repeats until
        the next edge or HOLD_MAX."""
        now = time.monotonic()
        other_press = e["ok"] and e["cls"].endswith("PRESS")
        consumed = False
        for fam, b in self.buttons.items():
            if not b["down"] or e["t"] - b["t"] < 0.06:
                continue
            # what counts as this button's release: for Vol- ANY following edge (its release is weak and often
            # unrecognisable - better to end a hold early than to run away); for Vol+ an edge that looks like it
            confident_down_press = e["fam"] == "DOWN" and other_press and e["shift"] > 0
            if fam == "DOWN":
                is_release = True
            elif e["t"] - b["t"] <= 0.5 and not confident_down_press:
                is_release = True                 # right after a Vol+ press: anything but a real Vol- press is its release
            else:
                is_release = (e["fam"] == fam and e["ncc"] >= 0.5) or e["amp"] >= 0.5 * b["amp"]
            if not is_release:
                continue
            b["down"] = False
            self.hold.stop(b["key"])
            if self.verbose:
                log(f"release {fam} after {e['t'] - b['t']:.2f}s ({e['cls']} ncc={e['ncc']:.2f})")
            # a confident NEW press of the same button (positive shift, well after the press): the release was
            # missed - end the stale state but count this press instead of swallowing it as the "release"
            new_press = e["fam"] == fam and other_press and e["shift"] > 0 and e["t"] - b["t"] >= 0.25
            if new_press:
                log(f"{fam}: release missed, new press counted")
            if (e["fam"] == fam and not new_press) or not other_press:
                consumed = True               # a press of the OTHER button still counts as a press
        if consumed:
            return
        if not e["ok"]:
            return
        b = self.buttons[e["fam"]]
        if e["t"] - b["last_step"] < 0.08:      # the same physical edge split across audio blocks
            return
        if e["cls"].endswith("PRESS"):
            log(f"KEY_{b['key']} ({e['cls']} ncc={e['ncc']:.2f} scale={e['scale']:.2f} shift={e['shift']:+.5f})")
            self.keys.send(b["key"])
            b.update(down=True, t=e["t"], amp=e["amp"], last_step=e["t"])
            self.hold.start(b["key"], now)
        elif (e["fam"] == "DOWN" and e["shift"] <= -detect.SHIFT_MIN["DOWN"] and e["t"] - b["last_step"] > 0.5
              and e["t"] - self.buttons["UP"]["last_step"] > 0.6 and not self.buttons["UP"]["down"]):
            # a Vol- release with no press seen: the (weak) press was missed - count the click late rather than lose it
            log(f"KEY_{b['key']} (late: {e['cls']} ncc={e['ncc']:.2f} shift={e['shift']:+.5f})")
            self.keys.send(b["key"])
            b["last_step"] = e["t"]

    def release(self, cls):
        key = {"UP_RELEASE": "VOLUMEUP", "DOWN_RELEASE": "VOLUMEDOWN"}.get(cls)
        if key:
            self.hold.stop(key)

    def lines(self, f, buf):
        *lines, buf[0] = (buf[0] + (f.read() or b"")).split(b"\n")
        return [ln.decode(errors="replace") for ln in lines]

    def on_mixer_event(self, f, buf):
        for line in self.lines(f, buf):
            if "event value" not in line:
                continue
            if f"'{self.cfg['jack_control']}'" in line:
                self.recheck_at = time.monotonic() + 0.1
            elif self.cap and any(f"'{c}" in line for c, _ in self.gains.pin):
                self.gains.user_changed()

    def on_pulse_event(self, f, buf):
        if any("source-output" in line for line in self.lines(f, buf)):
            self.recheck_at = time.monotonic() + 0.1

    def run(self):
        self.evaluate()
        if not self.cap:
            log("waiting for a free headset mic")
        while True:
            timeout = max(0.0, self.recheck_at - time.monotonic()) if self.recheck_at else None
            if self.hold.key:
                wait = max(0.0, self.hold.t_next - time.monotonic())
                timeout = wait if timeout is None else min(timeout, wait)
            for key, _ in self.sel.select(timeout):
                handler, buf = key.data
                handler(key.fileobj, buf)
            held = self.hold.due(time.monotonic())
            if held:
                self.keys.send(held)
            if self.recheck_at and time.monotonic() >= self.recheck_at:
                self.recheck_at = 0.0
                self.evaluate()

    def shutdown(self, *_):
        self.stop("exiting")
        self.keys.close()
        sys.exit(0)


def main(args):
    cfg = system.load_config()
    if not cfg["source"] or not cfg["templates"]:
        log(f"not configured: run 'applemote setup' (config: {system.CONFIG_FILE})")
        return 1
    d = Daemon(cfg, args.verbose)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, d.shutdown)
    try:
        d.run()
    finally:
        d.stop("exiting")
