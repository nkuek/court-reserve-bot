"""Copies a court's sign-up app lineup onto its CourtReserve reservation."""

import json
import logging
import os
import re
from datetime import date
from pathlib import Path

from playwright.sync_api import Page

from constants import BASE_URL, ORG_ID, SCHEDULE_ID, get_browser
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

# CourtReserve rejects a doubles reservation with fewer players than this.
MIN_PLAYERS = 4

IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)


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
        "a[data-testid=details-btn]",
        """els => els.map(e => {
            let n = e;
            while (n.parentElement && n.parentElement.querySelectorAll('a[data-testid=details-btn]').length === 1) n = n.parentElement;
            return [n.innerText, e.getAttribute('href')];
        })""",
    )
    when = f"{day:%a}, {day:%b} {_ordinal(day.day)}"
    court_re = re.compile(rf"Court #?{re.escape(court)}\b")
    hits = [href for text, href in cards if when in text and court_re.search(text)]
    if len(hits) > 1:
        log.warning(f"  {len(hits)} reservations for {court} on {day}, using the first")
    return hits[0].rstrip("/").split("/")[-1] if hits else None


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
            return {self: val('MemberId'), players};
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
        page.locator(".combobox-items-container li").first.wait_for(timeout=15000)
        return set(page.eval_on_selector_all(".combobox-items-container li", "els => els.map(e => e.getAttribute('value'))"))
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
            missing[name] = "no CourtReserve member by that name"
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


def plan(current: dict, targets: list[dict], unresolved: int) -> tuple[list[dict], list[dict]]:
    """Players to remove and add so the reservation matches the targets.

    Placeholders stay while they're needed for the player minimum or hold a spot for an
    unmatched name.
    """
    want = {t["org"] for t in targets}
    have = {p["org"] for p in current["players"]}
    add = [t for t in targets if t["org"] not in have]
    remove = [p for p in current["players"] if p["member"] != current["self"] and p["org"] not in want]
    after = len(current["players"]) - len(remove) + len(add)
    keep = max(MIN_PLAYERS - after, 0) + unresolved
    for p in [p for p in remove if p["name"].lower().startswith("placeholder")][:keep]:
        remove.remove(p)
    return remove, add


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
    if result["status"] != 200 or '"isValid":true' not in result["body"]:
        raise RuntimeError(f"Swap rejected: {result['status']} {result['body'][:300]}")


def edit(page: Page, rid: str, remove_orgs: list[str], add_orgs: list[str]) -> None:
    """Removes and adds players in one save through the Edit Reservation form.

    The form rebuilds its player fields from a server-rendered table, so it runs in the page
    instead of as a plain POST.
    """
    _open(page, rid)
    page.locator("[data-testid=btn-update-reservation]").click()
    page.locator("#createReservation-Form").wait_for(timeout=20000)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)
    for org in remove_orgs:
        page.evaluate("id => calculateTotalDue(Number(id))", org)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)
    if add_orgs:
        page.evaluate("ids => { ids.forEach(id => selectedMembers.push({ OrgMemberId: id })); calculateTotalDue(null, true); }", add_orgs)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2500)
    blank = page.evaluate("() => selectedMembers.filter(m => !$(`#hidden-memberid_${m.OrgMemberId}`).val()).map(m => String(m.OrgMemberId))")
    if blank:
        raise RuntimeError(f"Edit form never loaded players {blank}")
    with page.expect_response(lambda r: "UpdateMyReservation" in r.url and r.request.method == "POST", timeout=30000) as resp:
        page.evaluate("submitUpdateReservation()")
    body = resp.value.text()
    if resp.value.status != 200 or '"isValid":true' not in body:
        raise RuntimeError(f"Edit rejected: {resp.value.status} {body[:300]}")


def sync_court(page: Page, day: date, court: str, players: list[dict], booker: str) -> dict:
    """Makes one court's reservation match its lineup. Returns what happened."""
    rid = find_reservation(page, day, court)
    if not rid:
        return {"status": "not_found"}
    current = current_players(page, rid)
    names = [p["crName"] for p in players if p["name"].lower() != booker.lower() and p["crName"]]
    unnamed = [p["name"] for p in players if not p["crName"]]
    found, missing = resolve(page, rid, current["self"], names)
    missing.update({n: "guest without a name" for n in unnamed})
    remove, add = plan(current, list(found.values()), len(missing))

    if not remove and not add:
        action = "unchanged"
    elif len(remove) == 1 and len(add) == 1:
        swap(page, rid, remove[0]["org"], add[0]["member"])
        action = "swapped"
    else:
        edit(page, rid, [p["org"] for p in remove], [a["org"] for a in add])
        action = "edited"

    after = current_players(page, rid) if action != "unchanged" else current
    have = {p["org"] for p in after["players"]}
    absent = [n for n, m in found.items() if m["org"] not in have]
    if absent:
        raise RuntimeError(f"Still missing after {action}: {', '.join(absent)}")
    return {
        "status": action,
        "reservation": rid,
        "removed": [p["name"] for p in remove],
        "added": [n for n, m in found.items() if m in add],
        "players": [p["name"] for p in after["players"]],
        "unmatched": missing,
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
        edit(page, rid, [target["org"]], [])
    swap(page, rid, me["org"], target["member"])
    still_mine = find_reservation(page, day, court) == rid
    after = current_players(page, rid) if still_mine else None
    if after and not any(p["org"] == target["org"] for p in after["players"]):
        raise RuntimeError(f"{to_cr_name} is not on the reservation after the swap")
    return {"status": "transferred", "reservation": rid, "still_mine": still_mine}
