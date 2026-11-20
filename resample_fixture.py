import json
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from review import ReportRegister, publish_v1, read_v1
from shift_example import CENTER_FILENAME, connect, handoff, open_shift, register_tray


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


def main():
    for case in CASES:
        with TemporaryDirectory(prefix="resample-fixture-") as directory:
            root = Path(directory)
            with open_shift(root) as stores:
                publication, parents, spec = prepare(stores, case)
                stores.close_stations()
            report, _ = publication
            with closing(connect(root / CENTER_FILENAME)) as reopened:
                restored = read_v1(ReportRegister(reopened), report.report_id)
            if restored != publication:
                raise AssertionError(f"{case}: V1 publication changed on reopen")
            if [p.admission_id for p in parents] != [s.admission_id for s in report.body.sources]:
                raise AssertionError(f"{case}: wrong V1 parents")
            if list(report.body.coverage.uninspected) != spec["uninspected"]:
                raise AssertionError(f"{case}: wrong V1 uninspected slots")
            print(json.dumps({"case": case, "v1_report_id": report.report_id, "sample": spec}))


if __name__ == "__main__":
    main()
