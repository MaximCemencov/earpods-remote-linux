#!/bin/bash
set -uo pipefail
sudo systemctl disable --now headset-keys.service
sudo rm -f /etc/systemd/system/headset-keys.service /usr/local/libexec/headset-keys.py /etc/udev/rules.d/72-headset-keys.rules
sudo udevadm control --reload; sudo systemctl daemon-reload
