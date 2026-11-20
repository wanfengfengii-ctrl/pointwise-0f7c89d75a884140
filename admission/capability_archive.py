from dataclasses import dataclass
from hashlib import sha256
import json
import sqlite3

from station.capability import CapabilitySpec, capability_content
from station.envelope import encode_envelope

_REQUIRED_COLUMNS = ('id', 'admission_id', 'station', 'frozen_id', 'roster_id',
                     'parent_digest', 'content', 'raw')


@dataclass(frozen=True)
class AcceptedCapability:
    capability_id: int
    admission_id: int
    station: str
    frozen_id: int
    roster_id: str
    parent_digest: str
    spec: CapabilitySpec
    raw: bytes

    @property
    def parent(self) -> dict:
        return {
            'station': self.station,
            'frozen_id': self.frozen_id,
            'roster_id': self.roster_id,
            'content_sha256': self.parent_digest,
        }


def _spec_from_content(content: bytes) -> CapabilitySpec:
    data = json.loads(content.decode('ascii'))
    return CapabilitySpec(
        tuple(data['fixtures']), data['initial_fixture'],
        tuple(tuple(row) for row in data['changes']),
        tuple(tuple(row) for row in data['checks']),
    )


class CapabilityArchive:
    """Center-side storage for accepted resample capability companions."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS accepted_capabilities (
                id INTEGER PRIMARY KEY,
                admission_id INTEGER NOT NULL UNIQUE,
                station TEXT NOT NULL,
                frozen_id INTEGER NOT NULL,
                roster_id TEXT NOT NULL,
                parent_digest TEXT NOT NULL,
                content BLOB NOT NULL,
                raw BLOB NOT NULL
            )
        ''')

    def _record(self, row: tuple) -> AcceptedCapability:
        capability_id, admission_id, station, frozen_id, roster_id, parent_digest, content, raw = row
        return AcceptedCapability(
            capability_id, admission_id, station, frozen_id, roster_id, parent_digest,
            _spec_from_content(content), raw,
        )

    def bind_first(self, admission_id: int, envelope, roster, spec: CapabilitySpec,
                   raw: bytes) -> AcceptedCapability:
        content = capability_content(spec)
        digest = sha256(encode_envelope(envelope)).hexdigest()
        db = self.connection
        db.execute('BEGIN IMMEDIATE')
        with db:
            first = db.execute(
                f'SELECT {", ".join(_REQUIRED_COLUMNS)} FROM accepted_capabilities '
                'WHERE admission_id = ?', (admission_id,),
            ).fetchone()
            if first is not None:
                if first[6] != content:
                    raise ValueError(
                        'conflicting capability companion for an accepted V1 parent'
                    )
                return self._record(first)
            result = db.execute(
                '''INSERT INTO accepted_capabilities
                   (admission_id, station, frozen_id, roster_id, parent_digest, content, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (admission_id, envelope.station, envelope.frozen_id, envelope.roster_id,
                 digest, content, raw),
            )
            return self.find(result.lastrowid)

    def find(self, capability_id: int) -> AcceptedCapability:
        if type(capability_id) is not int or not 0 < capability_id <= 2**63 - 1:
            raise KeyError(capability_id)
        row = self.connection.execute(
            f'SELECT {", ".join(_REQUIRED_COLUMNS)} FROM accepted_capabilities WHERE id = ?',
            (capability_id,),
        ).fetchone()
        if row is None:
            raise KeyError(capability_id)
        return self._record(row)

    def find_by_admission(self, admission_id: int) -> AcceptedCapability:
        row = self.connection.execute(
            f'SELECT {", ".join(_REQUIRED_COLUMNS)} FROM accepted_capabilities '
            'WHERE admission_id = ?', (admission_id,),
        ).fetchone()
        if row is None:
            raise KeyError(admission_id)
        return self._record(row)

    def counts(self) -> int:
        return self.connection.execute(
            'SELECT count(*) FROM accepted_capabilities'
        ).fetchone()[0]
