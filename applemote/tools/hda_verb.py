#!/usr/bin/env python3
"""Send raw HDA verbs through the codec hwdep node (needs root / CAP_SYS_RAWIO).

  hda_verb.py [-d /dev/snd/hwC0D0] verb NID VERB PARAM   e.g. verb 0x19 0xf07 0
  hda_verb.py [-d ...] coef IDX [VAL]                    Realtek COEF on NID 0x20
  hda_verb.py [-d ...] coefdump [NID] [COUNT]            dump COEFs (default 0x20, 142)

Same ioctl as alsa-tools' hda-verb.  12-bit verbs (0x7xx/0xfxx) take an 8-bit
PARAM; 4-bit verbs (0x2xx-0x5xx, 0xaxx-0xdxx) take a 16-bit PARAM.
"""
import argparse
import fcntl
import struct

HDA_IOCTL_VERB_WRITE = (3 << 30) | (8 << 16) | (ord("H") << 8) | 0x11
SET_COEF_INDEX, GET_PROC_COEF, SET_PROC_COEF = 0x500, 0xC00, 0x400


class Codec:
    def __init__(self, dev="/dev/snd/hwC0D0"):
        self.fd = open(dev, "rb+", buffering=0)

    def verb(self, nid, verb, param):
        buf = bytearray(struct.pack("II", (nid << 24) | (verb << 8) | param, 0))
        fcntl.ioctl(self.fd, HDA_IOCTL_VERB_WRITE, buf, True)
        return struct.unpack("II", buf)[1]

    def coef(self, idx, val=None, nid=0x20):
        self.verb(nid, SET_COEF_INDEX, idx)
        if val is not None:
            self.verb(nid, SET_PROC_COEF, val)
            self.verb(nid, SET_COEF_INDEX, idx)
        return self.verb(nid, GET_PROC_COEF, 0)

    def coefdump(self, nid=0x20, count=142):
        return [self.coef(i, nid=nid) for i in range(count)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-d", "--device", default="/dev/snd/hwC0D0")
    ap.add_argument("cmd", choices=("verb", "coef", "coefdump"))
    ap.add_argument("args", nargs="*", type=lambda s: int(s, 0))
    a = ap.parse_args()
    c = Codec(a.device)
    if a.cmd == "verb":
        print(f"0x{c.verb(*a.args):08x}")
    elif a.cmd == "coef":
        print(f"0x{c.coef(*a.args):04x}")
    else:
        for i, v in enumerate(c.coefdump(*a.args)):
            print(f"0x{i:02x} 0x{v:04x}")


if __name__ == "__main__":
    main()
