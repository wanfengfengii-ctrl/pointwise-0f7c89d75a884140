from hashlib import sha256

from station.capability import decode_companion, normalize_capability
from station.envelope import decode_envelope, encode_envelope
from tray.catalog import RosterCatalog

from .archive import AcceptedEnvelope, AdmissionArchive
from .capability_archive import CapabilityArchive
from .identity import content_key, source_key
from .roster_check import validate_roster_binding


def accept_v1(
    archive: AdmissionArchive, catalog: RosterCatalog, roster_id: str, raw: bytes
) -> AcceptedEnvelope:
    roster = catalog.load_frozen(roster_id)
    envelope = decode_envelope(raw)
    validate_roster_binding(envelope, roster)
    return archive.bind_first(source_key(envelope), content_key(envelope), raw)


def accept_capability(
    archive: CapabilityArchive,
    catalog: RosterCatalog,
    parent_admission_id: int,
    raw: bytes,
) -> dict:
    accepted = AdmissionArchive(archive.connection).find(parent_admission_id)
    envelope = accepted.envelope
    roster = catalog.load_frozen(envelope.roster_id)
    validate_roster_binding(envelope, roster)

    parent, capability = decode_companion(raw)
    expected = {
        'station': envelope.station,
        'frozen_id': envelope.frozen_id,
        'roster_id': envelope.roster_id,
        'content_sha256': sha256(encode_envelope(envelope)).hexdigest(),
    }
    if parent != expected:
        raise ValueError('capability companion does not match its V1 parent envelope')

    spec = normalize_capability(capability, roster)
    record = archive.bind_first(parent_admission_id, envelope, roster, spec, bytes(raw))
    return {'capability_id': record.capability_id, 'raw': record.raw}
