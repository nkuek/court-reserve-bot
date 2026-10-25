"""Copies a court's sign-up app lineup onto its CourtReserve reservation."""

import json
import logging
import os
import re
import time
from datetime import date, datetime
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from constants import BASE_URL, ERROR_TOAST, LOCAL_TZ, ORG_ID, SCHEDULE_ID, get_browser
from utils.login import _attempt_login

log = logging.getLogger(__name__)

LIST_URL = f"{BASE_URL}/Online/Bookings/List/{ORG_ID}?type=1"
DETAIL_URL = f"{BASE_URL}/Online/MyProfile/Reservation/{ORG_ID}/{{rid}}"
# The swap form's member search. costTypeId is this account's membership id.
SEARCH_URL = (
    f"/Online/AjaxController/GetMembersToPlayWith/{ORG_ID}?costTypeId=10930256&customSchedulerId={SCHEDULE_ID}"
    "&isOpenReservation=false&organizationMemberIdsString=&userId={user}&filterValue={q}"
)
CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "cr_members.json"
# The Edit Reservation button opens the same modal as Create Reservation.
EDIT_MODAL = "[data-testid=reservation-create-page]"
NOT_FOUND = "no CourtReserve member by that name"
NO_NAME = "guest without a name"

# CourtReserve rejects a doubles reservation with fewer players than this.
MIN_PLAYERS = 4

IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)


class Rejected(RuntimeError):
    """CourtReserve refused a save. Its message usually names the player at fault."""

    def __init__(self, kind: str, status: int, body: str):
        super().__init__(f"{kind} rejected: {status} {body[:300]}")
        try:
            self.message = json.loads(body).get("message") or ""
        except (ValueError, AttributeError):
            # The modal's toasts are plain text.
            self.message = body


class TooEarly(Rejected):
    """A player can't be added yet: the session is past their own advance-booking window."""

    def __init__(self, kind: str, status: int, body: str, limit: datetime):
        super().__init__(kind, status, body)
        self.limit = limit


# CourtReserve's wording for a player outside their booking window, e.g. "up to 10/8/2026, 1:09 PM".
TOO_EARLY_RE = re.compile(r"only allowed to reserve up to (\d{1,2}/\d{1,2}/\d{4}, \d{1,2}:\d{2} [AP]M)")


def _refusal(kind: str, status: int, body: str) -> Rejected:
    m = TOO_EARLY_RE.search(body)
    if m:
        limit = datetime.strptime(m.group(1), "%m/%d/%Y, %I:%M %p").replace(tzinfo=LOCAL_TZ)
        return TooEarly(kind, status, body, limit)
    return Rejected(kind, status, body)


def _check(kind: str, status: int, body: str) -> None:
    if status == 200 and '"isValid":true' in body:
        return
    raise _refusal(kind, status, body)


def _blamed(message: str, names: list[str]) -> str | None:
    """The player a rejection names, e.g. "Ying Zhuge not allowed on this reservation: ..."."""
    hits = [n for n in names if n.lower() in message.lower()]
    return max(hits, key=len) if hits else None


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def find_reservation(page: Page, day: date, court: str) -> str | None:
    """The reservation id for this account's booking of a court on a day, from My Reservations."""
    page.goto(LIST_URL)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)
    # Cards read like "Mon, Oct 5th, 8:00 PM - 11:00 PM ... Pickleball Court #8A (Bubble B)".
    cards = page.eval_on_selector_all(
        "[data-testid^=booking-card-wrapper-]",
        "els => els.map(e => [e.innerText, e.dataset.testid.replace('booking-card-wrapper-', '')])",
    )
    when = f"{day:%a}, {day:%b} {_ordinal(day.day)}"
    court_re = re.compile(rf"Court #?{re.escape(court)}\b")
    hits = [rid for text, rid in cards if when in text and court_re.search(text)]
    if len(hits) > 1:
        log.warning(f"  {len(hits)} reservations for {court} on {day}, using the first")
    return hits[0] if hits else None


def _open(page: Page, rid: str) -> None:
    page.goto(DETAIL_URL.format(rid=rid))
    page.wait_for_load_state("networkidle")


