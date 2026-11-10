"""applemote setup: find the headset mic, its controls and a key device; write the config."""
import os
import re
from pathlib import Path

from . import system
from .system import CONFIG_DIR, DATA_DIR

# Machines with shipped templates: (sys_vendor, product_name) -> file in templates/
KNOWN = {("LENOVO", "83DJ"): "lenovo-83dj-alc287-earpods.npz"}


def find_card():
    """First card with a mic jack control; prefer a plugged-in jack, then 'Headset Mic Jack'."""
    found = []
    for d in sorted(Path("/proc/asound").glob("card[0-9]*")):
        card = d.name[4:]
        mixer = system.Mixer(card)
        for name in re.findall(r"iface=CARD,name='([^']*Mic Jack)'", mixer.controls()):
            found.append((not mixer.jack(name), name != "Headset Mic Jack", card, name))
    return sorted(found)[0][2:] if found else (None, None)


def find_source(card):
    cands = []
    for s in system.pactl_json("list", "sources"):
        props = s.get("properties", {})
        if not s.get("name", "").startswith("alsa_input") or props.get("alsa.card") != card:
            continue
        text = " ".join([s.get("description", "")] + [p.get("name", "") + " " + p.get("description", "")
                                                       for p in s.get("ports", [])]).lower()
        if "digital microphone" in text or "dmic" in text:
            continue
        rank = 0 if "headset" in text else 1 if "mic2" in text else 2
        cands.append((rank, s["name"], s.get("description", "")))
    return sorted(cands)


def find_key_device(card):
    for node, name, sysfs, bits in system.input_devices():
        if f"/sound/card{card}/" in sysfs and system.has_volume_keys(bits):
            return node, name
    return None, "uinput"


def find_templates():
    if (CONFIG_DIR / "templates.npz").exists():
        return str(CONFIG_DIR / "templates.npz")
    try:
        dmi = tuple(Path(f"/sys/class/dmi/id/{f}").read_text().strip()
                    for f in ("sys_vendor", "product_name"))
    except OSError:
        return ""
    return str(DATA_DIR / KNOWN[dmi]) if dmi in KNOWN else ""


def main(args):
    cfg = system.load_config()
    card, jack = find_card()
    if card is None:
        print("No sound card with a 'Mic Jack' / 'Headset Mic Jack' control found.")
        return 1
    mixer = system.Mixer(card)
    scontrols = mixer.scontrols()
    boost = next((c for c in (jack.replace(" Jack", " Boost"), "Headset Mic Boost", "Mic Boost")
                  if c in scontrols), "")
    capture = "Capture" if "Capture" in scontrols else ""
    sources = find_source(card)
    node, key_device = find_key_device(card)
    cfg.update(card=card, jack_control=jack, boost_control=boost, capture_control=capture,
               key_device=key_device, templates=find_templates())
    if sources:
        cfg["source"] = sources[0][1]

    print(f"card            {card}")
    print(f"jack control    {jack}  (plugged: {mixer.jack(jack)})")
    print(f"boost control   {boost or '(none)'}")
    print(f"capture control {capture or '(none)'}")
    print(f"headset source  {cfg['source'] or '(none found - plug in the headset and re-run)'}")
    for _, name, desc in sources[1:]:
        print(f"   other option {name} ({desc})")
    print(f"key device      {key_device}" + (f" ({node})" if node else ""))
    print(f"templates       {cfg['templates'] or '(none - run: applemote calibrate)'}")

    if node and not os.access(node, os.W_OK):
        print(f"\n! {node} is not writable: run 'make install' (udev rule) and log in again.")
    if not node and not os.access("/dev/uinput", os.W_OK):
        print("\n! This card's jack device has no volume keys, so keys go through /dev/uinput,")
        print("  which is not writable. Run 'make install-uinput' and log in again.")
    system.save_config(cfg)
    print(f"\nwrote {system.CONFIG_FILE}")
    if not cfg["templates"]:
        print("next: applemote calibrate")
    return 0
