"""
Core breakup-journey planning logic.

Strategy:
1. Look for direct trains source -> dest. If any has a confirmed/available
   seat in the requested class, that's the answer, no breakup needed.
2. Otherwise, generate candidate "break stations": major junctions that lie
   roughly on the geographic line between source and dest (using
   cross-track distance from the great-circle path, not just "closer to
   the destination"), excluding source/dest themselves.
3. For each candidate junction, look for:
     leg 1: source -> junction (must have availability)
     leg 2: junction -> dest (must have availability, and must depart
            after leg 1 arrives, with a minimum connection buffer)
4. Rank the resulting combinations by: total elapsed time, then number of
   waitlisted/RAC legs (fewer is better), then connection buffer.

This intentionally only considers a single break point (2 legs). Extending
to 2 break points (3 legs) is a straightforward recursive generalisation of
generate_candidates() + search_breakup(), left as a next step once the
1-break version is validated against real data.
"""

import math
import re
from datetime import datetime, timedelta

from stations_data import STATIONS, KNOWN_SCHEDULE_STATIONS, MAJOR_STATIONS

MIN_CONNECTION_MINUTES = 45  # minimum time to safely change trains at a junction
MAX_CROSS_TRACK_KM = 150     # how far off the direct line a junction may be
EARTH_RADIUS_KM = 6371

# Sentinel travel_class value meaning "any class will do - tell me which one
# actually has a seat", instead of checking one fixed class. Cheapest class
# first, since that's the more useful default when several classes on the
# same train are all open.
ALL_CLASSES = "ALL"
CLASS_ORDER = ["SL", "3A", "2A", "1A"]

# "Checking direct trains" no longer means checking every train IRCTC lists
# for the route - it means checking the fastest few. If none of those has a
# seat, breakup-journey alternatives are suggested even if some slower train
# further down the full list happens to have one. Keeps "no breakup needed"
# a meaningful bar rather than an exhaustive one.
DEFAULT_DIRECT_TRAINS_CHECKED = 5

# For KNOWN_SCHEDULE_STATIONS only: how much further than a perfectly
# straight line a junction may be before it's rejected as "not between"
# source and dest. A real junction near one endpoint (e.g. Patna, ~45km
# from Chhapra) can be a couple of km further from the far endpoint than
# the origin is, purely because it's slightly off-axis - that shouldn't
# disqualify it. Kept strict (1.0, i.e. off) for the pure-geometry
# fallback candidates, since there we have no other signal that a station
# is a sensible junction, and betweenness is the only guard against
# suggesting a detour.
KNOWN_BETWEENNESS_SLACK = 1.2


