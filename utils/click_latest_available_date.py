import time
from datetime import datetime, timedelta
from selenium.webdriver.common.by import By

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from constants import driver, BASE_URL
from utils.find import find


def click_latest_available_date():
    """Navigate to bookings page and select the date 5 days from now."""
    print("Navigating to bookings page...")
    driver.get(f"{BASE_URL}/Online/Reservations/Bookings/8449?sId=18493")


    print("Selecting date 5 days from now...")
    date_picker = find((By.CSS_SELECTOR, 'a[data-testid="link-0"]'), timeout=15)
    date_picker.click()
    time.sleep(1)  # Wait for date picker to open

    # Get the date 5 days from now
    current_date = datetime.now()
    future_date = current_date + timedelta(days=5)

    actual_date = f"{future_date.year}/{future_date.month}/{future_date.day}"
    # Format: YYYY/M/D (month is 0-indexed in JS, but not in Python)
    # The original JS uses: futureDate.getMonth() which is 0-indexed
    formatted_date = f"{future_date.year}/{future_date.month - 1}/{future_date.day}"
    
    date_element = find((By.CSS_SELECTOR, f'a[data-value="{formatted_date}"]'), timeout=10)
    print(f"Clicking on date: {actual_date}")
    date_element.click()
