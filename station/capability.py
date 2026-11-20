"""Resample capability companion pieces issued by a closed station.

A capability describes which fixtures a station declares, which one is already
mounted, the permitted directed fixture changes with their minutes, and the
inspection minutes for (slot label, fixture) pairs.  Companions are UTF-8 JSON
objects bound to one already issued V1 envelope.
"""
from dataclasses import dataclass
import json
from typing import Iterable

from tray.layout import require_name
from tray.resolve import resolve_slot
from tray.roster import FrozenRoster

CAPABILITY_KIND = 'resample-capability'
CAPABILITY_FIELDS = frozenset({'fixtures', 'initial_fixture', 'changes', 'checks'})
PARENT_FIELDS = frozenset({'station', 'frozen_id', 'roster_id', 'content_sha256'})
COMPANION_FIELDS = frozenset({'kind', 'parent', 'capability'})
_HEX = frozenset('0123456789abcdef')


@dataclass(frozen=True)
class CapabilitySpec:
    fixtures: tuple[str, ...]
    initial_fixture: str
    changes: tuple[tuple[str, str, int], ...]
    checks: tuple[tuple[str, str, int], ...]


def require_minutes(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f'{field} must be a positive integer number of minutes')
    return value


def _require_rows(value: object, field: str) -> Iterable[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f'{field} must be a list of rows')
    return value


def _require_row(row: object, field: str) -> tuple[object, object, object]:
    if not isinstance(row, (list, tuple)) or len(row) != 3:
        raise ValueError(f'each {field} row needs three fields')
    return row[0], row[1], row[2]


def normalize_capability(capability: object, roster: FrozenRoster) -> CapabilitySpec:
    if not isinstance(capability, dict) or set(capability) != CAPABILITY_FIELDS:
        raise ValueError('capability must contain exactly fixtures, initial_fixture, changes and checks')

    declared = capability['fixtures']
    if not isinstance(declared, (list, tuple)) or not 1 <= len(declared) <= 2:
        raise ValueError('fixtures must list one or two fixture names')
    fixtures = tuple(require_name(name, 'fixture') for name in declared)
    if len(set(fixtures)) != len(fixtures):
        raise ValueError('fixture names must be distinct')
    fixture_set = set(fixtures)

    initial = require_name(capability['initial_fixture'], 'initial fixture')
    if initial not in fixture_set:
        raise ValueError('the initial fixture must be a declared fixture')

    changes = []
    seen_changes = set()
    for row in _require_rows(capability['changes'], 'changes'):
        source, target, minutes = _require_row(row, 'change')
        source = require_name(source, 'change source fixture')
        target = require_name(target, 'change target fixture')
        minutes = require_minutes(minutes, 'change duration')
        if source not in fixture_set or target not in fixture_set:
            raise ValueError('a change must use declared fixtures')
        if source == target:
            raise ValueError('a change must connect two distinct fixtures')
        if (source, target) in seen_changes:
            raise ValueError('a directed change pair may appear at most once')
        seen_changes.add((source, target))
        changes.append((source, target, minutes))

    checks = []
    seen_checks = set()
    for row in _require_rows(capability['checks'], 'checks'):
        slot, fixture, minutes = _require_row(row, 'check')
        slot = require_name(slot, 'slot label')
        fixture = require_name(fixture, 'fixture')
        minutes = require_minutes(minutes, 'inspection duration')
        if fixture not in fixture_set:
            raise ValueError('a check must use a declared fixture')
        resolve_slot(roster, slot)
        if (slot, fixture) in seen_checks:
            raise ValueError('a slot and fixture pair may appear at most once')
        seen_checks.add((slot, fixture))
        checks.append((slot, fixture, minutes))

    return CapabilitySpec(
        tuple(sorted(fixtures)), initial, tuple(sorted(changes)), tuple(sorted(checks))
    )


def capability_fields(spec: CapabilitySpec) -> dict:
    return {
        'fixtures': list(spec.fixtures),
        'initial_fixture': spec.initial_fixture,
        'changes': [[source, target, minutes] for source, target, minutes in spec.changes],
        'checks': [[slot, fixture, minutes] for slot, fixture, minutes in spec.checks],
    }


def capability_content(spec: CapabilitySpec) -> bytes:
    return json.dumps(
        capability_fields(spec), sort_keys=True, separators=(',', ':'), ensure_ascii=True
    ).encode('utf-8')


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError('duplicate field in capability companion')
    return fields


def _require_digest(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or not set(value) <= _HEX:
        raise ValueError('parent content_sha256 must be a lowercase SHA256 hex digest')
    return value


def decode_companion(raw: object) -> tuple[dict, object]:
    """Decode a companion and return (parent fields, raw capability object)."""
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError('expected original UTF-8 capability companion bytes')
    try:
        data = json.loads(bytes(raw).decode('utf-8'), object_pairs_hook=_unique_fields)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('malformed capability companion') from exc
    if not isinstance(data, dict) or set(data) != COMPANION_FIELDS:
        raise ValueError('unexpected capability companion fields')
    if data['kind'] != CAPABILITY_KIND:
        raise ValueError('expected a resample-capability companion')
    parent = data['parent']
    if not isinstance(parent, dict) or set(parent) != PARENT_FIELDS:
        raise ValueError('unexpected capability parent fields')
    station = require_name(parent['station'], 'parent station')
    roster_id = require_name(parent['roster_id'], 'parent roster id')
    frozen_id = parent['frozen_id']
    if type(frozen_id) is not int or not 0 < frozen_id <= 2**63 - 1:
        raise ValueError('invalid parent frozen id')
    digest = _require_digest(parent['content_sha256'])
    if not isinstance(data['capability'], dict):
        raise ValueError('capability must be an object')
    return (
        {'station': station, 'frozen_id': frozen_id, 'roster_id': roster_id,
         'content_sha256': digest},
        data['capability'],
    )


def encode_companion(parent: dict, spec: CapabilitySpec) -> bytes:
    data = {
        'kind': CAPABILITY_KIND,
        'parent': {
            'station': parent['station'],
            'frozen_id': parent['frozen_id'],
            'roster_id': parent['roster_id'],
            'content_sha256': parent['content_sha256'],
        },
        'capability': capability_fields(spec),
    }
    return json.dumps(
        data, sort_keys=True, separators=(',', ':'), ensure_ascii=True
    ).encode('utf-8')
