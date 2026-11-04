import logging
import os

# Route this package's loggers through Prefect's handlers (console + Prefect UI), and
# stay quiet when these functions are called outside a flow run (tests, future tools).
# Must be set before Prefect is imported, since Prefect configures logging on import.
os.environ.setdefault("PREFECT_LOGGING_EXTRA_LOGGERS", "pipeline")
os.environ.setdefault("PREFECT_LOGGING_TO_API_WHEN_MISSING_FLOW", "ignore")
logging.getLogger("pipeline").setLevel(logging.INFO)
