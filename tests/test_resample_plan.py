from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from admission import accept_capability
from resample_fixture import CASES, prepare
from review import (
    ReportRegister, ResampleRegister, publish_resample, publish_v1, read_resample, read_v1,
)
from review.resample_schedule import schedule_resample
from shift_example import (
    CENTER_FILENAME, FailOnce, InjectedCommitError, connect, handoff, open_shift,
)
from station import issue_capability
from station.capability import CapabilitySpec
from tests._brute import validate
from tests.test_resample_capability import spec_of


class ResamplePlanTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.failure = FailOnce()
        self.stores = self.enterContext(
            open_shift(self.root, resample_before_commit=self.failure)
        )

    def build(self, case):
        publication, parents, spec = prepare(self.stores, case)
        report, receipt = publication
        capability_ids = []
        for parent, capability in zip(parents, spec['capabilities']):
            raw = issue_capability(
                self.stores.catalog,
                self.stores.stations[parent.envelope.station].outbox,
                parent.envelope.frozen_id, capability,
            )
            capability_ids.append(
                accept_capability(
                    self.stores.capabilities, self.stores.catalog,
                    parent.admission_id, raw,
                )['capability_id']
            )
        self.stores.close_stations()
        return report, capability_ids, spec

    def publish(self, report, ids):
        return publish_resample(
            self.stores.capabilities, self.stores.catalog, self.stores.resamples,
            report.report_id, *ids,
        )

    def reopened(self, plan_id, root=None):
        self.stores.close_center()
        with closing(connect((root or self.root) / CENTER_FILENAME)) as db:
            return read_resample(ResampleRegister(db), plan_id)

    def test_scheduled_plan(self):
        for case in ('wait', 'revisit'):
            with self.subTest(case=case):
                self.stores.close_center()
                self.stores = self.enterContext(
                    open_shift(self.root / case, resample_before_commit=self.failure)
                )
                root = self.root / case
                report, ids, spec = self.build(case)
                plan, receipt = self.publish(report, ids)
                self.assertEqual(plan['status'], 'scheduled')
                self.assertEqual(plan['finish'], spec['finish'])
                self.assertEqual(plan['report_id'], report.report_id)
                self.assertEqual(plan['roster_id'], report.body.roster_id)
                self.assertEqual(
                    [source['admission_id'] for source in plan['sources']],
                    [source.admission_id for source in report.body.sources],
                )
                self.assertEqual([source['capability_id'] for source in plan['sources']], ids)
                self.assertEqual(
                    [source['station'] for source in plan['sources']],
                    [source.station for source in report.body.sources],
                )
                specs = [
                    (source['station'], spec_of(capability))
                    for source, capability in zip(plan['sources'], spec['capabilities'])
                ]
                self.assertEqual(
                    validate(plan['schedules'], spec['uninspected'], specs), plan['finish']
                )
                self.assertEqual(receipt['plan_id'], plan['plan_id'])
                self.assertEqual(self.reopened(plan['plan_id'], root), (plan, receipt))

    def test_infeasible_and_empty(self):
        for case, expected in (('infeasible', ('infeasible', None, None)),
                               ('empty', ('scheduled', 0, [[], []]))):
            with self.subTest(case=case):
                self.stores.close_center()
                self.stores = self.enterContext(
                    open_shift(self.root / case, resample_before_commit=self.failure)
                )
                report, ids, spec = self.build(case)
                plan, receipt = self.publish(report, ids)
                self.assertEqual((plan['status'], plan['finish'], plan['schedules']), expected)
                self.assertEqual(self.reopened(plan['plan_id'], self.root / case), (plan, receipt))

    def test_json_round_trip(self):
        report, ids, spec = self.build('wait')
        plan, receipt = self.publish(report, ids)
        self.assertEqual(json.loads(json.dumps(plan)), plan)
        self.assertEqual(json.loads(json.dumps(receipt)), receipt)

    def test_parent_order_matters(self):
        report, ids, spec = self.build('wait')
        with self.assertRaises(ValueError):
            self.publish(report, list(reversed(ids)))
        self.assertEqual(self.stores.resamples.counts(), (0, 0))

    def test_duplicate_capability_rejected(self):
        report, ids, spec = self.build('wait')
        with self.assertRaises(ValueError):
            self.publish(report, [ids[0], ids[0]])
        self.assertEqual(self.stores.resamples.counts(), (0, 0))

    def test_unknown_ids(self):
        report, ids, spec = self.build('wait')
        for bad in ((report.report_id, ids[0], 999), (999, ids[0], ids[1])):
            with self.subTest(bad=bad), self.assertRaises(KeyError):
                self.publish(report, list(bad[1:])) if bad[0] == report.report_id else \
                    publish_resample(self.stores.capabilities, self.stores.catalog,
                                     self.stores.resamples, bad[0], bad[1], bad[2])
        with self.assertRaises(KeyError):
            read_resample(self.stores.resamples, 500)
        self.assertEqual(self.stores.resamples.counts(), (0, 0))

    def test_commit_failure_then_retry(self):
        report, ids, spec = self.build('wait')
        observer = connect(self.root / CENTER_FILENAME)
        self.enterContext(closing(observer))
        seen = []

        def inspect_staged():
            writer = self.stores.resamples
            self.assertEqual(writer.counts(), (1, 1))
            staged = writer.connection.execute('SELECT max(id) FROM resample_plans').fetchone()[0]
            seen.append(staged)
            plan, receipt = read_resample(writer, staged)
            self.assertEqual(receipt['plan_id'], plan['plan_id'])
            self.assertEqual(ResampleRegister(observer).counts(), (0, 0))

        self.failure.arm(inspect_staged)
        with self.assertRaises(InjectedCommitError):
            self.publish(report, ids)
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.stores.resamples.counts(), (0, 0))
        self.assertEqual(ResampleRegister(observer).counts(), (0, 0))
        with self.assertRaises(KeyError):
            read_resample(self.stores.resamples, seen[0])
        plan, receipt = self.publish(report, ids)
        self.assertEqual(self.stores.resamples.counts(), (1, 1))
        self.assertEqual(read_resample(ResampleRegister(observer), plan['plan_id']),
                         (plan, receipt))
        self.assertEqual(self.reopened(plan['plan_id']), (plan, receipt))

    def test_historical_plan_after_correction(self):
        report, ids, spec = self.build('wait')
        plan, receipt = self.publish(report, ids)
        stale = self.stores.resamples.load(plan['plan_id'])
        with open_shift(self.root) as later:
            corrected = handoff(later, 'A', report.body.roster_id, [('a', 1), ('b', 2), ('c', 3)])
            later.close_stations()
            newer, _ = publish_v1(
                later.archive, later.catalog, later.reports,
                corrected.admission_id, report.body.sources[1].admission_id,
            )
            self.assertEqual(newer.body.coverage.uninspected, ())
        self.assertEqual(self.stores.resamples.load(plan['plan_id']), stale)
        self.assertEqual(self.reopened(plan['plan_id']), stale)

    def test_report_register_hook_is_reused(self):
        report, ids, spec = self.build('wait')
        hook = FailOnce()
        register = ReportRegister(self.stores.reports.connection, before_commit=hook)
        hook.arm()
        with self.assertRaises(InjectedCommitError):
            publish_resample(self.stores.capabilities, self.stores.catalog, register,
                             report.report_id, *ids)
        self.assertEqual(self.stores.resamples.counts(), (0, 0))
        plan, receipt = publish_resample(self.stores.capabilities, self.stores.catalog,
                                         register, report.report_id, *ids)
        self.assertEqual(self.stores.resamples.counts(), (1, 1))
        self.assertEqual(read_resample(register, plan['plan_id']), (plan, receipt))

    def test_mismatched_companion_parent(self):
        report, ids, spec = self.build('wait')
        # A newer A revision gives a second report whose A parent differs, while
        # the accepted companions still point at the first report's parents.
        with open_shift(self.root) as later:
            newer_parent = handoff(later, 'A', report.body.roster_id, [('a', 9)])
            later.close_stations()
            newer, _ = publish_v1(
                later.archive, later.catalog, later.reports,
                newer_parent.admission_id, report.body.sources[1].admission_id,
            )
        self.assertNotEqual(newer.body.sources[0].admission_id,
                            report.body.sources[0].admission_id)
        with self.assertRaises(ValueError):
            self.publish(newer, ids)
        self.assertEqual(self.stores.resamples.counts(), (0, 0))
        # Same companions still sign the original report cleanly.
        plan, receipt = self.publish(report, ids)
        self.assertEqual(plan['status'], 'scheduled')
        self.assertEqual(self.stores.resamples.counts(), (1, 1))


if __name__ == '__main__':
    unittest.main()
