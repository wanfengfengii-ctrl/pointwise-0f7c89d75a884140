"""Independent exhaustive reference used only by the tests.

The reference enumerates every legal next operation (inspection or permitted
change) with explicit clocks, keeps the earliest finish it can reach, and
allows arbitrarily many changes.  It shares no pruning or bound with the
production scheduler, so agreement is meaningful.
"""


def validate(schedules, slots, specs):
    """Check a timetable is legal and return its finish minute."""
    if not isinstance(schedules, (list, tuple)) or len(schedules) != len(specs):
        raise AssertionError('need one schedule per station')
    inspected = []
    table = []
    finish = 0
    for rows, (name, spec) in zip(schedules, specs):
        current = spec.initial_fixture
        changes = {(a, b): m for a, b, m in spec.changes}
        checks = {(s, f): m for s, f, m in spec.checks}
        last_end = 0
        for row in rows:
            start, end = row['start'], row['end']
            if type(start) is not int or type(end) is not int:
                raise AssertionError('non-integer interval')
            if start < last_end or end <= start:
                raise AssertionError('station intervals must be ordered and positive')
            last_end = end
            finish = max(finish, end)
            if row['kind'] == 'change':
                if current != row['from']:
                    raise AssertionError('change must start from the current fixture')
                if (row['from'], row['to']) not in changes:
                    raise AssertionError('change direction is not permitted')
                if changes[(row['from'], row['to'])] != end - start:
                    raise AssertionError('wrong change duration')
                current = row['to']
                table.append((start, end))
            elif row['kind'] == 'inspect':
                if row['fixture'] != current:
                    raise AssertionError('inspection must use the current fixture')
                if (row['slot'], row['fixture']) not in checks:
                    raise AssertionError('inspection pair is not registered')
                if checks[(row['slot'], row['fixture'])] != end - start:
                    raise AssertionError('wrong inspection duration')
                inspected.append(row['slot'])
            else:
                raise AssertionError('unknown row kind')
    if sorted(inspected) != sorted(slots):
        raise AssertionError('every uninspected slot must be inspected exactly once')
    table.sort()
    for (a, b), (c, d) in zip(table, table[1:]):
        if c < b:
            raise AssertionError('the shared change table is double booked')
    return finish


def brute(slots, specs, limit=30):
    """Return the minimum finish or None when incomplete."""
    slots = tuple(slots)
    if not slots:
        return 0
    best = [None]
    seen = set()
    n = len(slots)

    def search(mask, fixtures, frees, table_free):
        if mask == 0:
            value = max(frees)
            if best[0] is None or value < best[0]:
                best[0] = value
            return
        if best[0] is not None and max(frees) >= best[0]:
            return
        if max(frees) > limit:
            return
        key = (mask, fixtures, frees, table_free)
        if key in seen:
            return
        seen.add(key)
        for index, (name, spec) in enumerate(specs):
            checks = {(s, f): m for s, f, m in spec.checks}
            for slot_index, slot in enumerate(slots):
                bit = 1 << slot_index
                if not mask & bit:
                    continue
                key_check = (slot, fixtures[index])
                if key_check not in checks:
                    continue
                end = frees[index] + checks[key_check]
                nxt = list(frees)
                nxt[index] = end
                search(mask ^ bit, fixtures, tuple(nxt), table_free)
            for a, b, minutes in spec.changes:
                if a != fixtures[index]:
                    continue
                start = max(frees[index], table_free)
                end = start + minutes
                if end > limit:
                    continue
                if end >= (best[0] if best[0] is not None else 10 ** 9):
                    continue
                nxt = list(frees)
                nxt[index] = end
                nxt_fixtures = list(fixtures)
                nxt_fixtures[index] = b
                search(mask, tuple(nxt_fixtures), tuple(nxt), end)

    search((1 << n) - 1, tuple(spec.initial_fixture for name, spec in specs), (0, 0), 0)
    return best[0]
