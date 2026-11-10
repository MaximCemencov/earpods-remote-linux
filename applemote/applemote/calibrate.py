"""applemote calibrate: record your remote's button signatures and build templates."""
import subprocess
import threading
import time
from datetime import date

import numpy as np
from scipy.io import wavfile

from . import detect, system

BYTES_PER_SEC = 48000 * 2 * 4   # stereo float32


def record_interactive(cfg, presses, keep):
    mixer = system.Mixer(cfg["card"])
    if not mixer.jack(cfg["jack_control"]):
        raise SystemExit("Plug in the headset first.")
    busy = system.other_consumers(cfg["source"])
    if busy:
        raise SystemExit(f"Close apps using the headset mic first: {', '.join(busy)}")
    gains = system.Gains(mixer, cfg)
    gains.borrow()
    gain_db = gains.total_db()
    proc = system.capture(cfg["source"])
    chunks, size = [], [0]

    def reader():
        while (b := proc.stdout.read(9600)):
            chunks.append(b)
            size[0] += len(b)
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    marks = {}
    try:
        print("Recording the headset mic" +
              (f" (saving a copy to {keep}).\n" if keep else " (kept in memory only, discarded afterwards).\n"))
        print("1/3  Keep the remote still and quiet...", end="", flush=True)
        time.sleep(1.0)
        marks["quiet"] = [size[0]]
        time.sleep(3.0)
        marks["quiet"].append(size[0])
        print(" ok")
        for button, label in (("UP", "Vol+"), ("DOWN", "Vol-")):
            n = "2/3" if button == "UP" else "3/3"
            input(f"{n}  Press Enter, then press {label} {presses} times, about once a second.")
            marks[button] = [size[0]]
            input(f"     ...press Enter when you have pressed {label} {presses} times.")
            marks[button].append(size[0])
    finally:
        proc.terminate()
        proc.wait()
        t.join(1)
        gains.give_back()
    raw = np.frombuffer(b"".join(chunks)[: size[0] // 8 * 8], dtype="<f4")[0::2].astype(np.float64)
    segments = {k: (a / BYTES_PER_SEC, b / BYTES_PER_SEC) for k, (a, b) in marks.items()}
    return raw, gain_db, segments


def save_wav(path, x):
    wavfile.write(path, detect.RATE, x.astype(np.float32))


def load_wav(path):
    """First channel of a 48 kHz WAV as float (full scale = 1.0)."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", wavfile.WavFileWarning)
        rate, x = wavfile.read(path)
    if rate != detect.RATE:
        raise SystemExit(f"{path}: {rate} Hz, need {detect.RATE} Hz")
    x = x[:, 0] if x.ndim > 1 else x
    if x.dtype.kind == "i":
        return x.astype(np.float64) / np.iinfo(x.dtype).max
    return x.astype(np.float64)


def parse_span(s):
    a, b = s.split("-")
    return float(a), float(b)


def main(args):
    cfg = system.load_config()
    if not cfg["source"]:
        raise SystemExit("Run 'applemote setup' first.")
    if args.wav:
        x = load_wav(args.wav)
        return learn(cfg, x * 10 ** (-args.gain_db / 20), args.gain_db,
                     {"UP": parse_span(args.up), "DOWN": parse_span(args.down)})
    # the service would fight over the mic gain and react to the presses
    active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "applemote"]).returncode == 0
    if active:
        subprocess.run(["systemctl", "--user", "stop", "applemote"])
    try:
        x, gain_db, segments = record_interactive(cfg, args.presses, args.save_wav)
        if args.save_wav:
            save_wav(args.save_wav, x)
            print(f"saved recording to {args.save_wav} (capture gain {gain_db:+.1f} dB)")
        return learn(cfg, x * 10 ** (-gain_db / 20), gain_db, segments)
    finally:
        if active:
            subprocess.run(["systemctl", "--user", "start", "applemote"])
            print("restarted the applemote service")


def learn(cfg, x, gain_db, segments):
    try:
        templates, report = detect.learn_templates(x, {k: segments[k] for k in ("UP", "DOWN")})
    except ValueError as e:
        raise SystemExit(f"Calibration failed: {e}")
    result = detect.validate(x, templates, segments)
    print("\nlearned:", ", ".join(f"{b}: {r['presses']} presses / {r['releases']} releases from "
                                    f"{r['edges']} edges" for b, r in report.items()))
    for seg, keys in result.items():
        print(f"  {seg:<5} -> Vol+ {keys['VOLUMEUP']:2d}   Vol- {keys['VOLUMEDOWN']:2d}")
    wrong = result["UP"]["VOLUMEDOWN"] + result["DOWN"]["VOLUMEUP"] + sum(result.get("quiet", {}).values())
    if wrong or min(result["UP"]["VOLUMEUP"], result["DOWN"]["VOLUMEDOWN"]) < 3:
        raise SystemExit("\nNot good enough to use (wrong or missing keys). Try again with "
                         "firm, separate presses; see docs/porting.md if it keeps failing.")
    out = system.CONFIG_DIR / "templates.npz"
    system.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    detect.save_templates(out, templates, gain_db=gain_db, date=str(date.today()))
    cfg["templates"] = str(out)
    system.save_config(cfg)
    print(f"\nsaved {out}")
    return 0
