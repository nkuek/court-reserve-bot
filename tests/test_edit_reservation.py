import importlib.util
import json
import unittest
from unittest import mock

from playwright.sync_api import sync_playwright

from utils.cr_roster import Rejected

# The reservation page and Edit Reservation modal as CourtReserve renders them. The modal root has no
# box, rows carry no id, and each search option holds its org member id only as a React key.
PAGE = """
<div id="root">
  <h1 data-testid="reservation-details-title">Pickleball Court 5A</h1>
  <button type="button" class="ant-btn">Edit Reservation</button>
</div>
<script>
const players = [
  {org: '100', name: 'Self', self: true},
  {org: '101', name: 'Placeholder #1'},
  {org: '102', name: 'Placeholder #2'},
  {org: '103', name: 'Placeholder #3'},
];
const members = [
  {org: '201', name: 'Pat Doe'},
  {org: '202', name: 'Pat Doe'},
  {org: '203', name: 'Sam Roe'},
  {org: '204', name: 'Lee Poe', refused: 'Lee Poe not allowed on this reservation'},
];
let modal;
function rows() {
  return players.map(p => `<tr><td>${p.name}</td><td>${p.self ? '' :
    `<button type="button" aria-label="Remove" data-testid="reservation-player-remove-o${p.org}">x</button>`}</td></tr>`).join('');
}
function render() {
  modal.querySelector('tbody').innerHTML = rows();
  for (const b of modal.querySelectorAll('[data-testid^=reservation-player-remove-]')) {
    b.onclick = () => {
      players.splice(players.findIndex(p => b.dataset.testid.endsWith('o' + p.org)), 1);
      render();
    };
  }
}
function closePortal() {
  document.getElementById('portal')?.remove();
}
function confirmAdd(m) {
  closePortal();
  if (m.refused) {
    const t = document.createElement('div');
    t.setAttribute('role', 'alert');
    t.textContent = m.refused;
    document.body.appendChild(t);
    return;
  }
  const d = document.createElement('div');
  d.id = 'portal';
  d.style.cssText = 'position:fixed;top:300px;left:0;z-index:10;background:#fff';
  d.innerHTML = '<div role="dialog">Add player?<button type="button">Cancel</button><button type="button">Yes</button></div>';
  document.body.appendChild(d);
  // The confirm fades out after the row appears, as the real one animates closed.
  d.querySelectorAll('button')[1].onclick = () => { players.push(m); render(); d.id = ''; setTimeout(() => d.remove(), 300); };
}
function search(q) {
  closePortal();
  if (q.length < 2) return;
  const d = document.createElement('div');
  d.id = 'portal';
  d.style.cssText = 'position:fixed;top:300px;left:0;z-index:10;background:#fff';
  for (const m of members.filter(m => m.name.toLowerCase().includes(q.toLowerCase()))) {
    const o = document.createElement('div');
    o.textContent = m.name;
    o['__reactFiber$test'] = {key: m.org};
    o.onmousedown = () => confirmAdd(m);
    d.appendChild(o);
  }
  document.body.appendChild(d);
}
document.querySelector('button').onclick = () => setTimeout(() => {
  modal = document.createElement('div');
  modal.dataset.testid = 'reservation-edit-modal';
  modal.style.cssText = 'width:0;height:0';
  modal.innerHTML = `<div style="position:fixed;top:0;left:0;width:600px;background:#fff">
    <div data-testid="reservation-additional-player-search"><input placeholder="Search for other player(s) to play with"></div>
    <table><tbody></tbody></table>
    <button type="button" data-testid="reservation-mobile-back-btn">Close</button>
    <button type="button" data-testid="reservation-mobile-submit-btn">Save</button>
  </div>`;
  document.body.appendChild(modal);
  modal.querySelector('input').oninput = e => search(e.target.value);
  modal.querySelector('[data-testid=reservation-mobile-submit-btn]').onclick = async () => {
    const r = await fetch('/Online/Reservations/UpdateMyReservation/8449', {method: 'POST', body: JSON.stringify(players.map(p => p.org))});
    if (r.ok) modal.remove();
  };
  render();
}, 50);
</script>
"""


class EditReservationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("utils.cr_roster"))
        from utils import cr_roster

        self.cr_roster = cr_roster
        self.page = self.browser.new_page()
        self.page.set_default_timeout(3000)
        self.saved = []
        self.page.route("**/Online/MyProfile/Reservation/**", lambda r: r.fulfill(content_type="text/html", body=PAGE))

        def save(route):
            self.saved.append(json.loads(route.request.post_data))
            route.fulfill(content_type="application/json", body='{"isValid":true}')

        self.page.route("**/UpdateMyReservation/**", save)

    def tearDown(self):
        self.page.close()

    def edit(self, remove: list[str], adds: dict[str, str]) -> Exception | None:
        """Runs the edit and returns what it raised, so a missing control fails on an assertion."""
        try:
            self.cr_roster.edit(self.page, "64460797", remove, adds)
        except Exception as e:
            return e
        return None

    def test_swaps_placeholders_for_members_picked_by_org_id(self):
        # Two members share the name Pat Doe. The add must pick org 202, not the first option.
        self.assertIsNone(self.edit(["102", "103"], {"202": "Pat Doe", "203": "Sam Roe"}))
        self.assertEqual(self.saved, [["100", "101", "202", "203"]])

    def test_removes_without_adding(self):
        self.assertIsNone(self.edit(["101"], {}))
        self.assertEqual(self.saved, [["100", "102", "103"]])

    def test_add_refused_with_a_toast_raises_rejected_and_saves_nothing(self):
        with mock.patch.object(self.cr_roster, "ADD_TIMEOUT_MS", 500, create=True):
            error = self.edit([], {"204": "Lee Poe"})
        self.assertIsInstance(error, Rejected)
        self.assertEqual(error.message, "Lee Poe not allowed on this reservation")
        self.assertEqual(self.saved, [])

# The mobile reservation page labels its Substitute buttons "Sub". The Substitute page tags each favorite with its org member id.
MOBILE_PAGE = """
<div><span>Placeholder #1</span><button type="button" onclick="location.href='/Online/MyProfile/SwapPlayers/8449?reservationId=1&orgMemberId=101'">Sub</button></div>
"""
SWAP_PAGE = """
<div data-testid="swap-players-search"><input></div>
<div data-testid="swap-players-fav-7221843">Pat Doe</div>
<div data-testid="swap-players-fav-10270428">Sam Roe</div>
<button type="button" data-testid="swap-players-confirm-btn">Confirm</button>
"""


class FavoritesTest(unittest.TestCase):
    def test_reads_favorite_org_ids_from_the_mobile_substitute_page(self):
        self.assertIsNotNone(importlib.util.find_spec("utils.cr_roster"))
        from utils import cr_roster

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)

            def context(**kw):
                ctx = browser.new_context(**kw)
                ctx.set_default_timeout(3000)
                ctx.route("**/Online/MyProfile/Reservation/**", lambda r: r.fulfill(content_type="text/html", body=MOBILE_PAGE))
                ctx.route("**/Online/MyProfile/SwapPlayers/**", lambda r: r.fulfill(content_type="text/html", body=SWAP_PAGE))
                return ctx

            fake = mock.Mock(new_context=context)
            env = {"EMAIL": "bot@example.com", "PASSWORD": "x"}
            with mock.patch.object(cr_roster, "get_browser", return_value=fake), \
                    mock.patch.object(cr_roster, "_attempt_login"), mock.patch.dict("os.environ", env):
                try:
                    favs = cr_roster._favorites("1")
                except Exception as e:
                    favs = e
            browser.close()
        self.assertEqual(favs, {"7221843", "10270428"})

if __name__ == "__main__":
    unittest.main()
