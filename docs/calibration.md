# Calibrating Vol+ / Vol- for your laptop and headset

The Vol+/Vol- signatures depend on the laptop's mic circuitry, the capture path and the headset, so a new machine or
a different headset needs its own templates. It takes 2-3 minutes in a quiet room.

1. Install (`./install.sh`) with the headset plugged into the laptop jack. `~/.config/applemote/config.ini` must name
   the headset source (`applemote setup` finds it) and use `boost_db = 12`, `capture_db = 0` (the installer sets them).
2. Record a labelled dataset - the screen tells you what to do (bright box = act, grey = hands off):

   ```sh
   python3 tools/collect.py                # -> ~/.local/state/applemote/collect/
   ```

   Sections: quiet, 10 Vol+ clicks, 12 Vol- clicks, Vol+ and Vol- holds, fast Vol- series, centre clicks.
   Press the buttons the way you normally do. The applemote service is paused meanwhile and restored afterwards.
3. Learn templates and see how they perform on the same recording:

   ```sh
   python3 tools/learn_templates.py ~/.local/state/applemote/collect
   ```

   It prints, per labelled action, how many Vol+/Vol- steps the real detector + state machine would produce, e.g.

   ```
     UP_CLICK    (Vol+/Vol- steps per action)  1/0 1/0 1/0 ...
     DOWN_CLICK  (Vol+/Vol- steps per action)  0/1 0/1 0/1 ...
     DOWN_HOLD   (Vol+/Vol- steps per action)  0/16 0/17 ...
     actions outside expectation (marked '!'): 0 of 35
   ```

   and saves `~/.config/applemote/templates.npz` (the previous file is kept as `.bak`).
4. `systemctl --user restart applemote`.

`python3 tools/evaluate.py DATASET [--templates FILE]` replays any recorded dataset with any template file - useful
to compare thresholds (`ncc_up`, `ncc_down` in the config) before changing the live service.

## Tuning notes

* `ncc_down` (Vol-) below ~0.65 starts producing false steps on silence; 0.70 is a good default. `ncc_up` 0.85.
* If clicks are missed but the report shows `!` only for soft presses, press a little more firmly while recording -
  templates learned from clean clicks generalise better than templates learned from weak ones.
* Debugging live behaviour: set `debug = 1` in the config; windows around every strong edge are saved (in a
  background thread) to `~/.local/state/applemote/debug/edges-*.npz` with a wall-clock reference. Turn it off again
  afterwards - these are snippets of the mic signal.

## Is my codec seeing the centre button?

```sh
sudo systemctl stop headset-keys        # the hwdep device can be opened only once
sudo python3 tools/hda_watch.py --seconds 20
sudo systemctl start headset-keys
```

Press and hold the centre button: on a CX11880 `nid 0x1c verb 0xca0` goes `0x81 -> 0xa1`. On other HDA codecs try
other vendor widgets (`--nid`), see `/proc/asound/card*/codec#*` for "Vendor Defined Widget" nodes.
