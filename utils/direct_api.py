"""
Direct API booking module for CourtReserve.

Parses session tokens from the reservation form HTML, constructs the full
POST payload (with hardcoded player data and static fields), then fires
direct HTTP POST requests to the CreateReservation API at click time.
No form filling is needed -- only a single page load to get session tokens.
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)

# Known court IDs (discovered from Playwright traces and scheduler API)
COURT_IDS = {
    "Pickleball Court 5A (Bubble B)": 36534,
    "Pickleball Court 5B (Bubble B)": 36535,
    "Pickleball Court 5C (Bubble B)": 36536,
    "Pickleball Court 6A (Bubble B)": 36537,
    "Pickleball Court 6B (Bubble B)": 36538,
    "Pickleball Court 6C (Bubble B)": 36539,
    "Pickleball Court #7A (Bubble B)": None,  # Discovered at runtime
    "Pickleball Court #7B (Bubble B)": None,  # Discovered at runtime
    "Pickleball Court #8A (Bubble B)": 25664,
    "Pickleball Court #8B (Bubble B)": 25665,
}

CREATE_RESERVATION_URL = "https://reservations.courtreserve.com//Online/ReservationsApi/CreateReservation/8449"

# Hardcoded placeholder player data (same every booking)
PLACEHOLDER_PLAYERS = [
    {
        "OrgMemberId": "7610044",
        "MemberId": "8094240",
        "OrgMemberFamilyId": "",
        "FirstName": "Placeholder",
        "LastName": "#1",
        "Email": "",
        "MembershipNumber": "7610044",
        "PaidAmt": "",
        "PriceToPay": "5",
    },
    {
        "OrgMemberId": "7610052",
        "MemberId": "8094247",
        "OrgMemberFamilyId": "",
        "FirstName": "Placeholder",
        "LastName": "#2",
        "Email": "",
        "MembershipNumber": "7610052",
        "PaidAmt": "",
        "PriceToPay": "5",
    },
    {
        "OrgMemberId": "7661328",
        "MemberId": "8133038",
        "OrgMemberFamilyId": "",
        "FirstName": "Placeholder ",
        "LastName": "#3",
        "Email": "",
        "MembershipNumber": "7661328",
        "PaidAmt": "",
        "PriceToPay": "5",
    },
]


def extract_booking_tokens(page) -> dict:
    """
    Extract session tokens from the reservation form by parsing hidden inputs.

    The form does NOT need to be filled -- we only need the session-specific
    tokens (CSRF, RequestData, LotteryGuid) and court-specific data (CourtId).
    Everything else is hardcoded in build_reservation_payload().

    Args:
        page: Playwright page with the reservation form open (after clicking a time slot).

    Returns a dict with the extracted hidden input values.
    """
    log.info("Extracting session tokens from form HTML...")

    tokens = {}
    inputs = page.query_selector_all("input[name]")
    for inp in inputs:
        name = inp.get_attribute("name")
        value = inp.get_attribute("value") or ""
        if name and name not in tokens:
            tokens[name] = value

    log.info(f"  Extracted {len(tokens)} hidden inputs")

    # Log key fields
    for key in ["CourtId", "MemberId", "RequestData", "ReservationTypeId",
                 "ReservationLotteryGuid", "__RequestVerificationToken"]:
        val = tokens.get(key, "(missing)")
        if len(str(val)) > 50:
            val = str(val)[:50] + "..."
        log.info(f"  {key}: {val}")

    return tokens


def extract_cookies_from_context(context) -> dict:
    """Extract cookies from a Playwright browser context as a requests-compatible dict."""
    cookies = {}
    for cookie in context.cookies():
        cookies[cookie["name"]] = cookie["value"]
    log.info(f"Extracted {len(cookies)} cookies from browser context")
    return cookies


def build_reservation_payload(
    session_tokens: dict,
    court_name: str,
    court_id: int,
    booking_date: datetime,
    start_time_str: str,
    duration_minutes: int,
) -> dict:
    """
    Build the complete POST payload for a reservation.

    Combines session tokens (from HTML), court-specific fields, user input
    (date/time/duration), hardcoded player data, and static config fields.

    Args:
        session_tokens: Tokens from extract_booking_tokens() (CSRF, RequestData, etc.)
        court_name: Full court name (e.g., "Pickleball Court 6B (Bubble B)")
        court_id: Numeric court ID
        booking_date: The date to book
        start_time_str: Start time in HH:MM:SS format (e.g., "21:00:00")
        duration_minutes: Duration in minutes (60, 90, 120, 150, 180)
    """
    date_str = booking_date.strftime("%-m/%-d/%Y 12:00:00 AM")

    # Start with session-specific tokens from the form HTML
    payload = {
        "__RequestVerificationToken": session_tokens.get("__RequestVerificationToken", ""),
        "RequestData": session_tokens.get("RequestData", ""),
        "ReservationLotteryGuid": session_tokens.get("ReservationLotteryGuid", ""),
    }

    # Organization and member IDs (static)
    payload.update({
        "Id": "8449",
        "OrgId": "8449",
        "MemberId": session_tokens.get("MemberId", "8714012"),
        "MemberIds": ",,",
        "MembershipId": session_tokens.get("MembershipId", "207681"),
        "CustomSchedulerId": "32125",
        "ReservationTypeId": session_tokens.get("ReservationTypeId", "33608"),
        "CourtTypeEnum": session_tokens.get("CourtTypeEnum", "3432"),
    })

    # Court-specific fields
    payload.update({
        "CourtId": str(court_id),
        "SelectedCourtType": f"Indoor Pickleball - {court_name}",
        "SelectedCourtTypeId": "0",
    })

    # Booking time fields
    payload.update({
        "Date": date_str,
        "StartTime": start_time_str,
        "Duration": str(duration_minutes),
    })

    # Disclosure (waiver agreement)
    payload.update({
        "DisclosureAgree": "true",
        "DisclosureName": session_tokens.get("DisclosureName", "Montgomery TennisPlex Release and Indemnity"),
        "DisclosureText": session_tokens.get("DisclosureText", ""),
    })

    # Primary member (SelectedMembers[0])
    payload.update({
        "SelectedMembers[0].OrgMemberId": session_tokens.get("SelectedMembers[0].OrgMemberId",
                                                               session_tokens.get("MemberId", "8446585")),
        "SelectedMembers[0].MemberId": session_tokens.get("MemberId", "8714012"),
        "SelectedMembers[0].OrgMemberFamilyId": "",
        "SelectedMembers[0].FirstName": session_tokens.get("SelectedMembers[0].FirstName", "Nick"),
        "SelectedMembers[0].LastName": session_tokens.get("SelectedMembers[0].LastName", "Kuek"),
        "SelectedMembers[0].Email": session_tokens.get("SelectedMembers[0].Email", "nkuek1@gmail.com"),
        "SelectedMembers[0].MembershipNumber": session_tokens.get("SelectedMembers[0].MembershipNumber",
                                                                    session_tokens.get("MemberId", "8446585")),
        "SelectedMembers[0].PaidAmt": "",
        "SelectedMembers[0].PriceToPay": "0",
    })

    # Placeholder players (SelectedMembers[1-3])
    for i, player in enumerate(PLACEHOLDER_PLAYERS, start=1):
        for field, value in player.items():
            payload[f"SelectedMembers[{i}].{field}"] = value

    # Static boolean/config fields
    payload.update({
        "IsConsolidatedScheduler": "False",
        "HoldTimeForReservation": "15",
        "RequirePaymentWhenBookingCourtsOnline": "True",
        "AllowMemberToPickOtherMembersToPlayWith": "True",
        "ReservableEntityName": "Court",
        "IsAllowedToPickStartAndEndTime": "False",
        "IsConsolidated": "False",
        "IsToday": "False",
        "IsFromDynamicSlots": "False",
        "InstructorId": "",
        "InstructorName": "",
        "CanSelectCourt": "False",
        "IsCourtRequired": "False",
        "CostTypeAllowOpenMatches": "False",
        "IsMultipleCourtRequired": "False",
        "ReservationQueueId": "",
        "ReservationQueueSlotId": "",
        "IsMobileLayout": "False",
        "IsCaptchaEnabledForPlayer": "False",
        "IsResourceReservation": "False",
        "Token": "",
        "IsEligibleForPreauthorization": "False",
        "MatchMakerSelectedRatingIdsString": "",
        "DurationType": "",
        "MaxAllowedCourtsPerReservation": "1",
        "SelectedResourceId": "",
        "SelectedResourceName": "",
        "UseMinTimeByDefault": "False",
        "IsOpenReservation": "false",
        "MatchMakerTypeId": "",
        "MatchMakerMinNumberOfPlayers": "2",
        "MatchMakerMaxNumberOfPlayers": "",
        "Description": "",
        "SelectedNumberOfGuests": "",
        "OwnersDropdown_input": "",
        "OwnersDropdown": "",
        "X-Requested-With": "XMLHttpRequest",
    })

    return payload


def submit_reservation(
    payload: dict,
    cookies: dict,
    court_name: str,
) -> dict:
    """
    Submit a single reservation via direct HTTP POST.

    Returns a dict with:
        - success: bool
        - court: str
        - response_status: int
        - response_text: str (truncated)
        - elapsed_ms: float
    """
    court_short = court_name.split()[2] if len(court_name.split()) > 2 else court_name

    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://app.courtreserve.com",
        "Referer": "https://app.courtreserve.com/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
    }

    start = time.time()
    try:
        resp = requests.post(
            f"{CREATE_RESERVATION_URL}?uiCulture=en-US",
            data=urlencode(payload, doseq=True),
            cookies=cookies,
            headers=headers,
            timeout=30,
        )
        elapsed_ms = (time.time() - start) * 1000

        response_text = resp.text[:2000]
        log.info(f"  [{court_short}] HTTP {resp.status_code} in {elapsed_ms:.0f}ms")
        log.info(f"  [{court_short}] Response: {resp.text[:200]}")

        # Server returns JSON: {"isValid":true/false, "message":"...", ...}
        is_success = False

        if resp.status_code != 200:
            log.warning(f"  [{court_short}] HTTP error: {resp.status_code}")
        else:
            try:
                data = resp.json()
                is_valid = data.get("isValid") or data.get("IsValid")
                message = data.get("message") or data.get("Message") or ""
                if is_valid:
                    is_success = True
                    log.info(f"  [{court_short}] ✓ BOOKED! {message}")
                else:
                    log.warning(f"  [{court_short}] ✗ Rejected: {message}")
            except Exception:
                # Not JSON - fall back to text analysis
                log.warning(f"  [{court_short}] Non-JSON response: {resp.text[:200]}")

        return {
            "success": is_success,
            "court": court_name,
            "court_short": court_short,
            "response_status": resp.status_code,
            "response_text": response_text,
            "elapsed_ms": elapsed_ms,
        }

    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000
        log.error(f"  [{court_short}] Request failed: {e}")
        return {
            "success": False,
            "court": court_name,
            "court_short": court_short,
            "response_status": 0,
            "response_text": str(e),
            "elapsed_ms": elapsed_ms,
        }


def fire_parallel_bookings(
    base_tokens: dict,
    cookies: dict,
    courts: list[tuple[str, int]],  # List of (court_name, court_id) tuples
    booking_date: datetime,
    start_time_str: str,
    duration_minutes: int,
    target_time: datetime,
    stagger_ms: list[int] | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """
    Fire parallel booking requests at the target time.

    Args:
        base_tokens: Form fields from extract_booking_tokens()
        cookies: Session cookies from extract_cookies_from_context()
        courts: List of (court_name, court_id) tuples to try
        booking_date: Date to book
        start_time_str: Start time in HH:MM:SS format
        duration_minutes: Duration in minutes
        target_time: When to fire the requests
        stagger_ms: Optional list of offsets in ms (e.g., [-500, -250, 0])
                    If None, fires all at once at target_time

    Returns list of result dicts from submit_reservation()
    """
    if stagger_ms is None:
        stagger_ms = [0]

    # Pre-build all payloads
    payloads = []
    for court_name, court_id in courts:
        for offset_ms in stagger_ms:
            payload = build_reservation_payload(
                session_tokens=base_tokens,
                court_name=court_name,
                court_id=court_id,
                booking_date=booking_date,
                start_time_str=start_time_str,
                duration_minutes=duration_minutes,
            )
            payloads.append((court_name, court_id, offset_ms, payload))

    total = len(payloads)
    log.info(f"Prepared {total} booking requests for {len(courts)} court(s)")
    log.info(f"  Stagger offsets: {stagger_ms}")

    if dry_run:
        log.info("")
        log.info("=" * 50)
        log.info("DRY RUN - NOT sending any requests")
        log.info("=" * 50)
        log.info(f"  Would fire {total} POSTs to: {CREATE_RESERVATION_URL}")
        log.info(f"  Cookies: {len(cookies)} cookies available")
        log.info(f"  Stagger: {stagger_ms}")
        log.info("")

        # Log one sample payload in detail
        sample_court, sample_id, sample_offset, sample_payload = payloads[0]
        court_short = sample_court.split()[2]
        log.info(f"  Sample payload for {court_short} (CourtId={sample_id}):")
        for key in sorted(sample_payload.keys()):
            val = sample_payload[key]
            if key == "DisclosureText":
                val = f"({len(val)} chars)"
            elif len(str(val)) > 60:
                val = str(val)[:60] + "..."
            log.info(f"    {key} = {val}")

        # Log all courts that would be tried
        log.info("")
        log.info("  Courts that would be booked:")
        for court_name, court_id, offset_ms, _ in payloads:
            court_short = court_name.split()[2]
            log.info(f"    {court_short} (ID: {court_id}) at offset {offset_ms:+d}ms")

        return [{"success": False, "court": c, "court_short": c.split()[2],
                 "response_status": 0, "response_text": "DRY RUN", "elapsed_ms": 0}
                for c, _, _, _ in payloads]

    # Wait until target time
    delay = (target_time - datetime.now()).total_seconds()
    if delay > 0:
        hours = int(delay // 3600)
        minutes = int((delay % 3600) // 60)
        seconds = int(delay % 60)
        log.info(f"Waiting {hours}h {minutes}m {seconds}s until target time ({target_time.strftime('%H:%M:%S')})")

        # Sleep until close, then busy-wait
        # Account for earliest stagger offset
        earliest_offset_s = min(stagger_ms) / 1000.0
        earliest_fire_time = target_time + timedelta(seconds=earliest_offset_s)

        sleep_until = (earliest_fire_time - datetime.now()).total_seconds() - 0.05
        if sleep_until > 0:
            # Log countdown
            last_log = None
            while True:
                remaining = (earliest_fire_time - datetime.now()).total_seconds()
                if remaining <= 0.05:
                    break
                remaining_int = int(remaining)
                if last_log is None or (remaining_int % 30 == 0 and remaining_int != last_log) or remaining <= 10:
                    h = int(remaining // 3600)
                    m = int((remaining % 3600) // 60)
                    s = int(remaining % 60)
                    log.info(f"  {h:02d}:{m:02d}:{s:02d} remaining...")
                    last_log = remaining_int
                time.sleep(min(1.0, remaining - 0.05))

        # Busy-wait for precise timing
        while datetime.now() < earliest_fire_time:
            pass

    log.info(f"FIRING {total} booking requests!")

    # Group payloads by offset for staggered firing
    by_offset = {}
    for court_name, court_id, offset_ms, payload in payloads:
        by_offset.setdefault(offset_ms, []).append((court_name, court_id, payload))

    all_results = []

    # Use a shared session for connection reuse
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update({
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://app.courtreserve.com",
        "Referer": "https://app.courtreserve.com/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
    })

    def _submit(court_name, payload):
        """Submit via the shared session."""
        court_short = court_name.split()[2] if len(court_name.split()) > 2 else court_name
        start = time.time()
        try:
            resp = session.post(
                f"{CREATE_RESERVATION_URL}?uiCulture=en-US",
                data=urlencode(payload, doseq=True),
                timeout=30,
            )
            elapsed_ms = (time.time() - start) * 1000

            # Server returns JSON: {"isValid":true/false, "message":"..."}
            is_success = False
            message = ""
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    is_valid = data.get("isValid") or data.get("IsValid")
                    message = data.get("message") or data.get("Message") or ""
                    is_success = bool(is_valid)
                except Exception:
                    message = resp.text[:200]

            return {
                "success": is_success,
                "court": court_name,
                "court_short": court_short,
                "response_status": resp.status_code,
                "response_text": message or resp.text[:500],
                "elapsed_ms": elapsed_ms,
            }
        except Exception as e:
            return {
                "success": False,
                "court": court_name,
                "court_short": court_short,
                "response_status": 0,
                "response_text": str(e),
                "elapsed_ms": (time.time() - start) * 1000,
            }

    # Fire all requests in parallel using threads
    with ThreadPoolExecutor(max_workers=total) as executor:
        futures = []
        for offset_ms in sorted(by_offset.keys()):
            # Wait for this offset's fire time
            fire_time = target_time + timedelta(milliseconds=offset_ms)
            now = datetime.now()
            if fire_time > now:
                wait = (fire_time - now).total_seconds()
                if wait > 0:
                    time.sleep(wait)

            fire_actual = datetime.now()
            diff = (fire_actual - fire_time).total_seconds() * 1000
            log.info(f"  Firing offset {offset_ms:+d}ms at {fire_actual.strftime('%H:%M:%S.%f')[:-3]} (diff: {diff:+.1f}ms)")

            for court_name, court_id, payload in by_offset[offset_ms]:
                futures.append(executor.submit(_submit, court_name, payload))

        # Collect results
        for future in as_completed(futures):
            result = future.result()
            status = "SUCCESS" if result["success"] else "FAILED"
            log.info(f"  [{result['court_short']}] {status} - HTTP {result['response_status']} in {result['elapsed_ms']:.0f}ms")
            all_results.append(result)

    session.close()
    return all_results
