import json
import os
import re
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Flask, redirect, render_template, request, session, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import auth
from data_source import LiveDataSource, MockDataSource, RailDataError
from planner import (
    search_direct, search_breakup, generate_candidate_junctions, best_direct_trains,
    match_leg, best_unavailable_status, ALL_CLASSES, CLASS_ORDER,
    MIN_CONNECTION_MINUTES, DEFAULT_DIRECT_TRAINS_CHECKED, _arrival_datetime,
)
from stations_data import STATIONS, KNOWN_SCHEDULE_STATIONS, MAJOR_STATIONS

app = Flask(__name__)

# Needed to sign the session cookie that remembers "this visitor entered a
# valid access code" / "this visitor is the admin". Set a real SECRET_KEY
# env var on the actual deployment (e.g. `python -c "import secrets;
# print(secrets.token_hex(32))"` once, then paste it into Render's
# environment settings) - the fallback below is fine for local testing only
# and is deliberately obvious/insecure so it's never mistaken for a real one.
app.secret_key = os.environ.get("SECRET_KEY", "dev-insecure-secret-change-me")

# The password for /admin (where access codes are generated/revoked) - this
# is separate from the per-visitor access codes. Set ADMIN_PASSWORD as an
# env var on the real deployment; there is no usable fallback for this one
# on purpose, so /admin refuses to work at all until it's actually set,
# rather than silently running with a guessable default.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")

auth.init_db()

# A single search can fan out into a dozen-plus outbound calls to the live
# (unofficial) train-data source (one candidate junction, two legs each,
# plus a re-fetch per available leg-1 train for onward filtering). Publicly
# hosted with no limit, one person mashing "Search" - or a bot - could burn
# through that source's tolerance for everyone. This caps it per visitor,
# not per the whole app, so normal use is unaffected. Uses in-memory
# storage, which is fine for a single-process/single-instance deployment
# (e.g. Render's free tier) but resets on restart and doesn't share state
# across multiple instances - see README.md if this ever needs to scale
# past that.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)
# Real (unofficial, no-signup) train data by default - see data_source.py
# and README.md for what "unofficial" means and how to fall back to
# MockDataSource() here for offline testing if the live source ever needs
# to be worked around.
data_source = LiveDataSource()
USING_MOCK = isinstance(data_source, MockDataSource)
# The mock demo only has trains between 16 fixed stations, so breakup search
# prioritizes exactly those (KNOWN_SCHEDULE_STATIONS). Live data has no such
# restriction - any of the 1,300+ stations could have real trains - but pure
# geography alone can't tell a real junction from a random small halt that
# happens to sit almost exactly on the straight line between two far-apart
# cities, so it prioritizes MAJOR_STATIONS (the ~140 hand-picked real
# junctions/state capitals) instead. See planner.py for the full reasoning.
BREAKUP_MAX_CANDIDATES = 6 if USING_MOCK else 12
BREAKUP_PRIORITY_STATIONS = KNOWN_SCHEDULE_STATIONS if USING_MOCK else MAJOR_STATIONS

# Generic IRCTC search page - IRCTC's own site is a login-gated single-page
# app with no reliable way to deep-link a prefilled search (no per-route or
# per-train URL), so every "book"/"check" link in the UI points here and the
# person re-enters the search themselves on IRCTC. The app's own data still
# comes from the live source described in data_source.py/README.md, but the
# UI itself never names or links to it - every outbound link the person sees
# goes to IRCTC only.
IRCTC_SEARCH_URL = "https://www.irctc.co.in/nget/train-search"


def _booking_route_url(src, dst):
    return IRCTC_SEARCH_URL


def _booking_train_url(train_no):
    return IRCTC_SEARCH_URL


STATION_LIST = sorted(
    [(code, name) for code, (name, _, _) in STATIONS.items()],
    key=lambda x: x[1],
)
STATION_LIST_JSON = json.dumps(
    [{"code": code, "name": name} for code, name in STATION_LIST]
)