def current_players(page: Page, rid: str) -> dict:
    """The reservation's players as the edit form lists them, plus which one is this account."""
    _open(page, rid)
    return page.evaluate(
        """async rid => {
            const h = await (await fetch(`/Online/MyProfile/UpdateMyReservation/%s?reservationId=${rid}`,
                {headers: {'X-Requested-With': 'XMLHttpRequest'}})).text();
            const doc = new DOMParser().parseFromString(h, 'text/html');
            const val = n => doc.querySelector(`[name="${n}"]`)?.value ?? '';
            const players = [];
            for (let i = 0; doc.querySelector(`[name="InitialMembers[${i}].MemberOrgId"]`); i++) {
                const f = k => val(`InitialMembers[${i}].${k}`);
                players.push({org: f('MemberOrgId'), member: f('MemberId'), name: `${f('FirstName')} ${f('LastName')}`.trim()});
            }
            return {self: val('MemberId'), start: `${val('Date')} ${val('StartTime')}`, players};
        }""" % ORG_ID,
        rid,
    )


def _search(page: Page, user: str, query: str) -> list[dict]:
    url = SEARCH_URL.format(user=user, q="__Q__")
    return page.evaluate(
        """async ([url, q]) => {
            const r = await fetch(url.replace('__Q__', encodeURIComponent(q)), {headers: {'X-Requested-With': 'XMLHttpRequest'}});
            return r.ok ? await r.json() : [];
        }""",
        [url, query],
    )


def _favorites(rid: str) -> set[str]:
    """Org ids of this account's favorite players. Only the mobile layout lists them."""
    ctx = get_browser().new_context(viewport={"width": 402, "height": 874}, is_mobile=True, has_touch=True, user_agent=IPHONE_UA)
    try:
        page = ctx.new_page()
        _attempt_login(page, os.environ["EMAIL"], os.environ["PASSWORD"])
        _open(page, rid)
        page.locator("[data-testid=btn-swap-name]").first.click()
        # The Substitute page lists favorites before any search. Each row's id is the org member id.
        page.locator("[data-testid^=swap-players-row-]").first.wait_for(timeout=15000)
        return set(page.eval_on_selector_all("[data-testid^=swap-players-row-]", "els => els.map(e => e.dataset.testid.replace('swap-players-row-', ''))"))
    finally:
        ctx.close()


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def resolve(page: Page, rid: str, user: str, names: list[str]) -> tuple[dict, dict]:
    """Maps CourtReserve names to members. Returns the matches and, for the rest, why not."""
    cache = _load_cache()
    found, missing, ambiguous = {}, {}, {}
    for name in names:
        key = name.lower()
        if key in cache:
            found[name] = cache[key]
            continue
        hits = [h for h in _search(page, user, name) if (h.get("FullName") or h.get("DisplayName") or "").strip().lower() == key]
        if len(hits) == 1:
            found[name] = {"org": str(hits[0]["MemberOrgId"]), "member": str(hits[0]["MemberId"])}
        elif hits:
            ambiguous[name] = hits
        else:
            missing[name] = NOT_FOUND
    if ambiguous:
        favs = _favorites(rid)
        for name, hits in ambiguous.items():
            picks = [h for h in hits if str(h["MemberOrgId"]) in favs]
            if len(picks) == 1:
                found[name] = {"org": str(picks[0]["MemberOrgId"]), "member": str(picks[0]["MemberId"])}
            else:
                missing[name] = f"{len(hits)} CourtReserve members share that name. Favorite the right one."
    for name, m in found.items():
        cache[name.lower()] = m
    CACHE_PATH.parent.mkdir(exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=1, sort_keys=True))
    return found, missing


def plan(current: dict, targets: list[dict], hold: bool = False) -> tuple[list[dict], list[dict]]:
    """Players to remove and add so the reservation matches the targets.

    Placeholders stay only while they're needed for the player minimum. With `hold`, real players
    stay too, since an unmatched lineup name may be one of them under another spelling.
    """
    want = {t["org"] for t in targets}
    have = {p["org"] for p in current["players"]}
    add = [t for t in targets if t["org"] not in have]
    remove = [p for p in current["players"] if p["member"] != current["self"] and p["org"] not in want]
    if hold:
        remove = [p for p in remove if _placeholder(p)]
    after = len(current["players"]) - len(remove) + len(add)
    keep = max(MIN_PLAYERS - after, 0)
    for p in [p for p in remove if _placeholder(p)][:keep]:
        remove.remove(p)
    return remove, add


def _placeholder(p: dict) -> bool:
    return p["name"].lower().startswith("placeholder")


