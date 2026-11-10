#!/usr/bin/env python3
"""Watch what an HDA codec reports while you press headset buttons (needs root).

  sudo tools/codec_monitor.py [-d /dev/snd/hwC0D0] [--coefs 142] SECONDS LOGFILE

Logs on CLOCK_MONOTONIC:
  - key/switch events from the sound card's jack input devices
  - raw HDA unsolicited responses (tracefs hda:hda_unsol_event)
  - every change in the vendor COEF bank (Realtek: NID 0x20) and in the
    headset-mic pin sense
If a remote button shows up here, the codec can see it in hardware and a
kernel quirk is a better fix than applemote; see docs/porting.md.
"""
import argparse
import fcntl
import os
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hda_verb import Codec  # noqa: E402

TRACE = "/sys/kernel/tracing"
EVIOCSCLOCKID = (1 << 30) | (4 << 16) | (ord("E") << 8) | 0xA0
EV_NAMES = {0x01: "KEY", 0x05: "SW"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-d", "--device", default="/dev/snd/hwC0D0")
    ap.add_argument("--coefs", type=int, default=142, help="COEF count on NID 0x20 (0 = skip)")
    ap.add_argument("--mic-pin", type=lambda s: int(s, 0), default=0x19)
    ap.add_argument("seconds", type=float)
    ap.add_argument("logfile")
    a = ap.parse_args()
    card = a.device.split("hwC")[1].split("D")[0]
    stop = time.monotonic() + a.seconds
    out = open(a.logfile, "w", buffering=1)
    lock = threading.Lock()

    def log(msg, t=None):
        with lock:
            out.write(f"{t if t is not None else time.monotonic():.6f} {msg}\n")

    def evdev(path, name):
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        fcntl.ioctl(fd, EVIOCSCLOCKID, struct.pack("i", 1))
        while time.monotonic() < stop:
            try:
                data = os.read(fd, 24 * 64)
            except BlockingIOError:
                time.sleep(0.002)
                continue
            for off in range(0, len(data), 24):
                sec, usec, typ, code, val = struct.unpack("qqHHi", data[off:off + 24])
                if typ in EV_NAMES:
                    log(f"EV[{name}] {EV_NAMES[typ]} code={code} val={val}", sec + usec / 1e6)
        os.close(fd)

    def trace():
        fd = os.open(f"{TRACE}/trace_pipe", os.O_RDONLY | os.O_NONBLOCK)
        buf = b""
        while time.monotonic() < stop:
            try:
                buf += os.read(fd, 65536)
            except BlockingIOError:
                time.sleep(0.005)
                continue
            *lines, buf = buf.split(b"\n")
            for ln in lines:
                log("UNSOL " + ln.decode(errors="replace").strip())
        os.close(fd)

    def coefs():
        c = Codec(a.device)
        prev = c.coefdump(count=a.coefs) if a.coefs else []
        sense = c.verb(a.mic_pin, 0xF09, 0)
        log("COEF baseline " + " ".join(f"{i:02x}={v:04x}" for i, v in enumerate(prev)))
        while time.monotonic() < stop:
            cur = c.coefdump(count=a.coefs) if a.coefs else []
            s = c.verb(a.mic_pin, 0xF09, 0)
            diff = [f"{i:02x}:{p:04x}->{v:04x}" for i, (p, v) in enumerate(zip(prev, cur)) if p != v]
            if diff:
                log("COEF " + " ".join(diff))
            if s != sense:
                log(f"PINSENSE 0x{a.mic_pin:02x} {sense:08x}->{s:08x}")
            prev, sense = cur, s
            if not a.coefs:
                time.sleep(0.005)

    clock = [w.strip("[]") for w in open(f"{TRACE}/trace_clock").read().split() if w.startswith("[")][0]
    for path, val in (("trace_clock", "mono"), ("trace", ""), ("events/hda/hda_unsol_event/enable", "1")):
        Path(f"{TRACE}/{path}").write_text(val)
    threads = [threading.Thread(target=trace), threading.Thread(target=coefs)]
    for d in sorted(Path("/sys/class/input").glob("event*")):
        try:
            name = (d / "device/name").read_text().strip()
        except OSError:
            continue
        if f"/sound/card{card}/" in str((d / "device").resolve()):
            log(f"DEV /dev/input/{d.name} {name}")
            threads.append(threading.Thread(target=evdev, args=(f"/dev/input/{d.name}", name)))
    print(f"logging for {a.seconds:.0f}s to {a.logfile} - press the remote buttons now", flush=True)
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    Path(f"{TRACE}/events/hda/hda_unsol_event/enable").write_text("0")
    Path(f"{TRACE}/trace_clock").write_text(clock)
    print("done")


if __name__ == "__main__":
    main()
