# Breakup Journey Finder (prototype)

A small Flask app: enter source, destination, date and class. If direct
trains have no availability, it searches for a "breakup journey" — split
into two tickets via a junction station along the route — and ranks the
combinations.

## Run it

```bash
cd breakup-journey-planner
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000. It runs entirely on the bundled mock data — no
internet or credentials needed.

## How the search works (`planner.py`)

1. **Direct check** — look up trains source→dest, rank them fastest-first,
   and check only the **5 best (fastest) trains** — not every train IRCTC
   might list for that route. If any of those 5 has an available/RAC seat in
   the chosen class, show those and stop. This cap is
   `DEFAULT_DIRECT_TRAINS_CHECKED` in `planner.py` — raise it if you want a
   wider direct check before falling back to breakup suggestions.
   `best_direct_trains()` returns the checked 5 regardless of availability,
   so if none has a seat, the app shows exactly which 5 were checked (with
   their waitlist status) directly above the alternate-route suggestions —
   not just "no seats, here are alternatives" with nothing to verify it against.
2. **Candidate junctions** — from a station master list, keep only
   stations that lie geographically between source and dest, using
   *cross-track distance* from the great-circle line (not just "closer to
   the destination", which would wrongly include detours). This is a
   heuristic — a real system should instead use each candidate train's
   actual stop list, which is more accurate than lat/lon geometry but
   needs a train-route dataset.
3. **Two-leg search** — for each junction, find an available leg1
   (source→junction) and an available leg2 (junction→dest) whose departure
   leaves at least 45 minutes after leg1 arrives.
4. **Ranking** — fewer waitlisted/RAC legs first, then shortest total
   travel time, then largest connection buffer.

Currently limited to **one break point (two legs)**. Three-leg journeys are
a straightforward extension of `generate_candidate_junctions()` +
`search_breakup()` if you need them later.

## Data: where it comes from now

As of this version, **the app uses real train data by default** —
`app.py` calls `LiveDataSource`, not `MockDataSource`. There's no signup
and no API key, but there's also no official contract with anyone, so
read this whole section before relying on it.

**What `LiveDataSource` actually does:** it calls the same public,
undocumented endpoint that confirmtkt.com's own website calls to power
its search box (`cttrainsapi.confirmtkt.com/api/v1/trains/search`). This
isn't an IRCTC or Indian Railways API, and it isn't something ConfirmTkt
publishes or supports for outside use — it was reverse-engineered from
their site's network traffic, the same way the open-source project
[indian_railway_mcp-server](https://github.com/RishiMaddheshiya/indian_railway_mcp-server)
documents doing. That means:
- **No guarantee it keeps working.** ConfirmTkt can change the response
  shape, rate-limit it, or block it outright, at any time, with no notice
  to anyone. There's no ToS being honored here because there's no
  agreement to honor — just the same caution that applied to scraping
  NTES/IRCTC directly: personal experimentation only, not something to
  build a real product on or share with others.
- **When it fails, it fails loudly.** `get_trains_between()` raises
  `RailDataError` on any network error, non-200 response, or response
  shape it doesn't recognize — `app.py` catches that and shows a plain
  "live lookup failed" message. It never silently substitutes a fake
  result, so a broken lookup can't be mistaken for a real "no seats"
  answer.
- **If it breaks:** every failure dumps the raw response into
  `debug_responses/` (or says why it couldn't). Open the newest file
  there, see what ConfirmTkt is actually sending back now, and fix the
  field names read in `LiveDataSource._parse_train()` accordingly — the
  parsing logic is deliberately isolated in that one method so a change
  there never has to touch `planner.py`.
- **Two things worth knowing about the data:** dates are sent as
  `DD/MM/YYYY` (only tested by reasoning about the reverse-engineered
  request, not by actually calling the endpoint from this environment —
  if it looks like the wrong trains come back, this is the first thing to
  check), and a breakup search can make several live calls in one page
  load (once per candidate junction) — there's a same-run in-memory cache
  to avoid repeating an identical call, but no cross-request caching.

**`MockDataSource`** (deterministic fake data, hashed from `train_no` /
`date` / `class`) is still in `data_source.py` and still fully working —
switch `data_source = LiveDataSource()` back to `MockDataSource()` in
`app.py` any time you want to demo the breakup-journey *logic* offline,
without depending on ConfirmTkt being reachable.

**A paid alternative, if you ever want one:** rail-data listings exist on
RapidAPI (e.g. "IRCTC1") with official-ish endpoints for train search and
seat availability. They require a paid plan (no meaningful free tier was
found at the time of writing) — worth it only if reliability matters more
than cost for your use case.

`stations_data.py` now ships with **8,700 stations covering all of India**:
~170 hand-picked major junctions/state capitals (kept as `MAJOR_STATIONS`,
used to prioritize real interchanges over random geographically-close
halts when ranking breakup candidates), plus 8,697 real stations (code,
name, lat/lon) nationwide pulled from `stations_all_india.json`, sourced
from the [datameet/railways](https://github.com/datameet/railways) open
dataset (CC0). This isn't a live-updated master list (a station master
list drifts over time — new stations open, some get renumbered), but it's
the real official station list for 8,697 stations across every state, not
hand-typed guesses, and covers the overwhelming majority of what IRCTC
itself lets you search between. If a station you need genuinely isn't in
the dropdown, the same dataset's source repo is the place to check for an
update, or data.gov.in / Kaggle's "Indian Railways stations" listing.

## Known simplifications (worth knowing before you rely on this)

- **The station list and the train schedule are two separate, independent
  pieces of data.** `stations_data.py` has 8,700 real stations you can pick
  from in the dropdown, but `SAMPLE_TRAIN_ROUTES` (mock schedule data, also
  in `stations_data.py`) only defines a handful of made-up demo trains,
  currently touching: HWH, ASN, GAYA, PNBE, DDU, PRYJ, CNB, NDLS, CSMT, NGP,
  ET, BPL, JHS, CPR, MFP, GKP. Adding a station to the dropdown never adds a
  train through it — **most searches between two arbitrary stations will
  correctly return "no routes found," even for real routes with real
  trains**, simply because this prototype has no schedule data for them.
  This is not a bug; it's what a demo with fake data looks like before
  `LiveDataSource` is wired up (see above). The "no routes found" page and
  the footer under the search form both spell this out and list the
  stations that do have demo data, so it's not confused with a real
  "sold out" result.
- Each demo train now has a return-direction counterpart (e.g. `12561`
  Chhapra→New Delhi and `12562` New Delhi→Chhapra), so searching a round
  trip (source/dest swapped) also finds mock trains, matching how real
  railways run paired "up" and "down" services. Earlier every demo train
  only ran one direction, so the return leg of a trip always came back
  empty even on dates where the outbound leg had trains — that was a bug,
  now fixed.
- The mock availability hash is keyed only on `(train_no, date, class)`, not
  on which two stops you searched between. That means two different legs
  of the *same* physical train always show the same status on a given
  date, so a breakup suggestion can never be built from two portions of one
  train — it needs at least two distinct trains covering the source and
  destination sides. This mostly matters for the three original routes
  (`12302`, `12321`, `12245`), which are each the only train covering their
  stretch; the Chhapra-area routes added later have enough overlapping
  trains (e.g. `12561`, `15561`, `15277` all touch different combinations
  of CPR/PNBE/DDU/CNB) that breakups do show up there. Worth knowing if a
  route only ever shows "no routes found" or "direct trains" and never a
  breakup suggestion.
- Junction candidates for a breakup are chosen from `STATIONS` by geography
  (which stations lie roughly on the source→dest line), then re-ranked so
  any station that's actually in `SAMPLE_TRAIN_ROUTES`
  (`KNOWN_SCHEDULE_STATIONS` in `planner.py`) is tried first, even if a
  geometrically closer station (with zero schedule data) exists. In a real
  integration, with a real nationwide schedule, every station would be
  "known" and this reordering would have no effect — it exists purely to
  keep this specific prototype's tiny mock schedule from being buried under
  its much larger station list. Real production logic should instead pick
  junctions from each candidate train's actual stop list, which is more
  accurate than lat/lon geometry but needs a real train-route dataset.
- No fare comparison yet — a breakup itinerary can occasionally cost more
  than a full-quota direct ticket in a different class; worth adding once
  fares are in the data source.
- Doesn't yet account for IRCTC's own breakup-journey booking rules (e.g.
  minimum distance/time between the two legs); worth double-checking a
  suggested combo against IRCTC's own booking flow before relying on it.

## Deploying it publicly (free)

This needs a host that (a) runs a real Python process (not a static-site or
serverless-function platform) and (b) doesn't restrict outbound HTTPS to an
allowlist, since every search calls `cttrainsapi.confirmtkt.com` — an
arbitrary third-party domain. That second point specifically rules out
**PythonAnywhere's free tier**: its free plan only allows outbound requests
to a fixed allowlist of well-known sites, so every real search would fail
there with a `RailDataError` even though the app itself is fine.

**Render.com's free web service** works and needs no credit card:

1. Push this folder to a **public GitHub repo** (Render deploys from git,
   not a zip upload):
   ```bash
   cd breakup-journey-planner
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<you>/breakup-journey-finder.git
   git push -u origin main
   ```
2. On [render.com](https://render.com), sign up (GitHub login is fine), then
   **New + → Web Service**, and pick that repo. Render should auto-detect
   the included `render.yaml` (build command `pip install -r
   requirements.txt`, start command `gunicorn app:app --bind
   0.0.0.0:$PORT`, plan `free`) — if it doesn't, enter those two commands
   manually and select the **Free** instance type.
3. **Before or right after the first deploy**, set the two environment
   variables the login system needs — **Dashboard → your service →
   Environment**: `SECRET_KEY` (any long random string) and `ADMIN_PASSWORD`
   (your own choice). Without these, `/admin` refuses to load and sessions
   won't persist properly — see "Access control" below for exactly what
   each one does. Save triggers a redeploy automatically.
4. Deploy. You'll get a `https://<something>.onrender.com` URL — that's the
   public link. Open `<that URL>/admin`, log in with `ADMIN_PASSWORD`, and
   generate your first access code before sharing the link with anyone —
   the app itself is unreachable without one (see "Access control" below).