def _to_rad(deg):
    return deg * math.pi / 180


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(_to_rad, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _cross_track_distance_km(lat, lon, lat1, lon1, lat2, lon2):
    """Perpendicular distance of point (lat,lon) from the great-circle path
    between (lat1,lon1) and (lat2,lon2), in km."""
    d13 = _haversine_km(lat1, lon1, lat, lon) / EARTH_RADIUS_KM
    brng13 = math.atan2(
        math.sin(_to_rad(lon - lon1)) * math.cos(_to_rad(lat)),
        math.cos(_to_rad(lat1)) * math.sin(_to_rad(lat))
        - math.sin(_to_rad(lat1)) * math.cos(_to_rad(lat)) * math.cos(_to_rad(lon - lon1)),
    )
    brng12 = math.atan2(
        math.sin(_to_rad(lon2 - lon1)) * math.cos(_to_rad(lat2)),
        math.cos(_to_rad(lat1)) * math.sin(_to_rad(lat2))
        - math.sin(_to_rad(lat1)) * math.cos(_to_rad(lat2)) * math.cos(_to_rad(lon2 - lon1)),
    )
    return abs(math.asin(math.sin(d13) * math.sin(brng13 - brng12)) * EARTH_RADIUS_KM)


def generate_candidate_junctions(source, dest, max_candidates=6, priority_stations=None):
    """Return station codes that lie roughly on the source->dest path,
    ordered by how close to the direct line they are.

    priority_stations, if given, is a set of station codes surfaced first,
    ahead of geometrically-closer stations without that status. This
    exists because pure geometry can't tell a real junction from a random
    small halt that happens to sit almost exactly on the straight line
    between two far-apart cities - which happens more often than you'd
    expect, and otherwise floods the candidate list with useless halts a
    few passengers a day pass through, never reaching a real interchange.
    app.py passes KNOWN_SCHEDULE_STATIONS (the mock demo's own tiny train
    set) when running against MockDataSource, and MAJOR_STATIONS (the
    hand-picked ~140 real junctions/state capitals) against LiveDataSource,
    where any of the 1,300+ stations could technically have a real train
    but only some of them are places people actually interchange at.
    """
    if source not in STATIONS or dest not in STATIONS:
        return []
    _, lat1, lon1 = STATIONS[source]
    _, lat2, lon2 = STATIONS[dest]
    d_total = _haversine_km(lat1, lon1, lat2, lon2)
    priority_stations = priority_stations or frozenset()

    # A junction essentially co-located with source or dest (a nearby halt,
    # a few km away) isn't a useful breakup point - nobody splits a ticket
    # at a station a 10-minute walk from where they started. Since pure
    # cross-track distance ranks "practically on top of the source" as the
    # *best* candidate (distance ~0), without a floor like this the
    # candidate list fills up with source's immediate neighbors and never
    # reaches a real, distant junction. Scales with the trip length so a
    # short trip doesn't get an unreasonably large exclusion zone.
    min_endpoint_distance_km = min(50, d_total * 0.15)

    priority = []
    geometric = []
    for code, (name, lat, lon) in STATIONS.items():
        if code in (source, dest):
            continue
        is_priority = code in priority_stations
        # must be geographically between the two endpoints, not beyond
        # either (priority stations get a little slack - see constant above)
        limit = d_total * KNOWN_BETWEENNESS_SLACK if is_priority else d_total
        d_from_src = _haversine_km(lat1, lon1, lat, lon)
        d_from_dst = _haversine_km(lat2, lon2, lat, lon)
        if d_from_src > limit or d_from_dst > limit:
            continue
        if d_from_src < min_endpoint_distance_km or d_from_dst < min_endpoint_distance_km:
            continue
        xt = _cross_track_distance_km(lat, lon, lat1, lon1, lat2, lon2)
        if is_priority:
            priority.append((xt, code))
        elif xt <= MAX_CROSS_TRACK_KM:
            geometric.append((xt, code))
    priority.sort()
    geometric.sort()
    ordered = priority + geometric
    return [code for _, code in ordered[:max_candidates]]


def _status_available(status):
    return status.startswith("AVAILABLE") or status.startswith("RAC")


def match_leg(leg, travel_class):
    """Check one train leg against the requested travel_class and return
    (is_available, matched_class, status).

    travel_class is either one specific class code ("SL", "3A", ...), in
    which case only that class is checked, or the ALL_CLASSES sentinel,
    meaning "I don't care which class - check all of them and tell me the
    first one that has a seat". This is what lets a breakup journey mix
    classes across its two legs (e.g. 2A into a junction, Sleeper onward),
    since real passengers care about getting there, not about matching
    class codes between two unrelated trains.
    """
    if travel_class == ALL_CLASSES:
        for cls in CLASS_ORDER:
            status = leg["classes"].get(cls, "")
            if status and _status_available(status):
                return True, cls, status
        return False, None, None
    status = leg["classes"].get(travel_class, "WL-999")
    return _status_available(status), travel_class, status


def best_unavailable_status(leg):
    """When nothing is AVAILABLE/RAC in any class, pick the single best
    (lowest) waitlist number across all classes on this train, rather than
    reporting a bare "nothing matched". A passenger deciding whether a
    waitlisted train is worth chasing needs the real number (WL 3 is a very
    different prospect from WL 190), and this is also what makes an
    "ALL Classes" search mode show something more useful than "NODATA" for
    a train that's genuinely waitlisted in every class it runs.

    Returns (class_code_or_None, status_string).
    """
    best_cls, best_status, best_n = None, None, None
    any_regret = False
    for cls, status in leg["classes"].items():
        if not status or status == "NODATA":
            continue
        if status.startswith("REGRET"):
            any_regret = True
            continue
        matches = re.findall(r"(\d+)", status)
        if matches:
            n = int(matches[-1])
            if best_n is None or n < best_n:
                best_cls, best_status, best_n = cls, status, n
    if best_status:
        return best_cls, best_status
    if any_regret:
        return None, "REGRET"
    return None, "NODATA"


def _with_match(leg, travel_class):
    """Return a shallow copy of leg annotated with which class matched, so
    templates can show "2A: AVAILABLE-0012" without re-deriving it."""
    available, matched_class, status = match_leg(leg, travel_class)
    if not available and matched_class is None:
        # ALL_CLASSES mode and nothing available - fall back to the best
        # waitlist number across classes (see best_unavailable_status) so an
        # unavailable train still shows something meaningful instead of a
        # blank matched_class/status. Only ever hits the unavailable branch,
        # so combos built from available legs (search_breakup) are unaffected.
        matched_class, status = best_unavailable_status(leg)
    leg_copy = dict(leg)
    leg_copy["matched_class"] = matched_class
    leg_copy["matched_status"] = status
    leg_copy["is_available"] = available
    # ConfirmTkt's own confirmation-chance prediction for the matched class,
    # flattened out of the per-class confirm_pct dict for the same reason
    # matched_class/matched_status are - so templates don't have to look it
    # up themselves.
    leg_copy["matched_confirm_pct"] = (leg.get("confirm_pct") or {}).get(matched_class) if matched_class else None
    return leg_copy


def _arrival_datetime(base_date, day_offset, hhmm):
    h, m = map(int, hhmm.split(":"))
    return base_date + timedelta(days=day_offset, hours=h, minutes=m)


def _train_rank_key(leg):
    """Sort key for "best trains first": fastest journey first (shortest
    duration_minutes), then earliest departure as a tiebreaker. "Best" is
    about which trains a passenger would actually consider, not which ones
    happen to be listed first by the data source - checking only these
    before offering a breakup-journey alternative keeps that offer
    meaningful (an alternate route is suggested because the realistic
    options are full, not because some obscure, much-slower train 15 rows
    down the list has a seat).
    """
    duration = leg.get("duration_minutes")
    duration_key = duration if duration is not None else 10 ** 9  # unknown duration sorts last
    return (duration_key, leg.get("dep_day_offset", 0), leg.get("dep_time", ""))


def best_direct_trains(data_source, source, dest, date_str, travel_class, max_trains=DEFAULT_DIRECT_TRAINS_CHECKED):
    """Return the `max_trains` best (fastest) direct trains between source
    and dest, each annotated with its status in travel_class - regardless
    of whether they actually have a seat. This is the full list app.py
    shows when none of them do, so the person can see what was actually
    checked rather than just being told "no seats, here are alternates".
    """
    legs = data_source.get_trains_between(source, dest, date_str)
    ranked = sorted(legs, key=_train_rank_key)[:max_trains]
    return [_with_match(leg, travel_class) for leg in ranked]


def search_direct(data_source, source, dest, date_str, travel_class, max_trains=DEFAULT_DIRECT_TRAINS_CHECKED):
    """Direct-train seats among the `max_trains` best (fastest) direct
    trains only - see best_direct_trains() above and DEFAULT_DIRECT_TRAINS_CHECKED.
    A breakup-journey alternative is offered once none of these have a seat,
    even if a slower train further down the full list does.
    """
    candidates = best_direct_trains(data_source, source, dest, date_str, travel_class, max_trains)
    return [t for t in candidates if t["is_available"]]


def search_breakup(data_source, source, dest, date_str, travel_class, max_candidates=6, priority_stations=None):
    base_date = datetime.strptime(date_str, "%Y-%m-%d")
    junctions = generate_candidate_junctions(source, dest, max_candidates, priority_stations=priority_stations)

    combos = []
    for junction in junctions:
        leg1_options = [
            _with_match(leg, travel_class)
            for leg in data_source.get_trains_between(source, junction, date_str)
            if match_leg(leg, travel_class)[0]
        ]
        if not leg1_options:
            continue

        for leg1 in leg1_options:
            arr1 = _arrival_datetime(base_date, leg1["arr_day_offset"], leg1["arr_time"])
            leg2_search_date = (base_date + timedelta(days=leg1["arr_day_offset"])).strftime("%Y-%m-%d")
            leg2_options = [
                _with_match(leg, travel_class)
                for leg in data_source.get_trains_between(junction, dest, leg2_search_date)
                if match_leg(leg, travel_class)[0]
            ]
            for leg2 in leg2_options:
                dep2 = _arrival_datetime(base_date, leg1["arr_day_offset"] + leg2["dep_day_offset"], leg2["dep_time"])
                buffer_minutes = (dep2 - arr1).total_seconds() / 60
                if buffer_minutes < MIN_CONNECTION_MINUTES:
                    continue
                arr2 = _arrival_datetime(
                    base_date,
                    leg1["arr_day_offset"] + leg2["dep_day_offset"] + leg2["arr_day_offset"],
                    leg2["arr_time"],
                )
                dep1 = _arrival_datetime(base_date, leg1["dep_day_offset"], leg1["dep_time"])
                total_minutes = (arr2 - dep1).total_seconds() / 60
                # matched_status already reflects whichever class matched for
                # each leg (the same one, or a different one per leg in ALL
                # Classes mode) - so this still correctly counts RAC/WL legs
                # regardless of mode.
                waitlisted_legs = sum(
                    1 for leg in (leg1, leg2)
                    if (leg["matched_status"] or "").startswith("WL")
                    or (leg["matched_status"] or "").startswith("RAC")
                )
                combos.append({
                    "junction": junction,
                    "junction_name": STATIONS.get(junction, (junction,))[0],
                    "leg1": leg1,
                    "leg2": leg2,
                    "connection_buffer_minutes": round(buffer_minutes),
                    "total_minutes": round(total_minutes),
                    "risk_score": waitlisted_legs,
                })

    combos.sort(key=lambda c: (c["risk_score"], c["total_minutes"], -c["connection_buffer_minutes"]))
    return combos
