#!/bin/bash
# Run by udev (via a oneshot unit) whenever the Apple USB-C to 3.5 mm adapter (05ac:110a) (re)enumerates - it
# re-enumerates on every plug insertion/removal. Checks whether it recognised a headset WITH a mic (only then the
# remote buttons work), logs to /var/log/apple-dongle.log and shows a desktop notification to the active user.
LOG=/var/log/apple-dongle.log
user_of_seat() {   # the user of the active graphical session on seat0
  local s; s=$(loginctl show-seat seat0 -p ActiveSession --value 2>/dev/null)
  [ -n "$s" ] && loginctl show-session "$s" -p Name --value 2>/dev/null
}
notify() {
  local u uid; u=$(user_of_seat); [ -z "$u" ] && return
  uid=$(id -u "$u") || return
  runuser -u "$u" -- env DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
    notify-send -a "Apple USB-C adapter" -u "$1" -t 12000 "${TEST:+[test] }$2" "$3" 2>/dev/null
}
[ -z "$TEST" ] && sleep 4                      # let snd-usb-audio and usbhid bind first
cardno=$(awk '/USB-C to 3.5mm|Headphone Jack A/ {print $1; exit}' /proc/asound/cards)
{
  echo "=== $(date '+%F %T') ==="
  lsusb -d 05ac:110a 2>/dev/null
  lsusb -t | grep -i -E "audio|hid" | head -8
  [ -n "$cardno" ] && { echo "card $cardno:"; cat /proc/asound/card$cardno/stream0 2>/dev/null; }
} >> "$LOG" 2>&1
if [ -z "$cardno" ]; then
  notify normal "Apple adapter: no sound card" "The USB device is there but no sound card appeared. Unplug and replug the adapter. Details: $LOG"
elif grep -q "^Capture:" /proc/asound/card$cardno/stream0 2>/dev/null; then
  echo "RESULT: headset with mic" >> "$LOG"
  notify normal "Apple adapter: headset detected" "Mic and remote buttons should work. If sound goes elsewhere, pick 'USB-C to 3.5mm' as output."
else
  echo "RESULT: no mic (plug seen as 3-pole)" >> "$LOG"
  notify critical "Apple adapter does not see the mic/remote" "It took the plug for plain headphones, so the buttons stay silent. Push the plug in all the way and re-insert it. Details: $LOG"
fi
