"""Exact two-station resample scheduling.

Each station runs a single machine; inspections occupy only their own station,
a fixture change occupies both its station and the shared change table.  Every
uninspected slot is inspected exactly once, with the station's current fixture
and only when the capability registers a duration for that (slot, fixture)
pair.  The search returns the minimum possible finish minute and one complete
named timetable per station.

A station declares at most two fixtures, so a useful station programme needs at
most two changes (initial -> other -> initial).  Further changes only replay a
fixture state already visited and can never shorten a schedule.
"""

import math

_MAX_CHANGES = 2


class _Station:
    def __init__(self, name, spec):
        self.name = name
        self.fixtures = tuple(spec.fixtures)
        self.initial = spec.initial_fixture
        others = [fixture for fixture in self.fixtures if fixture != self.initial]
        self.other = others[0] if others else None
        self.changes = {(source, target): minutes for source, target, minutes in spec.changes}
        self.checks = {(slot, fixture): minutes for slot, fixture, minutes in spec.checks}

    @property
    def reachable(self):
        if self.other is not None and (self.initial, self.other) in self.changes:
            return (self.initial, self.other)
        return (self.initial,)


def _complete_schedule(stations, slots):
    for slot in slots:
        if not any(
            (slot, fixture) in station.checks
            for station in stations
            for fixture in station.reachable
        ):
            return False
    return True


def schedule_resample(stations_spec, uninspected):
    """Return (status, finish, schedules) for the ordered station pair."""
    slots = tuple(uninspected)
    stations = [_Station(name, spec) for name, spec in stations_spec]
    if not slots:
        return 'scheduled', 0, [[], []]
    if not _complete_schedule(stations, slots):
        return 'infeasible', None, None

    durations = [
        {
            fixture: {index: station.checks[(slot, fixture)]
                      for index, slot in enumerate(slots)
                      if (slot, fixture) in station.checks}
            for fixture in station.reachable
        }
        for station in stations
    ]

    best = [math.inf, None]
    frontier = {}

    # Admissible lower bounds.  `cheapest` is the cheapest inspection of a slot
    # on either station; `forced` adds work that only one station can perform.
    # Change costs and change-table contention are ignored, so neither bound can
    # overestimate the achievable finish.
    cheapest = []
    forced = []
    for slot_index, slot in enumerate(slots):
        per_station = []
        for station in stations:
            options = [station.checks[(slot, fixture)]
                       for fixture in station.reachable
                       if (slot, fixture) in station.checks]
            per_station.append(min(options) if options else None)
        cheapest.append(min(value for value in per_station if value is not None))
        if per_station[0] is not None and per_station[1] is None:
            forced.append((0, per_station[0]))
        elif per_station[1] is not None and per_station[0] is None:
            forced.append((1, per_station[1]))
        else:
            forced.append(None)

    def lower_bound(mask, frees):
        work = 0
        dedicated = [0, 0]
        for slot_index in range(len(slots)):
            if not mask & (1 << slot_index):
                continue
            work += cheapest[slot_index]
            if forced[slot_index] is not None:
                dedicated[forced[slot_index][0]] += forced[slot_index][1]
        spread = (work + frees[0] + frees[1] + 1) // 2
        return max(frees[0], frees[1], frees[0] + dedicated[0], frees[1] + dedicated[1], spread)

    def rank(frees, table_free, used):
        return (frees[0], frees[1], table_free, used[0], used[1])

    # Identical station capabilities make the two stations interchangeable, so
    # only the first ordering of equal station states has to be explored.
    symmetric = (
        stations[0].fixtures == stations[1].fixtures
        and stations[0].initial == stations[1].initial
        and stations[0].reachable == stations[1].reachable
        and stations[0].changes == stations[1].changes
        and stations[0].checks == stations[1].checks
    )

    def dominated(key, point):
        for other in frontier.get(key, ()):
            if all(left <= right for left, right in zip(other, point)):
                return True
        return False

    def remember(key, point):
        entry = frontier.setdefault(key, [])
        entry[:] = [
            other for other in entry
            if not all(left <= right for left, right in zip(point, other))
        ]
        entry.append(point)

    def visit(mask, fixtures, used, frees, table_free, logs, lasts):
        if mask == 0:
            finish = max(frees)
            if finish < best[0]:
                best[0] = finish
                best[1] = ([dict(row) for row in logs[0]], [dict(row) for row in logs[1]])
            return
        if lower_bound(mask, frees) >= best[0]:
            return
        key = (mask, fixtures[0], fixtures[1])
        point = rank(frees, table_free, used)
        if dominated(key, point):
            return
        remember(key, point)

        for index in range(len(stations)):
            station = stations[index]
            free = frees[index]
            fixture = fixtures[index]
            if symmetric and index == 1:
                if (fixtures[0] == fixtures[1] and frees[0] == frees[1]
                        and used[0] == used[1] and table_free <= free):
                    continue

            # Inspections never touch the shared table, so two consecutive
            # inspections in one fixture block may be swapped freely.  Requiring
            # ascending slot index within a block removes that symmetry without
            # changing block totals or block boundary times.
            moves = []
            for slot_index, minutes in durations[index][fixture].items():
                bit = 1 << slot_index
                if not mask & bit:
                    continue
                if slot_index <= lasts[index]:
                    continue
                end = free + minutes
                if end >= best[0]:
                    continue
                moves.append((end, 0, slot_index, bit, minutes))
            if station.other is not None and used[index] < _MAX_CHANGES:
                target = station.other if fixture == station.initial else station.initial
                minutes = station.changes.get((fixture, target))
                if minutes is not None:
                    start = max(free, table_free)
                    end = start + minutes
                    if end < best[0]:
                        moves.append((end, 1, start, target, minutes))
            moves.sort()

            for end, kind, payload, extra, minutes in moves:
                if kind == 0:
                    slot_index, bit = payload, extra
                    logs[index].append({
                        'kind': 'inspect', 'slot': slots[slot_index],
                        'fixture': fixture, 'start': free, 'end': end,
                    })
                    next_frees = list(frees)
                    next_frees[index] = end
                    next_lasts = list(lasts)
                    next_lasts[index] = slot_index
                    visit(mask ^ bit, fixtures, used, tuple(next_frees), table_free, logs,
                          tuple(next_lasts))
                    logs[index].pop()
                else:
                    start, target = payload, extra
                    next_fixtures = list(fixtures)
                    next_fixtures[index] = target
                    next_used = list(used)
                    next_used[index] += 1
                    next_frees = list(frees)
                    next_frees[index] = end
                    next_lasts = list(lasts)
                    next_lasts[index] = -1
                    logs[index].append({
                        'kind': 'change', 'from': fixture, 'to': target,
                        'start': start, 'end': end,
                    })
                    visit(mask, tuple(next_fixtures), tuple(next_used),
                          tuple(next_frees), end, logs, tuple(next_lasts))
                    logs[index].pop()

    visit((1 << len(slots)) - 1, (stations[0].initial, stations[1].initial),
          (0, 0), (0, 0), 0, ([], []), (-1, -1))

    if best[1] is None:
        return 'infeasible', None, None
    schedules = [sorted(row, key=lambda entry: entry['start']) for row in best[1]]
    return 'scheduled', best[0], schedules
