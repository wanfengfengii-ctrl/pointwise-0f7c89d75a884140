import json
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import closing
import unittest

from admission import accept_capability, accept_v1
from resample_fixture import CASES, prepare
from review import (
    ReportRegister, ResampleRegister, publish_resample, read_resample, read_v1,
)
from shift_example import (
    CENTER_FILENAME, FailOnce, InjectedCommitError, connect, handoff, open_shift, register_tray,
)
from review.resample_schedule import schedule_resample
from station import decode_envelope, issue_capability
from station.capability import CapabilitySpec, decode_companion
from tests._brute import brute, validate


def spec_of(capability):
    return CapabilitySpec(
        tuple(capability['fixtures']), capability['initial_fixture'],
        tuple(map(tuple, capability['changes'])), tuple(map(tuple, capability['checks'])),
    )


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.failure = FailOnce()
        self.stores = self.enterContext(open_shift(self.root, outbox_before_commit=self.failure))
        self.roster_id = register_tray(self.stores)

    def parent(self, station='A', readings=(('a', 0),)):
        return handoff(self.stores, station, self.roster_id, readings)

    def issue(self, parent, capability, station=None):
        outbox = self.stores.stations[station or parent.envelope.station].outbox
        return issue_capability(self.stores.catalog, outbox, parent.envelope.frozen_id, capability)

    def test_companion_shape(self):
        parent = self.parent()
        raw = self.issue(parent, CASES['wait']['capabilities'][0])
        companion = json.loads(raw)
        self.assertEqual(set(companion), {'kind', 'parent', 'capability'})
        self.assertEqual(companion['kind'], 'resample-capability')
        self.assertEqual(companion['parent']['station'], 'A')
        self.assertEqual(companion['parent']['roster_id'], self.roster_id)
        self.assertEqual(companion['parent']['frozen_id'], parent.envelope.frozen_id)
        self.assertEqual(len(companion['parent']['content_sha256']), 64)
        payload, capability = decode_companion(raw)
        self.assertEqual(payload['station'], 'A')
        self.assertEqual(spec_of(capability).initial_fixture, 'X')

    def test_equivalent_resend_returns_first_bytes(self):
        parent = self.parent()
        capability = CASES['wait']['capabilities'][0]
        raw = self.issue(parent, capability)
        shuffled = {
            'fixtures': list(reversed(capability['fixtures'])),
            'initial_fixture': capability['initial_fixture'],
            'changes': [list(row) for row in reversed(capability['changes'])],
            'checks': [list(row) for row in reversed(capability['checks'])],
        }
        # Different JSON whitespace and key order on the wire is normalised too.
        self.assertEqual(self.issue(parent, shuffled), raw)
        wire = json.dumps({
            'kind': 'resample-capability',
            'parent': json.loads(raw)['parent'],
            'capability': shuffled,
        }, indent=2).encode('utf-8')
        first = accept_capability(
            self.stores.capabilities, self.stores.catalog, parent.admission_id, raw
        )
        accepted = accept_capability(
            self.stores.capabilities, self.stores.catalog, parent.admission_id, wire
        )
        self.assertEqual(first['raw'], raw)
        self.assertEqual(accepted, first)

    def test_conflicting_capability_rejected(self):
        parent = self.parent()
        first = self.stores.capabilities
        ok = accept_capability(
            first, self.stores.catalog, parent.admission_id,
            self.issue(parent, CASES['wait']['capabilities'][0]),
        )
        changed = dict(CASES['wait']['capabilities'][0])
        changed['changes'] = [['X', 'Y', 5]]
        with self.assertRaises(ValueError):
            accept_capability(
                first, self.stores.catalog, parent.admission_id, self.issue(parent, changed)
            )
        self.assertEqual(first.find(ok['capability_id']).spec.changes, (('X', 'Y', 4),))

    def test_parent_mismatch_and_unknown(self):
        parent = self.parent()
        raw = self.issue(parent, CASES['wait']['capabilities'][0])
        other = self.parent('B', (('b', 1),))
        with self.assertRaises(ValueError):
            accept_capability(
                self.stores.capabilities, self.stores.catalog, other.admission_id, raw
            )
        data = json.loads(raw)
        data['parent']['station'] = 'B'
        with self.assertRaises(ValueError):
            accept_capability(
                self.stores.capabilities, self.stores.catalog, parent.admission_id,
                json.dumps(data).encode('utf-8'),
            )
        data = json.loads(raw)
        data['parent']['content_sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            accept_capability(
                self.stores.capabilities, self.stores.catalog, parent.admission_id,
                json.dumps(data).encode('utf-8'),
            )
        with self.assertRaises(KeyError):
            accept_capability(
                self.stores.capabilities, self.stores.catalog, parent.admission_id + 100, raw
            )
        with self.assertRaises(KeyError):
            self.stores.capabilities.find(999)

    def test_invalid_capabilities(self):
        parent = self.parent()
        base = CASES['wait']['capabilities'][0]
        invalid = [
            dict(base, fixtures=[]),
            dict(base, fixtures=['X', 'X']),
            dict(base, initial_fixture='Z'),
            dict(base, changes=[['X', 'X', 1]]),
            dict(base, changes=[['X', 'Z', 1]]),
            dict(base, changes=[['X', 'Y', 0]]),
            dict(base, changes=[['X', 'Y', True]]),
            dict(base, changes=[['X', 'Y', 1], ['X', 'Y', 2]]),
            dict(base, checks=[['a', 'Z', 1]]),
            dict(base, checks=[['zz', 'X', 1]]),
            dict(base, checks=[['a', 'X', 0]]),
            dict(base, checks=[['a', 'X', 1.5]]),
            dict(base, checks=[['a', 'X', 1], ['a', 'X', 2]]),
            {'fixtures': ['X'], 'initial_fixture': 'X', 'changes': []},
            'not a mapping',
        ]
        for capability in invalid:
            with self.subTest(capability=capability):
                with self.assertRaises(ValueError):
                    self.issue(parent, capability)
        self.assertEqual(self.stores.stations['A'].outbox.get(parent.envelope.frozen_id),
                         accept_v1(self.stores.archive, self.stores.catalog, self.roster_id,
                                   self.stores.stations['A'].outbox.get(parent.envelope.frozen_id)).raw)

    def test_bad_wire(self):
        parent = self.parent()
        raw = self.issue(parent, CASES['wait']['capabilities'][0])
        data = json.loads(raw)
        malformed = [
            b'{', b'\xff', b'[]',
            json.dumps(dict(data, extra=1)).encode(),
            json.dumps({'kind': 'other', 'parent': data['parent'],
                        'capability': data['capability']}).encode(),
            json.dumps({'kind': data['kind'], 'parent': data['parent'],
                        'capability': data['capability'], 'junk': 0}).encode(),
            json.dumps({'kind': data['kind'], 'parent': {'station': 'A'},
                        'capability': data['capability']}).encode(),
            raw.replace(b'{', b'{"kind":"resample-capability",', 1),
        ]
        for bad in malformed:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    accept_capability(
                        self.stores.capabilities, self.stores.catalog, parent.admission_id, bad
                    )
        self.assertEqual(self.stores.capabilities.counts(), 0)

    def test_issue_retry_and_unknown_frozen(self):
        parent = self.parent()
        outbox = self.stores.stations['A'].outbox
        capability = CASES['wait']['capabilities'][0]
        self.failure.arm()
        with self.assertRaises(InjectedCommitError):
            issue_capability(self.stores.catalog, outbox, parent.envelope.frozen_id, capability)
        with self.assertRaises(KeyError):
            outbox.get_capability(parent.envelope.frozen_id)
        raw = issue_capability(self.stores.catalog, outbox, parent.envelope.frozen_id, capability)
        self.assertEqual(outbox.get_capability(parent.envelope.frozen_id), raw)
        with self.assertRaises(KeyError):
            issue_capability(self.stores.catalog, outbox, parent.envelope.frozen_id + 100, capability)

    def test_issue_needs_issued_parent(self):
        self.stores.stations['A'].journal  # keep reference
        outbox = self.stores.stations['A'].outbox
        with self.assertRaises(KeyError):
            issue_capability(self.stores.catalog, outbox, 12345, CASES['wait']['capabilities'][0])


