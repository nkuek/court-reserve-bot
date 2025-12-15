import os
import platform

BASE_URL = "https://app.courtreserve.com"

# Lazy-loaded driver instance
_driver = None


def _is_arm():
    """Check if running on ARM architecture (e.g., Raspberry Pi)."""
    machine = platform.machine().lower()
    return machine.startswith('arm') or machine.startswith('aarch')


def get_driver():
    """Get or create the Chrome driver (lazy initialization)."""
    global _driver
    if _driver is None:
        headless = os.environ.get("HEADLESS", "false").lower() == "true"
        chrome_path = os.environ.get("CHROME_PATH")

        # On ARM (Raspberry Pi), use regular Selenium with system chromedriver
        # undetected_chromedriver doesn't support ARM architecture
        if _is_arm():
            from selenium import webdriver
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.chrome.options import Options

            options = Options()
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-logging")
            options.add_argument("--disable-infobars")
            options.add_argument("--disable-notifications")
            options.add_argument("--disable-popup-blocking")
            options.add_argument("--blink-settings=imagesEnabled=false")
            options.add_argument("--disable-background-networking")
            options.add_argument("--disable-sync")
            options.add_argument("--disable-translate")

            if chrome_path:
                options.binary_location = chrome_path

            if headless:
                options.add_argument("--headless=new")
                options.add_argument("--disable-gpu")
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")

            # Use system chromedriver
            service = Service("/usr/bin/chromedriver")
            _driver = webdriver.Chrome(service=service, options=options)
        else:
            # On x86, use undetected_chromedriver for anti-bot bypass
            import undetected_chromedriver as uc

            options = uc.ChromeOptions()
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--start-maximized")
            options.add_argument("--disable-extensions")

            # Performance optimizations
            options.add_argument("--disable-logging")
            options.add_argument("--disable-infobars")
            options.add_argument("--disable-notifications")
            options.add_argument("--disable-popup-blocking")
            options.add_argument("--blink-settings=imagesEnabled=false")
            options.add_argument("--disable-background-networking")
            options.add_argument("--disable-sync")
            options.add_argument("--disable-translate")
            options.add_argument("--metrics-recording-only")
            options.add_argument("--safebrowsing-disable-auto-update")
            options.add_argument("--disable-component-update")

            if chrome_path:
                options.binary_location = chrome_path

            if headless:
                options.add_argument("--disable-gpu")
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--disable-software-rasterizer")
                options.add_argument("--remote-debugging-port=9222")
                options.add_argument("--memory-pressure-off")
                options.add_argument("--max_old_space_size=512")
                options.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

            _driver = uc.Chrome(
                options=options,
                headless=headless,
                use_subprocess=True,
            )
    return _driver