def swap(page: Page, rid: str, out_org: str, in_member: str) -> None:
    """Replaces one player with one member through the Sub form."""
    _open(page, rid)
    result = page.evaluate(
        """async ([org, rid, out, inn]) => {
            const h = await (await fetch(`/Online/MyProfile/SwapPlayers/${org}?reservationId=${rid}&orgMemberId=${out}`,
                {headers: {'X-Requested-With': 'XMLHttpRequest'}})).text();
            const form = new DOMParser().parseFromString(h, 'text/html').querySelector('#swap-player-form');
            const fd = new URLSearchParams();
            for (const el of form.querySelectorAll('input[name]')) fd.append(el.name, el.value);
            // The form wants the incoming player's MemberId, not their org member id.
            fd.set('SwapWithMemberId', inn);
            const r = await fetch(form.getAttribute('action'), {method: 'POST', body: fd,
                headers: {'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'}});
            return {status: r.status, body: await r.text()};
        }""",
        [ORG_ID, rid, out_org, in_member],
    )
    _check("Swap", result["status"], result["body"])


def edit(page: Page, rid: str, remove_orgs: list[str], adds: dict[str, str]) -> None:
    """Removes players and adds members (org id to CourtReserve name) in one save through the Edit Reservation modal."""
    _open(page, rid)
    page.locator("[data-testid=btn-update-reservation]").click()
    page.locator(EDIT_MODAL).wait_for(timeout=20000)
    row = lambda org: page.locator(f"[data-testid=reservation-player-row-o{org}]")
    # The table renders after the modal opens.
    page.locator("[data-testid^=reservation-player-row-]").first.wait_for(timeout=20000)
    for org in remove_orgs:
        row(org).get_by_role("button", name="Remove").click()
        row(org).wait_for(state="detached", timeout=10000)
    search = page.get_by_placeholder("Search for other player(s) to play with")
    for org, name in adds.items():
        try:
            search.fill(name)
            # The option's id is the org member id, so two members with one name stay apart.
            page.locator(f"[data-testid=reservation-additional-player-option-{org}]").click(timeout=10000)
            # Each add asks for confirmation before the row appears.
            page.get_by_role("button", name="Yes").click(timeout=10000)
            row(org).wait_for(timeout=10000)
        except PlaywrightTimeoutError:
            # An ineligible player may be refused at add time with a toast. A timeout with no toast is a plain failure.
            toast = " ".join(page.locator("[role=alert], .ant-message, .ant-notification").all_inner_texts()).strip()
            if not toast:
                raise
            raise _refusal("Edit", 0, toast) from None
    saves = []
    track = lambda r: saves.append(r) if r.request.method == "POST" and "/UpdateMyReservation/" in r.url else None
    page.on("response", track)
    try:
        page.locator("[data-testid=reservation-mobile-footer]").get_by_role("button", name="Save").click()
        error = page.get_by_text(ERROR_TOAST)
        # A saved reservation closes the modal. A refused one leaves it open.
        deadline = time.time() + 30
        while time.time() < deadline and page.locator(EDIT_MODAL).count() and not error.count() and not saves:
            page.wait_for_timeout(200)
    finally:
        page.remove_listener("response", track)
    if saves:
        _check("Edit", saves[-1].status, saves[-1].text())
    elif error.count():
        raise _refusal("Edit", 0, error.first.inner_text())
    else:
        raise RuntimeError("Edit saved nothing within 30s")


