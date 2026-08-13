from __future__ import annotations

import re
from dataclasses import dataclass, field


INDIAN_STATES = {
    "andhra pradesh",
    "arunachal pradesh",
    "assam",
    "bihar",
    "chhattisgarh",
    "delhi",
    "goa",
    "gujarat",
    "haryana",
    "himachal pradesh",
    "jharkhand",
    "karnataka",
    "kerala",
    "madhya pradesh",
    "maharashtra",
    "manipur",
    "meghalaya",
    "mizoram",
    "nagaland",
    "odisha",
    "orissa",
    "punjab",
    "rajasthan",
    "sikkim",
    "tamil nadu",
    "telangana",
    "tripura",
    "uttar pradesh",
    "uttarakhand",
    "west bengal",
}

COUNTRIES = {
    "india",
    "united states",
    "usa",
    "us",
    "uk",
    "united kingdom",
    "israel",
    "palestine",
    "russia",
    "ukraine",
    "china",
    "japan",
    "france",
    "germany",
    "australia",
    "canada",
}

CITIES = {
    "bengaluru",
    "bangalore",
    "kolkata",
    "new delhi",
    "delhi",
    "mumbai",
    "chennai",
    "hyderabad",
    "pune",
    "jaipur",
    "lucknow",
    "ahmedabad",
}

POLITICIANS = {
    "suvendu adhikari",
    "suvendhu adhikari",
    "narendra modi",
    "siddaramaiah",
    "d k shivakumar",
    "dk shivakumar",
    "amit shah",
    "rahul gandhi",
    "arvind kejriwal",
    "yogi adityanath",
    "mk stalin",
    "m k stalin",
    "pinarayi vijayan",
}

LOCATION_ALIASES = {
    "orissa": "odisha",
    "bangalore": "bengaluru",
    "usa": "united states",
    "us": "united states",
    "uk": "united kingdom",
    "suvendhu adhikari": "suvendu adhikari",
}


@dataclass(frozen=True)
class EntitySet:
    states: set[str] = field(default_factory=set)
    countries: set[str] = field(default_factory=set)
    cities: set[str] = field(default_factory=set)
    politicians: set[str] = field(default_factory=set)
    dates: set[str] = field(default_factory=set)

    def is_empty(self) -> bool:
        return not (self.states or self.countries or self.cities or self.politicians or self.dates)


def extract_entities(text: str) -> EntitySet:
    normalized = _normalize(text)
    return EntitySet(
        states=_find_known_entities(normalized, INDIAN_STATES),
        countries=_find_known_entities(normalized, COUNTRIES),
        cities=_find_known_entities(normalized, CITIES),
        politicians=_find_known_entities(normalized, POLITICIANS),
        dates=_extract_dates(normalized),
    )


def entities_conflict(query_entities: EntitySet, memory_entities: EntitySet) -> bool:
    return any(
        _category_conflicts(query_values, memory_values)
        for query_values, memory_values in (
            (query_entities.states, memory_entities.states),
            (query_entities.countries, memory_entities.countries),
            (query_entities.cities, memory_entities.cities),
            (query_entities.politicians, memory_entities.politicians),
            (query_entities.dates, memory_entities.dates),
        )
    )


def merge_entities(*entity_sets: EntitySet) -> EntitySet:
    return EntitySet(
        states=set().union(*(entities.states for entities in entity_sets)),
        countries=set().union(*(entities.countries for entities in entity_sets)),
        cities=set().union(*(entities.cities for entities in entity_sets)),
        politicians=set().union(*(entities.politicians for entities in entity_sets)),
        dates=set().union(*(entities.dates for entities in entity_sets)),
    )


def _find_known_entities(text: str, values: set[str]) -> set[str]:
    found: set[str] = set()
    for value in values:
        if re.search(rf"\b{re.escape(value)}\b", text):
            found.add(LOCATION_ALIASES.get(value, value))
    return found


def _extract_dates(text: str) -> set[str]:
    found = set(re.findall(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", text))
    found.update(re.findall(r"\b(?:19|20)\d{2}\b", text))
    for word in ("today", "yesterday", "tomorrow", "recent", "latest", "current"):
        if re.search(rf"\b{word}\b", text):
            found.add(word)
    return found


def _category_conflicts(query_values: set[str], memory_values: set[str]) -> bool:
    if not query_values or not memory_values:
        return False
    return query_values.isdisjoint(memory_values)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())
