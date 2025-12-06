from selenium import webdriver
from selenium.webdriver.chrome.service import Service

BASE_URL = "https://app.courtreserve.com"

# Create a shared driver instance
driver = webdriver.Chrome()
