"""Copies sign-up app lineups onto CourtReserve reservations.

Reads a JSON list of courts on stdin, each {"date", "court", "players": [{"name", "crName"}]},
and prints one result per court between the result markers. Logs in once for all of them.
"""

import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from constants import close_browser, get_page
from utils.cr_roster import sync_court
from utils.login import login

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def main() -> None:
    jobs = json.load(sys.stdin)
    booker = os.environ["SIGNUP_BOOKER_NAME"]
    results = []
    try:
        login()
        page = get_page()
        for job in jobs:
            label = f"{job['court']} on {job['date']}"
            try:
                result = sync_court(page, date.fromisoformat(job["date"]), job["court"], job["players"], booker)
                log.info(f"{label}: {result['status']} {json.dumps({k: v for k, v in result.items() if k != 'status'})}")
            except Exception as e:
                log.exception(f"{label}: failed")
                result = {"status": "error", "error": str(e)}
            results.append({"date": job["date"], "court": job["court"], **result})
    except Exception as e:
        log.exception("Roster sync could not start")
        results = [{"date": j["date"], "court": j["court"], "status": "error", "error": str(e)} for j in jobs]
    finally:
        close_browser()
    print(f"===RESULT_JSON==={json.dumps(results)}===END_RESULT_JSON===")


if __name__ == "__main__":
    main()
