"""Generate a background-message profile (JSON) from a capture.

    python tools/build_baseline.py reference_logs/Q50_Dumps/idle.pcapng \
        --out profiles/main_idle_baseline.json --name "Q50 engine idle (real car)" \
        --start 30 --end 150
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from q50tb.capture.formats import load_capture  # noqa: E402
from q50tb.sim.baseline_builder import build_messages  # noqa: E402
from q50tb.signals.q50_main import METER_ORIGIN_MAIN, build_database  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("capture")
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--bus", default="main")
    ap.add_argument("--interface", default=None, help="pcapng interface name to use")
    ap.add_argument("--start", type=float, default=None)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--exclude", default="", help="extra hex IDs to exclude, comma separated")
    args = ap.parse_args()

    cap = load_capture(args.capture)
    exclude = set(METER_ORIGIN_MAIN) | set(range(0x700, 0x800))
    exclude |= {int(x, 16) for x in args.exclude.split(",") if x.strip()}
    window = (args.start or 0.0, args.end or 1e12) if (args.start or args.end) else None
    msgs = build_messages(cap, interface=args.interface, bus=args.bus, exclude=exclude,
                          db=build_database(), window=window)
    data = {"name": args.name or os.path.basename(args.capture), "format": "q50tb-background-1",
            "source": os.path.basename(args.capture), "bus": args.bus,
            "messages": [m.to_json() for m in msgs]}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    print(f"wrote {len(msgs)} messages to {args.out}")
    for m in msgs:
        extra = []
        if m.counters:
            extra.append("ctr " + ",".join(f"B{c.byte}[{c.lsb + c.width - 1}:{c.lsb}]" for c in m.counters))
        if m.checksums:
            extra.append("cs " + ",".join(f"B{c.byte}={c.kind}+{c.add:02X}" for c in m.checksums))
        if m.sequence:
            extra.append(f"seq{len(m.sequence)}")
        print(f"  {m.arbitration_id:03X} {m.period_ms:6.0f}ms [{m.base.hex(' ').upper()}] {'; '.join(extra)}")


if __name__ == "__main__":
    main()