# Routes reachable without a valid access code. Everything else (the search
# page, and the diagnostics it can show) requires session["authenticated"]
# to be set by a successful /login. /admin has its own, separate password
# gate (see admin()) - it deliberately does NOT accept a regular access code.
PUBLIC_ENDPOINTS = {"login", "static"}


@app.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return None
    if request.endpoint == "admin":
        return None  # admin() checks its own password gate itself
    if not session.get("authenticated"):
        return redirect(url_for("login", next=request.path))
    return None


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10/minute", methods=["POST"])
def login():
    error = None
    if request.method == "POST":
        code = request.form.get("code", "")
        if auth.validate_code(code):
            session["authenticated"] = True
            session.permanent = True
            next_url = request.args.get("next") or url_for("index")
            # Only ever redirect to a path within this app - a "next" value
            # is user-controlled input, so anything else (e.g. a full URL to
            # another site) is ignored rather than followed.
            if not next_url.startswith("/"):
                next_url = url_for("index")
            return redirect(next_url)
        error = "That code isn't valid or has been revoked. Check it and try again."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not ADMIN_PASSWORD:
        return (
            "Admin panel is disabled: the ADMIN_PASSWORD environment "
            "variable isn't set on this deployment. Set it and restart the "
            "app to use /admin.",
            503,
        )

    if not session.get("is_admin"):
        error = None
        if request.method == "POST" and "password" in request.form:
            if request.form.get("password") == ADMIN_PASSWORD:
                session["is_admin"] = True
                session.permanent = True
            else:
                error = "Wrong password."
        if not session.get("is_admin"):
            return render_template("admin.html", logged_in=False, error=error)

    new_code = None
    message = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "generate":
            label = request.form.get("label", "")
            new_code = auth.generate_code(label)
            message = "New code generated - copy it now, it won't be shown again."
        elif action == "revoke":
            auth.revoke_code(int(request.form["code_id"]))
            message = "Code revoked."
        elif action == "unrevoke":
            auth.unrevoke_code(int(request.form["code_id"]))
            message = "Code re-enabled."

    return render_template(
        "admin.html",
        logged_in=True,
        codes=auth.list_codes(),
        new_code=new_code,
        message=message,
        error=None,
    )


@app.route("/", methods=["GET", "POST"])
@limiter.limit("10/minute", methods=["POST"])
def index():
    context = {
        "stations": STATION_LIST,
        "stations_json": STATION_LIST_JSON,
        "station_count": len(STATION_LIST),
        "classes": data_source.CLASSES,
        "all_classes_value": ALL_CLASSES,
        "today": date.today().isoformat(),
        "result": None,
        "form": {},
        "using_mock": USING_MOCK,
    }

    if request.method == "POST":
        source = request.form.get("source", "").upper()
        dest = request.form.get("dest", "").upper()
        travel_date = request.form["date"]
        # Default to "any class" rather than requiring one to be picked -
        # most people splitting a ticket don't care which class gets them
        # there, they just want to know what's open on each half.
        travel_class = request.form.get("travel_class") or ALL_CLASSES
        class_label = "any class" if travel_class == ALL_CLASSES else travel_class
        context["form"] = {
            "source": source, "dest": dest,
            "source_name": request.form.get("source_name", ""),
            "dest_name": request.form.get("dest_name", ""),
            "date": travel_date, "travel_class": travel_class,
            "class_label": class_label,
        }

        if not source or not dest or source not in STATIONS or dest not in STATIONS:
            context["error"] = "Please pick both stations from the dropdown suggestions."
            return render_template("index.html", **context)

        if source == dest:
            context["error"] = "Source and destination can't be the same station."
            return render_template("index.html", **context)

        try:
            # "Checking direct trains" now means checking the 5 best
            # (fastest) ones, not every train IRCTC lists for this route -
            # see DEFAULT_DIRECT_TRAINS_CHECKED/best_direct_trains() in
            # planner.py. If none of those 5 has a seat, breakup-journey
            # alternatives are suggested even if a slower train further down
            # the full list happens to have one.
            checked_direct = best_direct_trains(
                data_source, source, dest, travel_date, travel_class,
                max_trains=DEFAULT_DIRECT_TRAINS_CHECKED,
            )
            direct = [t for t in checked_direct if t["is_available"]]
            breakup = []
            if not direct:
                breakup = search_breakup(
                    data_source, source, dest, travel_date, travel_class,
                    max_candidates=BREAKUP_MAX_CANDIDATES, priority_stations=BREAKUP_PRIORITY_STATIONS,
                )
        except RailDataError as e:
            # Never silently fall back to fake data here - if the live
            # lookup failed, the person needs to SEE that it failed, not
            # get a plausible-looking result that isn't real.
            context["error"] = (
                f"Live train-data lookup failed: {e} "
                f"This means no real answer could be fetched just now - "
                f"it is NOT a \"no seats\" result. Try again in a bit, or "
                f"see README.md if this keeps happening."
            )
            return render_template("index.html", **context)

        context["result"] = {
            "direct": direct,
            "breakup": breakup,
            # Only populated when direct is empty - the best trains that
            # were actually checked before falling back to breakup
            # suggestions, so the person can see what was ruled out rather
            # than just being told "no seats, here are alternates".
            "checked_best_direct": [] if direct else checked_direct,
        }

        # When nothing found, show exactly what was actually fetched, raw
        # and unfiltered, instead of leaving it a black box. LiveDataSource
        # caches per (source, dest, date) for the life of the process, so
        # these calls are free - search_direct/search_breakup above already
        # made most of them.
        if not direct and not breakup and not USING_MOCK:
            context["diagnostics"] = _build_diagnostics(source, dest, travel_date, travel_class)

    return render_template("index.html", **context)


