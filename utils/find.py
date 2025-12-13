import time
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.remote.webelement import WebElement

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from constants import get_driver
from utils.exceptions import ElementNotFoundError


def find(by: tuple, timeout: float = 10.0) -> WebElement:
    """
    Find an element with retry logic for stale element references.

    Args:
        by: A tuple of (By.XXX, "selector") e.g. (By.CSS_SELECTOR, "form")
        timeout: Maximum time to wait in seconds

    Returns:
        WebElement: The found element

    Raises:
        ElementNotFoundError: If the element is not found within the timeout
    """
    locator_type, locator_value = by
    end_time = time.time() + timeout

    driver = get_driver()
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

        except TimeoutException:
            current_url = driver.current_url
            raise ElementNotFoundError(
                f"Could not find element on page.\n"
                f"  Selector: {locator_type}={locator_value!r}\n"
                f"  Timeout: {timeout}s\n"
                f"  Current URL: {current_url}\n"
                f"  Possible causes:\n"
                f"    - The page hasn't fully loaded yet\n"
                f"    - The element doesn't exist (selector may be wrong)\n"
                f"    - The page structure has changed"
            )
        except StaleElementReferenceException:
            # If it's stale and we still have time, retry with a fresh find
            if time.time() < end_time:
                continue
            raise ElementNotFoundError(
                f"Element became stale (page changed while finding element).\n"
                f"  Selector: {locator_type}={locator_value!r}\n"
                f"  This can happen if the page refreshes or updates dynamically."
            )
