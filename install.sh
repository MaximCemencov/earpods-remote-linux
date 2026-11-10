#!/bin/bash
# Install everything (idempotent):
#   1. headset-keys   - system service (sudo): centre button via the Conexant CX11880 register, Apple adapter bridge,
#                       and the "Headset remote keys" virtual keyboard
#   2. applemote      - user service: Vol+/Vol- from the mic line (patched copy bundled in ./applemote)
#   3. dongle-check   - optional (--with-dongle-check): notification when the Apple USB-C adapter misses the mic
# Usage: ./install.sh [--with-dongle-check]
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)

echo "== checking dependencies"
missing=()
for c in python3 amixer pw-record pactl notify-send; do command -v "$c" >/dev/null || missing+=("$c"); done
python3 -c "import numpy, scipy" 2>/dev/null || missing+=("python3-numpy/python3-scipy")
if ((${#missing[@]})); then
  echo "missing: ${missing[*]}"
  echo "  Fedora:        sudo dnf install python3-numpy python3-scipy alsa-utils pipewire-utils pulseaudio-utils libnotify"
  echo "  Debian/Ubuntu: sudo apt install python3-numpy python3-scipy alsa-utils pipewire-bin pulseaudio-utils libnotify-bin"
  exit 1
fi

echo "== 1/3 headset-keys (system service, sudo)"
grep -qs "Vendor Id: 0x14f11f86" /proc/asound/card*/codec#* \
  || echo "   note: no Conexant CX11880 codec found - the centre-button part will stay idle (the Apple adapter bridge still works)"
"$here/headset-keys/install.sh"

echo "== 2/3 applemote (user service)"
PREFIX=$HOME/.local; LIBDIR=$PREFIX/share/applemote
install -d "$LIBDIR/applemote" "$LIBDIR/templates" "$PREFIX/bin" "$HOME/.config/systemd/user" "$HOME/.config/applemote"
install -m644 "$here"/applemote/applemote/*.py "$LIBDIR/applemote/"
install -m644 "$here"/applemote/templates/*.npz "$here"/templates/*.npz "$LIBDIR/templates/"
printf '#!/bin/sh\nPYTHONPATH="%s${PYTHONPATH:+:$PYTHONPATH}" exec python3 -P -m applemote "$@"\n' "$LIBDIR" > "$PREFIX/bin/applemote"
chmod 755 "$PREFIX/bin/applemote"
sed "s|@BINDIR@|$PREFIX/bin|" "$here/applemote/contrib/applemote.service" > "$HOME/.config/systemd/user/applemote.service"
systemctl --user daemon-reload
"$PREFIX/bin/applemote" setup >/dev/null || true          # finds card, jack control, gain controls, headset source
cfg=$HOME/.config/applemote/config.ini
tpl=""
if grep -qs "X1 Extreme 2nd" /sys/class/dmi/id/product_version && grep -qs "Vendor Id: 0x14f11f86" /proc/asound/card*/codec#*; then
  tpl="$LIBDIR/templates/thinkpad-x1e2-cx11880-earpods.npz"
fi
[ -f "$HOME/.config/applemote/templates.npz" ] && tpl="$HOME/.config/applemote/templates.npz"   # your own calibration wins
classifier=template
grep -qs "Vendor Id: 0x14f11f86" /proc/asound/card*/codec#* && classifier=split   # rules tuned on CX11880
python3 - "$cfg" "$tpl" "$classifier" <<'EOF'
import configparser, sys
path, tpl, classifier = sys.argv[1], sys.argv[2], sys.argv[3]
cp = configparser.ConfigParser(); cp.read(path)
if not cp.has_section("applemote"): cp.add_section("applemote")
c = cp["applemote"]
c["key_device"] = "Headset remote keys"          # the virtual keyboard of headset-keys (no /dev/uinput access needed)
# the operating point the bundled templates and tools/collect.py use (applemote's own defaults differ)
c.update(boost_db="12", capture_db="0", ncc_up="0.85", ncc_down="0.70", classifier=classifier)
c.setdefault("debug", "0")
if tpl: c["templates"] = tpl
cp.write(open(path, "w"))
print("   config:", path, "| classifier:", classifier, "| templates:", c.get("templates") or "(none - calibrate, see docs/calibration.md)")
EOF
if grep -qE "^templates = .+" "$cfg"; then
  systemctl --user enable --now applemote
  systemctl --user restart applemote
  echo "   applemote: $(systemctl --user is-active applemote)   (logs: journalctl --user -u applemote -f)"
else
  echo "   applemote is installed but NOT enabled: record and learn templates first (docs/calibration.md),"
  echo "   then: systemctl --user enable --now applemote"
fi

if [[ "${1:-}" == "--with-dongle-check" ]]; then
  echo "== 3/3 dongle-check (sudo)"
  "$here/dongle-check/install.sh"
else
  echo "== 3/3 dongle-check skipped (add --with-dongle-check to install it)"
fi
echo "Done. Centre button: 1 click play/pause, 2 next, 3 previous. Vol+/Vol-: click = 1 step, hold = repeat."
