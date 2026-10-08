"""
Station master data.

STATIONS now covers (nearly) every Indian Railways station nationwide, not
just a hand-picked subset. It combines two sources:
1. A hand-picked set of ~170 major junctions/state capitals (kept as
   MAJOR_STATIONS below - see that comment for why it still matters even
   though STATIONS itself is now nationwide).
2. stations_all_india.json - 8,697 real stations (code, name, lat/lon)
   nationwide, pulled from the datameet/railways open dataset (CC0) at
   https://github.com/datameet/railways (its `stations.json`, every
   feature that has geometry). This is not a guarantee of literally every
   halt that has ever existed (a station master list drifts - new stations
   open, some get renumbered), but it is the real official station list for
   8,697 stations across every state, not hand-typed guesses, and covers
   the overwhelming majority of what IRCTC itself lets you search between.
"""

import json
import os

# code -> (name, lat, lon)
#
# This is a curated list of ~170 major stations (state capitals, big cities,
# and well-known junctions) spanning every zone, compiled by hand rather than
# scraped, so it is small enough to review and ship in source control. It is
# NOT the full ~8,000-station Indian Railways master list — see README.md
# for where to get that (data.gov.in / Kaggle "Indian Railways stations") if
# you need every halt station too. Coordinates are city-level approximations,
# accurate enough for the geographic junction search in planner.py.
STATIONS = {
    # Delhi / NCR
    "NDLS": ("New Delhi", 28.6432, 77.2197),
    "DLI":  ("Old Delhi", 28.6621, 77.2273),
    "NZM":  ("Hazrat Nizamuddin", 28.5877, 77.2536),
    "DEC":  ("Delhi Cantt", 28.6100, 77.1150),
    "ANVT": ("Anand Vihar Terminal", 28.6497, 77.3152),
    "GGN":  ("Gurgaon", 28.4670, 77.0180),
    # Uttar Pradesh
    "CNB":  ("Kanpur Central", 26.4499, 80.3319),
    "PRYJ": ("Prayagraj Jn", 25.4484, 81.8280),
    "DDU":  ("Deen Dayal Upadhyaya Jn (Mughalsarai)", 25.2851, 83.1287),
    "LKO":  ("Lucknow NR", 26.8467, 80.9462),
    "LJN":  ("Lucknow Jn NER", 26.8375, 80.9246),
    "GKP":  ("Gorakhpur Jn", 26.7606, 83.3732),
    "BSB":  ("Varanasi Jn", 25.3269, 82.9862),
    "AGC":  ("Agra Cantt", 27.1577, 77.9899),
    "JHS":  ("Jhansi Jn", 25.4484, 78.5685),
    "ALD":  ("Prayagraj (Allahabad) City", 25.4425, 81.8299),
    "MTJ":  ("Mathura Jn", 27.4924, 77.6737),
    "ALJN": ("Aligarh Jn", 27.8892, 78.0742),
    "BE":   ("Bareilly", 28.3371, 79.4108),
    "MB":   ("Moradabad", 28.8386, 78.7768),
    "GD":   ("Gonda Jn", 27.1333, 81.9667),
    "FD":   ("Faizabad Jn (Ayodhya Cantt)", 26.7735, 82.1442),
    "AY":   ("Ayodhya", 26.7879, 82.2007),
    # Bihar / Jharkhand
    "PNBE": ("Patna Jn", 25.6086, 85.1414),
    "PPTA": ("Patliputra Jn", 25.6208, 85.0977),
    "GAYA": ("Gaya Jn", 24.7955, 84.9994),
    "DNR":  ("Danapur", 25.5822, 85.0449),
    "MFP":  ("Muzaffarpur Jn", 26.1197, 85.3910),
    "DBG":  ("Darbhanga Jn", 26.1567, 85.9072),
    "BJU":  ("Barauni Jn", 25.4621, 85.9891),
    "SEE":  ("Samastipur Jn", 25.8626, 85.7833),
    "DHN":  ("Dhanbad Jn", 23.7912, 86.4292),
    "RNC":  ("Ranchi Jn", 23.3719, 85.3297),
    "TATA": ("Tatanagar Jn", 22.7925, 86.1841),
    "JSME": ("Jamshedpur", 22.7925, 86.1841),
    "BKSC": ("Bokaro Steel City", 23.6567, 86.0851),
    # West Bengal
    "HWH":  ("Howrah Jn", 22.5851, 88.3468),
    "SDAH": ("Sealdah", 22.5675, 88.3703),
    "ASN":  ("Asansol Jn", 23.6739, 86.9524),
    "KGP":  ("Kharagpur Jn", 22.3460, 87.2320),
    "DGR":  ("Durgapur", 23.5204, 87.3119),
    "NJP":  ("New Jalpaiguri", 26.6839, 88.4310),
    "MLDT": ("Malda Town", 25.0300, 88.1419),
    "BWN":  ("Barddhaman Jn", 23.2400, 87.8600),
    # Odisha
    "BBS":  ("Bhubaneswar", 20.2650, 85.8428),
    "PURI": ("Puri", 19.8065, 85.8312),
    "CTC":  ("Cuttack", 20.4625, 85.8828),
    "ROU":  ("Rourkela", 22.2492, 84.8828),
    "VZM":  ("Vizianagaram Jn", 18.1172, 83.4114),
    # Andhra Pradesh / Telangana
    "VSKP": ("Visakhapatnam Jn", 17.6868, 83.2185),
    "BZA":  ("Vijayawada Jn", 16.5062, 80.6480),
    "SC":   ("Secunderabad Jn", 17.4399, 78.4983),
    "HYB":  ("Hyderabad Deccan", 17.3850, 78.4867),
    "TPTY": ("Tirupati", 13.6339, 79.4192),
    "GNT":  ("Guntur Jn", 16.3067, 80.4365),
    "NLR":  ("Nellore", 14.4426, 79.9865),
    "RJY":  ("Rajahmundry", 17.0005, 81.7799),
    "KCG":  ("Kacheguda", 17.3833, 78.4914),
    "WL":   ("Warangal", 17.9689, 79.5941),
    # Karnataka
    "SBC":  ("Bengaluru City Jn (KSR)", 12.9784, 77.5719),
    "YPR":  ("Yesvantpur Jn", 13.0233, 77.5372),
    "UBL":  ("Hubballi Jn", 15.3647, 75.1240),
    "MYS":  ("Mysuru Jn", 12.3072, 76.6553),
    "BGM":  ("Belagavi", 15.8497, 74.4977),
    "GTL":  ("Ballari (Bellary) Jn", 15.1394, 76.9214),
    "UD":   ("Udupi", 13.3409, 74.7421),
    "MAJN": ("Mangaluru Central", 12.8698, 74.8420),
    # Tamil Nadu
    "MAS":  ("Chennai Central", 13.0827, 80.2707),
    "MS":   ("Chennai Egmore", 13.0732, 80.2609),
    "TBM":  ("Tambaram", 12.9246, 80.1265),
    "MDU":  ("Madurai Jn", 9.9195, 78.1193),
    "TPJ":  ("Tiruchirappalli Jn", 10.7952, 78.7087),
    "CBE":  ("Coimbatore Jn", 10.9975, 76.9661),
    "SA":   ("Salem Jn", 11.6529, 78.1600),
    "ED":   ("Erode Jn", 11.3410, 77.7172),
    "TEN":  ("Tirunelveli Jn", 8.7211, 77.7554),
    "TVC":  ("Thiruvananthapuram Central", 8.4875, 76.9525),
    # Kerala
    "ERS":  ("Ernakulam Jn", 9.9816, 76.2999),
    "CLT":  ("Kozhikode", 11.2469, 75.7807),
    "SRR":  ("Shoranur Jn", 10.7594, 76.2696),
    "TCR":  ("Thrissur", 10.5237, 76.2144),
    "KTYM": ("Kottayam", 9.5895, 76.5222),
    # Maharashtra
    "CSMT": ("Mumbai CSMT", 18.9401, 72.8352),
    "BCT":  ("Mumbai Central", 18.9696, 72.8194),
    "LTT":  ("Lokmanya Tilak Terminus", 19.0728, 72.8826),
    "DR":   ("Dadar", 19.0186, 72.8429),
    "TNA":  ("Thane", 19.1868, 72.9750),
    "PUNE": ("Pune Jn", 18.5286, 73.8744),
    "NGP":  ("Nagpur Jn", 21.1466, 79.0882),
    "NK":   ("Nashik Road", 19.9480, 73.8330),
    "AWB":  ("Aurangabad", 19.8762, 75.3433),
    "KOP":  ("Kolhapur", 16.6913, 74.2433),
    "SUR":  ("Solapur", 17.6607, 75.9111),
    "MMR":  ("Manmad Jn", 20.2540, 74.4380),
    "BSL":  ("Bhusaval Jn", 21.0470, 75.7884),
    "WR":   ("Wardha Jn", 20.7453, 78.6022),
    # Madhya Pradesh / Chhattisgarh
    "BPL":  ("Bhopal Jn", 23.2599, 77.4126),
    "ET":   ("Itarsi Jn", 22.6108, 77.7611),
    "JBP":  ("Jabalpur", 23.1670, 79.9330),
    "INDB": ("Indore Jn", 22.7245, 75.8571),
    "UJN":  ("Ujjain Jn", 23.1793, 75.7849),
    "STA":  ("Satna", 24.5670, 80.8320),
    "BINA": ("Bina Jn", 24.1832, 78.1908),
    "REWA": ("Rewa", 24.5364, 81.2961),
    "BSP":  ("Bilaspur Jn", 22.0797, 82.1409),
    "R":    ("Raipur Jn", 21.2379, 81.6337),
    "DURG": ("Durg", 21.1900, 81.2849),
    "KRBA": ("Korba", 22.3595, 82.7501),
    # Gujarat
    "ADI":  ("Ahmedabad Jn", 23.0225, 72.5714),
    "BRC":  ("Vadodara Jn", 22.3072, 73.1812),
    "ST":   ("Surat", 21.1959, 72.8302),
    "RJT":  ("Rajkot Jn", 22.2870, 70.7920),
    "BVC":  ("Bhavnagar Terminus", 21.7663, 72.1519),
    "JAM":  ("Jamnagar", 22.4707, 70.0577),
    "BHUJ": ("Bhuj", 23.2540, 69.6693),
    "GNC":  ("Gandhinagar Cap", 23.2237, 72.6455),
    # Rajasthan
    "JP":   ("Jaipur Jn", 26.9196, 75.7878),
    "JU":   ("Jodhpur Jn", 26.2870, 73.0180),
    "UDZ":  ("Udaipur City", 24.5713, 73.6900),
    "AII":  ("Ajmer Jn", 26.4498, 74.6382),
    "BKN":  ("Bikaner Jn", 28.0182, 73.3060),
    "KOTA": ("Kota Jn", 25.1804, 75.8494),
    "ABR":  ("Abu Road", 24.4784, 72.7772),
    "SGNR": ("Sri Ganganagar", 29.9257, 73.8779),
    # Punjab / Haryana / HP / J&K
    "ASR":  ("Amritsar Jn", 31.6340, 74.8723),
    "JUC":  ("Jalandhar City", 31.3260, 75.5762),
    "LDH":  ("Ludhiana Jn", 30.9010, 75.8573),
    "PTA":  ("Patiala", 30.3398, 76.3869),
    "UMB":  ("Ambala Cantt Jn", 30.3398, 76.8237),
    "KKDE": ("Kurukshetra Jn", 29.9695, 76.8783),
    "ROK":  ("Rohtak Jn", 28.8955, 76.6066),
    "HSR":  ("Hisar", 29.1492, 75.7217),
    "CDG":  ("Chandigarh", 30.7046, 76.8189),
    "SML":  ("Shimla", 31.1048, 77.1734),
    "JAT":  ("Jammu Tawi", 32.6934, 74.8570),
    "SVDK": ("Shri Mata Vaishno Devi Katra", 32.9916, 74.9310),
    # Uttarakhand
    "DDN":  ("Dehradun", 30.3226, 78.0322),
    "HW":   ("Haridwar Jn", 29.9457, 78.1642),
    "KTGM": ("Kathgodam", 29.2650, 79.5330),
    # Assam / North-East
    "GHY":  ("Guwahati", 26.1809, 91.7467),
    "DBRG": ("Dibrugarh", 27.4728, 94.9120),
    "NTSK": ("Tinsukia Jn", 27.4923, 95.3597),
    "AGTL": ("Agartala", 23.8398, 91.2799),
    # Odisha/others already added above
}

