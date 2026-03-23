from __future__ import annotations

from typing import Optional

ARIZONA_CITIES = [
    "Apache Junction",
    "Avondale",
    "Buckeye",
    "Casa Grande",
    "Chandler",
    "El Mirage",
    "Fountain Hills",
    "Gilbert",
    "Glendale",
    "Goodyear",
    "Laveen",
    "Litchfield Park",
    "Maricopa",
    "Mesa",
    "Paradise Valley",
    "Peoria",
    "Phoenix",
    "Queen Creek",
    "Scottsdale",
    "Sun City",
    "Sun City West",
    "Surprise",
    "Tempe",
    "Tolleson",
    "Wickenburg",
    "Youngtown",
]


_CITY_LOOKUP = {c.upper(): c for c in ARIZONA_CITIES}
_CITY_LOOKUP.update({
    "SUN CITY GRAND": "Sun City",
    "SUN CITY AZ": "Sun City",
    "PHX": "Phoenix",
})


def canonicalize_city(city: Optional[str]) -> Optional[str]:
    if city is None:
        return None
    c = str(city).strip()
    if not c:
        return None
    c = " ".join(c.split())
    up = c.upper()
    if up in _CITY_LOOKUP:
        return _CITY_LOOKUP[up]
    return c.title()
