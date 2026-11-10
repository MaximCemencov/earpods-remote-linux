# Findings (ThinkPad X1 Extreme Gen 2, Conexant CX11880, Apple EarPods)

All numbers were measured on this machine. "Unity-gain units" = captured samples divided by the total capture gain;
"LP window" = the 2 kHz low-passed 12 ms window applemote matches, baseline removed.

## 1. What the kernel exposes

* Codec `0x14f11f86` (CX11880), subsystem `17aa:229f`. The kernel Conexant driver configures headset detection via
  vendor verbs on widget `0x1c` (`0xca0` = detection status, `0xcb0` = CTIA/OMTP type) but registers **no button
  events**: the "HDA Intel PCH Mic/Headphone" input devices only carry `EV_SW` (plug switches).
* The mic pin `0x19` has no impedance-sense capability (`Pincap 0x1324: IN Detect`), so the line voltage cannot be
  measured through standard verbs.

## 2. The centre button is in a vendor register

The hwdep ioctl `HDA_IOCTL_VERB_WRITE` (`0xC0084811`, argument `nid<<24 | verb<<8 | parm`, needs `CAP_SYS_RAWIO`)
can read any verb. Widget `0x1c` answers `GET_PROC_COEF`-style verbs `0xc00..0xcf0` with 16 12-bit registers. In idle
with an EarPods headset: `0xca0 = 0x81` (bit 7: detection done), `0xcb0 = 0x800` (CTIA).

| Experiment | Result |
|---|---|
| 16 registers polled at ~600 Hz while holding each button | only `0xca0` changes: `0x81 -> 0xa1` (bit 5) while the **centre** button is held |
| all 65 536 `verb 0xc00..0xcff x parm 0..255` of `0x1c` + `0xc00..0xcf0` of `0x1b`, `0x1e`, idle vs holding Vol+ / Vol- / centre | 0 noisy registers; Vol+: 0 changes; Vol-: 0 changes; centre: only `0xca0` |
| bit 4 of `0xca0` | flickers ~110 times/s in all states, unrelated to buttons |

`headset-keys` polls `0xca0` every 15 ms while `SW_MICROPHONE_INSERT` is set. The hwdep device can only be opened by
one process at a time.

## 3. What the buttons do to the mic signal

Capture at Mic Boost +12 dB, Capture 0 dB, through PipeWire (`pw-record`, 48 kHz).

| Edge | LP window peak | residual shift 3-8 ms after the edge |
|---|---|---|
| centre press / release | saturates (raw ±20 000-35 000 of 32 768); the mic is silent while held | - |
| Vol+ press | -0.08 ... -0.14 | +0.0009 ... +0.003 |
| Vol+ release | +0.06 ... +0.07 | -0.0007 ... -0.0013 |
| Vol- press | -0.003 ... -0.005 | +0.00016 ... +0.0004 |
| Vol- release | -0.002 ... -0.004 (sometimes up to 0.03) | -0.00013 ... -0.00036 |

Consequences:

* **Which button**: by amplitude (Vol+ edges are ~20x stronger). Template shape alone sometimes assigned a Vol- edge to
  Vol+ and then rejected it.
* **Press or release**: by the sign of the residual shift (press moves the line up, release back). For Vol- the
  ringing of press and release has the *same* shape and sign, so the best-matching template mixed them up (in live use
  17 recognised Vol- releases against 99 "presses" - most releases were read as presses).
* **The remote does not repeat while held.** During a hold the line is very quiet: deviation of the low-passed signal
  from its 20 ms mean stays below 1e-4, while any release produces 3e-3 ... 6e-3. "Any line activity >= 3e-4 more than
  0.1 s after the press" is therefore used as a release signal - it catches soft releases that have no sharp edge.
  (Test: with all recognised Vol- releases removed from a recording, holds still ended exactly at the real release.)
* After a press, the next edge of the same button arrived within 2 s in 99 % (Vol-) / 100 % (Vol+) of live presses,
  median 0.14 s. The state machine treats it as the release whatever the classifier says.
* Mic noise does not change while Vol- is held (x0.9 ... x1.15), so "still held" cannot be observed directly.
* Playing the GNOME volume-change sound and a drum loop into the earphones produced no detections (no crosstalk at
  normal volume).
* Natural presses are weaker than prompted test clicks (Vol+ scale 0.23 ... 1.0 of the template), so the accepted
  amplitude range is 0.12 ... 4 x the template.

## 4. Apple USB-C to 3.5 mm adapter (05ac:110a)

* Three USB configurations. With a 4-pole headset fully inserted Linux picks configuration 2 with **three audio
  interfaces** (control, playback, capture) and two HID interfaces; if the plug is not fully seated it shows up as
  3-pole: only two audio interfaces (no capture) and **no button events at all**. The adapter re-enumerates on every
  plug change. `dongle-check` warns about the second case.
* HID report descriptor of the button interface: Consumer page, report ID 1, 4 bits: Play/Pause (`0xCD`),
  Volume Increment (`0xE9`), Volume Decrement (`0xEA`), Voice Command (`0xCF`). Raw reports: `01 01 00` play/pause,
  `01 02 00` vol+, `01 04 00` vol-, `01 00 00` release. The kernel input device has no `EV_REP`, so holding a button
  gives a single step; double/triple clicks arrive as plain Play/Pause.
* When the adapter appears, PipeWire may keep the built-in output as default - `wpctl set-default <adapter sink id>`
  once; WirePlumber remembers it and falls back automatically when the adapter is unplugged.
