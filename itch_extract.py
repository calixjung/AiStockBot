"""Extract selected symbols from Nasdaq TotalView-ITCH 5.0 into replay CSVs."""

import argparse
from contextlib import ExitStack
import csv
import gzip
import heapq
from pathlib import Path
from struct import unpack_from
from tempfile import TemporaryDirectory
from datetime import date


def number(message, start, width):
    return int.from_bytes(message[start:start + width], "big")


def packets(stream):
    while length := stream.read(2):
        size = int.from_bytes(length, "big")
        packet = stream.read(size)
        if len(length) != 2 or len(packet) != size or size < 11:
            raise ValueError("Truncated or invalid ITCH packet")
        yield packet


class Book:
    def __init__(self):
        self.orders = {}
        self.levels = {-1: {}, 1: {}}
        self.heaps = {-1: [], 1: []}

    def add(self, reference, side, size, price):
        self.orders[reference] = side, size, price
        levels = self.levels[side]
        if price not in levels:
            heapq.heappush(self.heaps[side], -side * price)
        levels[price] = levels.get(price, 0) + size

    def remove(self, reference, quantity=None):
        side, size, price = self.orders[reference]
        removed = size if quantity is None else quantity
        if not 0 < removed <= size:
            raise ValueError("Invalid ITCH order reduction")
        levels = self.levels[side]
        levels[price] -= removed
        if not levels[price]:
            del levels[price]
        if removed == size:
            del self.orders[reference]
        else:
            self.orders[reference] = side, size - removed, price
        return side, price

    def best(self, side):
        heap, levels = self.heaps[side], self.levels[side]
        while heap and -side * heap[0] not in levels:
            heapq.heappop(heap)
        if not heap:
            return 0, 0
        price = -side * heap[0]
        return price, levels[price]


def update(book, kind, message):
    reference = number(message, 11, 8)
    if kind in (b"A", b"F"):
        book.add(reference, 1 if message[19:20] == b"B" else -1,
                 number(message, 20, 4), number(message, 32, 4))
    elif kind in (b"E", b"C", b"X"):
        size = number(message, 19, 4)
        side, price = book.remove(reference, size)
        if kind == b"E":
            return 4, reference, size, price, side
        if kind == b"C" and message[31:32] == b"Y":
            return 4, reference, size, number(message, 32, 4), side
    elif kind == b"D":
        book.remove(reference)
    elif kind == b"U":
        side, _ = book.remove(reference)
        book.add(number(message, 19, 8), side, number(message, 27, 4),
                 number(message, 31, 4))
    elif kind == b"P":
        # Nasdaq encodes B here regardless of resting side.
        return 5, 0, number(message, 20, 4), number(message, 32, 4), 0
    return 1, 0, 0, 0, 0


def extract_session(source, output, session, symbols, allow_missing=False):
    books, writers, counts = {}, {}, {}
    halted = set()
    regular_close_seen = False
    with ExitStack() as stack:
        stream = stack.enter_context(gzip.open(source, "rb"))
        for message in packets(stream):
            kind = message[:1]
            if kind == b"S" and message[11:12] == b"M":
                regular_close_seen = True
                break
            locate = unpack_from(">H", message, 1)[0]
            if kind == b"R":
                symbol = message[11:19].decode("ascii").strip()
                if symbol in symbols:
                    folder = output / session.isoformat() / symbol
                    folder.mkdir(parents=True, exist_ok=True)
                    writers[locate] = [
                        csv.writer(stack.enter_context((folder / f"{name}.csv").open("w", newline="")))
                        for name in ("message", "orderbook")
                    ]
                    counts[locate] = [symbol, 0, 0]
                    books[locate] = Book()
                continue
            if locate not in books:
                continue
            book = books[locate]
            seconds = number(message, 5, 6) / 1_000_000_000
            if kind == b"H":
                if message[19:20] == b"T":
                    halted.discard(locate)
                else:
                    halted.add(locate)
                event = (7, 0, 0, int(locate not in halted), 0)
            elif kind in (b"A", b"F", b"E", b"C", b"X", b"D", b"U", b"P"):
                event = update(book, kind, message)
            else:
                continue
            if 34200 <= seconds < 57600:
                ask, ask_size = book.best(-1)
                bid, bid_size = book.best(1)
                if locate in halted:
                    ask = bid = ask_size = bid_size = 0
                trades, quotes = writers[locate]
                trades.writerow((f"{seconds:.9f}", *event))
                quotes.writerow((ask, ask_size, bid, bid_size))
                counts[locate][1] += 1
                counts[locate][2] += event[0] in (4, 5)
    if not regular_close_seen:
        raise ValueError("ITCH file did not reach the Nasdaq end-of-market-hours event")
    missing = set(symbols) - {result[0] for result in counts.values()}
    if missing and not allow_missing:
        raise ValueError("At least one requested symbol is missing from the stock directory")
    # Symbols not listed that day (IPO later, delisted earlier) are reported, not invented.
    return list(counts.values()) + [[symbol, "missing", 0] for symbol in sorted(missing)]


def extract(source, output, session, symbols, allow_missing=False):
    output.mkdir(parents=True, exist_ok=True)
    destination = output / session.isoformat()
    if destination.exists():
        raise FileExistsError(f"Session already exists: {destination}")
    with TemporaryDirectory(dir=output) as staging:
        folder = Path(staging)
        counts = extract_session(source, folder, session, symbols, allow_missing)
        (folder / session.isoformat()).rename(destination)
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("session", type=date.fromisoformat)
    parser.add_argument("symbols", nargs="+")
    parser.add_argument("--allow-missing", action="store_true",
                        help="Skip symbols absent from that day's stock directory instead of failing.")
    args = parser.parse_args()
    for result in extract(args.source, args.output, args.session, args.symbols, args.allow_missing):
        print(*result)

