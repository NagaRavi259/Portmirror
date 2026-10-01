"""
log_generator.py
-----------------
Writes randomized log events into a Postgres `logs` table and pushes each
one out immediately via `pg_notify('new_log', ...)` so ws_db_log_stream.py
can forward them to WebSocket clients in real time.

Subjects are pulled from an existing dataset table (SUBJECT_TABLE /
SUBJECT_ID_COL / SUBJECT_LABEL_COL); falls back to Faker if unset/missing.

Env: PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE
     SUBJECT_TABLE SUBJECT_ID_COL SUBJECT_LABEL_COL (optional)
"""

import json
import os
import random
import time

import psycopg2
from psycopg2 import sql
from faker import Faker

fake = Faker()

SUBJECT_TABLE = os.environ.get("SUBJECT_TABLE", "")
SUBJECT_ID_COL = os.environ.get("SUBJECT_ID_COL", "")
SUBJECT_LABEL_COL = os.environ.get("SUBJECT_LABEL_COL", "")


def connect(retries=60, delay=2):
    for attempt in range(1, retries + 1):
        try:
            conn = psycopg2.connect(
                host=os.environ.get("PGHOST", "127.0.0.1"),
                port=os.environ.get("PGPORT", "5432"),
                user=os.environ.get("PGUSER", "postgres"),
                password=os.environ.get("PGPASSWORD", "postgres"),
                dbname=os.environ.get("PGDATABASE", "postgres"),
            )
            conn.autocommit = True
            return conn
        except psycopg2.OperationalError as e:
            print(f"DB not ready (attempt {attempt}/{retries}): {e}".strip(), flush=True)
            time.sleep(delay)
    raise SystemExit("could not connect to Postgres")


conn = connect()
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS logs (
    log_id  BIGSERIAL PRIMARY KEY,
    ts      TIMESTAMP NOT NULL DEFAULT now(),
    level   TEXT NOT NULL,
    service TEXT NOT NULL,
    message TEXT NOT NULL
);
""")

subjects = []
if SUBJECT_TABLE:
    try:
        cur.execute(sql.SQL("SELECT * FROM {} LIMIT 0").format(sql.Identifier(SUBJECT_TABLE)))
        col_names = [d[0] for d in cur.description]
        id_col = SUBJECT_ID_COL or col_names[0]
        label_col = SUBJECT_LABEL_COL or (col_names[1] if len(col_names) > 1 else col_names[0])
        cur.execute(sql.SQL("SELECT {}, {} FROM {} ORDER BY random() LIMIT 100").format(
            sql.Identifier(id_col), sql.Identifier(label_col), sql.Identifier(SUBJECT_TABLE)))
        subjects = cur.fetchall()
        print(f"Loaded {len(subjects)} sample subjects from {SUBJECT_TABLE}.{id_col}/{label_col}", flush=True)
    except Exception as e:
        print(f"Could not read SUBJECT_TABLE={SUBJECT_TABLE!r} ({e}); falling back to synthetic subjects", flush=True)
        subjects = []

if not subjects:
    subjects = [(i, fake.name()) for i in range(1, 101)]

LEVELS_SERVICES = [
    ("INFO",  "auth-service"),
    ("INFO",  "checkout-service"),
    ("WARN",  "auth-service"),
    ("ERROR", "payment-service"),
    ("INFO",  "inventory-service"),
    ("DEBUG", "cache-service"),
]

MESSAGE_TEMPLATES = [
    "user {label!r} (id={id}) logged in from {city}",
    "record id={id} ({label!r}) updated",
    "failed auth attempt for id={id} from {city}",
    "payment processing failed for id={id}",
    "cache miss for id={id}",
    "background job completed for id={id} ({label!r})",
]

print("log_generator started, writing to Postgres + NOTIFY on channel 'new_log'", flush=True)

while True:
    level, service = random.choice(LEVELS_SERVICES)
    subj_id, subj_label = random.choice(subjects)
    message = random.choice(MESSAGE_TEMPLATES).format(id=subj_id, label=subj_label, city=fake.city())

    cur.execute(
        "INSERT INTO logs (level, service, message) VALUES (%s,%s,%s) RETURNING log_id, ts",
        (level, service, message)
    )
    log_id, ts = cur.fetchone()

    payload = json.dumps({
        "log_id": log_id, "ts": ts.isoformat(), "level": level, "service": service, "message": message
    })
    cur.execute("SELECT pg_notify('new_log', %s)", (payload,))

    time.sleep(random.uniform(0.15, 0.6))
