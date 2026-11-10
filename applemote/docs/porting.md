# Porting to other machines and codecs

The detector is generic, but the button signatures depend on the headset, the
laptop's mic circuitry and any processing in the capture path. So each new
machine or headset needs its own templates. This page goes from "does it
apply at all?" to "send us your templates".

## 1. Find the pieces

```sh
applemote setup
```

This finds the sound card with a headset-mic jack, the PipeWire source for the
headset mic, the gain controls, and an input device that already has volume
keys (otherwise it falls back to `uinput`). Check the output. If the source is
wrong, edit `source` in `~/.config/applemote/config.ini`; `pactl list sources
short` lists the alternatives.

## 2. Check whether you need applemote at all

Some codecs decode the buttons themselves. Watch the jack input device while
pressing Vol+ / Vol−:

```sh
sudo libinput debug-events --device /dev/input/eventN   # the key device from setup
```

If `KEY_VOLUMEUP` / `KEY_VOLUMEDOWN` already appear, you're done.

On **HDA codecs**, also check whether the codec *sees* the buttons but nobody
maps them. That calls for a kernel quirk rather than applemote:

```sh
sudo tools/codec_monitor.py 30 buttons.log   # press each button a few times
```

Look for `UNSOL` lines or `COEF` changes that line up with presses. On Realtek
codecs, headset-button events arrive as unsolicited responses from a hidden
node (0x55) and are decoded in `alc_headset_btn_callback()`
(`sound/hda/codecs/realtek/alc269.c`). If you see new bits there, open an issue
with the log. `tools/hda_verb.py` reads and writes single verbs and COEFs.

## 3. Calibrate

```sh
applemote calibrate --save-wav calib.wav
```

The tool:
1. pins the headset-mic gain to 0 dB, as the daemon does;
2. records 3 s of quiet, then your Vol+ presses, then your Vol− presses;
3. finds every fast edge, groups the strongest ones into "press" and "release"
   shapes per button, and averages them into templates;
4. replays the recording through the detector and refuses to save unless every
   segment gives only the right key.

Reading the output:

```
learned: UP: 8 presses / 8 releases from 23 edges, DOWN: 8 presses / 8 releases from 19 edges
  quiet -> Vol+  0   Vol-  0
  UP    -> Vol+  8   Vol-  0
  DOWN  -> Vol+  0   Vol-  8
```

| Symptom | Likely cause |
|---|---|
| `only N edges found` | Wrong source, or the gain controls don't exist (`boost_control` / `capture_control` empty) so the signal is tiny. Check `amixer -c N scontrols`. |
| clusters too small | Presses too soft or too fast. Press firmly, about once a second. |
| wrong-direction keys | Vol+ and Vol− look alike on your hardware. Re-run once; if it persists, open an issue with `calib.wav`. |
| works in calibration, misses live | Something else changes the capture path (an EQ, or different gains). Run `applemote run -v` and look at the near misses. |

`applemote calibrate --wav calib.wav --up 4-20 --down 21-38` re-learns from a
saved recording (times in seconds) without recording again.

## 4. Contribute

If it works, please open a PR that:
- adds `templates/<vendor>-<model>-<codec>-<headset>.npz` (copy
  `~/.config/applemote/templates.npz`);
- adds the DMI pair (`/sys/class/dmi/id/sys_vendor`, `product_name`) to `KNOWN`
  in `applemote/setup.py`;
- adds a row to the status table in the README.

## How it was worked out (Lenovo 83DJ, ALC287)

- **The codec is blind to the volume buttons.** The ALC287's headset-button
  detector (COEF 0x48/0x49/0x44, unsolicited events on NID 0x55) reports the
  centre button as `KEY_PLAYPAUSE`. Volume presses produced no unsolicited
  events and no change in any of the 142 COEFs.
- **The mic line shows them anyway.** While a volume button is held, the remote
  shifts the mic line slightly. After the codec's AC coupling, each press and
  release is a fast edge plus a ~5 ms tail: about −25 dBFS for Vol+ and −47 dBFS
  for Vol− at unity gain. Releases mirror presses, and holding a button doesn't
  repeat. The mic sits inside the remote, so each press also makes an acoustic
  click; low-passing at 2 kHz before matching removes most of it.
- **Gain matters.** The default headset gain on this laptop is +60 dB (boost +30,
  ADC +30). Vol+ transients then clip inside the codec, before the DSP's EQ,
  and the shape is lost. Hence the gain borrowing.
- **What Apple's codecs do differently.** The CS42L83 transmits an
  identification request on the mic bias, waits for the remote's acknowledgment
  and three ID symbols, then switches its comparator to a "buttons" threshold
  and decodes volume pulses in hardware (see the PR linked in the README). PC
  codecs have no way to transmit that request; their mic bias is a handful of
  fixed levels.

## Configuration reference

`~/.config/applemote/config.ini`:

| key | meaning |
|---|---|
| `card` | ALSA card number |
| `source` | PipeWire source name of the headset mic |
| `jack_control` | ALSA jack control telling whether a headset mic is plugged in |
| `boost_control`, `capture_control` | gain controls pinned while listening (empty = leave alone) |
| `boost_db`, `capture_db` | the pinned gains (must match the calibration) |
| `key_device` | input device name to write keys into, or `uinput` |
| `templates` | template file (`.npz`) |
