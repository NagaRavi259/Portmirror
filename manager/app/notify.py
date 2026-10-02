"""Turns real events into in-app notifications - distinct from the audit log (who changed what);
this is "something happened that you might want to know about," surfaced in the UI without needing
to watch the dashboard. A muted type is silently dropped at the store layer (`Store.add_notification`),
so callers here never need to check that themselves.
"""
import logging

log = logging.getLogger("pm.notify")


class Notifier:
    def __init__(self, store):
        self.store = store

    def notify(self, type_: str, severity: str, message: str, context: dict | None = None):
        self.store.add_notification(type_, severity, message, context)
        log.info("notification [%s/%s]: %s", type_, severity, message)
