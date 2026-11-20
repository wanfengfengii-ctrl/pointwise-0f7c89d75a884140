"""Sign a night-shift resample plan from a V1 report and two capability companions.

Only the center and catalog databases are used.  The plan, receipt and sources
are written in one center transaction; a failure leaves nothing behind and the
same selected companions can be retried.  The register's ``before_commit`` hook
fires after the plan and receipt rows exist but before the transaction commits.
"""
from hashlib import sha256

from admission.archive import AdmissionArchive
from admission.capability_archive import CapabilityArchive
from review.register import ReportRegister
from review.resample_register import commit_resample
from review.resample_schedule import schedule_resample
from station.envelope import encode_envelope
from tray.catalog import RosterCatalog


def _center_connection(register):
    connection = getattr(register, 'connection', None)
    if connection is None:
        raise ValueError('a center register with a live connection is required')
    return connection


def publish_resample(
    archive: CapabilityArchive,
    catalog: RosterCatalog,
    register,
    report_id: int,
    first_capability_id: int,
    second_capability_id: int,
) -> tuple[dict, dict]:
    connection = _center_connection(register)
    reports = ReportRegister(connection)
    report, _ = reports.load(report_id)
    parents = report.body.sources
    if len(parents) != 2:
        raise ValueError('a resample plan needs a two-station V1 report')

    roster = catalog.load_frozen(report.body.roster_id)
    if roster.tray != report.body.tray or roster.round_id != report.body.round_id:
        raise ValueError('report roster identity does not match the frozen catalog')

    capabilities = (
        CapabilityArchive(archive.connection).find(first_capability_id),
        CapabilityArchive(archive.connection).find(second_capability_id),
    )
    admissions = AdmissionArchive(archive.connection)
    digests = []
    for parent, capability in zip(parents, capabilities):
        if (capability.admission_id != parent.admission_id
                or capability.station != parent.station
                or capability.frozen_id != parent.frozen_id):
            raise ValueError('capability companions do not match the report parents in order')
        if capability.roster_id != report.body.roster_id:
            raise ValueError('capability companion belongs to a different roster')
        accepted = admissions.find(parent.admission_id)
        digests.append(sha256(encode_envelope(accepted.envelope)).hexdigest())

    for capability, digest in zip(capabilities, digests):
        if capability.parent_digest != digest:
            raise ValueError('capability companion parent digest does not match the report')

    sources = [
        {
            'admission_id': parent.admission_id,
            'capability_id': capability.capability_id,
            'station': parent.station,
            'frozen_id': parent.frozen_id,
        }
        for parent, capability in zip(parents, capabilities)
    ]

    status, finish, schedules = schedule_resample(
        [(capability.station, capability.spec) for capability in capabilities],
        report.body.coverage.uninspected,
    )
    body = {
        'report_id': report_id,
        'roster_id': report.body.roster_id,
        'sources': sources,
        'status': status,
        'finish': finish,
        'schedules': schedules,
    }
    return commit_resample(connection, body, getattr(register, 'before_commit', None))
