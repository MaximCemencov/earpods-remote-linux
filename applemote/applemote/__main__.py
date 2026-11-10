"""applemote - Apple 3.5 mm headset remote volume buttons on Linux laptops."""
import argparse
import sys


def main():
    ap = argparse.ArgumentParser(prog="applemote", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="detect remote buttons (what the service runs)")
    run.add_argument("-v", "--verbose", action="store_true", help="log releases and near misses")
    sub.add_parser("setup", help="detect the headset mic and controls, write the config")
    cal = sub.add_parser("calibrate", help="record your remote and build templates")
    cal.add_argument("--presses", type=int, default=8, help="presses per button (default 8)")
    cal.add_argument("--save-wav", metavar="FILE", help="also save the recording (for bug reports)")
    cal.add_argument("--wav", metavar="FILE", help="learn from a saved recording instead")
    cal.add_argument("--up", metavar="T0-T1", help="with --wav: seconds holding Vol+ presses")
    cal.add_argument("--down", metavar="T0-T1", help="with --wav: seconds holding Vol- presses")
    cal.add_argument("--gain-db", type=float, default=0.0, help="with --wav: capture gain used")
    args = ap.parse_args()

    if args.cmd == "run":
        from . import daemon
        return daemon.main(args)
    if args.cmd == "setup":
        from . import setup
        return setup.main(args)
    from . import calibrate
    return calibrate.main(args)


if __name__ == "__main__":
    sys.exit(main())
