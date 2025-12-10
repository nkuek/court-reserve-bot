#!/usr/bin/env python3
"""
Open Play registration script for CourtReserve.

Usage:
    python open_play.py
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
from selenium.webdriver.common.by import By

from constants import driver
from utils.find import find
from utils.login import login
from utils.click_latest_available_date import click_latest_available_date


# Load .env from the same directory as this script
load_dotenv(Path(__file__).parent / ".env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)


def main():
    log.info("=" * 50)
    log.info("OPEN PLAY REGISTRATION STARTED")
    log.info("=" * 50)

    login()
    click_latest_available_date()

    log.info("Looking for Pickleball Open Play - Intermediate...")
    details_link = find(
        (
            By.XPATH,
            "//span[@data-testid='reservation-name' and contains(., 'Pickleball Open Play - Intermediate')]"
            "/ancestor::div[contains(@class,'reservation-container')]"
            "//a[@data-testid='event-btn-detail' and normalize-space(.)='Details']",
        )
    )
    details_link.click()
    log.info("  Found event, clicked Details")

    log.info("Clicking Register link...")
    register_link = find((By.LINK_TEXT, "Register"))
    register_link.click()
    log.info("  Clicked Register")

    log.info("Finalizing registration...")
    finalize_button = find(
        (By.XPATH, "//button[normalize-space(.)='Finalize Registration']")
    )
    finalize_button.click()

    log.info("=" * 50)
    log.info("SUCCESS! Registration completed")
    log.info("=" * 50)

    log.info("Closing browser...")
    driver.quit()
    log.info("Done.")


if __name__ == "__main__":
    main()
