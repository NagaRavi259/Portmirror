"""portmirror manager - REST + WebSocket API and the static UI."""
import asyncio
import ipaddress
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from . import config, diag, engine
from . import export as csvexport
from .auth import COOKIE, Auth
from .collector import Collector
from .models import DeviceNameIn, ForwardIn, ImportIn, KillConnIn, LoginIn, NotificationStateIn, PasswordIn, ToggleIn, TokenIn
from .notify import Notifier
from .prober import Prober
from .service import Manager
from .store import ConflictError, Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pm")

config.STATE_DIR.mkdir(parents=True, exist_ok=True)
store = Store(config.DB_PATH)
notifier = Notifier(store)
auth = Auth(store, notifier)
manager = Manager(store, notifier)
prober = Prober(manager.forwards, notifier)
collector = Collector(store, manager.forwards, prober)
manager.live_bytes = collector.current_minute_bytes


def _bump():
    collector.version += 1


manager.on_change = _bump


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.bootstrap()
    await manager.seed_if_empty()
    try:
        await manager.reapply("system", "manager.start")
    except engine.NftError as e:
        log.error("initial apply failed: %s", e)
    tasks = [asyncio.create_task(c) for c in (collector.run(), prober.run(), manager.housekeeping_loop())]
    log.info("manager ready on :%s with %d forwards", config.UI_PORT, len(manager.forwards()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="portmirror manager", version="1.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)


@app.exception_handler(ConflictError)
async def _conflict(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(engine.NftError)
async def _nft(_, exc):
    return JSONResponse({"detail": f"nftables rejected the change: {exc}"}, status_code=500)


@app.exception_handler(KeyError)
async def _missing(_, exc):
    return JSONResponse({"detail": f"forward {exc} not found"}, status_code=404)


RANGE_RE = "^(live|1h|24h|7d|30d|1y|all)$"


def who(request: Request) -> str:
    return auth.require(request)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


# ---- auth -------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {"ok": True, "forwards": len(manager.forwards()), "kernel_table": collector.snapshot["global"].get("kernel_table")}


@app.post("/api/auth/login")
async def login(body: LoginIn, request: Request, response: Response):
    sid = auth.login(body.username, body.password, _client_ip(request))
    response.set_cookie(COOKIE, sid, httponly=True, samesite="strict", max_age=config.SESSION_TTL_S, path="/")
    return {"username": body.username, "must_change_password": config.ADMIN_PASSWORD_FILE.exists()}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    sid = request.cookies.get(COOKIE)
    if sid:
        store.drop_session(sid)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
async def me(actor: str = Depends(who)):
    return {"username": actor, "must_change_password": config.ADMIN_PASSWORD_FILE.exists()}


@app.post("/api/auth/password")
async def password(body: PasswordIn, response: Response, actor: str = Depends(who)):
    if actor.startswith("token:") or actor == "pmctl":
        raise HTTPException(403, "log in with the password to change it")
    auth.change_password(actor, body.current, body.new)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


# ---- forwards -----------------------------------------------------------------
def _view(f, snap=None):
    d = f.model_dump(mode="json")
    d["target_port_end"] = f.target_port_end if f.listen_port_end else None
    d["expired"] = f.expired()
    d["stats"] = (snap or collector.snapshot)["forwards"].get(f.id)
    d["health"] = prober.status.get(f.id)
    d["quota_used"] = manager.quota_usage.get(f.id)
    return d


@app.get("/api/forwards")
async def list_forwards(_: str = Depends(who)):
    snap = collector.snapshot
    return [_view(f, snap) | {"spark": collector.sparkline(f.id)} for f in manager.forwards()]


@app.post("/api/forwards", status_code=201)
async def create_forward(body: ForwardIn, actor: str = Depends(who)):
    f = await manager.create(body, actor)
    asyncio.create_task(prober.probe(f))
    return _view(f)


@app.get("/api/forwards/{fid}")
async def get_forward(fid: int, _: str = Depends(who)):
    f = store.forward(fid)
    if not f:
        raise KeyError(fid)
    return _view(f)


@app.put("/api/forwards/{fid}")
async def update_forward(fid: int, body: ForwardIn, kill: bool = False, actor: str = Depends(who)):
    f = await manager.update(fid, body, actor, kill=kill)
    asyncio.create_task(prober.probe(f))
    return _view(f)


@app.delete("/api/forwards/{fid}")
async def delete_forward(fid: int, kill: bool = False, actor: str = Depends(who)):
    killed = await manager.delete(fid, actor, kill=kill)
    return {"deleted": fid, "killed": killed}


@app.post("/api/forwards/{fid}/toggle")
async def toggle_forward(fid: int, body: ToggleIn, actor: str = Depends(who)):
    return _view(await manager.toggle(fid, body.enabled, actor, kill=body.kill))


@app.get("/api/forwards/{fid}/connections")
async def connections(fid: int, limit: int = Query(500, le=5000), _: str = Depends(who)):
    if not store.forward(fid):
        raise KeyError(fid)
    flows = collector.flows_by_fid.get(fid, [])
    now = datetime.now(timezone.utc).timestamp()
    flows = sorted(flows, key=lambda x: -(x.bytes_in + x.bytes_out))
    return {"total": len(flows), "as_of": collector.snapshot["ts"] or now,
            "connections": [fl.as_dict() for fl in flows[:limit]]}


@app.post("/api/forwards/{fid}/kill")
async def kill_all(fid: int, actor: str = Depends(who)):
    f = store.forward(fid)
    if not f:
        raise KeyError(fid)
    n = await engine.kill_forward_flows(f)
    store.audit(actor, "connections.kill_all", f"#{fid} {f.name}", {"killed": n})
    return {"killed": n}


@app.post("/api/connections/kill")
async def kill_one(body: KillConnIn, actor: str = Depends(who)):
    ok = await engine.kill_flow(body.protocol, str(body.src), body.sport, str(config.GW_LAN_IP), body.dport)
    store.audit(actor, "connections.kill", f"{body.protocol} {body.src}:{body.sport} -> :{body.dport}", {"ok": ok})
    if not ok:
        raise HTTPException(404, "connection not found (already closed?)")
    return {"killed": 1}


@app.get("/api/forwards/{fid}/history")
async def history(fid: int, range: str = Query("live", pattern=RANGE_RE), _: str = Depends(who)):
    return collector.history(fid, range)


# ---- global -------------------------------------------------------------------
@app.get("/api/stats")
async def stats(_: str = Depends(who)):
    return collector.snapshot


@app.get("/api/history")
async def global_history(range: str = Query("live", pattern=RANGE_RE), _: str = Depends(who)):
    return collector.history(0, range)


@app.get("/api/system")
async def system(_: str = Depends(who)):
    return {
        "lan_if": config.LAN_IF, "vpn_if": config.VPN_IF, "lan_net": str(config.LAN_NET), "vpn_net": str(config.VPN_NET),
        "gw_lan_ip": str(config.GW_LAN_IP), "gw_vpn_ip": str(config.GW_VPN_IP), "ui_port": config.UI_PORT,
        "reserved_tcp_ports": sorted(config.RESERVED_TCP_PORTS), "max_range": config.MAX_RANGE,
        "last_apply": manager.last_apply, "hold": config.HOLD_FILE.exists(),
        "history_retention_days": config.HISTORY_RETENTION_S / 86400 if config.HISTORY_RETENTION_S else None,
        "audit_retention_days": config.AUDIT_RETENTION_S / 86400 if config.AUDIT_RETENTION_S else None,
        "connection_log_retention_days": config.CONNECTION_LOG_RETENTION_S / 86400 if config.CONNECTION_LOG_RETENTION_S else None,
        "storage": store.storage_stats(),
        "db_bytes": config.DB_PATH.stat().st_size if config.DB_PATH.exists() else 0,
    }


@app.post("/api/admin/reapply")
async def reapply(actor: str = Depends(who)):
    await manager.reapply(actor, "kernel.reapply")
    return {"ok": True}


@app.get("/api/diag")
async def diagnostics(_: str = Depends(who)):
    checks = await diag.run(manager, collector, prober)
    return {"ok": all(c["status"] in ("ok", "skip") for c in checks), "checks": checks}


@app.get("/api/audit")
async def audit(limit: int = Query(200, le=1000), before: int | None = None, _: str = Depends(who)):
    return store.audit_log(limit, before)


@app.get("/api/export/audit.csv")
async def export_audit_csv(_: str = Depends(who)):
    body = csvexport.audit_csv(store.audit_log(limit=100_000))
    return Response(body, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="portmirror-audit.csv"'})


@app.get("/api/export/history.csv")
async def export_history_csv(forward_id: int = Query(0, ge=0), range: str = Query("all", pattern=RANGE_RE),
                             _: str = Depends(who)):
    h = collector.history(forward_id, range)
    body = csvexport.history_csv(h["points"], h.get("step_s"))
    name = f"portmirror-history-{'all' if forward_id == 0 else f'forward-{forward_id}'}-{range}.csv"
    return Response(body, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/connection-log")
async def connection_log(forward_id: int | None = None, client_ip: str | None = None, since: str | None = None,
                         limit: int = Query(200, le=1000), before: int | None = None, _: str = Depends(who)):
    """Real connections the kernel has observed - who talked to which forward, when, and how much
    data moved - as opposed to /api/audit, which is administrative actions (who changed what)."""
    return store.connection_log(forward_id=forward_id, client_ip=client_ip, since=since, limit=limit, before_id=before)


@app.get("/api/connection-log/summary")
async def connection_log_summary(group_by: str = Query("client", pattern="^(client|forward)$"),
                                 since: str | None = None, limit: int = Query(50, le=500), _: str = Depends(who)):
    return store.connection_log_summary(group_by=group_by, since=since, limit=limit)


@app.get("/api/export")
async def export(_: str = Depends(who)):
    items = [f.model_dump(mode="json", exclude={"id", "created_at", "updated_at"}) for f in manager.forwards()]
    return {"version": 1, "exported_at": datetime.now(timezone.utc).isoformat(), "forwards": items}


@app.post("/api/import")
async def import_(body: ImportIn, actor: str = Depends(who)):
    return await manager.import_forwards(body.forwards, body.mode, actor)


@app.get("/api/tokens")
async def tokens(_: str = Depends(who)):
    return store.tokens()


@app.post("/api/tokens", status_code=201)
async def create_token(body: TokenIn, actor: str = Depends(who)):
    return auth.create_token(body.name, actor)


@app.delete("/api/tokens/{tid}")
async def delete_token(tid: int, actor: str = Depends(who)):
    if not store.delete_token(tid):
        raise HTTPException(404, "token not found")
    store.audit(actor, "token.delete", str(tid))
    return {"deleted": tid}


# ---- device names ---------------------------------------------------------------
@app.get("/api/device-names")
async def device_names(_: str = Depends(who)):
    return store.device_names()


@app.put("/api/device-names/{ip}")
async def set_device_name(ip: str, body: DeviceNameIn, actor: str = Depends(who)):
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise HTTPException(422, "not a valid IP address")
    store.set_device_name(ip, body.name)
    store.audit(actor, "device.name", f"{ip} -> {body.name}")
    return {"ip": ip, "name": body.name}


@app.delete("/api/device-names/{ip}")
async def delete_device_name(ip: str, actor: str = Depends(who)):
    if not store.delete_device_name(ip):
        raise HTTPException(404, "no name set for this address")
    store.audit(actor, "device.unname", ip)
    return {"ok": True}


# ---- notifications ----------------------------------------------------------------
@app.get("/api/notifications")
async def notifications(limit: int = Query(100, le=500), state: str | None = None, _: str = Depends(who)):
    return {"unread": store.unread_notification_count(), "items": store.notifications(limit, state),
            "muted": store.muted_types()}


@app.post("/api/notifications/read-all")
async def mark_all_notifications_read(actor: str = Depends(who)):
    store.mark_all_notifications_read()
    return {"ok": True}


@app.post("/api/notifications/{nid}/state")
async def set_notification_state(nid: int, body: NotificationStateIn, actor: str = Depends(who)):
    if not store.set_notification_state(nid, body.state):
        raise HTTPException(404, "notification not found")
    store.audit(actor, f"notification.{body.state}", str(nid))
    return {"ok": True}


@app.post("/api/notifications/mute/{type_}")
async def mute_notification_type(type_: str, actor: str = Depends(who)):
    store.mute_type(type_)
    store.audit(actor, "notification.mute", type_)
    return {"ok": True}


@app.delete("/api/notifications/mute/{type_}")
async def unmute_notification_type(type_: str, actor: str = Depends(who)):
    store.unmute_type(type_)
    store.audit(actor, "notification.unmute", type_)
    return {"ok": True}


# ---- live stream --------------------------------------------------------------
@app.websocket("/api/ws")
async def ws(websocket: WebSocket):
    if not auth.require_ws(websocket):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    try:
        while websocket.application_state == WebSocketState.CONNECTED:
            await websocket.send_json({"type": "snapshot", **collector.snapshot,
                                       "spark": {fid: collector.sparkline(fid) for fid in collector.snapshot["forwards"]}})
            try:
                await asyncio.wait_for(collector.tick_event.wait(), 3)
            except asyncio.TimeoutError:
                pass
    except (WebSocketDisconnect, RuntimeError):
        pass


# ---- UI -----------------------------------------------------------------------
if config.UI_DIR.exists():
    app.mount("/assets", StaticFiles(directory=config.UI_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = config.UI_DIR / path
        if path and candidate.is_file() and config.UI_DIR in candidate.resolve().parents:
            return FileResponse(candidate)
        return FileResponse(config.UI_DIR / "index.html")
