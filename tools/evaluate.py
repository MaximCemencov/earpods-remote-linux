#!/usr/bin/env python3
"""Replay a dataset recorded with tools/collect.py through the detector + button state machine and report.

    python3 tools/evaluate.py DATASET_DIR [--templates ~/.config/applemote/templates.npz]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import argparse, os, sys
from _common import load_dataset, load_templates, load_config, thresholds, effective_config, replay, report


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dataset")
    ap.add_argument("--templates", default=os.path.expanduser("~/.config/applemote/templates.npz"))
    args = ap.parse_args()
    x, marks, gain_db = load_dataset(args.dataset)
    cfg = effective_config(load_config())
    th = thresholds(cfg)
    print(f"dataset: {len(x) / 48000:.0f} s, gain {gain_db:+.1f} dB, thresholds Vol+ {th['UP_PRESS']} Vol- {th['DOWN_PRESS']}")
    sys.exit(1 if report(replay(x, load_templates(args.templates), cfg), marks) else 0)


if __name__ == "__main__":
    main()
