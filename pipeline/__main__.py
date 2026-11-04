"""Command line entry point.

python -m pipeline run        # one daily run now
python -m pipeline backfill   # load the last 30 days
python -m pipeline serve      # long-running process that triggers the daily run
"""

import argparse
import json
import logging

from pipeline.flow import etl_flow

# 11:00 UTC: after the previous US session has closed and NewsAPI's delay has passed.
DAILY_CRON = "0 11 * * *"


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline")
    parser.add_argument("command", choices=["run", "backfill", "serve"])
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    if args.command == "serve":
        etl_flow.serve(name="daily", cron=DAILY_CRON, parameters={"mode": "daily"})
        return
    mode = "backfill" if args.command == "backfill" else "daily"
    print(json.dumps(etl_flow(mode=mode), indent=2))


if __name__ == "__main__":
    main()
