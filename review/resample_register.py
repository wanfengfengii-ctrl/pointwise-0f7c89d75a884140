"""Center-side storage for night-shift resample plans and receipts.

Plans and receipts are plain JSON-serializable dictionaries so that the value
returned by the signing call and the value read back after reopening are equal.
The tables live in the center database and can share its connection with the V1
``ReportRegister``; the same ``before_commit`` hook convention is used.
"""
import json
import sqlite3
from typing import Callable

PLAN_FIELDS = ('report_id', 'roster_id', 'sources', 'status', 'finish', 'schedules')


def ensure_resample_tables(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS resample_plans (
            id INTEGER PRIMARY KEY,
            report_id INTEGER NOT NULL,
            roster_id TEXT NOT NULL,
            body TEXT NOT NULL
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS resample_receipts (
            id INTEGER PRIMARY KEY,
            plan_id INTEGER NOT NULL UNIQUE REFERENCES resample_plans(id)
        )
    ''')


def _encode(body: dict) -> str:
    return json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _require_lookup_id(value: object) -> None:
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise KeyError(value)


def commit_resample(
    connection: sqlite3.Connection, body: dict,
    before_commit: Callable[[], None] | None = None,
) -> tuple[dict, dict]:
    if not isinstance(body, dict) or set(body) != set(PLAN_FIELDS):
        raise ValueError('a resample plan needs exactly the documented fields')
    encoded = _encode(body)
    ensure_resample_tables(connection)
    db = connection
    db.execute('BEGIN IMMEDIATE')
    with db:
        plan_id = db.execute(
            'INSERT INTO resample_plans (report_id, roster_id, body) VALUES (?, ?, ?)',
            (body['report_id'], body['roster_id'], encoded),
        ).lastrowid
        receipt_id = db.execute(
            'INSERT INTO resample_receipts (plan_id) VALUES (?)', (plan_id,)
        ).lastrowid
        if before_commit is not None:
            before_commit()
    return load_resample(connection, plan_id)


def load_resample(connection: sqlite3.Connection, plan_id: int) -> tuple[dict, dict]:
    _require_lookup_id(plan_id)
    ensure_resample_tables(connection)
    row = connection.execute(
        '''SELECT p.body, r.id FROM resample_plans p
           JOIN resample_receipts r ON r.plan_id = p.id WHERE p.id = ?''',
        (plan_id,),
    ).fetchone()
    if row is None:
        raise KeyError(plan_id)
    body, receipt_id = row
    return {'plan_id': plan_id, **json.loads(body)}, {'receipt_id': receipt_id, 'plan_id': plan_id}


def resample_counts(connection: sqlite3.Connection) -> tuple[int, int]:
    ensure_resample_tables(connection)
    return connection.execute(
        'SELECT (SELECT count(*) FROM resample_plans), (SELECT count(*) FROM resample_receipts)'
    ).fetchone()


class ResampleRegister:
    def __init__(self, connection: sqlite3.Connection,
                 before_commit: Callable[[], None] | None = None):
        self.connection = connection
        self.before_commit = before_commit
        ensure_resample_tables(connection)

    def commit(self, body: dict) -> tuple[dict, dict]:
        return commit_resample(self.connection, body, self.before_commit)

    def load(self, plan_id: int) -> tuple[dict, dict]:
        return load_resample(self.connection, plan_id)

    def counts(self) -> tuple[int, int]:
        return resample_counts(self.connection)
