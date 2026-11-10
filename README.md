# earpods-remote-linux

Make the **remote buttons of Apple EarPods (3.5 mm)** work on a Linux laptop: play/pause, next/previous track,
volume up/down with click *and* hold. Built and tested on a **ThinkPad X1 Extreme Gen 2** (Conexant **CX11880**
codec), plus a bridge for the **Apple USB-C to 3.5 mm adapter** (`05ac:110a`).

| Button | Laptop jack | Apple USB-C adapter |
|---|---|---|
| Centre: 1 / 2 / 3 clicks | play-pause / next / previous | play-pause / next / previous |
| Vol+ / Vol- click | one volume step | one volume step |
| Vol+ / Vol- hold | repeats while held (after 0.45 s) | repeats while held (after 0.45 s) |

## Why this is needed

* The kernel's Conexant HDA driver has **no headset-button support** (Realtek codecs have it). The jack input devices
  only report plug/unplug switches, so desktop environments never see any button.
* The codec **does** see the centre button: vendor widget `0x1c`, register `0xa` (`verb 0xca0`), bit 5 goes
  `0x81 -> 0xa1` while the mic line is shorted. Nothing else in the codec reacts to any button
  (all 65 536 vendor verb/parameter combinations of that widget were scanned while holding each button).
* **Vol+ / Vol- are invisible to every PC codec**: Apple's remote expects an identification handshake on the mic bias
  that only Apple's own codecs perform. Unaddressed, the remote still nudges the mic line on every press and release,
  leaving a short repeatable waveform in the mic signal - that is what [applemote](https://github.com/RyanTheTide/applemote)
  detects. On this machine its stock detection did not work well (Vol- is ~20x weaker than Vol+, its press and release
  look alike, soft presses were rejected), so a patched copy is bundled. See [docs/findings.md](docs/findings.md).
* The Apple USB-C adapter exposes the buttons as normal HID keys (works out of the box), but has no key autorepeat
  and nobody decodes double/triple clicks; the bridge adds both.

## How it works

```
 laptop jack ──► codec register 0xca0 bit 5 ──► headset-keys (root service) ─┐
 Apple adapter (HID, grabbed) ───────────────► headset-keys ──────────────────┼─► virtual keyboard ──► desktop media keys
 headset mic ──► PipeWire ──► applemote (user service, Vol+/Vol-) ───────────┘   "Headset remote keys"
```

* `headset-keys/` - system service. Polls the codec register only while a headset with a mic is plugged in, decodes
  click series, grabs the Apple adapter's button device and re-emits its keys with click counting and hold-to-repeat.
  Creates the uinput keyboard "Headset remote keys"; a udev rule lets *only that device* be written by your session.
* `applemote/` - patched copy of applemote (MIT): template matching picks the button by amplitude, press vs release by
  the sign of the residual DC shift, and a per-button state machine turns edges into steps (a click is always exactly
  one step; a hold repeats until the next edge or any "line activity"). Details in `applemote/MODIFIED.md`.
  The CX11880-tuned rules are enabled with `classifier = split` in `~/.config/applemote/config.ini` (the installer
  sets it only when it finds that codec); elsewhere `classifier = template` keeps applemote's original classification
  and only adds the state machine.
* `dongle-check/` - optional: a desktop notification when the Apple adapter mistakes the EarPods for 3-pole headphones
  (then it exposes no mic and no buttons - the plug is not fully inserted).
* `tools/` - calibration and diagnostics (record a labelled dataset, learn templates, replay/evaluate, watch codec registers).

## Install

Requirements: PipeWire, Python 3.11+ with NumPy and SciPy, `amixer`, `pw-record`, `pactl`, `notify-send`.

```sh
# Fedora:        sudo dnf install python3-numpy python3-scipy alsa-utils pipewire-utils pulseaudio-utils libnotify
# Debian/Ubuntu: sudo apt install python3-numpy python3-scipy alsa-utils pipewire-bin pulseaudio-utils libnotify-bin
git clone https://github.com/MaximCemencov/earpods-remote-linux
cd earpods-remote-linux
./install.sh                    # add --with-dongle-check for the Apple adapter notification
```

Plug the EarPods into the laptop jack before running it (the installer asks applemote to find the headset mic).
On a ThinkPad X1 Extreme Gen 2 with CX11880 the bundled templates are used directly. Anywhere else - or with a
different headset - calibrate Vol+/Vol- (2-3 minutes): see [docs/calibration.md](docs/calibration.md).

Check it:

```sh
journalctl -u headset-keys -f          # centre button and adapter events
journalctl --user -u applemote -f      # Vol+/Vol- detections
```

Uninstall: `headset-keys/uninstall.sh`, `systemctl --user disable --now applemote`,
`rm -rf ~/.local/share/applemote ~/.local/bin/applemote ~/.config/applemote`.

## What it changes, privacy

* **The headset mic is listened to** while a headset with a mic is plugged into the jack *and* no other app records
  from it (GNOME/KDE show the mic indicator). Audio is analysed in memory and never stored or sent anywhere. When a call
  or recording starts, applemote steps aside until it ends.
* While listening, the headset-mic gain is pinned (Mic Boost +12 dB, Capture 0 dB) and a **muted source is unmuted**;
  both are restored when listening stops (and after a crash).
* `headset-keys` runs as root because the codec register is read through the ALSA hwdep interface (`CAP_SYS_RAWIO`).
  It polls only while a headset is plugged in; otherwise it sleeps.
* No kernel module is needed - works with Secure Boot / lockdown.

## Limitations

* Vol+/Vol- templates are specific to a laptop *and* a headset: recalibrate for another one.
* Vol- is a weak signal. Very soft presses can still be missed; if a release goes undetected, a hold stops by itself
  after 2.5 s. A noisy environment (talking into the mic) can end a hold early - by design, that is the safe side.
* The codec part targets the Conexant CX11880 (`0x14f11f86`). The SN6140 (`0x14f11f87`) is likely identical but
  untested: check with `sudo tools/hda_watch.py` and add its ID to `CODEC_IDS` in `headset-keys.py`.
  Realtek laptops usually get the centre button from the kernel already - use only the applemote part there.

## Tested on

Fedora 45, kernel 7.3, PipeWire 1.6, GNOME; ThinkPad X1 Extreme Gen 2 (Conexant CX11880, subsystem `17aa:229f`);
Apple EarPods with Remote and Mic (3.5 mm); Apple USB-C to 3.5 mm Headphone Jack Adapter (`05ac:110a`).

## Credits

* [RyanTheTide/applemote](https://github.com/RyanTheTide/applemote) - the idea and the original detector (MIT).
* [Doug Brown: using Apple EarPods with a Windows or Linux PC](https://www.downtowndougbrown.com/2020/09/using-apple-earpods-with-a-windows-or-linux-pc-working-mic-and-buttons/) - the Apple USB-C adapter route.
* The Linux kernel Conexant HDA driver (`sound/hda/codecs/conexant.c`) - the vendor-verb access pattern (`0xca0`, `0xcb0`).

## License

MIT (see `LICENSE`). The bundled `applemote/` keeps its own MIT license (`applemote/LICENSE`).
