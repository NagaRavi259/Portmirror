"""Single admin account + API tokens. Sessions are random ids (stored hashed) in an httponly cookie."""
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from fastapi import HTTPException, Request, WebSocket

from . import config
from .store import Store

log = logging.getLogger("pm.auth")
ph = PasswordHasher()
COOKIE = "pm_session"
ADMIN = "admin"


class Auth:
    def __init__(self, store: Store, notifier=None):
        self.store = store
        self.notifier = notifier
        self.failures: dict[str, deque] = defaultdict(deque)
        self.internal_token = ""

    def bootstrap(self):
        if not self.store.has_users():
            pw = os.environ.get("PM_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
            self.store.set_user(ADMIN, ph.hash(pw))
            if not os.environ.get("PM_ADMIN_PASSWORD"):
                config.ADMIN_PASSWORD_FILE.write_text(pw + "\n")
                os.chmod(config.ADMIN_PASSWORD_FILE, 0o600)
                log.warning("created admin account - initial password in %s", config.ADMIN_PASSWORD_FILE)
        # local-only token for pmctl / tests running inside the container
        self.internal_token = secrets.token_urlsafe(32)
        config.INTERNAL_TOKEN_FILE.write_text(self.internal_token + "\n")
        os.chmod(config.INTERNAL_TOKEN_FILE, 0o600)

    # ---- login ------------------------------------------------------------
    def throttle(self, ip: str):
        q = self.failures[ip]
        now = time.time()
        while q and q[0] < now - 60:
            q.popleft()
        if len(q) >= 5:
            raise HTTPException(429, "too many failed logins - wait a minute")

    def login(self, username: str, password: str, ip: str) -> str:
        self.throttle(ip)
        h = self.store.user_hash(username)
        try:
            if not h or not ph.verify(h, password):
                raise VerifyMismatchError
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            self.failures[ip].append(time.time())
            self.store.audit(username or "?", "auth.login_failed", ip)
            # Fires once, exactly when the count crosses the threshold within the window - not on
            # every failure past it, so one real burst is one notification, not a flood.
            if self.notifier and len(self.failures[ip]) == 3:
                self.notifier.notify("login_failures", "error", f"Repeated failed logins from {ip}", {"ip": ip})
            raise HTTPException(401, "invalid username or password")
        if ph.check_needs_rehash(h):
            self.store.set_user(username, ph.hash(password))
        sid = secrets.token_urlsafe(32)
        self.store.add_session(sid, username, config.SESSION_TTL_S)
        self.store.purge_sessions()
        self.store.audit(username, "auth.login", ip)
        return sid

    def change_password(self, username: str, current: str, new: str):
        try:
            ph.verify(self.store.user_hash(username) or "", current)
        except Exception:
            raise HTTPException(403, "current password is wrong")
        self.store.set_user(username, ph.hash(new))
        self.store.drop_user_sessions(username)
        if config.ADMIN_PASSWORD_FILE.exists():
            config.ADMIN_PASSWORD_FILE.unlink()
        self.store.audit(username, "auth.password_changed")

    # ---- request identity ---------------------------------------------------
    def identify(self, cookies, headers, query=None) -> Optional[str]:
        authz = headers.get("authorization", "")
        token = authz[7:].strip() if authz.lower().startswith("bearer ") else (query or {}).get("token")
        if token:
            if self.internal_token and secrets.compare_digest(token, self.internal_token):
                return "pmctl"
            name = self.store.token_name(token)
            return f"token:{name}" if name else None
        sid = cookies.get(COOKIE)
        return self.store.session_user(sid) if sid else None

    def require(self, request: Request) -> str:
        who = self.identify(request.cookies, request.headers)
        if not who:
            raise HTTPException(401, "not authenticated")
        return who

    def require_ws(self, ws: WebSocket) -> Optional[str]:
        return self.identify(ws.cookies, ws.headers, dict(ws.query_params))

    def create_token(self, name: str, actor: str) -> dict:
        token = "pm_" + secrets.token_urlsafe(32)
        tid = self.store.add_token(name, token)
        self.store.audit(actor, "token.create", name)
        return {"id": tid, "name": name, "token": token}
