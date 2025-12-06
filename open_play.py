#!/usr/bin/env python3
"""
Open Play registration script for CourtReserve.

Usage:
    python open_play.py
"""

from pathlib import Path

from dotenv import load_dotenv
from selenium.webdriver.common.by import By

from constants import driver
from utils.find import find
from utils.login import login
from utils.click_latest_available_date import click_latest_available_date


# Load .env from the same directory as this script
load_dotenv(Path(__file__).parent / ".env")


def main():
    login()
    click_latest_available_date()

    print('Selecting "Pickleball Open Play - Intermediate" event details...')
    details_link = find(
        (
            By.XPATH,
            "//span[@data-testid='reservation-name' and contains(., 'Pickleball Open Play - Intermediate')]"
            "/ancestor::div[contains(@class,'reservation-container')]"
            "//a[@data-testid='event-btn-detail' and normalize-space(.)='Details']",
        )
    )
    details_link.click()

    print('Clicking "Register" link...')
    register_link = find((By.LINK_TEXT, "Register"))
    register_link.click()

    print("Finalizing registration...")
    finalize_button = find(
        (By.XPATH, "//button[normalize-space(.)='Finalize Registration']")
    )
    # finalize_button.click()

    print("Registration completed!")
    driver.quit()


if __name__ == "__main__":
    main()