@app.errorhandler(429)
def rate_limited(_e):
    # A friendly page in the app's own style, not Flask-Limiter's default
    # plain-text 429 - and it says why the limit exists (protecting a
    # shared, unofficial data source), not just "try later", since that's
    # more honest about what's actually being protected.
    travel_class = request.form.get("travel_class") or ALL_CLASSES
    context = {
        "stations": STATION_LIST,
        "stations_json": STATION_LIST_JSON,
        "station_count": len(STATION_LIST),
        "classes": data_source.CLASSES,
        "all_classes_value": ALL_CLASSES,
        "today": date.today().isoformat(),
        "result": None,
        "using_mock": USING_MOCK,
        "form": {
            "source": request.form.get("source", ""),
            "dest": request.form.get("dest", ""),
            "source_name": request.form.get("source_name", ""),
            "dest_name": request.form.get("dest_name", ""),
            "date": request.form.get("date", ""),
            "travel_class": travel_class,
            "class_label": "any class" if travel_class == ALL_CLASSES else travel_class,
        },
        "error": (
            "You're searching faster than this tool allows (max 10 searches/minute "
            "per visitor). This limit protects the live train-data source, which "
            "every visitor shares - please wait a minute and try again."
        ),
    }
    return render_template("index.html", **context), 429


MAX_ONWARD_PER_TRAIN = 5  # cap the onward list shown under each leg-1 train, so a
                          # junction with a busy leg 2 doesn't turn into a wall of
                          # text - "+N more, see live" covers the rest.