class SchedulerTests(unittest.TestCase):
    def assert_valid(self, status, finish, schedules, slots, specs):
        if status == 'infeasible':
            self.assertIsNone(finish)
            self.assertIsNone(schedules)
            return
        self.assertEqual(validate(schedules, slots, specs), finish)

    def test_sample_finishes(self):
        for case in ('wait', 'revisit', 'infeasible', 'empty'):
            with self.subTest(case=case):
                sample = CASES[case]
                specs = [
                    ('A' if index == 0 else 'B', spec_of(capability))
                    for index, capability in enumerate(sample['capabilities'])
                ]
                status, finish, schedules = schedule_resample(specs, sample['uninspected'])
                self.assertEqual(status, sample['status'])
                self.assertEqual(finish, sample['finish'])
                self.assert_valid(status, finish, schedules, sample['uninspected'], specs)

    def test_matches_brute_force(self):
        import random
        random.seed(11)
        for trial in range(120):
            length = random.randint(0, 4)
            slots = [chr(ord('a') + index) for index in range(length)]
            specs = []
            for station in ('A', 'B'):
                fixtures = ['X'] if random.random() < 0.25 else ['X', 'Y']
                initial = random.choice(fixtures)
                changes = [
                    (a, b, random.randint(1, 4))
                    for a in fixtures for b in fixtures
                    if a != b and random.random() < 0.6
                ]
                checks = [
                    (slot, fixture, random.randint(1, 3))
                    for slot in slots for fixture in fixtures
                    if random.random() < 0.6
                ]
                specs.append((station, CapabilitySpec(
                    tuple(fixtures), initial, tuple(changes), tuple(checks))))
            status, finish, schedules = schedule_resample(specs, slots)
            reference = brute(slots, specs)
            if reference is None:
                self.assertEqual(status, 'infeasible')
            else:
                self.assertEqual((status, finish), ('scheduled', reference))
                self.assert_valid(status, finish, schedules, slots, specs)

    def test_change_table_exclusive(self):
        # Both stations must change fixtures at once; the shared table serialises
        # them, so a naive parallel start would understate the finish.
        spec = CapabilitySpec(
            ('X', 'Y'), 'X', (('X', 'Y', 3), ('Y', 'X', 3)),
            (('a', 'Y', 1), ('b', 'Y', 1)),
        )
        status, finish, schedules = schedule_resample([('A', spec), ('B', spec)], ['a', 'b'])
        self.assertEqual(status, 'scheduled')
        self.assertGreaterEqual(finish, 4)
        self.assertEqual(validate(schedules, ['a', 'b'], [('A', spec), ('B', spec)]), finish)
        changes = sorted(
            (row['start'], row['end'])
            for station in schedules for row in station if row['kind'] == 'change'
        )
        for (_, end), (start, _) in zip(changes, changes[1:]):
            self.assertLessEqual(end, start)


if __name__ == '__main__':
    unittest.main()
