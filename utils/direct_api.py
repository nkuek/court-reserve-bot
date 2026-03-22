"""
Direct API booking module for CourtReserve.

Parses session tokens from the reservation form HTML, constructs the full
POST payload (with hardcoded player data and static fields), then fires
direct HTTP POST requests to the CreateReservation API at click time.
No form filling is needed -- only a single page load to get session tokens.

Uses separate httpx HTTP/2 clients per stagger offset so each request gets
its own TCP+TLS connection, independently routed by Cloudflare's load
balancer across multiple backend servers.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

log = logging.getLogger(__name__)

# Suppress httpx's built-in request/response logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Known court IDs (discovered from Playwright traces and scheduler API)
COURT_IDS = {
    "Pickleball Court 5A (Bubble B)": 36534,
    "Pickleball Court 5B (Bubble B)": 36535,
    "Pickleball Court 5C (Bubble B)": 36536,
    "Pickleball Court 6A (Bubble B)": 36537,
    "Pickleball Court 6B (Bubble B)": 36538,
    "Pickleball Court 6C (Bubble B)": 36539,
    "Pickleball Court #7A (Bubble B)": None,   # Discovered at runtime
    "Pickleball Court #7B (Bubble B)": 25663,
    "Pickleball Court #8A (Bubble B)": 25664,
    "Pickleball Court #8B (Bubble B)": 25665,
}

CREATE_RESERVATION_URL = "https://reservations.courtreserve.com/Online/ReservationsApi/CreateReservation/8449"

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
    num_clients: int = 4,
) -> list[dict]:
    """
    Fire parallel booking requests at the target time.

    Creates multiple httpx HTTP/2 clients for backend diversity — each has
    its own TCP+TLS connection, independently routed by Cloudflare's load
    balancer across backend servers (__cflb cookie is stripped). Every court
    is tried on every client for maximum coverage.

    All requests fire at T-750ms (the earliest stagger offset). The 10ms
    micro-stagger between clients avoids local socket contention without
    sacrificing queue position.

    Args:
        base_tokens: Form fields from extract_booking_tokens()
        cookies: Session cookies from extract_cookies_from_context()
        courts: List of (court_name, court_id) tuples to try
        booking_date: Date to book
        start_time_str: Start time in HH:MM:SS format
        duration_minutes: Duration in minutes
        target_time: When to fire the requests
        stagger_ms: Offsets in ms relative to target_time (e.g., [-750])
                    All clients fire at the earliest offset; the list length
                    is ignored — use num_clients to control client count.
        dry_run: If True, log what would happen without sending requests
        num_clients: Number of HTTP/2 clients for backend diversity (default 4)

    Returns list of result dicts.
    """
    if stagger_ms is None:
        stagger_ms = [-750]

    fire_offset_ms = min(stagger_ms)
    fire_offset_s = fire_offset_ms / 1000.0

    # Pre-build payloads (encode once to bytes, reuse across clients)
    court_payloads = []
    for court_name, court_id in courts:
        payload = build_reservation_payload(
            session_tokens=base_tokens,
            court_name=court_name,
            court_id=court_id,
            booking_date=booking_date,
            start_time_str=start_time_str,
            duration_minutes=duration_minutes,
        )
        encoded = urlencode(payload, doseq=True).encode()
        court_short = court_name.split()[2] if len(court_name.split()) > 2 else court_name
        court_payloads.append((court_name, court_short, court_id, encoded))

    total = len(court_payloads) * num_clients
    log.info(f"Prepared {total} booking requests ({len(court_payloads)} court(s) x {num_clients} clients)")
    log.info(f"  Fire offset: {fire_offset_ms:+d}ms, micro-stagger: 10ms between clients")

    if dry_run:
        log.info("")
        log.info("=" * 50)
        log.info("DRY RUN - NOT sending any requests")
        log.info("=" * 50)
        log.info(f"  Would fire {total} POSTs to: {CREATE_RESERVATION_URL}")
        log.info(f"  Cookies: {len(cookies)} cookies available")
        log.info(f"  Would strip __cflb cookie for backend distribution")
        log.info(f"  Would create {num_clients} HTTP/2 clients for backend diversity")
        log.info(f"  Each client fires all {len(court_payloads)} court(s) at {fire_offset_ms:+d}ms")
        log.info("")

        # Log one sample payload in detail
        sample_court, sample_short, sample_id, sample_encoded = court_payloads[0]
        sample_payload = build_reservation_payload(
            session_tokens=base_tokens, court_name=sample_court,
            court_id=sample_id, booking_date=booking_date,
            start_time_str=start_time_str, duration_minutes=duration_minutes,
        )
        log.info(f"  Sample payload for {sample_short} (CourtId={sample_id}):")
        for key in sorted(sample_payload.keys()):
            val = sample_payload[key]
            if key == "DisclosureText":
                val = f"({len(val)} chars)"
            elif len(str(val)) > 60:
                val = str(val)[:60] + "..."
            log.info(f"    {key} = {val}")

        # Log all courts that would be tried
        log.info("")
        log.info("  Courts that would be booked (on each client):")
        for court_name, court_short, court_id, _ in court_payloads:
            log.info(f"    {court_short} (ID: {court_id})")

        return [{"success": False, "court": cn, "court_short": cs,
                 "response_status": 0, "response_text": "DRY RUN", "elapsed_ms": 0,
                 "offset_ms": fire_offset_ms, "client_idx": ci, "cf_ray": ""}
                for ci in range(num_clients)
                for cn, cs, _, _ in court_payloads]

    all_results = []

    # Strip Cloudflare load-balancer pinning cookie so each client gets
    # independently routed to a backend. Keep __cf_bm (bot management)
    # to avoid triggering JS challenges.
    filtered_cookies = {k: v for k, v in cookies.items() if k != "__cflb"}
    if "__cflb" in cookies:
        log.info(f"  Stripped __cflb cookie for backend distribution (was: {cookies['__cflb'][:20]}...)")

    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://app.courtreserve.com",
        "Referer": "https://app.courtreserve.com/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
    }

    # N HTTP/2 clients for backend diversity — each gets its own TCP+TLS
    # connection, independently routed by Cloudflare across backends.
    # keepalive_expiry=120 ensures warmed connections survive the ~60s
    # gap between warmup and firing (default 5s was silently killing them).
    clients: list[httpx.Client] = []
    for _ in range(num_clients):
        clients.append(httpx.Client(
            http2=True,
            timeout=60,
            headers=headers,
            cookies=filtered_cookies,
            limits=httpx.Limits(keepalive_expiry=120),
        ))
    log.info(f"Created {num_clients} HTTP/2 clients for backend distribution")

    try:
        # Wait until target time
        fire_time = target_time + timedelta(seconds=fire_offset_s)
        delay = (fire_time - datetime.now()).total_seconds()
        if delay > 0:
            hours = int(delay // 3600)
            minutes = int((delay % 3600) // 60)
            seconds = int(delay % 60)
            log.info(f"Waiting {hours}h {minutes}m {seconds}s until fire time ({fire_time.strftime('%H:%M:%S.%f')[:-3]})")

            # TLS warmup: attempt ~60s before fire time with a hard timeout
            # so it can never delay firing. Each client gets its own HEAD
            # request to establish an independent connection to (potentially)
            # a different Cloudflare backend.
            warmup_time = fire_time - timedelta(seconds=60)
            tls_warmed = False

            last_log = None
            while True:
                remaining = (fire_time - datetime.now()).total_seconds()
                if remaining <= 0.05:
                    break

                # TLS warmup at ~60s before fire time
                if not tls_warmed and datetime.now() >= warmup_time:
                    tls_warmed = True
                    # Hard timeout: at most 50s, but also leave 5s buffer before fire time
                    max_timeout = min(50, max(1, remaining - 5))
                    log.info(f"Pre-warming {num_clients} HTTP/2 connections (timeout={max_timeout:.0f}s)...")

                    def _warmup_one(idx, c):
                        try:
                            ws = time.time()
                            wr = c.head(
                                f"{CREATE_RESERVATION_URL}?uiCulture=en-US",
                                timeout=max_timeout,
                            )
                            wms = (time.time() - ws) * 1000
                            cflb_val = c.cookies.get("__cflb", "none")
                            cf_ray = wr.headers.get("cf-ray", "N/A")
                            log.info(f"  [client {idx}] {wms:.0f}ms {wr.http_version} __cflb={cflb_val[:20]} cf-ray={cf_ray}")
                            return idx, True
                        except Exception as e:
                            log.warning(f"  [client {idx}] warmup failed (non-fatal): {e}")
                            return idx, False

                    with ThreadPoolExecutor(max_workers=num_clients) as warmup_exec:
                        warmup_futures = [
                            warmup_exec.submit(_warmup_one, i, c)
                            for i, c in enumerate(clients)
                        ]
                        for f in as_completed(warmup_futures):
                            f.result()

                    # Re-apply Playwright cookies (sans __cflb) to avoid
                    # contamination from Cloudflare cookies set during warmup
                    for c in clients:
                        c.cookies.clear()
                        c.cookies.update(filtered_cookies)

                remaining_int = int(remaining)
                if last_log is None or (remaining_int % 30 == 0 and remaining_int != last_log) or remaining <= 10:
                    h = int(remaining // 3600)
                    m = int((remaining % 3600) // 60)
                    s = int(remaining % 60)
                    log.info(f"  {h:02d}:{m:02d}:{s:02d} remaining...")
                    last_log = remaining_int
                time.sleep(max(0, min(1.0, remaining - 0.05)))

            # Busy-wait for precise timing using perf_counter (monotonic,
            # no heap allocations per iteration unlike datetime.now())
            anchor_wall = datetime.now()
            anchor_pc = time.perf_counter()
            fire_pc = anchor_pc + (fire_time - anchor_wall).total_seconds()
            while time.perf_counter() < fire_pc:
                pass

        log.info(f"FIRING {total} booking requests!")

        # Event to signal early cancellation once a booking succeeds
        success_event = threading.Event()

        def _submit(client_idx, client, court_name, court_short, encoded_payload):
            """Submit via the given HTTP/2 client. Skips if another thread already succeeded."""
            if success_event.is_set():
                return {
                    "success": False, "court": court_name, "court_short": court_short,
                    "response_status": 0, "response_text": "CANCELLED (another request succeeded)",
                    "elapsed_ms": 0, "offset_ms": fire_offset_ms,
                    "client_idx": client_idx, "cf_ray": "", "cancelled": True,
                }

            start = time.time()
            try:
                resp = client.post(
                    f"{CREATE_RESERVATION_URL}?uiCulture=en-US",
                    content=encoded_payload,
                )
                elapsed_ms = (time.time() - start) * 1000

                cf_ray = resp.headers.get("cf-ray", "")

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

                if is_success:
                    success_event.set()

                return {
                    "success": is_success, "court": court_name, "court_short": court_short,
                    "response_status": resp.status_code,
                    "response_text": message or resp.text[:500],
                    "elapsed_ms": elapsed_ms, "offset_ms": fire_offset_ms,
                    "client_idx": client_idx, "cf_ray": cf_ray,
                }
            except Exception as e:
                return {
                    "success": False, "court": court_name, "court_short": court_short,
                    "response_status": 0, "response_text": str(e),
                    "elapsed_ms": (time.time() - start) * 1000, "offset_ms": fire_offset_ms,
                    "client_idx": client_idx, "cf_ray": "",
                }

        # Fire all requests: each client sends all courts, with 10ms micro-stagger
        # between clients to avoid local socket contention
        with ThreadPoolExecutor(max_workers=total) as executor:
            # Pre-warm worker threads so creation overhead doesn't affect firing
            warmup_futs = [executor.submit(lambda: None) for _ in range(total)]
            for f in warmup_futs:
                f.result()

            futures = []
            for i, c in enumerate(clients):
                if i > 0:
                    time.sleep(0.010)  # 10ms micro-stagger between clients

                fire_actual = datetime.now()
                log.info(f"  Firing client {i} at {fire_actual.strftime('%H:%M:%S.%f')[:-3]}")

                for court_name, court_short, court_id, encoded in court_payloads:
                    futures.append(executor.submit(_submit, i, c, court_name, court_short, encoded))

            # Collect results as they complete
            for future in as_completed(futures):
                result = future.result()
                if result.get("cancelled"):
                    continue
                status = "SUCCESS" if result["success"] else "FAILED"
                cf_ray_tag = f" cf-ray={result['cf_ray']}" if result.get("cf_ray") else ""
                log.info(f"  [{result['court_short']}@c{result['client_idx']}] {status} - HTTP {result['response_status']} in {result['elapsed_ms']:.0f}ms{cf_ray_tag}")
                all_results.append(result)

    finally:
        for c in clients:
            c.close()

    return all_results