**Trade-off of the free plan**: it spins the app down after ~15 minutes with
no traffic, so the first request after a quiet spell takes 30-60 seconds to
wake back up. Fine for a personal/low-traffic tool; not great if you want it
always-instant.

**Worth knowing before sharing it widely**: `cttrainsapi.confirmtkt.com` is
an unofficial, undocumented endpoint (see the caveats above) — it has no
contract with anyone and no published rate limits. There's no way to fully
prevent it eventually getting noticed, rate-limited, or blocked at some
point once real public traffic hits it - it isn't your API, and nothing
below is a guarantee. What *is* in place to meaningfully lower that risk:

- **A 3-minute cache per (source, destination, date)**, shared across every
  visitor (`LiveDataSource.CACHE_TTL_SECONDS` in `data_source.py`). If ten
  people search the same popular route within a few minutes, only the
  first one actually calls the live source - everyone else gets the cached
  result. This is the single biggest lever, since it cuts request volume
  roughly in proportion to how many people search the same routes. Short
  enough that waitlist/RAC numbers (which genuinely change during the day)
  don't go stale for long.
- **A per-visitor rate limit** (10 searches/minute, in `app.py` via
  Flask-Limiter) stops one person - or a bot - from hammering the live
  source through this app. It shows a plain, honest "you're searching too
  fast" page rather than a confusing error.