# Snapshot of the ~170 hand-picked major junctions/state capitals, BEFORE
# the merge below adds ~8,500 more (mostly much smaller) stations nationwide.
# Used by planner.py as a proxy for "a station real trains actually stop at
# and interchange at" - pure geography can't tell a real junction from a
# small halt that happens to sit almost exactly on the straight line between
# two far-apart cities (which happens more often than you'd expect), so this
# gives the breakup-journey search something better than raw distance to
# rank candidates by for LiveDataSource, where any of the 8,000+ stations
# could technically have a real train, but only some of them are the kind
# of place people actually interchange at.
MAJOR_STATIONS = frozenset(STATIONS.keys())

# --- merge in the full nationwide station list ---
_ALL_INDIA_PATH = os.path.join(os.path.dirname(__file__), "stations_all_india.json")
try:
    with open(_ALL_INDIA_PATH, encoding="utf-8") as _f:
        for _row in json.load(_f):
            # Don't let a nationwide-list entry overwrite one of the
            # hand-picked names above (e.g. "New Delhi" vs a terser official
            # name) - those were chosen for clarity, keep them as-is.
            if _row["code"] not in STATIONS:
                STATIONS[_row["code"]] = (_row["name"], _row["lat"], _row["lon"])
except FileNotFoundError:
    # stations_all_india.json missing - fall back to the curated list only.
    pass

