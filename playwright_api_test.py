#!/usr/bin/env python3
"""
Playwright API Test - Proof of Concept

Tests whether Playwright's request context can bypass Cloudflare
after authenticating through the browser.

Usage:
    python playwright_api_test.py
"""

import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

# Load environment variables
load_dotenv(Path(__file__).parent / ".env")

BASE_URL = "https://app.courtreserve.com"
RESERVATIONS_URL = "https://reservations.courtreserve.com"
ORG_ID = "8449"
SCHEDULE_ID = "18493"


def main():
    email = os.environ.get("EMAIL")
    password = os.environ.get("PASSWORD")

    if not email or not password:
        print("❌ Missing EMAIL or PASSWORD environment variables")
        sys.exit(1)

    print("=" * 60)
    print("PLAYWRIGHT API CONTEXT TEST")
    print("=" * 60)
    print()

    # Use Stealth wrapper for automatic bot detection evasion
    stealth = Stealth(
        navigator_platform_override="MacIntel",  # Match Mac
        navigator_vendor_override="Google Inc.",
    )

    with stealth.use_sync(sync_playwright()) as p:
        # Launch browser (non-headless to see what's happening)
        print("🚀 Launching browser with stealth mode...")
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        print("🥷 Stealth patches applied automatically")

        # Step 1: Login via browser
        print("🔐 Logging in via browser...")
        page.goto(f"{BASE_URL}/Account/Login")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)  # Extra wait for JS to load

        # Fill login form - find inputs like Selenium does
        form = page.locator("form").first
        inputs = form.locator("input").all()

        # First non-hidden input is email, look for password type
        visible_inputs = [i for i in inputs if i.get_attribute("type") not in ["hidden"]]
        print(f"   Found {len(visible_inputs)} visible input fields")

        if len(visible_inputs) >= 2:
            # Fill first input (email)
            visible_inputs[0].fill(email)
            print("   Filled email field")

            # Fill second input (password)
            visible_inputs[1].fill(password)
            print("   Filled password field")

        # Click the submit button (not form.submit() which doesn't trigger JS handlers)
        page.wait_for_timeout(500)
        submit_btn = form.locator("button[type='submit'], input[type='submit'], button:has-text('Log'), button:has-text('Sign')").first
        submit_btn.click()

        # Wait for navigation
        print("   Waiting for login redirect...")
        try:
            page.wait_for_url(lambda url: "Login" not in url and "LogIn" not in url, timeout=15000)
            print("✅ Login successful!")
            print(f"   Current URL: {page.url}")
        except:
            print("❌ Login may have failed")
            print(f"   Current URL: {page.url}")
        print()

        # Step 2: Navigate to booking page to establish session
        print("📅 Navigating to booking page...")
        page.goto(f"{BASE_URL}/Online/Reservations/Bookings/{ORG_ID}?sId={SCHEDULE_ID}")
        page.wait_for_timeout(5000)  # Wait for page to load
        print(f"✅ On booking page: {page.url}")
        print()

        # Step 3: Get cookies for debugging
        cookies = context.cookies()
        print(f"🍪 Got {len(cookies)} cookies")
        for c in cookies[:5]:
            print(f"   - {c['name']}: {c['value'][:30]}...")
        print()

        # Step 4: Try API request using Playwright's request context
        print("🧪 Testing API access via Playwright request context...")
        print()

        # Get the request context that shares browser session
        api = context.request

        # Test 1: Try to access the scheduler data API
        print("Test 1: Scheduler API (backend.courtreserve.com)")
        try:
            response = api.get(
                f"https://backend.courtreserve.com/api/scheduler/member-expanded",
                params={
                    "start": "2025-12-17",
                    "end": "2025-12-23",
                    "orgId": ORG_ID,
                    "scheduleId": SCHEDULE_ID,
                }
            )
            print(f"   Status: {response.status}")
            if response.ok:
                data = response.json()
                print(f"   ✅ SUCCESS! Got {len(data) if isinstance(data, list) else 'object'} response")
                print(f"   Sample: {str(data)[:200]}...")
            else:
                print(f"   ❌ Failed: {response.text()[:200]}")
        except Exception as e:
            print(f"   ❌ Error: {e}")
        print()

        # Test 2: Try the reservations API
        print("Test 2: Reservations API (reservations.courtreserve.com)")
        try:
            # First, let's try a simple GET to see if we can access the domain
            response = api.get(f"{RESERVATIONS_URL}/Online/Reservations/Bookings/{ORG_ID}")
            print(f"   Status: {response.status}")
            if response.ok:
                print(f"   ✅ Can access reservations domain!")
            else:
                print(f"   ❌ Blocked: {response.status}")
        except Exception as e:
            print(f"   ❌ Error: {e}")
        print()

        # Test 3: Click on a time slot and capture the CSRF token, then try API booking
        print("Test 3: Full booking flow test")
        print("   Looking for an available slot...")

        try:
            # Wait for page to fully load
            page.wait_for_timeout(3000)

            # Find buttons that are visible (not hidden)
            # The buttons have class "hide" when collapsed - we need to find visible ones

            # Find first available slot - look for visible buttons with Reserve text
            buttons = page.query_selector_all("button[data-courtlabel]")
            print(f"   Found {len(buttons)} total buttons")

            available = None
            for btn in buttons:
                text = btn.text_content() or ""
                is_visible = btn.is_visible()
                class_attr = btn.get_attribute("class") or ""

                # Skip hidden or disabled buttons
                if "hide" in class_attr or not is_visible:
                    continue

                if "Reserve" in text:
                    available = btn
                    print(f"   Found visible slot: {text.strip()}")
                    break

            # If no visible buttons, try clicking to expand a court column first
            if not available:
                print("   No visible slots - trying to expand court view...")
                # Click on a court header to expand
                court_headers = page.query_selector_all("[data-courtlabel]")
                for header in court_headers[:1]:
                    try:
                        header.click()
                        page.wait_for_timeout(1000)
                    except:
                        pass

                # Try finding buttons again
                buttons = page.query_selector_all("button[data-courtlabel]:not(.hide)")
                for btn in buttons:
                    text = btn.text_content() or ""
                    if "Reserve" in text and btn.is_visible():
                        available = btn
                        print(f"   Found slot after expand: {text.strip()}")
                        break

            if available:
                court = available.get_attribute("data-courtlabel")
                time_text = available.text_content()
                print(f"   Found: {court} - {time_text}")

                # Click to open the form
                print("   Clicking to open booking form...")
                available.click()
                # Wait for form to load (hidden inputs don't need to be visible)
                page.wait_for_selector("input[name='__RequestVerificationToken']", state="attached", timeout=10000)

                # Get CSRF token
                csrf = page.input_value("input[name='__RequestVerificationToken']")
                print(f"   Got CSRF token: {csrf[:50]}...")

                # Get other form data
                court_id = page.input_value("input[name='CourtId']") if page.query_selector("input[name='CourtId']") else ""
                member_id = page.input_value("input[name='MemberId']") if page.query_selector("input[name='MemberId']") else ""
                date = page.input_value("input[name='Date']") if page.query_selector("input[name='Date']") else ""

                print(f"   Court ID: {court_id}")
                print(f"   Member ID: {member_id}")
                print(f"   Date: {date}")
                print()

                # Now try to POST via API context
                print("   Attempting API booking request...")

                # Close the form first (we're just testing)
                close_btn = page.query_selector(".k-window-action, .k-i-close, button.close")
                if close_btn:
                    close_btn.click()
                    page.wait_for_timeout(500)

                # Build the API request
                form_data = {
                    "__RequestVerificationToken": csrf,
                    "Id": ORG_ID,
                    "OrgId": ORG_ID,
                    "MemberId": member_id,
                    "CourtId": court_id,
                    "Date": date,
                    "Duration": "60",
                    "IsOpenReservation": "false",
                    "DisclosureAgree": "true",
                }

                # Try the API POST
                api_url = f"{RESERVATIONS_URL}/Online/ReservationsApi/CreateReservation/{ORG_ID}?uiCulture=en-US"

                response = api.post(
                    api_url,
                    form=form_data,
                    headers={
                        "X-Requested-With": "XMLHttpRequest",
                        "Accept": "application/json",
                    }
                )

                print(f"   API Response Status: {response.status}")

                if response.status == 200:
                    try:
                        result = response.json()
                        print(f"   ✅ API ACCESSIBLE!")
                        print(f"   isValid: {result.get('isValid')}")
                        print(f"   message: {result.get('message', 'No message')}")
                    except:
                        print(f"   Response: {response.text()[:300]}")
                elif response.status == 403:
                    print(f"   ❌ BLOCKED BY CLOUDFLARE (403)")
                    print(f"   Response: {response.text()[:200]}")
                else:
                    print(f"   ❌ Unexpected status")
                    print(f"   Response: {response.text()[:200]}")

            else:
                print("   ⚠️ No available slots found to test with")

        except Exception as e:
            print(f"   ❌ Error during test: {e}")
            import traceback
            traceback.print_exc()

        print()
        print("=" * 60)
        print("TEST COMPLETE")
        print("=" * 60)

        # Keep browser open for 5 seconds to see results
        page.wait_for_timeout(5000)
        browser.close()


if __name__ == "__main__":
    main()
