import os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

BASE_URL = "https://app.courtreserve.com"

options = Options()

# Run headless if HEADLESS=true environment variable is set
if os.environ.get("HEADLESS", "false").lower() == "true":
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # Use a real user agent to avoid detection
    options.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

driver = webdriver.Chrome(options=options)
