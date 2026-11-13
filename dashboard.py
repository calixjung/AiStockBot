"""Run the paper replay and open its generated dashboard."""

import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import webbrowser

from demo import replay
from historical import run as run_historical


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Path for the generated HTML")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--historical", type=Path, help="LOBSTER data folder")
    parser.add_argument("--intraday-reference", action="store_true")
    parser.add_argument("--utc-offset", type=int, choices=(-5, -4), default=-4)
    args = parser.parse_args()

    log = io.StringIO()
    with redirect_stdout(log):
        if args.historical:
            report = run_historical(args.historical, offset=args.utc_offset,
                                    intraday_reference=args.intraday_reference)
        else:
            report = {"summary": replay()}
    events = [json.loads(line) for line in log.getvalue().splitlines()]
    payload = json.dumps({**report, "events": events}).replace("<", "\\u003c")
    template = (root / "dashboard-template.html").read_text(encoding="utf-8-sig")
    default_name = "historical-dashboard.html" if args.historical else "trading-dashboard.html"
    output = (args.output or root / default_name).resolve()
    output.write_text(template.replace("__REPLAY_DATA__", payload), encoding="utf-8")
    print(output)
    if not args.no_open:
        webbrowser.open(output.as_uri())


if __name__ == "__main__":
    main()

