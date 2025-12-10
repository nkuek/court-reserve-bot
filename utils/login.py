import logging
import os
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from constants import driver, BASE_URL
from utils.find import find

log = logging.getLogger(__name__)


def login():
    """Log in to CourtReserve using credentials from environment variables."""
    email = os.environ.get("EMAIL")
    password = os.environ.get("PASSWORD")

    if not email or not password:
        raise ValueError("Missing EMAIL or PASSWORD environment variables.")

    log.info("Logging in...")
    driver.get(f"{BASE_URL}/Account/Login")
    time.sleep(2)  # Wait for page to fully load

    form = find((By.CSS_SELECTOR, "form"), timeout=15)
    inputs = form.find_elements(By.TAG_NAME, "input")

    inputs[0].send_keys(email)
    inputs[1].send_keys(password)

    form.submit()

    # Wait for login to complete by checking URL no longer contains /Login
    log.info("  Waiting for login to complete...")
    WebDriverWait(driver, 30).until(
        EC.url_changes(f"{BASE_URL}/Account/Login")
    )
    log.info("  Login successful")
