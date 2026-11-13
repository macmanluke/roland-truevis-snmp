#!/usr/bin/env python3
"""Check rolandvg.decode() against the labelled readings in samples/samples.tsv."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rolandvg import decode  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
failures = 0
with open(os.path.join(HERE, "..", "samples", "samples.tsv"), encoding="utf-8") as fh:
    for line in fh:
        if line.startswith("#") or line.startswith("label\t") or not line.strip():
            continue
        label, expected, _code, event, status = line.rstrip("\n").split("\t")
        st = decode(f"MFG:Roland;MDL:VG3-640;STATUS:{status};LASTEVENT:{event};")
        ok = st is not None and st["state"] == expected
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {expected:<14} {st['state'] if st else '-':<14} {label}")
print("all samples decode as expected" if not failures else f"{failures} failure(s)")
sys.exit(1 if failures else 0)
