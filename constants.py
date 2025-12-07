import os
import undetected_chromedriver as uc

BASE_URL = "https://app.courtreserve.com"

headless = os.environ.get("HEADLESS", "false").lower() == "true"
chrome_path = os.environ.get("CHROME_PATH")  # Optional: path to Chrome binary

options = uc.ChromeOptions()
options.add_argument("--window-size=1920,1080")
options.add_argument("--start-maximized")
options.add_argument("--disable-extensions")

if chrome_path:
    options.binary_location = chrome_path

if headless:
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # Use a real user agent to avoid detection
    options.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Use use_subprocess=True for better headless support
driver = uc.Chrome(
    options=options,
    headless=headless,
    use_subprocess=True,
)
