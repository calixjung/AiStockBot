"""Count GDELT GKG news per 15-minute file: articles and average tone per company, plus market news.

Output CSV columns: stamp (file time, UTC YYYYMMDDHHMMSS), key (ticker or MARKET), articles, tone_sum.
A company is matched through `data/company_names.tsv`; MARKET is any article with theme ECON_STOCKMARKET.
"""

import argparse
import csv
import io
from pathlib import Path
import zipfile

ORGANIZATIONS, THEMES, TONE = 13, 7, 15


def load_names(path):
    by_first_word = {}
    with open(path, encoding="utf-8") as handle:
        next(handle)
        for line in handle:
            ticker, names = line.rstrip("\r\n").split("\t")
            for name in names.split("|"):
                by_first_word.setdefault(name.split()[0], []).append((name, ticker))
    return by_first_word


def tickers_in(organizations, by_first_word):
    found = set()
    for organization in organizations.split(";"):
        words = organization.split()
        for name, ticker in by_first_word.get(words[0], ()) if words else ():
            if organization == name or organization.startswith(name + " "):
                found.add(ticker)
    return found


def count_file(path, by_first_word):
    totals = {}
    with zipfile.ZipFile(path) as archive:
        with archive.open(archive.namelist()[0]) as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8", errors="replace"):
                fields = line.split("\t")
                if len(fields) <= TONE or not fields[TONE]:
                    continue
                try:
                    tone = float(fields[TONE].split(",")[0])
                except ValueError:
                    continue
                keys = tickers_in(fields[ORGANIZATIONS], by_first_word)
                if "ECON_STOCKMARKET" in fields[THEMES]:
                    keys.add("MARKET")
                for key in keys:
                    articles, tone_sum = totals.get(key, (0, 0.0))
                    totals[key] = (articles + 1, tone_sum + tone)
    return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("day_folder", type=Path, help="Folder of one day's *.gkg.csv.zip files")
    parser.add_argument("output", type=Path)
    parser.add_argument("--names", type=Path, default=Path(__file__).resolve().parent / "data" / "company_names.tsv")
    args = parser.parse_args()
    by_first_word = load_names(args.names)
    with open(args.output, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("stamp", "key", "articles", "tone_sum"))
        for path in sorted(args.day_folder.glob("*.gkg.csv.zip")):
            for key, (articles, tone_sum) in sorted(count_file(path, by_first_word).items()):
                writer.writerow((path.name[:14], key, articles, round(tone_sum, 3)))
