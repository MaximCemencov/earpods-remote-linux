#!/usr/bin/env python3
"""Record a labelled calibration dataset of your headset remote through the same capture path as applemote.

Run as your normal user in a terminal, with the headset plugged into the laptop jack:

    python3 tools/collect.py [--out DIR]

The screen tells you what to do (bright box = act now, grey text = hands off) and shows what comes next. Phases:
quiet, Vol+ clicks, Vol- clicks, Vol+ holds, Vol- holds, fast Vol- series, centre clicks (should produce no volume
keys). Timestamps are taken from the number of audio samples received, so labels and audio cannot drift apart.

The applemote service is stopped while recording; mixer gains, the source mute state and the service are restored on
exit (also if the window is closed). Output: DIR/audio.npy (mono float32, 48 kHz, raw capture) and DIR/meta.json.
"""
import argparse, atexit, configparser, json, os, re, signal, subprocess, sys, threading, time
import numpy as np

B, DIM, R, G, Y, C, X = "\033[1m", "\033[2m", "\033[7m", "\033[92m", "\033[93m", "\033[96m", "\033[0m"

PLAN = [
    ("QUIET", "Quiet", [("DO NOT TOUCH THE REMOTE", 4.0, False)]),
    ("UP_CLICK", "Vol+ clicks x10", [("ONE CLICK  Vol+  (+)", 0.8, True), ("pause", 1.0, False)] * 10),
    ("DOWN_CLICK", "Vol- clicks x12", [("ONE CLICK  Vol-  (-)", 0.8, True), ("pause", 1.0, False)] * 12),
    ("UP_HOLD", "Vol+ holds x3", [("PRESS AND HOLD  Vol+", 2.0, True), ("RELEASE and wait", 1.5, False)] * 3),
    ("DOWN_HOLD", "Vol- holds x4", [("PRESS AND HOLD  Vol-", 2.0, True), ("RELEASE and wait", 1.5, False)] * 4),
    ("DOWN_RAPID", "Fast Vol- series x2", [("CLICK  Vol-  FAST, 5 times", 3.0, True), ("pause", 1.5, False)] * 2),
    ("CENTRE", "Centre button x3", [("ONE CLICK  centre button", 0.8, True), ("pause", 1.2, False)] * 3),
]
TOTAL = sum(d for _, _, steps in PLAN for _, d, _ in steps) + 3.0 * (len(PLAN) - 1)


