#!/usr/bin/env python3
"""Watch HDA codec registers while you press headset buttons - to check whether YOUR codec sees them.

    sudo python3 tools/hda_watch.py [--node /dev/snd/hwC0D0] [--nid 0x1c] [--seconds 30]

Reads the 16 vendor "registers" of a vendor-defined widget (verbs 0xc00..0xcf0, as used by the kernel's Conexant
driver) plus pin-sense of the mic/headphone pins, as fast as possible, and prints every change. On a Conexant
CX11880 the centre button shows up as reg 0xa (verb 0xca0) bit 5: 0x81 -> 0xa1. Stop the headset-keys service first
(the hwdep device can be opened only once):  sudo systemctl stop headset-keys
"""
import argparse, fcntl, glob, os, struct, time

IOCTL_VERB = 0xC0084811          # HDA_IOCTL_VERB_WRITE


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--node", default=(sorted(glob.glob("/dev/snd/hwC*D*")) or [None])[0])
    ap.add_argument("--nid", type=lambda s: int(s, 0), default=0x1c)
    ap.add_argument("--pins", default="0x19,0x16", help="pin widgets whose pin-sense to watch")
    ap.add_argument("--seconds", type=float, default=30)
    a = ap.parse_args()
    fd = os.open(a.node, os.O_RDWR)

    def rd(nid, verb, parm=0):
        buf = bytearray(struct.pack("II", (nid << 24) | (verb << 8) | parm, 0))
        fcntl.ioctl(fd, IOCTL_VERB, buf, True)
        return struct.unpack("II", bytes(buf))[1]
    regs = [(a.nid, 0xc00 | hi) for hi in range(0, 0x100, 0x10)] + [(int(p, 0), 0xf09) for p in a.pins.split(",")]
    last = {r: rd(*r) for r in regs}
    print(f"{a.node}: watching nid {a.nid:#x} vendor regs + pin sense of {a.pins} for {a.seconds:.0f} s - press buttons now")
    for r, v in last.items():
        print(f"   start  nid {r[0]:#04x} verb {r[1]:#05x} = {v:#x}")
    t0 = time.time(); n = 0
    while time.time() - t0 < a.seconds:
        n += 1
        for r in regs:
            v = rd(*r)
            if v != last[r]:
                print(f"{time.time() - t0:7.3f}s  nid {r[0]:#04x} verb {r[1]:#05x}: {last[r]:#x} -> {v:#x}", flush=True)
                last[r] = v
    print(f"{n} polling rounds")


if __name__ == "__main__":
    main()
