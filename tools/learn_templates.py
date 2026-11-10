#!/usr/bin/env python3
"""Learn applemote templates (UP/DOWN x PRESS/RELEASE) from a dataset recorded with tools/collect.py.

    python3 tools/learn_templates.py DATASET_DIR [--out ~/.config/applemote/templates.npz]

For every labelled single click the press and the release edge are located explicitly (the two edges of the click,
0.10-0.35 s apart; for Vol+ the press is the much stronger one), windows are aligned on the main edge and averaged.
The result is then replayed through the real detector + state machine and a per-action report is printed.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import argparse, datetime, os, shutil, sys
from _common import np, detect, load_dataset, load_config, thresholds, effective_config, replay, report
from applemote.detect import window, lowpass, onsets, _average, MS


def edge_groups(x, a, b, floor, min_step=0.0015):
    o = onsets(x[a:b], floor=floor) + a
    g = []
    for k in o:
        s = abs(x[k] - x[k - 1])
        if g and k - g[-1][2] < 3 * MS:
            g[-1][1] = max(g[-1][1], s); g[-1][2] = k
        else:
            g.append([k, s, k])
    return [(k, s) for k, s, _ in g if s >= min_step]


def find_pair(x, lp, a_s, b_s, floor, button):
    """The click's press and release: 0.10-0.35 s apart, press shifts the line up, release back down
    (residual shift 3-8 ms after the edge); for Vol+ the press is also much stronger than the release."""
    g = edge_groups(x, int(a_s * 48000), int(b_s * 48000), floor)
    sh = [detect.residual_shift(window(lp, k)) for k, _ in g]
    best = None
    for i in range(len(g)):
        for j in range(i + 1, len(g)):
            gap = (g[j][0] - g[i][0]) / 48000
            if not 0.10 <= gap <= 0.35 or not (sh[i] > 0 > sh[j]):
                continue
            if button == "UP" and g[i][1] < 3 * g[j][1]:
                continue
            score = g[i][1] + g[j][1]
            if best is None or score > best[0]:
                best = (score, g[i][0], g[j][0])
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dataset")
    ap.add_argument("--out", default=os.path.expanduser("~/.config/applemote/templates.npz"))
    args = ap.parse_args()
    x, marks, gain_db = load_dataset(args.dataset)
    lp, _ = lowpass(x)
    floor = np.median(np.abs(np.diff(x)))
    tpl = {}
    for button, phase in (("UP", "UP_CLICK"), ("DOWN", "DOWN_CLICK")):
        P, R = [], []
        for m in (m for m in marks if m["phase"] == phase):
            p = find_pair(x, lp, m["a"] - 0.05, m["b"] + 0.9, floor, button)
            if p:
                P.append(window(lp, p[1])); R.append(window(lp, p[2]))
        print(f"{button}: {len(P)} clean press/release pairs out of {sum(1 for m in marks if m['phase'] == phase)} clicks")
        if len(P) < 4:
            sys.exit(f"too few clean {button} clicks - record again with firm, separate clicks")
        tpl[f"{button}_PRESS"] = _average(P, list(range(len(P))))
        tpl[f"{button}_RELEASE"] = _average(R, list(range(len(R))))
    cfg = effective_config(load_config())
    th = thresholds(cfg)
    print("\nReplay of the dataset with the new templates:")
    bad = report(replay(x, tpl, cfg), marks)
    if os.path.exists(args.out):
        shutil.copy2(args.out, args.out + ".bak")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    np.savez(args.out, **tpl, meta_gain_db=np.array(gain_db), meta_date=np.array(str(datetime.date.today())))
    print(f"\nsaved {args.out}" + (" (previous one: .bak)" if os.path.exists(args.out + ".bak") else ""))
    print("Make sure ~/.config/applemote/config.ini has templates = that path and boost_db/capture_db matching "
          f"{gain_db:+.1f} dB, then: systemctl --user restart applemote")
    if bad:
        print(f"\nNOTE: {bad} labelled actions did not give the expected steps - see the report above.")


if __name__ == "__main__":
    main()
