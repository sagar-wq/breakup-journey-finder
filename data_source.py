"""
Data source layer.

This is the ONLY file you need to rewrite to go from "demo with fake data"
to "works with real trains". Everything else (planner.py, app.py) just
calls the methods below.

Two implementations are given:

1. MockDataSource - deterministic fake data, built from stations_data.py.
   Kept around for offline testing, but app.py no longer uses it by
   default now that LiveDataSource is wired up - see below.

2. LiveDataSource - real (but unofficial, unlicensed, no-signup) train
   data via the same public endpoint confirmtkt.com's own site calls. See
   its class docstring below, and README.md, for what "unofficial" means
   in practice and what to do when it breaks.

A "leg" dict returned by get_trains_between() looks like:
{
    "train_no": "12302",
    "train_name": "Howrah Rajdhani",
    "from": "NDLS", "to": "CNB",
    "dep_time": "22:40", "arr_time": "23:55",
    "dep_day_offset": 0, "arr_day_offset": 0,   # days after the search date
    "duration_minutes": 435,                    # total journey time, this train
    "classes": {"3A": "AVAILABLE-42", "SL": "WL-12", "2A": "AVAILABLE-5"},
    "confirm_pct": {"3A": None, "SL": 62, "2A": None},  # ConfirmTkt's own
        # waitlist/RAC confirmation-chance prediction (0-100), per class -
        # None where ConfirmTkt didn't return one (e.g. AVAILABLE classes,
        # or classes it just doesn't predict for). Never guessed/computed
        # locally - only ever a real number ConfirmTkt itself returned.
}
"""

from datetime import datetime, timedelta
import hashlib
import json
import os
import time
import requests
from stations_data import SAMPLE_TRAIN_ROUTES