def _build_diagnostics(source, dest, travel_date, travel_class):
    base_date = datetime.strptime(travel_date, "%Y-%m-%d")

    def raw_legs_on(src, dst, date_str):
        try:
            return data_source.get_trains_between(src, dst, date_str), None
        except RailDataError as e:
            return [], str(e)

    def raw_legs(src, dst):
        return raw_legs_on(src, dst, travel_date)

    def format_train(leg):
        # best_unavailable_status (planner.py) is the same "find the best
        # waitlist number across classes" logic used for the checked-best-5
        # direct-trains table - shared so both views agree on what an
        # unavailable "ALL Classes" train should display instead of a bare
        # "NODATA".
        available, matched_class, status = match_leg(leg, travel_class)
        if not available and travel_class == ALL_CLASSES:
            matched_class, status = best_unavailable_status(leg)
        # ConfirmTkt's own confirmation-chance prediction for whichever class
        # ended up shown - never computed locally, only ever the real number
        # ConfirmTkt returned for that class (None if it didn't send one).
        confirm_pct = (leg.get("confirm_pct") or {}).get(matched_class) if matched_class else None
        return {
            "train_no": leg["train_no"],
            "train_name": leg["train_name"],
            "dep_time": leg["dep_time"],
            "arr_time": leg["arr_time"],
            "arr_day_offset": leg["arr_day_offset"],
            "duration_minutes": leg.get("duration_minutes"),
            "confirm_pct": confirm_pct,
            "status": status or "NODATA",
            "matched_class": matched_class,
            "is_available": available,
            "schedule_url": _booking_train_url(leg["train_no"]),
        }

    def available_trains(legs):
        # Trains on this leg that actually have a seat (or RAC) - in any
        # class when travel_class is "ALL", in just the one class
        # otherwise. Matches what search_breakup() would have required.
        return [format_train(leg) for leg in legs if match_leg(leg, travel_class)[0]]

    def onward_trains_for(leg1_train, junction):
        # Every train a passenger COULD actually catch onward from the
        # junction, given they only arrive there when this specific leg-1
        # train does - not every train that happens to run the route. A
        # train that left the junction before this leg-1 train even gets in
        # is not a real option, however available it is, so it's filtered
        # out here rather than left for the person to notice is impossible.
        arr1 = _arrival_datetime(base_date, leg1_train["arr_day_offset"], leg1_train["arr_time"])
        leg2_search_date = (base_date + timedelta(days=leg1_train["arr_day_offset"])).strftime("%Y-%m-%d")
        legs2, _ = raw_legs_on(junction, dest, leg2_search_date)
        catchable = []
        for leg in legs2:
            dep2 = _arrival_datetime(
                base_date,
                leg1_train["arr_day_offset"] + leg["dep_day_offset"],
                leg["dep_time"],
            )
            buffer_minutes = (dep2 - arr1).total_seconds() / 60
            if buffer_minutes < MIN_CONNECTION_MINUTES:
                continue
            formatted = format_train(leg)
            formatted["connection_buffer_minutes"] = round(buffer_minutes)
            catchable.append((leg["dep_day_offset"], leg["dep_time"], formatted))
        catchable.sort(key=lambda x: (x[0], x[1]))
        trains = [t for _, _, t in catchable]
        return {
            "trains": trains[:MAX_ONWARD_PER_TRAIN],
            "more_count": max(0, len(trains) - MAX_ONWARD_PER_TRAIN),
        }

    def leg_summary(legs, available):
        # A compact one-line verdict for a leg, for the "at a glance" table -
        # so the person can see whether ANY option is worth chasing without
        # reading every train row. When nothing is available, the smallest
        # waitlist number across all trains on that leg is still useful: a
        # WL 3 is a very different prospect from a WL 190, even though both
        # count as "not available" today.
        if available:
            return {"state": "available", "count": len(available), "best_wl": None}
        if not legs:
            return {"state": "no_trains", "count": 0, "best_wl": None}
        wl_numbers = []
        for leg in legs:
            # In "any class" mode, scan every class on this train for the
            # best waitlist number - a passenger flexible on class wants to
            # know the single best number across all of them, not just one.
            statuses = (
                list(leg["classes"].values()) if travel_class == ALL_CLASSES
                else [leg["classes"].get(travel_class, "")]
            )
            for status in statuses:
                # Waitlist statuses end in a number (e.g. "GNWL93/WL43",
                # "RLWL212/WL122") - the last number is the one that matters,
                # since it's the current position, not the chart-time quota.
                matches = re.findall(r"(\d+)", status)
                if matches and not status.startswith("REGRET") and status != "NODATA":
                    wl_numbers.append(int(matches[-1]))
        return {
            "state": "waitlisted" if wl_numbers else "regret",
            "count": 0,
            "best_wl": min(wl_numbers) if wl_numbers else None,
        }

    direct_legs, direct_err = raw_legs(source, dest)
    junctions = generate_candidate_junctions(
        source, dest, BREAKUP_MAX_CANDIDATES, priority_stations=BREAKUP_PRIORITY_STATIONS,
    )
    junction_rows = []
    highlights = []
    summary_rows = []
    for code in junctions:
        leg1_legs, leg1_err = raw_legs(source, code)
        leg2_legs, leg2_err = raw_legs(code, dest)
        leg1_available = available_trains(leg1_legs)
        leg2_available = available_trains(leg2_legs)
        junction_name = STATIONS.get(code, (code,))[0]
        junction_rows.append({
            "code": code,
            "name": junction_name,
            "leg1_legs": leg1_legs, "leg1_err": leg1_err,
            "leg2_legs": leg2_legs, "leg2_err": leg2_err,
            "leg1_available": leg1_available,
            "leg2_available": leg2_available,
        })
        # A junction where BOTH legs have at least one seat in this class is
        # a genuine partial match, even though search_breakup() rejected it
        # (usually because no pair of those trains connects in time). Worth
        # surfacing prominently - the person may still be able to use it by
        # picking a different date for the second leg, for example.
        if leg1_available and leg2_available:
            highlights.append({
                "code": code,
                "name": junction_name,
                "leg1_available": leg1_available,
                "leg2_available": leg2_available,
                "leg1_url": _booking_route_url(source, code),
                "leg2_url": _booking_route_url(code, dest),
            })
        # Leg 2 (the onward/continuing leg): NOT one shared list for the
        # whole junction, but a separate, time-filtered list per leg-1
        # train - a passenger who boards the 10AM leg-1 train and one who
        # boards the 6PM leg-1 train can catch different onward trains, and
        # neither should see options that had already left before they
        # arrived.
        leg1_options = [
            {"train": t1, "onward": onward_trains_for(t1, code)}
            for t1 in leg1_available
        ]
        summary_rows.append({
            "code": code,
            "name": junction_name,
            "leg1": leg_summary(leg1_legs, leg1_available),
            "leg2": leg_summary(leg2_legs, leg2_available),
            # Leg 1 (the boarding leg): only the trains with an actual seat
            # (or RAC) - alternates for a leg the person hasn't boarded yet
            # aren't useful here, they just want to know what they can get on.
            "leg1_available": leg1_available,
            "leg1_options": leg1_options,
            # Leg 2's own available trains, shown on their own (without a
            # connection-time filter) when Leg 1 currently has nothing to
            # board - there's no leg-1 arrival time to filter "catchable"
            # against yet, but the person asking "why isn't it SHOWING me
            # those N trains" deserves to actually see them, with a plain
            # note that they're not bookable as a connection until Leg 1
            # opens up. Once Leg 1 does have options, leg1_options already
            # shows the real, time-filtered Leg 2 trains per leg-1 train, so
            # this is only used as a fallback.
            "leg2_available": leg2_available,
            "leg1_url": _booking_route_url(source, code),
            "leg2_url": _booking_route_url(code, dest),
        })
    # Sort so junctions closest to a real match float to the top: both legs
    # available first, then one leg available, then smallest waitlist
    # numbers, then plain "no trains"/"regret only" last.
    def sort_key(row):
        def leg_score(leg):
            if leg["state"] == "available":
                return (0, 0)
            if leg["state"] == "waitlisted":
                return (1, leg["best_wl"])
            return (2, 0)
        s1, s2 = leg_score(row["leg1"]), leg_score(row["leg2"])
        return (s1[0] + s2[0], s1[1] + s2[1])
    summary_rows.sort(key=sort_key)
    return {
        "direct_legs": direct_legs, "direct_err": direct_err,
        "junctions": junction_rows,
        "highlights": highlights,
        "summary_rows": summary_rows,
        "irctc_url": IRCTC_SEARCH_URL,
        "min_connection_minutes": MIN_CONNECTION_MINUTES,
    }


if __name__ == "__main__":
    app.run(debug=True, port=5000)
