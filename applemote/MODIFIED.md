# Modified copy of applemote

This directory is a copy of [RyanTheTide/applemote](https://github.com/RyanTheTide/applemote) (MIT, see `LICENSE`,
original README in `README.upstream.md`) with local changes made for the Conexant CX11880 / EarPods setup described in
the top-level README. The full diff against upstream is `../patches/applemote-local.patch`. Summary:

* `system.py` — lift a user mute of the headset source while listening (and restore it, also after a crash);
  per-class thresholds `ncc_up` / `ncc_down` and `debug` in the config.
* `detect.py` — the template only identifies the *button* (by amplitude: Vol+ edges are ~20x stronger than Vol-);
  press vs release is decided by the sign of the residual DC shift 3-8 ms after the edge; a per-5-ms "line activity"
  measure for robust release detection; wider amplitude range (soft presses); optional edge logging for retraining.
* `daemon.py` — a per-button state machine: after a press, the next edge of the same button (or any line activity)
  is its release, so a click is always exactly one step, fast clicks all count, and a hold (no edge for 0.45 s)
  repeats until released (cap 2.5 s). Larger capture pipe buffer; debug edge logging in a background thread.

The rules tuned on the CX11880 (button by amplitude, press/release by residual shift) are active only with
`classifier = split` in the config (the top-level installer sets it when it finds that codec). With the default
`classifier = template` the best-matching template decides, as upstream; `classify()`, `process()` events, upstream
calibration and the upstream tests (`python3 -m unittest discover -s tests`, all pass) keep the original behaviour.