def sh(*a):
    return subprocess.run(list(a), capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"}).stdout


def frame(sec_i, text, active, left, dur, nxt, done):
    """Flicker-free redraw: cursor home, lines overwritten in place, no full clear."""
    L = [f"{B}{C}== Headset remote calibration =={X}    {done:4.0f} of ~{TOTAL:.0f} s", ""]
    for i, (_, title, _) in enumerate(PLAN):
        mark = f"{G}v{X}" if i < sec_i else (f"{Y}>{X}" if i == sec_i else " ")
        L.append(f"   {mark} {title}")
    L += ["", ""]
    box = f"   {text}   "
    if active:
        L += [f"      {R}{B}{' ' * len(box)}{X}", f"      {R}{B}{box}{X}", f"      {R}{B}{' ' * len(box)}{X}"]
    else:
        L += ["", f"      {DIM}{box}{X}", ""]
    n = int(40 * (1 - left / dur)) if dur else 40
    L += ["", f"      [{'#' * n}{'.' * (40 - n)}]  {left:3.1f} s", "", f"      {DIM}next: {nxt}{X}"]
    sys.stdout.write("\033[H" + "".join(line + "\033[K\n" for line in L) + "\033[J")
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.expanduser("~/.local/state/applemote/collect"))
    ap.add_argument("--config", default=os.path.expanduser("~/.config/applemote/config.ini"))
    args = ap.parse_args()
    cp = configparser.ConfigParser(); cp.read(args.config)
    cfg = cp["applemote"] if cp.has_section("applemote") else {}
    card, src = cfg.get("card", "0"), cfg.get("source", "")
    jack = cfg.get("jack_control", "Mic Jack")
    pins = [(cfg.get("boost_control", "Mic Boost"), float(cfg.get("boost_db", "12"))),
            (cfg.get("capture_control", "Capture"), float(cfg.get("capture_db", "0")))]
    pins = [(c, db) for c, db in pins if c]
    if not src:
        sys.exit("No headset source configured: run 'applemote setup' first (it writes ~/.config/applemote/config.ini).")
    amix = lambda *a: sh("amixer", "-c", card, *a)
    step = lambda c: int(re.search(r": (?:Capture |Playback )?(\d+) \[", amix("sget", c)).group(1))
    os.makedirs(args.out, exist_ok=True)

    os.system("clear")
    print(f"{B}{C}== Headset remote calibration =={X}\n")
    print(f"Headset in the laptop jack. About {TOTAL / 60:.1f} minutes. Press the buttons the way you normally do.")
    print("The big line is WHAT TO DO NOW: a bright box means act, grey text means hands off. 'next:' shows what follows.\n")
    if "values=on" not in amix("cget", f"iface=CARD,name={jack}"):
        print("Waiting for the headset...", end="", flush=True)
        while "values=on" not in amix("cget", f"iface=CARD,name={jack}"):
            time.sleep(1)
        print(" ok\n")
    input(f"{B}Press Enter when ready...{X} ")

    old = {c: step(c) for c, _ in pins}
    was_muted = "yes" in sh("pactl", "get-source-mute", src).lower()
    service_was = sh("systemctl", "--user", "is-active", "applemote").strip() == "active"
    proc = [None]

    def restore():
        if proc[0]:
            proc[0].terminate(); proc[0].wait(); proc[0] = None
        for c, s in old.items():
            amix("sset", c, str(s))
        if was_muted:
            sh("pactl", "set-source-mute", src, "1")
        if service_was:
            sh("systemctl", "--user", "start", "applemote")
        sys.stdout.write("\033[?25h"); sys.stdout.flush()
    atexit.register(restore)
    for sg in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(sg, lambda *_: sys.exit(1))

    if service_was:
        sh("systemctl", "--user", "stop", "applemote")
    for c, db in pins:
        amix("sset", c, f"{db}dB")
    gain_db = sum(float(re.search(r"\[(-?[\d.]+)dB\]", amix("sget", c)).group(1)) for c, _ in pins)
    if was_muted:
        sh("pactl", "set-source-mute", src, "0")
    proc[0] = subprocess.Popen(["pw-record", "--raw", "--target", src, "--rate", "48000", "--channels", "2",
                                "--format", "f32", "--latency", "20ms", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    chunks, nbytes = [], [0]

    def reader():
        while (b := proc[0].stdout.read(9600)):
            chunks.append(b); nbytes[0] += len(b)
    threading.Thread(target=reader, daemon=True).start()
    time.sleep(1.2)
    pos = lambda: nbytes[0] / 8 / 48000            # seconds of audio received so far

    marks, t_start = [], time.time()
    sys.stdout.write("\033[2J\033[H\033[?25l"); sys.stdout.flush()
    flat = [(si, name, st) for si, (name, _, steps) in enumerate(PLAN) for st in steps]
    for k, (si, name, (text, dur, active)) in enumerate(flat):
        if k and flat[k - 1][0] != si:
            t0 = time.time()
            while (left := 3.0 - (time.time() - t0)) > 0:
                frame(si, f"Next section: {PLAN[si][1]}", False, left, 3.0, text, time.time() - t_start); time.sleep(0.1)
        nxt = flat[k + 1][2][0] if k + 1 < len(flat) else "done"
        a = pos(); t0 = time.time()
        while (left := dur - (time.time() - t0)) > 0:
            frame(si, text, active, left, dur, nxt, time.time() - t_start); time.sleep(0.1)
        if active or name == "QUIET":
            marks.append({"phase": name, "i": sum(1 for m in marks if m["phase"] == name) + 1, "a": a, "b": pos()})
    time.sleep(0.5)
    proc[0].terminate(); proc[0].wait(); proc[0] = None
    raw = np.frombuffer(b"".join(chunks)[: nbytes[0] // 8 * 8], dtype="<f4")[0::2].copy()
    np.save(os.path.join(args.out, "audio.npy"), raw)
    json.dump({"marks": marks, "gain_db": gain_db, "rate": 48000, "note": "a/b = seconds of audio"},
              open(os.path.join(args.out, "meta.json"), "w"), indent=1)
    restore(); atexit.unregister(restore)
    os.system("clear")
    print(f"{G}{B}Done.{X}  {len(raw) / 48000:.0f} s recorded, {len(marks)} labelled actions, capture gain {gain_db:+.1f} dB.")
    print(f"Saved to {args.out}. Gains, mute and the applemote service are restored.")
    print(f"Next:  python3 tools/learn_templates.py {args.out}")


if __name__ == "__main__":
    main()
