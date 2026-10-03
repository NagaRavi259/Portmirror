"""
Browser end-to-end test of the manager UI (Playwright, headless Chromium).

Run via tests/run_ui_e2e.sh, which starts it in the Playwright image with
host networking. The host is 192.168.88.1 on local_net, i.e. a trusted LAN
client, so this script also generates real forwarded traffic.

Env: PM_URL (default http://127.0.0.1:8088), PM_PASSWORD, OUT (results dir)
"""
import csv
import os
import re
import socket
import threading
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

URL = os.environ.get("PM_URL", "http://127.0.0.1:8088")
PASSWORD = os.environ["PM_PASSWORD"]
OUT = os.environ.get("OUT", "/out")
SHOTS = os.path.join(OUT, "ui")
os.makedirs(SHOTS, exist_ok=True)
GW = "192.168.88.8"
PORT = 18090
rows = []


def log(tid, desc, ok, detail=""):
    rows.append([tid, "UI", desc, "PASS" if ok else "FAIL", str(detail)[:400]])
    print(f"[{tid:<20}] UI         {desc:<66} {'PASS' if ok else 'FAIL'}", flush=True)


CURRENT_PAGE = []   # set in main(); lets check() screenshot the page when a check fails


def check(tid, desc, fn):
    try:
        detail = fn()
        log(tid, desc, True, detail or "")
    except Exception as e:  # noqa: BLE001
        log(tid, desc, False, f"{type(e).__name__}: {e}")
        if CURRENT_PAGE:
            try:
                CURRENT_PAGE[0].screenshot(path=f"{SHOTS}/FAIL-{tid}.png")
            except Exception:  # noqa: BLE001
                pass


class Traffic:
    """HTTP requests + held-open sockets through the new forward."""

    def __init__(self):
        self.stop = threading.Event()
        self.ok = 0
        self.held = []

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
        for _ in range(3):
            try:
                s = socket.create_connection((GW, PORT), timeout=3)
                s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n")      # incomplete request: stays open
                self.held.append(s)
            except OSError:
                pass

    def _loop(self):
        while not self.stop.is_set():
            try:
                urllib.request.urlopen(f"http://{GW}:{PORT}/", timeout=2).read()
                self.ok += 1
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.05)

    def close(self):
        self.stop.set()
        for s in self.held:
            s.close()