def _hhmm_to_minutes(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


class BaseDataSource:
    def get_trains_between(self, source, dest, date_str):
        """Return a list of 'leg' dicts (see module docstring) for direct
        trains from source to dest departing on date_str (YYYY-MM-DD)."""
        raise NotImplementedError

    def station_exists(self, code):
        raise NotImplementedError


class MockDataSource(BaseDataSource):
    """Deterministic fake data for demoing the app without any live calls.

    Availability status is derived from a hash of (train_no, date, class)
    so results are stable across repeated searches but vary by date/route,
    which is enough to demonstrate the "direct trains are full, breakup
    works" behaviour realistically.
    """

    CLASSES = ["SL", "3A", "2A", "1A"]

    def _fake_status(self, train_no, date_str, cls, seed_bump=0):
        h = int(hashlib.sha1(f"{train_no}-{date_str}-{cls}-{seed_bump}".encode()).hexdigest(), 16)
        r = h % 100
        if r < 35:
            return f"WL-{(h % 40) + 1}"
        elif r < 45:
            return "RAC-12"
        else:
            return f"AVAILABLE-{(h % 60) + 1}"

    def _fake_confirm_pct(self, train_no, date_str, cls, status):
        # Only demo data invents this - LiveDataSource only ever reports a
        # real number ConfirmTkt itself returned (see data_source.py's
        # module docstring). A confirmed/available seat doesn't need a
        # prediction, so this is only used for WL/RAC statuses.
        if status.startswith("AVAILABLE"):
            return None
        h = int(hashlib.sha1(f"pct-{train_no}-{date_str}-{cls}".encode()).hexdigest(), 16)
        return 15 + (h % 80)  # 15-94%

    def station_exists(self, code):
        from stations_data import STATIONS
        return code.upper() in STATIONS

    def get_trains_between(self, source, dest, date_str):
        source, dest = source.upper(), dest.upper()
        legs = []
        for train_no, name, stops in SAMPLE_TRAIN_ROUTES:
            codes = [s[0] for s in stops]
            if source not in codes or dest not in codes:
                continue
            i_src, i_dst = codes.index(source), codes.index(dest)
            if i_src >= i_dst:
                continue  # dest must come after source on this route
            src_stop, dst_stop = stops[i_src], stops[i_dst]
            classes = {
                cls: self._fake_status(train_no, date_str, cls)
                for cls in self.CLASSES
            }
            confirm_pct = {
                cls: self._fake_confirm_pct(train_no, date_str, cls, classes[cls])
                for cls in self.CLASSES
            }
            dep_total = _hhmm_to_minutes(src_stop[3]) + src_stop[1] * 24 * 60
            arr_total = _hhmm_to_minutes(dst_stop[2]) + dst_stop[1] * 24 * 60
            legs.append({
                "train_no": train_no,
                "train_name": name,
                "from": source,
                "to": dest,
                "dep_time": src_stop[3],
                "arr_time": dst_stop[2],
                "dep_day_offset": src_stop[1],
                "arr_day_offset": dst_stop[1],
                "duration_minutes": max(0, arr_total - dep_total),
                "classes": classes,
                "confirm_pct": confirm_pct,
            })
        return legs


class RailDataError(Exception):
    """Raised whenever LiveDataSource can't get a real answer back - a
    network failure, a non-200 response, or a response that doesn't parse
    the way this code expects. app.py catches this and shows the person a
    plain "live lookup failed" message. It must NEVER be swallowed to fall
    back to fake numbers - a failed live lookup should look like a failure,
    not like a (fake) result."""


class LiveDataSource(BaseDataSource):
    """Real train data with NO signup and NO API key, using the same public
    (undocumented, unofficial) endpoint that confirmtkt.com's own website
    calls to power its search page. This is not an official IRCTC/Indian
    Railways API and nothing here is guaranteed by anyone - see the caveat
    in README.md before relying on this for anything beyond personal use:

    - It's reverse-engineered from public network traffic, documented and
      used the same way by the open-source project github.com/
      RishiMaddheshiya/indian_railway_mcp-server, which is where the
      request/response shape below came from.
    - ConfirmTkt could change this endpoint, rate-limit it, or block it at
      any time without notice - there is no contract with them.
    - If it breaks, the fix is almost always: re-check what
      cttrainsapi.confirmtkt.com/api/v1/trains/search actually returns now
      (see DEBUG_DIR below) and adjust the field names in _parse_train().
    """

    CLASSES = MockDataSource.CLASSES  # keep the same 4 classes as the mock UI
    SEARCH_URL = "https://cttrainsapi.confirmtkt.com/api/v1/trains/search"
    HEADERS = {
        "Accept": "application/json",
        "clientid": "ct-web",
        "apikey": "ct-web!2$",
        "deviceid": "irctc-mcp-0000-0000-0000-000000000000",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    }
    TIMEOUT_SECONDS = 10

    # Whenever a response can't be parsed as expected, the raw JSON is
    # dumped here so you can paste its contents back for a fix, instead of
    # having to describe the problem in words.
    DEBUG_DIR = os.path.join(os.path.dirname(__file__), "debug_responses")

    # How long a (source, dest, date) result is reused before asking
    # ConfirmTkt again. This is the main lever for staying gentle on an
    # unofficial endpoint under real public traffic: if a dozen people
    # search the same popular route within a few minutes, only the first
    # one actually calls ConfirmTkt - everyone else gets the cached result.
    # Short enough that WL/RAC numbers (which genuinely move during the
    # day as people book/cancel) don't go stale for long, long enough to
    # meaningfully cut request volume during a burst.
    CACHE_TTL_SECONDS = 180

    def __init__(self):
        self._session = requests.Session()
        self._cache = {}  # (source, dest, date_str) -> (fetched_at, list of legs)

    def station_exists(self, code):
        from stations_data import STATIONS
        return code.upper() in STATIONS

    def _dump_debug(self, label, payload):
        try:
            os.makedirs(self.DEBUG_DIR, exist_ok=True)
            path = os.path.join(self.DEBUG_DIR, f"{label}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            return path
        except OSError:
            return None

    # Tried in order until one doesn't come back as an "invalid date"
    # error. The first confirmed-working format (learned from a real
    # ConfirmTkt error message: HTTP 400, code 4001, "Invalid date of
    # journey", ruling out DD/MM/YYYY) is remembered for the rest of the
    # run so later calls don't have to re-guess.
    DATE_FORMATS = ["%d-%m-%Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"]

    def get_trains_between(self, source, dest, date_str):
        source, dest = source.upper(), dest.upper()
        cache_key = (source, dest, date_str)
        cached = self._cache.get(cache_key)
        if cached is not None:
            fetched_at, legs = cached
            if time.monotonic() - fetched_at < self.CACHE_TTL_SECONDS:
                return legs

        try:
            journey_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            raise RailDataError(f"Unexpected date format: {date_str!r} (want YYYY-MM-DD)")

        formats = self.DATE_FORMATS
        if getattr(self, "_working_date_format", None):
            # put the last-known-good format first without losing the others
            formats = [self._working_date_format] + [f for f in formats if f != self._working_date_format]

        last_error = None
        for date_fmt in formats:
            api_date = journey_date.strftime(date_fmt)
            try:
                resp = self._session.get(
                    self.SEARCH_URL,
                    headers=self.HEADERS,
                    params={
                        "sourceStationCode": source,
                        "destinationStationCode": dest,
                        "dateOfJourney": api_date,
                    },
                    timeout=self.TIMEOUT_SECONDS,
                )
            except requests.RequestException as e:
                raise RailDataError(f"Could not reach the live train-data source: {e}") from e

            if resp.status_code == 400 and "invalid date" in resp.text.lower():
                # wrong guess for this run - try the next format instead of
                # giving up immediately
                last_error = resp
                continue

            if resp.status_code in (429, 403):
                # Rate-limited or blocked, rather than a parsing/format
                # problem - a distinct, honest message instead of the
                # generic "HTTP nnn" one, since the fix here is "wait", not
                # "report a bug". Never retried automatically: hammering an
                # endpoint that's already pushing back only makes it worse.
                dump_path = self._dump_debug(f"http_{resp.status_code}_{source}_{dest}_{date_str}", {
                    "status_code": resp.status_code, "text": resp.text[:5000],
                })
                raise RailDataError(
                    f"The live train-data source is rate-limiting or blocking requests "
                    f"right now (HTTP {resp.status_code}). This usually clears on its own "
                    f"- try again in a few minutes. Raw response saved to {dump_path}."
                )

            if resp.status_code != 200:
                dump_path = self._dump_debug(f"http_{resp.status_code}_{source}_{dest}_{date_str}", {
                    "status_code": resp.status_code, "text": resp.text[:5000],
                    "date_format_tried": date_fmt,
                })
                raise RailDataError(
                    f"The live train-data source returned HTTP {resp.status_code} for "
                    f"{source}->{dest} on {date_str}. Raw response saved to {dump_path}."
                )

            # this format worked - remember it for the rest of the run
            self._working_date_format = date_fmt
            break
        else:
            dump_path = self._dump_debug(f"alldateformats_{source}_{dest}_{date_str}", {
                "status_code": last_error.status_code if last_error is not None else None,
                "text": last_error.text[:5000] if last_error is not None else None,
                "formats_tried": formats,
            })
            raise RailDataError(
                f"The live train-data source rejected every date format tried "
                f"({formats}) for {date_str}. Raw response saved to {dump_path} - "
                f"the API may want a format not in DATE_FORMATS, or this date may "
                f"be outside its bookable window."
            )

        try:
            payload = resp.json()
        except ValueError as e:
            dump_path = self._dump_debug(f"badjson_{source}_{dest}_{date_str}", {"text": resp.text[:5000]})
            raise RailDataError(f"The live train-data source's response wasn't JSON: {e}. Saved to {dump_path}.") from e

        train_list = (payload.get("data") or {}).get("trainList")
        if train_list is None:
            dump_path = self._dump_debug(f"shape_{source}_{dest}_{date_str}", payload)
            raise RailDataError(
                f"The live train-data source's response didn't have the expected "
                f"data.trainList shape. Full response saved to {dump_path} - paste "
                f"its contents back so the parsing code can be fixed."
            )

        if len(train_list) == 0:
            # Not necessarily wrong - could be a genuine no-trains day - but
            # since this is easy to get wrong (bad station code, bad date
            # format that didn't error, a wrong query param name the API
            # just ignores), always leave evidence behind so a suspicious
            # empty result (e.g. on a route known to have trains) can be
            # checked against the raw response instead of guessed at.
            self._dump_debug(f"empty_{source}_{dest}_{date_str}", {
                "request_params": {
                    "sourceStationCode": source,
                    "destinationStationCode": dest,
                    "dateOfJourney": self._working_date_format and journey_date.strftime(self._working_date_format),
                },
                "full_response": payload,
            })

        legs = []
        for train in train_list:
            try:
                legs.append(self._parse_train(train, source, dest))
            except (KeyError, TypeError, ValueError):
                dump_path = self._dump_debug(f"badtrain_{source}_{dest}_{date_str}", train)
                # Skip this one train rather than failing the whole search,
                # but leave evidence behind so the mapping can be fixed.
                continue

        self._cache[cache_key] = (time.monotonic(), legs)
        return legs

    def _parse_train(self, train, source, dest):
        train_no = str(train["trainNumber"])
        train_name = train["trainName"]
        dep_time = _normalise_clock(train["departureTime"])
        arr_time = _normalise_clock(train["arrivalTime"])
        duration_minutes = int(train.get("duration") or 0)

        dep_h, dep_m = map(int, dep_time.split(":"))
        arr_day_offset = (dep_h * 60 + dep_m + duration_minutes) // (24 * 60)

        availability = train.get("availabilityCache") or {}
        classes = {}
        confirm_pct = {}
        for cls in self.CLASSES:
            entry = availability.get(cls)
            if not entry:
                classes[cls] = "NODATA"  # this train doesn't show this class at all
                confirm_pct[cls] = None
                continue
            # `availability` has the full word ("AVAILABLE-0193", "RAC12",
            # "REGRET"); `availabilityDisplayName` is ConfirmTkt's abbreviated
            # UI label for the same thing ("AVL 193", "RAC 12", "Regret").
            # planner.py's availability check looks for the word "AVAILABLE"
            # / "RAC" at the start of the string, so `availability` MUST be
            # read first - a real confirmed seat reported only via the
            # abbreviated display name was silently being treated as
            # unavailable before this fix.
            status = (entry.get("availability") or entry.get("availabilityDisplayName") or "").strip()
            classes[cls] = _normalise_status(status) if status else "NODATA"
            confirm_pct[cls] = _normalise_confirm_pct(entry.get("predictionPercentage"))

        return {
            "train_no": train_no,
            "train_name": train_name,
            "from": source,
            "to": dest,
            "dep_time": dep_time,
            "arr_time": arr_time,
            "dep_day_offset": 0,
            "arr_day_offset": arr_day_offset,
            "duration_minutes": duration_minutes,
            "classes": classes,
            "confirm_pct": confirm_pct,
        }


def _normalise_clock(value):
    """ConfirmTkt has been seen returning times as 'HH:MM' or 'HH:MM:SS' -
    trim to 'HH:MM' either way."""
    return ":".join(str(value).split(":")[:2])


def _normalise_confirm_pct(raw):
    """ConfirmTkt's own waitlist/RAC confirmation-chance prediction, as a
    whole number 0-100, or None when it didn't return one for this class
    (e.g. AVAILABLE classes usually have nothing to predict). Seen as a
    0-100 percentage in practice, but tolerate a 0-1 fraction too rather
    than silently mis-scaling a real number into something misleading -
    never invents a value ConfirmTkt didn't actually send."""
    if raw is None:
        return None
    try:
        pct = float(raw)
    except (TypeError, ValueError):
        return None
    if 0 <= pct <= 1:
        pct *= 100
    if not (0 <= pct <= 100):
        return None
    return round(pct)


def _normalise_status(status):
    """Make a real ConfirmTkt status string ('AVAILABLE-0056', 'RAC 12',
    'WL 45', 'REGRET', ...) match the shape planner.py's _status_available()
    checks (startswith 'AVAILABLE' or 'RAC'), without inventing any digits
    that weren't in the original string."""
    return status.strip().upper().replace("  ", " ")
