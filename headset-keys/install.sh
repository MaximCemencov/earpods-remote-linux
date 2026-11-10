#!/bin/bash
# Install / update the headset-keys system service (needs sudo). Idempotent.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
sudo install -d /usr/local/libexec
sudo install -m 755 "$here/headset-keys.py" /usr/local/libexec/headset-keys.py
sudo install -m 644 "$here/headset-keys.service" /etc/systemd/system/headset-keys.service
sudo install -m 644 "$here/72-headset-keys.rules" /etc/udev/rules.d/72-headset-keys.rules
command -v restorecon >/dev/null && sudo restorecon /usr/local/libexec/headset-keys.py /etc/systemd/system/headset-keys.service || true
sudo udevadm control --reload
sudo systemctl daemon-reload
sudo systemctl enable headset-keys.service
sudo systemctl restart headset-keys.service
echo "headset-keys: $(systemctl is-active headset-keys)   (logs: journalctl -u headset-keys -f)"