# For the mock data source: a hand-written set of routes so the demo has
# something realistic to search over. Each entry is
# (train_no, name, [(station, day_offset, arr "HH:MM", dep "HH:MM"), ...])
SAMPLE_TRAIN_ROUTES = [
    ("12302", "Howrah Rajdhani", [
        ("HWH", 0, None, "16:55"),
        ("ASN", 0, "18:20", "18:25"),
        ("GAYA", 0, "20:35", "20:40"),
        ("PNBE", 0, "22:00", "22:10"),
        ("DDU", 0, "23:55", "00:05"),
        ("PRYJ", 1, "01:10", "01:15"),
        ("CNB", 1, "02:35", "02:40"),
        ("NDLS", 1, "09:55", None),
    ]),
    ("12321", "Mumbai Mail (via Nagpur)", [
        ("CSMT", 0, None, "21:35"),
        ("NGP", 1, "10:30", "10:40"),
        ("ET", 1, "14:05", "14:10"),
        ("BPL", 1, "15:35", "15:45"),
        ("JHS", 1, "18:40", "18:50"),
        ("NDLS", 2, "01:15", None),
    ]),
    ("12245", "Duronto (short-haul demo)", [
        ("NDLS", 0, None, "22:40"),
        ("CNB", 0, "23:55", "00:00"),
        ("PRYJ", 1, "01:15", "01:20"),
        ("PNBE", 1, "05:10", None),
    ]),
    # Chhapra-area demo trains, added so Bihar/UP stations that were only
    # added to the station *list* also have some mock *schedule* data to
    # search over (see README: station list size and route/schedule data
    # are independent - adding a station doesn't add a train through it).
    ("12561", "Chhapra - New Delhi Express (demo)", [
        ("CPR", 0, None, "20:35"),
        ("DDU", 0, "23:50", "00:00"),
        ("CNB", 1, "02:10", "02:15"),
        ("NDLS", 1, "07:40", None),
    ]),
    ("15561", "Chhapra - Patna Intercity (demo)", [
        ("CPR", 0, None, "06:00"),
        ("PNBE", 0, "09:15", None),
    ]),
    ("15277", "Satyagrah Express (demo)", [
        ("GKP", 0, None, "14:00"),
        ("MFP", 0, "16:40", "16:50"),
        ("CPR", 0, "18:05", "18:10"),
        ("DDU", 0, "22:15", "22:25"),
        ("PRYJ", 0, "23:45", "23:55"),
        ("CNB", 1, "01:20", "01:25"),
        ("NDLS", 1, "07:50", None),
    ]),
    # --- return-direction ("down") counterparts of every route above ---
    # Real trains run both ways; each demo route so far only went one
    # direction (source always earlier in the stop list than dest), so a
    # return search (e.g. NDLS -> CPR after searching CPR -> NDLS) always
    # came up empty even though the "up" direction had trains. These give
    # every mock route a same-day-ish return working, same as IRCTC does
    # with paired train numbers, so a round trip search isn't a dead end.
    ("12301", "New Delhi - Howrah Rajdhani", [
        ("NDLS", 0, None, "16:55"),
        ("CNB", 0, "22:35", "22:40"),
        ("PRYJ", 1, "00:55", "01:00"),
        ("DDU", 1, "02:45", "02:55"),
        ("PNBE", 1, "04:50", "05:00"),
        ("GAYA", 1, "06:20", "06:25"),
        ("ASN", 1, "08:35", "08:40"),
        ("HWH", 1, "10:05", None),
    ]),
    ("12322", "Mumbai Mail (via Nagpur), return", [
        ("NDLS", 0, None, "22:00"),
        ("JHS", 1, "04:25", "04:35"),
        ("BPL", 1, "07:30", "07:40"),
        ("ET", 1, "09:05", "09:10"),
        ("NGP", 1, "12:35", "12:45"),
        ("CSMT", 2, "01:40", None),
    ]),
    ("12246", "Duronto (short-haul demo), return", [
        ("PNBE", 0, None, "22:00"),
        ("PRYJ", 1, "01:50", "01:55"),
        ("CNB", 1, "03:10", "03:15"),
        ("NDLS", 1, "08:30", None),
    ]),
    ("12562", "New Delhi - Chhapra Express (demo)", [
        ("NDLS", 0, None, "21:00"),
        ("CNB", 1, "02:30", "02:35"),
        ("DDU", 1, "04:50", "05:00"),
        ("CPR", 1, "08:20", None),
    ]),
    ("15562", "Patna - Chhapra Intercity (demo)", [
        ("PNBE", 0, None, "16:00"),
        ("CPR", 0, "19:15", None),
    ]),
    ("15278", "Satyagrah Express (demo), return", [
        ("NDLS", 0, None, "20:00"),
        ("CNB", 1, "02:25", "02:30"),
        ("PRYJ", 1, "03:55", "04:00"),
        ("DDU", 1, "05:20", "05:30"),
        ("CPR", 1, "09:35", "09:45"),
        ("MFP", 1, "10:55", "11:05"),
        ("GKP", 1, "13:45", None),
    ]),
]

# Every station code that appears in at least one mock route above - i.e.
# every station this demo actually has *schedule* data for, as opposed to
# just being in the 1,300+ entry STATIONS *list*. planner.py uses this to
# stop proposing geometrically-plausible junctions that have zero trains
# in the mock data (which is most of them - see README).
KNOWN_SCHEDULE_STATIONS = frozenset(
    stop[0] for _train_no, _name, _stops in SAMPLE_TRAIN_ROUTES for stop in _stops
)
