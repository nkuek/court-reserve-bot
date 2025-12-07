import os
import time
from selenium.webdriver.common.by import By

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from constants import driver, BASE_URL
from utils.find import find


def login():
    """Log in to CourtReserve using credentials from environment variables."""
    email = os.environ.get("EMAIL")
    password = os.environ.get("PASSWORD")

    if not email or not password:
        raise ValueError("Missing EMAIL or PASSWORD environment variables.")

    print("Navigating to login page...")
    driver.get(f"{BASE_URL}/Account/Login")
    time.sleep(2)  # Wait for page to fully load

    form = find((By.CSS_SELECTOR, "form"), timeout=15)
    inputs = form.find_elements(By.TAG_NAME, "input")

    inputs[0].send_keys(email)
    inputs[1].send_keys(password)

    form.submit()
    print("Logging in...")
    time.sleep(1)
