import time
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.remote.webelement import WebElement

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from constants import driver


def find(by: tuple, timeout: float = 10.0) -> WebElement:
    """
    Find an element with retry logic for stale element references.

    Args:
        by: A tuple of (By.XXX, "selector") e.g. (By.CSS_SELECTOR, "form")
        timeout: Maximum time to wait in seconds

    Returns:
        WebElement: The found element
    """
    end_time = time.time() + timeout

    while True:
        try:
            # Wait until element is located
            WebDriverWait(driver, max(0, end_time - time.time())).until(
                EC.presence_of_element_located(by)
            )

            # Get a fresh element instance
            el = driver.find_element(*by)

            # Wait until element is visible
            WebDriverWait(driver, max(0, end_time - time.time())).until(
                EC.visibility_of(el)
            )

            return el

        except StaleElementReferenceException:
            # If it's stale and we still have time, retry with a fresh find
            if time.time() < end_time:
                continue
            raise
