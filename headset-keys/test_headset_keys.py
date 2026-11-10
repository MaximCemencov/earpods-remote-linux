#!/usr/bin/env python3
"""Unit tests for the click decoder, hold-to-repeat and the adapter bridge (simulated time, no hardware)."""
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("hk", os.path.join(os.path.dirname(os.path.abspath(__file__)), "headset-keys.py"))
hk = importlib.util.module_from_spec(spec); spec.loader.exec_module(hk)
hk.log = lambda *_: None
N = {hk.KEY_PLAYPAUSE: "PLAYPAUSE", hk.KEY_NEXT: "NEXT", hk.KEY_PREV: "PREV"}

def clicks(segments, tail=1.0, step=0.015):
    d = hk.ClickDecoder(); t = 0.0; out = []
    for pressed, dur in segments + [(False, tail)]:
        end = t + dur
        while t < end:
            out += [N[k] for _, k in d.feed(pressed, t)]; t += step
    return out

class FakeUin:
    def __init__(self): self.taps = []; self.now = 0.0
    def tap(self, code): self.taps.append((round(self.now, 2), code))

def bridge(events, until, step=0.015):
    u = FakeUin(); b = hk.DongleBridge(u); ev = sorted(events); i = 0; t = 0.0
    while t < until:
        u.now = t
        while i < len(ev) and ev[i][0] <= t: b.on_key(ev[i][1], ev[i][2], t); i += 1
        b.tick(t); t += step
    return u.taps

UP, DN, PP = hk.KEY_VOLUP, hk.KEY_VOLDOWN, hk.KEY_PLAYPAUSE
count = lambda taps, code: sum(1 for _, c in taps if c == code)
checks = [
    ("1 click = Play/Pause", clicks([(False, .5), (True, .10)]) == ["PLAYPAUSE"]),
    ("1 slow click (0.8 s) = Play/Pause", clicks([(False, .5), (True, .80)]) == ["PLAYPAUSE"]),
    ("2 clicks = next", clicks([(False, .5), (True, .08), (False, .15), (True, .08)]) == ["NEXT"]),
    ("3 clicks = previous", clicks([(False, .5), (True, .08), (False, .12), (True, .08), (False, .12), (True, .08)]) == ["PREV"]),
    ("hold 1.5 s = nothing", clicks([(False, .5), (True, 1.5)]) == []),
    ("10 ms bounce = nothing", clicks([(False, .5), (True, .01), (False, .02), (True, .01), (False, 1.0)]) == []),
    ("adapter: short Vol+ click = 1 step", count(bridge([(0.1, UP, 1), (0.25, UP, 0)], 2.0), UP) == 1),
    ("adapter: Vol+ held 1.5 s = 9..14 steps", 9 <= count(bridge([(0.1, UP, 1), (1.6, UP, 0)], 3.0), UP) <= 14),
    ("adapter: lost release stops by HOLD_MAX", max(t for t, _ in bridge([(0.1, UP, 1)], 8.0)) <= hk.HOLD_MAX + 0.6),
    ("adapter: 2 centre clicks = next", [c for _, c in bridge([(0.1, PP, 1), (0.18, PP, 0), (0.3, PP, 1), (0.38, PP, 0)], 2.0)] == [hk.KEY_NEXT]),
]
bad = 0
for name, ok in checks:
    print(("ok  " if ok else "FAIL"), name); bad += not ok
sys.exit(1 if bad else 0)
