"""
ws_db_log_stream.py
--------------------
WebSocket server that streams live log events from Postgres to connected
clients using LISTEN/NOTIFY (no polling). On connect, each client first
receives a short backlog of the most recent rows, then live events.

Env: PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE
     WS_PORT (default 8765), BACKLOG_ROWS (default 5)
"""

import asyncio
import json
import os
import select
import threading
import time

import psycopg2
import websockets

WS_PORT = int(os.environ.get("WS_PORT", "8765"))
BACKLOG_ROWS = int(os.environ.get("BACKLOG_ROWS", "5"))

CLIENTS = set()


def _connect():
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "127.0.0.1"),
        port=os.environ.get("PGPORT", "5432"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", "postgres"),
        dbname=os.environ.get("PGDATABASE", "postgres"),
    )


def pg_listener_thread(loop):
    # Reconnects forever, so a Postgres restart doesn't silently stall the stream.
    while True:
        try:
            conn = _connect()
            conn.autocommit = True
            conn.cursor().execute("LISTEN new_log;")
            print("Listening on Postgres channel 'new_log'", flush=True)
            while True:
                if select.select([conn], [], [], 5) == ([], [], []):
                    continue
                conn.poll()
                while conn.notifies:
                    notify = conn.notifies.pop(0)
                    asyncio.run_coroutine_threadsafe(broadcast(notify.payload), loop)
        except Exception as e:
            print(f"listener error ({e}); reconnecting in 2s", flush=True)
            time.sleep(2)


async def broadcast(payload):
    dead = set()
    for ws in list(CLIENTS):
        try:
            await ws.send(payload)
        except Exception:
            dead.add(ws)
    CLIENTS.difference_update(dead)


async def handler(ws):
    CLIENTS.add(ws)
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT log_id, ts, level, service, message FROM logs ORDER BY log_id DESC LIMIT %s",
                    (BACKLOG_ROWS,))
        rows = list(reversed(cur.fetchall()))
        conn.close()
        for r in rows:
            await ws.send(json.dumps({
                "log_id": r[0], "ts": r[1].isoformat(), "level": r[2], "service": r[3], "message": r[4], "backlog": True
            }))
        await ws.wait_closed()
    finally:
        CLIENTS.discard(ws)


async def main():
    loop = asyncio.get_running_loop()
    threading.Thread(target=pg_listener_thread, args=(loop,), daemon=True).start()
    async with websockets.serve(handler, "0.0.0.0", WS_PORT):
        print(f"WebSocket log stream listening on 0.0.0.0:{WS_PORT}", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
