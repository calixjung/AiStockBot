"""Rank Nasdaq-listed stocks by closing-cross dollar value in one ITCH 5.0 file.

Used once on the first sample day to fix a wider trading universe before any strategy result is seen.
Only common stocks and ADRs (issue classification C or A), no ETPs, production issues only.
"""

import argparse
import gzip
from struct import unpack_from

from itch_extract import number, packets


def rank(source):
    eligible, symbols, cross = set(), {}, {}
    with gzip.open(source, "rb") as stream:
        for message in packets(stream):
            kind = message[:1]
            if kind == b"R":
                locate = unpack_from(">H", message, 1)[0]
                symbols[locate] = message[11:19].decode("ascii").strip()
                if (message[19:20] in b"QGS" and message[26:27] in b"CA"
                        and message[29:30] == b"P" and message[33:34] != b"Y"):
                    eligible.add(locate)
            elif kind == b"Q" and message[39:40] == b"C":
                locate = unpack_from(">H", message, 1)[0]
                if locate in eligible:
                    cross[locate] = number(message, 11, 8) * number(message, 27, 4) / 10_000
    return sorted(((value, symbols[locate]) for locate, value in cross.items()), reverse=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--top", type=int, default=100)
    args = parser.parse_args()
    for value, symbol in rank(args.source)[:args.top]:
        print(symbol, round(value))
