"""Command line entry point.

python -m pipeline run        # one daily run now
python -m pipeline backfill   # same news gap-filling, plus 30 days of prices
python -m pipeline serve      # long-running process that triggers the daily run
python -m pipeline coverage   # which ticker/days are fetched, capped or still missing
python -m pipeline rematch    # re-apply the ticker matching rules to stored articles
"""

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime

# 11:00 UTC: after the previous US session has closed and NewsAPI's delay has passed.
DAILY_CRON = "0 11 * * *"


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m pipeline")
    parser.add_argument("command", choices=["run", "backfill", "serve", "coverage", "rematch"])
    args = parser.parse_args()

    # These two only need the database, so skip importing Prefect for them.
    if args.command in ("coverage", "rematch"):
        from db.session import get_engine
        from pipeline.coverage import coverage_report, format_report
        from pipeline.load import rematch_articles

        if args.command == "coverage":
            print(format_report(*coverage_report(get_engine(), datetime.now(UTC))))
        else:
            print(json.dumps(asdict(rematch_articles(get_engine())), indent=2))
        return

    from pipeline.flow import etl_flow

    if args.command == "serve":
        etl_flow.serve(name="daily", cron=DAILY_CRON, parameters={"mode": "daily"})
        return
    mode = "backfill" if args.command == "backfill" else "daily"
    print(json.dumps(etl_flow(mode=mode), indent=2))


if __name__ == "__main__":
    main()