def sync_court(page: Page, day: date, court: str, players: list[dict], booker: str) -> dict:
    """Makes one court's reservation match its lineup. Returns what happened."""
    rid = find_reservation(page, day, court)
    if not rid:
        return {"status": "not_found"}
    current = current_players(page, rid)
    names = [p["crName"] for p in players if p["name"].lower() != booker.lower() and p["crName"]]
    unnamed = [p["name"] for p in players if not p["crName"]]
    found, missing = resolve(page, rid, current["self"], names)
    missing.update({n: NO_NAME for n in unnamed})
    # One ineligible player fails the whole save. Each rejection drops the player it names and saves again.
    refused, waiting, retry_at = {}, {}, None
    while True:
        allowed = {n: m for n, m in found.items() if n not in refused and n not in waiting}
        remove, add = plan(current, list(allowed.values()), hold=bool(missing))
        try:
            if not remove and not add:
                action = "unchanged"
            elif len(remove) == 1 and len(add) == 1:
                swap(page, rid, remove[0]["org"], add[0]["member"])
                action = "swapped"
            else:
                names = {m["org"]: n for n, m in allowed.items()}
                edit(page, rid, [p["org"] for p in remove], {a["org"]: names[a["org"]] for a in add})
                action = "edited"
            break
        except Rejected as e:
            name = _blamed(e.message, [n for n, m in allowed.items() if m in add])
            if not name:
                raise
            log.warning(f"  Leaving {name} off: {e.message}")
            if isinstance(e, TooEarly):
                # The window slides with the clock, so the player becomes addable that far before the start.
                start = datetime.strptime(current["start"], "%m/%d/%Y %I:%M %p").replace(tzinfo=LOCAL_TZ)
                at = (start - (e.limit - datetime.now(LOCAL_TZ))).timestamp()
                waiting[name] = at
                retry_at = min(retry_at or at, at)
            else:
                refused[name] = e.message

    after = current_players(page, rid) if action != "unchanged" else current
    have = {p["org"] for p in after["players"]}
    want = {m["org"] for m in allowed.values()}
    kept = [p["name"] for p in after["players"] if p["member"] != after["self"] and p["org"] not in want and not _placeholder(p)] if missing else []
    absent = [n for n, m in allowed.items() if m["org"] not in have]
    if absent:
        raise RuntimeError(f"Still missing after {action}: {', '.join(absent)}")
    return {
        "status": action,
        "reservation": rid,
        "removed": [p["name"] for p in remove],
        "kept": kept,
        "added": [n for n, m in allowed.items() if m in add],
        "players": [p["name"] for p in after["players"]],
        "unmatched": {**missing, **refused},
        "waiting": waiting,
        # Keyed by CourtReserve name, or the app name for an unnamed guest.
        "problems": [
            *({"name": n, "kind": {NOT_FOUND: "not_found", NO_NAME: "no_name"}.get(why, "ambiguous"), "detail": why} for n, why in missing.items()),
            *({"name": n, "kind": "refused", "detail": why} for n, why in refused.items()),
            *({"name": n, "kind": "waiting", "detail": "", "at": int(at * 1000)} for n, at in waiting.items()),
        ],
        **({"retry_at": retry_at} if retry_at else {}),
    }


def cancel(page: Page, day: date, court: str, reason: str = "Cancel") -> dict:
    """Cancels this account's reservation of a court on a day."""
    rid = find_reservation(page, day, court)
    if not rid:
        return {"status": "not_found"}
    _open(page, rid)
    result = page.evaluate(
        """async ([org, rid, reason]) => {
            const h = await (await fetch(`/Online/MyProfile/CancelReservation/${org}?reservationId=${rid}`,
                {headers: {'X-Requested-With': 'XMLHttpRequest'}})).text();
            const form = new DOMParser().parseFromString(h, 'text/html').querySelector('#cancel-reservation-form');
            const fd = new URLSearchParams();
            for (const el of form.querySelectorAll('input[name], textarea[name]')) fd.append(el.name, el.value);
            fd.set('SelectedReservation.CancellationReason', reason);
            const r = await fetch(form.getAttribute('action'), {method: 'POST', body: fd,
                headers: {'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'}});
            return {status: r.status, body: await r.text()};
        }""",
        [ORG_ID, rid, reason],
    )
    # The list of active reservations is the proof. The response shape varies by outcome.
    if find_reservation(page, day, court) == rid:
        raise RuntimeError(f"Still booked after cancelling: {result['status']} {result['body'][:300]}")
    return {"status": "cancelled", "reservation": rid}


def transfer(page: Page, day: date, court: str, to_cr_name: str) -> dict:
    """Swaps this account out of its reservation for another member, to hand the court over.

    Returns whether the reservation still sits under this account afterwards.
    """
    rid = find_reservation(page, day, court)
    if not rid:
        return {"status": "not_found"}
    current = current_players(page, rid)
    me = next((p for p in current["players"] if p["member"] == current["self"]), None)
    if not me:
        return {"status": "already_out", "reservation": rid}
    found, missing = resolve(page, rid, current["self"], [to_cr_name])
    if missing:
        return {"status": "unmatched", "reason": missing[to_cr_name]}
    target = found[to_cr_name]
    # The Sub form only offers members who aren't on the reservation yet.
    if any(p["org"] == target["org"] for p in current["players"]):
        edit(page, rid, [target["org"]], {})
    swap(page, rid, me["org"], target["member"])
    still_mine = find_reservation(page, day, court) == rid
    after = current_players(page, rid) if still_mine else None
    if after and not any(p["org"] == target["org"] for p in after["players"]):
        raise RuntimeError(f"{to_cr_name} is not on the reservation after the swap")
    return {"status": "transferred", "reservation": rid, "still_mine": still_mine}