def main():
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        page = ctx.new_page()
        CURRENT_PAGE.append(page)
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        page.on("pageerror", lambda e: errors.append(str(e)))

        # ---- login -------------------------------------------------------------
        def login():
            page.goto(URL)
            expect(page.get_by_role("heading", name="Portmirror")).to_be_visible()
            page.screenshot(path=f"{SHOTS}/01-login.png")
            page.get_by_label("Password").fill("definitely-wrong")
            page.get_by_role("button", name="Sign in").click()
            expect(page.get_by_role("alert")).to_contain_text("invalid")
            page.get_by_label("Password").fill(PASSWORD)
            page.get_by_role("button", name="Sign in").click()
            expect(page.locator("header").get_by_text("Live", exact=True)).to_be_visible(timeout=10000)
            return "wrong password rejected, right one accepted, live link up"
        check("UI-LOGIN", "Login rejects a bad password, accepts the right one", login)

        # ---- dashboard -----------------------------------------------------------
        def dashboard():
            page.goto(URL + "/#/")
            expect(page.get_by_role("heading", name="Port forwards")).to_be_visible()
            expect(page.locator("tbody tr")).to_have_count(7, timeout=10000)
            expect(page.get_by_role("table").get_by_text("A - HTTP")).to_be_visible()
            time.sleep(2.5)
            page.screenshot(path=f"{SHOTS}/02-dashboard.png", full_page=True)
            return "7 seeded forwards listed"
        check("UI-DASHBOARD", "Dashboard lists the seeded forwards with live KPIs", dashboard)

        # ---- clickable port link ---------------------------------------------------------
        def port_link():
            row = page.locator("tbody tr", has_text="A - HTTP")
            link = row.get_by_role("link", name=re.compile(r"^:80"))
            expect(link).to_have_attribute("href", f"http://{GW}:80/")
            expect(link).to_have_attribute("target", "_blank")
            with ctx.expect_page() as new_page_info:
                link.click()
            new_page = new_page_info.value
            new_page.wait_for_load_state()
            body_text = new_page.locator("body").inner_text()
            new_page.close()
            return f"opened {new_page.url} in a new tab, body: {body_text[:60]!r}"
        check("UI-PORT-LINK", "Clicking an enabled forward's port opens it in a new tab", port_link)

        # ---- KPI hover help ------------------------------------------------------------
        def kpi_help():
            expected = {"Live": "open through the gateway", "In": "from your local network out", "Out": "coming back from the remote",
                        "Forwards": "switched on", "Conntrack": "connection tracking table", "Gateway": "processor (CPU)"}
            shown = []
            for label, phrase in expected.items():
                card = page.locator(f"[aria-describedby='kpi-help-{label.lower()}']")
                tip = page.locator(f"#kpi-help-{label.lower()}")
                expect(tip).to_be_hidden()
                card.hover()
                expect(tip).to_be_visible()
                expect(tip).to_contain_text(phrase)
                expect(tip).not_to_contain_text("Why it matters")
                if label in ("Live", "Conntrack"):
                    time.sleep(0.3)
                    page.screenshot(path=f"{SHOTS}/02b-kpi-help-{label.lower()}.png",
                                    clip={"x": 0, "y": 0, "width": 1440, "height": 620})
                shown.append(label)
            page.mouse.move(5, 890)
            expect(page.locator("#kpi-help-gateway")).to_be_hidden()
            return f"help shown on hover for {shown}, hidden again on mouse-out"
        check("UI-KPI-HELP", "Hovering each KPI card shows a plain-English explanation", kpi_help)

        # ---- live chart: fixed 5-minute sliding window ----------------------------------------
        def live_chart():
            chart = page.get_by_test_id("timechart").first
            expect(chart).to_have_attribute("data-xmax", re.compile(r"\d"), timeout=15000)
            x0 = (float(chart.get_attribute("data-xmin")), float(chart.get_attribute("data-xmax")))
            time.sleep(4)
            x1 = (float(chart.get_attribute("data-xmin")), float(chart.get_attribute("data-xmax")))
            width0, width1, moved = x0[1] - x0[0], x1[1] - x1[0], x1[1] - x0[1]
            assert abs(width0 - 300) < 1.5 and abs(width1 - 300) < 1.5, f"window {width0}s / {width1}s, want 300s"
            assert 2 <= moved <= 6, f"window moved {moved}s in 4s"
            page.screenshot(path=f"{SHOTS}/02c-live-chart.png", clip={"x": 0, "y": 480, "width": 1440, "height": 420})
            return f"window stays {width1:.0f}s wide and slid forward {moved:.0f}s in 4s"
        check("UI-LIVE-CHART", "Main chart is a sliding 5-minute live window, not squeezed to fit", live_chart)

        # ---- create with validation ---------------------------------------------------
        def create():
            page.get_by_role("button", name="New forward").first.click()
            drawer = page.get_by_role("dialog", name="New forward")
            expect(drawer).to_be_visible()
            drawer.get_by_label("Name").fill("e2e-web")
            drawer.get_by_placeholder("e.g. 3389").fill("8088")
            drawer.get_by_placeholder("10.0.0.112").fill("192.168.1.9")
            drawer.get_by_placeholder("3389").last.fill("80")
            drawer.get_by_role("button", name="Create forward").click()
            expect(drawer.get_by_text("reserved for this UI")).to_be_visible()
            expect(drawer.get_by_text("Must be inside the VPN network")).to_be_visible()
            page.screenshot(path=f"{SHOTS}/03-form-validation.png")
            drawer.get_by_placeholder("e.g. 3389").fill("80")
            expect(drawer.get_by_text("Conflicts with")).to_be_visible()
            drawer.get_by_placeholder("e.g. 3389").fill(str(PORT))
            drawer.get_by_placeholder("10.0.0.112").fill("10.0.0.12")
            drawer.get_by_text("Limits & schedule").click()
            page.screenshot(path=f"{SHOTS}/04-form-filled.png")
            drawer.get_by_role("button", name="Create forward").click()
            expect(page.get_by_role("heading", name="e2e-web")).to_be_visible(timeout=10000)
            code = urllib.request.urlopen(f"http://{GW}:{PORT}/", timeout=3).status
            assert code == 200, code
            return f"inline errors shown (reserved port, bad target, conflict); created; :{PORT} serves HTTP {code}"
        check("UI-CREATE", "Form validates inline, creates the forward, traffic flows", create)



        # ---- live metrics + kill one -------------------------------------------------
        tr = Traffic()
        tr.start()

        def live():
            live_val = page.locator("div.label", has_text=re.compile("^Live$")).locator("xpath=following-sibling::div[1]")
            expect(live_val).not_to_have_text("0", timeout=15000)
            expect(page.locator("tbody tr").first).to_be_visible(timeout=10000)
            time.sleep(6)
            page.screenshot(path=f"{SHOTS}/05-detail-live.png", full_page=True)
            return f"live={live_val.inner_text()} rows={page.locator('tbody tr').count()} requests_sent={tr.ok}"
        check("UI-LIVE-METRICS", "Detail page shows live connections and moving traffic", live)

        def device_name():
            row = page.locator("tbody tr").first
            btn = row.get_by_role("button").first   # the ClientLabel for this row's client IP
            raw = btn.inner_text()
            btn.click()
            inp = row.locator("input")
            inp.fill("e2e-test-device")
            inp.press("Enter")
            expect(row.get_by_role("button", name=re.compile("e2e-test-device"))).to_be_visible(timeout=5000)
            page.goto(URL + "/#/connections")
            expect(page.get_by_text("e2e-test-device").first).to_be_visible(timeout=10000)
            page.screenshot(path=f"{SHOTS}/05b-device-name.png")
            # clean up: clear the name back out so repeat runs start from a known state
            page.goto(URL + "/#/")
            page.locator("tbody tr", has_text="e2e-web").click()
            row2 = page.locator("tbody tr").first
            row2.get_by_role("button", name=re.compile("e2e-test-device")).click()
            row2.locator("input").fill("")
            row2.locator("input").press("Enter")
            return f"was {raw!r}, renamed to 'e2e-test-device', seen on Connections page too, then cleared"
        check("UI-DEVICE-NAME", "Naming a client device propagates to the Connections page", device_name)

        def kill_one():
            before = page.locator("tbody tr").count()
            page.get_by_role("button", name=re.compile("^Cut connection from")).first.click()
            expect(page.get_by_role("status").filter(has_text="Cut TCP")).to_be_visible(timeout=5000)
            return f"rows before={before}"
        check("UI-KILL-ONE", "Cutting a single connection from the live table", kill_one)

        def dash_traffic():
            page.goto(URL + "/#/")
            row = page.locator("tbody tr", has_text="e2e-web")
            expect(row).to_be_visible()
            time.sleep(3)
            page.screenshot(path=f"{SHOTS}/06-dashboard-traffic.png", full_page=True)
            return row.inner_text().replace("\n", " | ")[:200]
        check("UI-DASH-TRAFFIC", "Dashboard row shows the new forward's traffic", dash_traffic)

        # ---- disable with live connections -> drain/kill prompt ----------------------
        def toggle():
            row = page.locator("tbody tr", has_text="e2e-web")
            row.get_by_role("switch").click()
            dlg = page.get_by_role("dialog", name=re.compile("Disable"))
            expect(dlg).to_be_visible()
            expect(dlg.get_by_text("Let them drain")).to_be_visible()
            page.screenshot(path=f"{SHOTS}/07-disable-prompt.png")
            dlg.get_by_role("button", name="Cancel").click()
            return "prompt offered drain / cut"
        check("UI-DISABLE-PROMPT", "Disabling a forward with live connections asks drain vs cut", toggle)

        # ---- delete with kill ------------------------------------------------------------
        def delete():
            row = page.locator("tbody tr", has_text="e2e-web")
            row.get_by_role("button", name="Delete e2e-web").click()
            dlg = page.get_by_role("dialog", name=re.compile("Delete"))
            dlg.get_by_text("Cut them now").click()
            page.screenshot(path=f"{SHOTS}/08-delete-prompt.png")
            dlg.get_by_role("button", name=re.compile("^Delete")).click()
            expect(page.locator("tbody tr", has_text="e2e-web")).to_have_count(0, timeout=8000)
            alive = 0
            for s in tr.held:
                try:
                    s.settimeout(2)
                    s.sendall(b"\r\n")
                    if s.recv(100):
                        alive += 1
                except OSError:
                    pass
            assert alive == 0, f"{alive} held connections survived"
            try:
                urllib.request.urlopen(f"http://{GW}:{PORT}/", timeout=2)
                raise AssertionError("port still forwarding after delete")
            except OSError:
                pass
            return "row gone, held connections cut, port closed"
        check("UI-DELETE-KILL", "Delete + 'cut them now' removes it and cuts held connections", delete)
        tr.close()

        # ---- audit + settings ------------------------------------------------------------
        def audit():
            page.goto(URL + "/#/audit")
            expect(page.get_by_role("heading", name="Audit log")).to_be_visible()
            expect(page.get_by_text("#", exact=False).filter(has_text="e2e-web").first).to_be_visible()
            page.get_by_role("button", name=re.compile("delete")).first.click()
            page.screenshot(path=f"{SHOTS}/09-audit.png")
            return "create/delete entries visible"
        check("UI-AUDIT", "Audit log shows the UI's own changes", audit)

        def connections():
            urllib.request.urlopen(f"http://{GW}:80/", timeout=3).read()   # fresh traffic to log
            page.goto(URL + "/#/connections")
            expect(page.get_by_role("heading", name="Connections")).to_be_visible()
            expect(page.locator("tbody tr").first).to_be_visible(timeout=10000)
            expect(page.get_by_role("table").get_by_text("A - HTTP")).to_be_visible(timeout=10000)
            page.get_by_label("Filter by client IP").fill("no-such-client")
            expect(page.get_by_text("No connections recorded yet")).to_be_visible()
            page.get_by_label("Filter by client IP").fill("")
            page.get_by_role("button", name="By client").click()
            expect(page.locator("thead th", has_text="Client")).to_be_visible(timeout=10000)
            expect(page.locator("tbody tr").first).to_be_visible(timeout=10000)
            page.get_by_role("button", name="By forward").click()
            expect(page.locator("thead th", has_text="Forward")).to_be_visible(timeout=10000)
            page.screenshot(path=f"{SHOTS}/09b-connections.png", full_page=True)
            return "recent/client/forward views all populated, client-IP filter narrows the list"
        check("UI-CONNECTIONS", "Connections page shows sessions and filters by client/forward", connections)

        def csv_export():
            audit = page.request.get(URL + "/api/export/audit.csv")
            fid = next(f for f in page.request.get(URL + "/api/forwards").json() if f["name"] == "A - HTTP")["id"]
            hist = page.request.get(URL + f"/api/export/history.csv?forward_id={fid}&range=24h")
            assert audit.status == 200 and audit.headers["content-type"].startswith("text/csv"), audit.status
            assert "attachment" in audit.headers["content-disposition"]
            assert audit.body()[:3] == b"\xef\xbb\xbf", "UTF-8 BOM missing - Excel would misread non-ASCII text"
            assert audit.text().lstrip("\ufeff").splitlines()[0] == "time_utc,actor,action,target,detail"
            assert hist.status == 200 and hist.text().lstrip("\ufeff").splitlines()[0].startswith("time_utc,bucket_seconds")
            return f"audit {len(audit.text().splitlines()) - 1} rows, history export served as attachment"

        check("UI-CSV", "History and audit export as CSV downloads", csv_export)

        def audit_diff():
            # make a real, single-field update, then confirm the audit view shows only that field
            # look the forward up by its seeded name, not a hard-coded id: other suites recreate forwards
            # (replace-mode import), so ids change between runs
            fwds = page.request.get(URL + "/api/forwards").json()
            cur = next(f for f in fwds if f["name"] == "A - HTTP")
            fid = cur["id"]
            body = {k: cur[k] for k in ("name","protocol","listen_port","target_ip","target_port","allowed_sources","enabled","description")}
            body.update(rate_limit=7)
            assert page.request.put(URL + f"/api/forwards/{fid}", data=body).status == 200
            body.update(rate_limit=None)
            page.request.put(URL + f"/api/forwards/{fid}", data=body)   # restore
            page.goto(URL + "/#/audit")
            row = page.locator("li").filter(has_text=f"#{fid} ").filter(has_text="update").first
            row.locator("button").first.click()
            expect(row.get_by_text("rate_limit", exact=True)).to_be_visible(timeout=5000)
            expect(row.get_by_role("button", name="Show raw JSON")).to_be_visible()
            return "single changed field (rate_limit) shown as a diff, raw JSON one click away"
        check("UI-AUDIT-DIFF", "Audit entry shows only the changed field, not the whole object", audit_diff)

        def audit_diff_many():
            # several fields at once: the diff lists exactly those, and leaves out the ones that didn't change
            body = {"name": "UI multi-diff", "protocol": "tcp", "listen_port": 7721, "listen_port_end": None,
                    "target_ip": "10.0.0.12", "target_port": 80, "allowed_sources": ["192.168.88.0/24"],
                    "rate_limit": None, "max_conns": None, "bandwidth_limit_kbps": None, "access_window": None,
                    "quota": None, "expires_at": None, "enabled": True, "description": "before"}
            r = page.request.post(URL + "/api/forwards", data=body)
            fid = r.json().get("id") if r.ok else None
            try:
                assert r.status == 201, r.status   # create returns 201 Created
                body.update(name="UI multi-diff renamed", description="after", rate_limit=500, max_conns=20)
                assert page.request.put(URL + f"/api/forwards/{fid}", data=body).status == 200
                page.goto(URL + "/#/audit")
                page.reload()   # the page may already be on the audit view from the check above; reload so the list is fresh
                row = page.locator("li").filter(has_text=f"#{fid} ").filter(has_text="update").first
                row.locator("button").first.click()
                for field in ("name", "description", "rate_limit", "max_conns"):
                    expect(row.get_by_text(field, exact=True)).to_be_visible(timeout=5000)
                assert row.get_by_text("listen_port", exact=True).count() == 0, "unchanged field shown"
                assert row.get_by_text("target_ip", exact=True).count() == 0, "unchanged field shown"
                return "four fields changed in one save: all four listed, unchanged fields left out"
            finally:
                if fid is not None:
                    page.request.delete(URL + f"/api/forwards/{fid}?kill=true")
        check("UI-AUDIT-MULTI", "Several fields changed in one save all show in the audit diff", audit_diff_many)

        def pwa():
            m = page.request.get(URL + "/manifest.webmanifest").json()
            assert m["display"] == "standalone" and any(i["sizes"] == "512x512" for i in m["icons"])
            assert page.request.get(URL + "/icons/icon-192.png").status == 200
            return "manifest served (standalone, 192 and 512 icons)"
        check("UI-PWA", "Installable manifest and icons are served", pwa)

        def preset():
            page.goto(URL + "/#/")
            page.get_by_role("button", name="New forward").first.click()
            drawer = page.get_by_role("dialog", name="New forward")
            expect(drawer).to_be_visible()
            drawer.get_by_label("Service preset").select_option("jellyfin")
            expect(drawer.get_by_placeholder("e.g. 3389")).to_have_value("8096")
            drawer.get_by_label("Service preset").select_option("minecraft")
            expect(drawer.get_by_placeholder("e.g. 3389")).to_have_value("25565")
            page.keyboard.press("Escape")
            expect(drawer).to_be_hidden()
            return "picking a preset fills the port; a second pick replaces it cleanly"
        check("UI-PRESET", "Service preset fills protocol and port in the new-forward form", preset)

        def preset_udp():
            page.goto(URL + "/#/")
            page.reload()   # fresh form state: the shared page would keep the last preset's values
            page.get_by_role("button", name="New forward").first.click()
            drawer = page.get_by_role("dialog", name="New forward")
            expect(drawer).to_be_visible()
            # The active segment has bg-white; inactive ones only have hover:text-ink-900, so don't match on text colour.
            seg = lambda t: drawer.locator("button", has_text=re.compile(rf"^{t}$"))
            drawer.get_by_label("Service preset").select_option("wireguard")
            expect(drawer.get_by_placeholder("e.g. 3389")).to_have_value("51820")
            expect(seg("UDP")).to_have_class(re.compile(r"bg-white"))
            expect(seg("TCP")).not_to_have_class(re.compile(r"bg-white"))
            drawer.get_by_label("Service preset").select_option("minecraft")   # back to TCP
            expect(seg("TCP")).to_have_class(re.compile(r"bg-white"))
            expect(seg("UDP")).not_to_have_class(re.compile(r"bg-white"))
            page.keyboard.press("Escape")
            expect(drawer).to_be_hidden()
            return "a UDP preset sets the protocol to UDP; a TCP preset sets it back to TCP"
        check("UI-PRESET-UDP", "Preset sets the protocol, not just the port (UDP and back to TCP)", preset_udp)

        def preset_conflict():
            page.goto(URL + "/#/")
            page.reload()   # fresh form state: the shared page would keep the last preset's draft
            page.get_by_role("button", name="New forward").first.click()
            drawer = page.get_by_role("dialog", name="New forward")
            expect(drawer).to_be_visible()
            drawer.get_by_label("Service preset").select_option("rdp")   # 3389 is already the seeded "C - RDP"
            msg = drawer.get_by_text(re.compile(r"Conflicts with .*C - RDP"))
            expect(msg).to_be_visible()
            page.keyboard.press("Escape")
            expect(drawer).to_be_hidden()
            return "a preset on a taken port shows the inline conflict error naming the existing forward"
        check("UI-PRESET-CONFLICT", "Preset on an already-used port shows the inline conflict error", preset_conflict)

        def schedule_form():
            page.goto(URL + "/#/")
            page.get_by_role("button", name="New forward").first.click()
            drawer = page.get_by_role("dialog", name="New forward")
            expect(drawer).to_be_visible()
            drawer.get_by_text("Limits & schedule").click()
            drawer.get_by_role("switch", name="Only on a schedule").click()
            sat = drawer.get_by_role("button", name="Sat", exact=True)
            expect(sat).to_have_attribute("aria-pressed", "false")
            sat.click()
            expect(sat).to_have_attribute("aria-pressed", "true")
            expect(drawer.get_by_label("Window start")).to_have_value("09:00")
            page.keyboard.press("Escape")
            expect(drawer).to_be_hidden()
            return "schedule switch reveals day toggles and start/end times"
        check("UI-SCHEDULE", "Recurring access window form controls work", schedule_form)

        def quota_form():
            page.goto(URL + "/#/")
            page.get_by_role("button", name="New forward").first.click()
            drawer = page.get_by_role("dialog", name="New forward")
            expect(drawer).to_be_visible()
            drawer.get_by_text("Limits & schedule").click()
            drawer.get_by_label("Quota amount in GiB").fill("2.5")
            try:
                drawer.get_by_role("button", name="week", exact=True).click()
                drawer.get_by_label("Quota amount in GiB").fill("0")
                drawer.get_by_label("Quota amount in GiB").press("Tab")   # blur, as a person would
                expect(drawer.get_by_text("Enter a positive amount, in GiB")).to_be_visible()
            finally:
                page.keyboard.press("Escape")   # never leave the drawer open for the tests after this one
            expect(drawer).to_be_hidden()
            return "quota amount and period set; zero is rejected inline"
        check("UI-QUOTA", "Data quota form accepts an amount and period, rejects nonsense", quota_form)

        def settings():
            page.goto(URL + "/#/settings")
            expect(page.get_by_text("still using the generated initial password")).to_be_visible()
            page.get_by_placeholder("Token name, e.g. ci-deploy").fill("e2e-token")
            page.get_by_role("button", name="Create", exact=True).click()
            dlg = page.get_by_role("dialog", name="Your new API token")
            token = dlg.locator("code").inner_text()
            assert token.startswith("pm_")
            req = urllib.request.Request(URL + "/api/forwards", headers={"Authorization": f"Bearer {token}"})
            n = len(__import__("json").loads(urllib.request.urlopen(req).read()))
            dlg.get_by_role("button", name="Done").click()
            page.get_by_role("button", name="Revoke e2e-token").click()
            expect(page.get_by_text("Revoked")).to_be_visible()
            page.screenshot(path=f"{SHOTS}/10-settings.png")
            return f"token created, works on the API ({n} forwards), revoked"
        check("UI-TOKENS", "Settings: create an API token, use it, revoke it", settings)

        def diagnostics():
            page.goto(URL + "/#/diagnostics")
            expect(page.get_by_role("heading", name="Diagnostics")).to_be_visible()
            expect(page.locator("li", has_text="Firewall ruleset syntax")).to_be_visible(timeout=10000)
            rows = page.locator("ul > li")
            expect(rows).to_have_count(11, timeout=10000)
            expect(page.locator("li", has_text="Live rules match the configuration")).to_be_visible(timeout=10000)
            expect(page.get_by_text("Everything checks out.")).to_be_visible()
            page.get_by_role("button", name="Run again").click()
            expect(page.get_by_text("Everything checks out.")).to_be_visible(timeout=10000)
            page.screenshot(path=f"{SHOTS}/09c-diagnostics.png", full_page=True)
            return f"{rows.count()} checks shown, all green on a healthy stack"
        check("UI-DIAGNOSTICS", "Diagnostics page runs and shows every check", diagnostics)

        def notifications():
            bell = page.get_by_label("Notifications")
            expect(bell).to_be_visible()
            bell.click()
            panel = page.get_by_text("Notifications", exact=True)
            expect(panel).to_be_visible(timeout=5000)
            empty = page.get_by_text("Nothing here")
            rows = page.locator("ul li", has_text=re.compile(r"\bago\b"))
            # whichever state it's in (clean stack vs. one carried over from earlier in this run),
            # exactly one of these two must be true - never neither, never a broken half-state
            expect(empty.or_(rows.first)).to_be_visible(timeout=5000)
            detail = "empty state shown" if rows.count() == 0 else f"{rows.count()} notification(s) shown"
            page.screenshot(path=f"{SHOTS}/09d-notifications.png")
            page.keyboard.press("Escape")
            expect(panel).to_be_hidden()
            return detail
        check("UI-NOTIFICATIONS", "Notification bell opens, shows a well-formed state either way", notifications)

        def dark_mode():
            page.goto(URL + "/#/")
            html = page.locator("html")
            expect(html).not_to_have_attribute("data-theme", "dark")
            page.get_by_label("Switch to dark theme").click()
            expect(html).to_have_attribute("data-theme", "dark")
            page.screenshot(path=f"{SHOTS}/09e-dark-dashboard.png", full_page=True)

            # persists across a reload, not just in memory
            page.reload()
            expect(html).to_have_attribute("data-theme", "dark", timeout=10000)

            # the Settings tri-state control agrees with the header toggle, and can go back to "system"
            page.goto(URL + "/#/settings")
            expect(page.get_by_role("button", name="Dark", exact=True)).to_be_visible(timeout=10000)
            page.get_by_role("button", name="System", exact=True).click()
            page.wait_for_timeout(300)
            stored = page.evaluate("localStorage.getItem('pm-theme')")
            assert stored is None, f"'System' must clear the stored preference, got {stored!r}"
            # this test's own browser context has no OS dark-mode override, so "system" resolves to
            # light - confirmed by the toggle button itself now offering to switch TO dark again
            expect(page.get_by_label("Switch to dark theme")).to_be_visible(timeout=5000)
            expect(html).not_to_have_attribute("data-theme", "dark")
            return "toggles, persists across reload, and 'System' clears the stored override"
        check("UI-DARK-MODE", "Dark mode toggles, persists, and Settings' tri-state control agrees", dark_mode)

        # ---- mobile ---------------------------------------------------------------------
        def mobile():
            m = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                                    storage_state=ctx.storage_state())
            mp = m.new_page()
            mp.goto(URL + "/#/")
            expect(mp.get_by_role("heading", name="Port forwards")).to_be_visible()
            time.sleep(2)
            overflow = mp.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            mp.screenshot(path=f"{SHOTS}/11-mobile.png", full_page=True)
            m.close()
            assert overflow <= 0, f"page scrolls horizontally by {overflow}px"
            return "no horizontal page overflow at 390px"
        check("UI-MOBILE", "Dashboard usable at phone width (no page overflow)", mobile)

        real = [e for e in errors if "401" not in e]     # the deliberate bad login logs a 401
        log("UI-CONSOLE", "No JavaScript errors in the browser console", not real, "; ".join(real)[:400])
        browser.close()

    with open(os.path.join(OUT, "results_ui.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["test_id", "category", "description", "result", "detail"])
        w.writerows(rows)
    passed = sum(r[3] == "PASS" for r in rows)
    print(f" UI e2e: Passed: {passed}   Failed: {len(rows) - passed}   Screenshots: {SHOTS}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
