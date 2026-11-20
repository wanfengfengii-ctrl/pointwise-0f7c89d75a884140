from hashlib import sha256

from observations.journal import ObservationJournal
from observations.revision import get_frozen_revision
from tray.catalog import RosterCatalog

from .capability import (
    capability_content, encode_companion, normalize_capability,
)
from .envelope import V1Envelope, decode_envelope, encode_envelope
from .outbox import Outbox
from .summary import summarize_revision


def _digest(content: bytes) -> str:
    return sha256(content).hexdigest()


def issue_v1(
    journal: ObservationJournal, catalog: RosterCatalog, outbox: Outbox, frozen_id: int
) -> bytes:
    frozen = get_frozen_revision(journal, frozen_id)
    reading = frozen.reading
    roster = catalog.load_frozen(reading.roster_id)
    envelope = V1Envelope(
        reading.station, roster.tray, roster.round_id, roster.roster_id,
        frozen.frozen_id, summarize_revision(frozen, roster),
    )
    return outbox.save(frozen_id, encode_envelope(envelope))


def issue_capability(
    catalog: RosterCatalog, outbox: Outbox, frozen_id: int, capability: object
) -> bytes:
    parent_raw = outbox.get(frozen_id)
    envelope = decode_envelope(parent_raw)
    if envelope.frozen_id != frozen_id:
        raise ValueError('issued envelope does not carry the requested frozen id')
    roster = catalog.load_frozen(envelope.roster_id)
    spec = normalize_capability(capability, roster)
    parent = {
        'station': envelope.station,
        'frozen_id': envelope.frozen_id,
        'roster_id': envelope.roster_id,
        'content_sha256': _digest(encode_envelope(envelope)),
    }
    return outbox.save_capability(frozen_id, capability_content(spec), encode_companion(parent, spec))
