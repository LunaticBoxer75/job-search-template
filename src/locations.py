"""Where a job is: Seattle area vs rest of USA, and which big metro.

One definition shared by the scorer, the scraper, and the dashboard. The user's
priority (2026-09-27): Seattle area first (its own tab), then big US metros, then
everything else.

Matching is city + state, exact on the city name — "Portland, ME" is not
Portland OR, "Arlington, VA" is DC not Dallas, "Everett, MA" is not Seattle.
"""

import re
from functools import lru_cache

# metro -> {state: [cities]}. Cities are lowercase, matched exactly.
METROS = {
    "Bay Area": {"CA": [
        "san francisco", "south san francisco", "oakland", "berkeley", "emeryville", "san jose",
        "santa clara", "sunnyvale", "mountain view", "palo alto", "menlo park", "redwood city",
        "san mateo", "foster city", "burlingame", "san bruno", "cupertino", "milpitas", "fremont",
        "newark", "hayward", "pleasanton", "san ramon", "walnut creek", "los gatos", "campbell",
        "san carlos", "belmont", "brisbane", "livermore", "dublin"]},
    "New York area": {
        "NY": ["new york", "new york city", "manhattan", "brooklyn", "queens", "bronx", "staten island",
               "long island city", "white plains", "tarrytown", "purchase", "rye"],
        "NJ": ["jersey city", "hoboken", "newark", "weehawken", "secaucus", "parsippany", "princeton",
               "morristown", "edison", "iselin", "florham park", "whippany", "fort lee", "paramus"],
        "CT": ["stamford", "greenwich", "norwalk"]},
    "Los Angeles area": {"CA": [
        "los angeles", "santa monica", "culver city", "burbank", "pasadena", "glendale", "long beach",
        "el segundo", "torrance", "beverly hills", "playa vista", "irvine", "costa mesa", "newport beach",
        "anaheim", "santa ana", "carson", "commerce", "city of industry", "ontario", "rancho cucamonga"]},
    "Chicago area": {"IL": [
        "chicago", "evanston", "oak brook", "schaumburg", "naperville", "deerfield", "northbrook",
        "rosemont", "itasca", "lake forest", "downers grove", "lombard", "elk grove village",
        "des plaines", "skokie", "bolingbrook", "aurora"]},
    "Austin": {"TX": ["austin", "round rock", "cedar park", "georgetown", "pflugerville", "leander"]},
    "Dallas-Fort Worth": {"TX": [
        "dallas", "fort worth", "irving", "plano", "frisco", "arlington", "richardson", "addison",
        "carrollton", "grapevine", "coppell", "lewisville", "mckinney", "southlake", "allen",
        "las colinas", "garland", "denton", "westlake"]},
    "Boston area": {"MA": [
        "boston", "cambridge", "somerville", "waltham", "burlington", "woburn", "quincy", "newton",
        "needham", "lexington", "framingham", "bedford", "marlborough", "wellesley", "watertown",
        "andover", "canton", "braintree"]},
    "Atlanta area": {"GA": [
        "atlanta", "alpharetta", "marietta", "sandy springs", "duluth", "norcross", "kennesaw",
        "peachtree corners", "roswell", "smyrna", "dunwoody", "lawrenceville"]},
    "Denver area": {"CO": [
        "denver", "aurora", "englewood", "lakewood", "broomfield", "boulder", "littleton",
        "centennial", "greenwood village", "westminster", "golden", "louisville"]},
    # Big metros we don't currently search, but still rank as big cities when found
    "Houston": {"TX": ["houston", "the woodlands", "sugar land", "katy", "spring"]},
    "Washington DC area": {
        "DC": ["washington"], "VA": ["arlington", "alexandria", "reston", "mclean", "herndon", "tysons",
                                     "fairfax", "chantilly", "vienna"],
        "MD": ["bethesda", "rockville", "silver spring", "gaithersburg", "college park"]},
    "Philadelphia area": {"PA": ["philadelphia", "king of prussia", "conshohocken", "malvern", "wayne"]},
    "Phoenix area": {"AZ": ["phoenix", "tempe", "scottsdale", "chandler", "mesa", "gilbert", "glendale"]},
    "Charlotte": {"NC": ["charlotte"]},
    "Miami area": {"FL": ["miami", "fort lauderdale", "boca raton", "doral", "coral gables"]},
    "Minneapolis-St. Paul": {"MN": ["minneapolis", "saint paul", "st. paul", "st paul", "bloomington",
                                    "eden prairie", "minnetonka", "plymouth", "eagan", "edina"]},
    "San Diego": {"CA": ["san diego", "carlsbad", "la jolla"]},
    "Raleigh-Durham": {"NC": ["raleigh", "durham", "morrisville", "cary", "research triangle park"]},
    "Portland OR area": {"OR": ["portland", "beaverton", "hillsboro"]},
    "Salt Lake City": {"UT": ["salt lake city", "lehi", "south jordan", "draper", "sandy"]},
    "Detroit area": {"MI": ["detroit", "dearborn", "troy", "southfield", "auburn hills", "ann arbor"]},
    "Nashville": {"TN": ["nashville", "franklin", "brentwood"]},
}

_STATE_NAMES = {
    "california": "CA", "new york": "NY", "new jersey": "NJ", "connecticut": "CT", "illinois": "IL",
    "texas": "TX", "massachusetts": "MA", "georgia": "GA", "colorado": "CO", "virginia": "VA",
    "maryland": "MD", "district of columbia": "DC", "pennsylvania": "PA", "arizona": "AZ",
    "north carolina": "NC", "florida": "FL", "minnesota": "MN", "oregon": "OR", "utah": "UT",
    "michigan": "MI", "tennessee": "TN",
}

_INDEX = {(state, city): metro for metro, states in METROS.items()
          for state, cities in states.items() for city in cities}


@lru_cache(maxsize=50000)
def metro_of(location):
    """Big-metro name for 'City, ST[, US]' locations, else None."""
    parts = [p.strip() for p in str(location or "").split(",") if p.strip()]
    if len(parts) < 2:
        return None
    city = re.sub(r"\s+", " ", parts[0].lower())
    state = parts[1].strip()
    state = _STATE_NAMES.get(state.lower(), state.upper())
    return _INDEX.get((state, city))


def region_of(location, is_remote=False, title=""):
    """'seattle' only when the job is actually in the home area, else 'national'.

    'seattle' is the internal id of the home area set in config/profile.json
    (scoring.home_area); the stored rows use it, so it is not renamed per candidate.
    Decided by where the job is — not by which search returned it (a home-area
    search also returns out-of-area postings).
    """
    from .quick_scorer import HOME_LABELS, _location_points  # lazy: quick_scorer imports scraper

    _, label = _location_points(location, is_remote, title)
    return "seattle" if label in HOME_LABELS else "national"