- **A distinct, honest error for HTTP 429/403** ("rate-limited or blocked,
  try again in a few minutes") instead of a generic failure message, so a
  temporary block doesn't look like the app itself is broken. It's never
  auto-retried - retrying something that's already pushing back just makes
  it worse.

None of this changes the fundamental fact that this rides on an API that
was never meant to be depended on. If it does get blocked, the honest
failure (a "live lookup failed" message, never fake data) is the correct
behavior, not a bug - it just means waiting it out.

The per-visitor rate limit and the cache both live in a single process's
memory, so they reset on every restart and don't share state if you ever
run more than one instance. Fine for Render's free single-instance plan;
worth swapping for a shared store (e.g. Redis, via a free tier like
Upstash) if this ever needs to scale beyond that.

**Alternatives to Render**, if the free-tier sleep is a dealbreaker: Fly.io
and Google Cloud Run both have generous free-usage tiers with no outbound
restrictions, but both require a credit card on file even though normal
personal-scale traffic stays within the free quota.

## Access control: invite-only login

The whole app (except the login page itself) is gated behind a 10-character
access code, handed out from a separate admin panel. This is the main
mitigation for hosting on the unofficial data source publicly: instead of
"anyone on the internet", traffic is limited to the specific people you
personally give a code to. It doesn't change the ToS situation described
above, but it does substantially shrink who can actually reach the app.

**How it works:**
- `/login` asks for a 10-character code. A valid, non-revoked code sets a
  session cookie and lets that browser in; the code itself is reusable
  (the same person logs in with it again later) until you revoke it.
- `/admin` is a *separate* password-gated panel (not an access code) where
  you generate and revoke codes. It shows a list of every code issued, with
  a label, when it was created, when it was last used, and how many times.
- Codes are stored in a local SQLite file (`access_codes.db`) as SHA-256
  hashes, never in plaintext. A generated code's plaintext is shown exactly
  once, right after you create it, in the admin panel — copy it immediately,
  there's no way to look it up again afterwards (generate a new one and
  revoke the old one, like a password reset).

**Required environment variables** (the app deliberately has no usable
default for either, so set both before/while deploying):
- `SECRET_KEY` — signs the login session cookie. Generate one once with
  `python3 -c "import secrets; print(secrets.token_hex(32))"` and paste the
  result in as the env var. If this isn't set, a hardcoded, insecure dev
  value is used instead (fine for `python app.py` on your own machine, never
  for a public deployment).
- `ADMIN_PASSWORD` — the password for `/admin`. If this isn't set at all,
  `/admin` refuses to load (returns a plain "disabled" message) rather than
  running with a guessable default.

On Render: **Dashboard → your service → Environment**, add both as
environment variables, then redeploy (or it'll prompt you to on save).

**Using it day to day:** open `https://<your-app>.onrender.com/admin`, enter
`ADMIN_PASSWORD`, generate a code with a label (e.g. "Mom"), and send that
code to the person however you like (text, WhatsApp, etc.) — they open the
app's normal URL, get redirected to `/login`, and enter it. Revoke a code
any time from the same panel if you want to cut someone off; the app itself
is completely unreachable to anyone without a currently-valid code.

**Caveat worth knowing:** `access_codes.db` is a local file. Render's free
plan wipes local disk **on every redeploy** (not on every sleep/wake cycle —
just when you push new code or the service is rebuilt), which means every
code you've issued stops working after a redeploy and needs regenerating.
Fine for a small invite-only tool with infrequent code changes; move to a
real hosted database (e.g. a free Postgres instance) if that becomes
annoying.
