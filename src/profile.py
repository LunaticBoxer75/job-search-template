"""The candidate profile (config/profile.json), loaded once.

Everything person-specific lives in that file: identity and intro text, the Pass-2
rubric, and the Pass-1 role families, home area and description keywords. Retarget the
whole system to another person by editing it, not the code.
"""

import json
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).parent.parent / "config" / "profile.json"


@lru_cache(maxsize=None)
def load():
    with open(PATH) as f:
        return json.load(f)


def scoring():
    return load()["scoring"]


def identity():
    return load()["identity"]


def years():
    return load()["experience_summary"]["years"]


def first_name():
    return load()["candidate"]["name"].split()[0]


def intro(kind="short_intro"):
    return identity()[kind].format(years=years())


def home_label():
    return scoring()["home_area"]["label"]
