import json
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from admission import accept_capability
from review import (
    ReportRegister, ResampleRegister, publish_resample, publish_v1, read_resample, read_v1,
)
from shift_example import CENTER_FILENAME, connect, handoff, open_shift, register_tray
from station import issue_capability


def capability(changes, checks):
    return {"fixtures": ["X", "Y"], "initial_fixture": "X", "changes": changes, "checks": checks}


WAIT = [
    capability([["X", "Y", 4]], [["a", "Y", 1]]),
    capability([["X", "Y", 1]], [["b", "X", 1], ["c", "Y", 5]]),
]
REVISIT = [
    capability([["X", "Y", 2], ["Y", "X", 1]], [["a", "Y", 3], ["c", "X", 1]]),
    capability([["X", "Y", 1]], [["b", "X", 2], ["d", "Y", 4]]),
]
CASES = {
    "wait": {"slots": list("abc"), "uninspected": list("abc"), "capabilities": WAIT,
             "status": "scheduled", "finish": 7},
    "revisit": {"slots": list("abcd"), "uninspected": list("abcd"), "capabilities": REVISIT,
                "status": "scheduled", "finish": 7},
    "infeasible": {"slots": list("abc"), "uninspected": list("abc"),
                   "capabilities": [WAIT[0], capability([], WAIT[1]["checks"])],
                   "status": "infeasible", "finish": None},
    "empty": {"slots": list("abc"), "uninspected": [], "capabilities": WAIT,
              "status": "scheduled", "finish": 0},
}


def prepare(stores, case):
    spec = deepcopy(CASES[case])
    roster_id = register_tray(stores, spec["slots"])
    readings = [(slot, 0) for slot in spec["slots"]] if case == "empty" else []
    parents = (
        handoff(stores, "A", roster_id, readings),
        handoff(stores, "B", roster_id, []),
    )
    report, receipt = publish_v1(
        stores.archive, stores.catalog, stores.reports, *(parent.admission_id for parent in parents)
    )
    return (report, receipt), parents, spec


def build_plan(stores, case, publication=None, parents=None, spec=None):
    """End to end: prepare a V1 report, issue/accept companions, sign a plan."""
    if publication is None or parents is None or spec is None:
        publication, parents, spec = prepare(stores, case)
    report, _ = publication
    capability_ids = []
    for parent, capability_input in zip(parents, spec["capabilities"]):
        raw = issue_capability(
            stores.catalog, stores.stations[parent.envelope.station].outbox,
            parent.envelope.frozen_id, capability_input,
        )
        accepted = accept_capability(
            stores.capabilities, stores.catalog, parent.admission_id, raw
        )
        capability_ids.append(accepted["capability_id"])
    stores.close_stations()
    return publish_resample(
        stores.capabilities, stores.catalog, stores.resamples, report.report_id, *capability_ids
    )


def main():
    for case in CASES:
        with TemporaryDirectory(prefix="resample-fixture-") as directory:
            root = Path(directory)
            with open_shift(root) as stores:
                publication, parents, spec = prepare(stores, case)
                plan, receipt = build_plan(stores, case, publication, parents, spec)
            report, _ = publication
            if plan["status"] != spec["status"] or plan["finish"] != spec["finish"]:
                raise AssertionError(f"{case}: plan disagrees with the hand calculation")
            with closing(connect(root / CENTER_FILENAME)) as reopened:
                if read_v1(ReportRegister(reopened), report.report_id) != publication:
                    raise AssertionError(f"{case}: V1 publication changed on reopen")
                if read_resample(ResampleRegister(reopened), plan["plan_id"]) != (plan, receipt):
                    raise AssertionError(f"{case}: resample plan changed on reopen")
            if [p.admission_id for p in parents] != [s.admission_id for s in report.body.sources]:
                raise AssertionError(f"{case}: wrong V1 parents")
            if list(report.body.coverage.uninspected) != spec["uninspected"]:
                raise AssertionError(f"{case}: wrong V1 uninspected slots")
            print(json.dumps({"case": case, "v1_report_id": report.report_id,
                              "plan": plan, "sample": spec}, ensure_ascii=False))


if __name__ == "__main__":
    main()
