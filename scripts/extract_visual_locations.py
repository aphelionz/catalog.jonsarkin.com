#!/usr/bin/env python3
"""
extract_visual_locations.py — Extract geographic locations from visual work transcriptions.

Dictionary scan only ($0) — locations are straightforward proper nouns on artwork surfaces.
Uses the SAME custom vocab and dcterms:spatial property as JIM Stories for cross-media matching.

Usage:
  python scripts/extract_visual_locations.py                    # dry run
  python scripts/extract_visual_locations.py --apply            # write to DB
  python scripts/extract_visual_locations.py --item-id 5102     # single item
  python scripts/extract_visual_locations.py --cross-media      # cross-media reports
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

# ── Constants ──────────────────────────────────────────────────────────────

PROPERTY_ID_CONTENT = 91    # bibo:content (transcription)
PROPERTY_ID_IDENT = 10      # dcterms:identifier
PROPERTY_ID_TITLE = 1       # dcterms:title
PROPERTY_ID_DATE = 7        # dcterms:date
PROPERTY_ID_SPATIAL = 40    # dcterms:spatial (locations)

EXCLUDED_ITEM_SETS = (8020, 8021, 7502)  # JIM Stories, Press & Docs, jsarkin.com Writings
MIN_TRANSCRIPTION_LENGTH = 10

CUSTOM_VOCAB_ID = 10
CUSTOM_VOCAB_TYPE = "customvocab:10"
CUSTOM_VOCAB_LABEL = "JIM Story Locations"

FACET_ID_LOCATION = 22      # existing Location facet

VALID_LOCATION_TYPES = [
    "city", "state", "country", "region", "landmark", "body_of_water",
]


# ── DB helpers ─────────────────────────────────────────────────────────────

def db_query(sql: str) -> str:
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "db",
         "mariadb", "-u", "root", "-proot", "omeka",
         "--batch", "--raw", "-e", sql],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def db_execute(sql: str) -> None:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "db",
         "mariadb", "-u", "root", "-proot", "omeka",
         "-e", sql],
        capture_output=True, text=True, check=True,
    )


def fetch_visual_works(item_id: int | None = None) -> list[dict]:
    """Fetch visual work items with transcription, identifier, title, date.
    Excludes JIM Stories, Press & Docs, and jsarkin.com Writings.
    """
    excluded_csv = ",".join(str(s) for s in EXCLUDED_ITEM_SETS)
    where_item = f"AND i.id = {int(item_id)}" if item_id else ""
    sep = "<<<REC>>>"
    sql = f"""
        SELECT CONCAT_WS('{sep}',
               i.id,
               MAX(CASE WHEN v.property_id = {PROPERTY_ID_IDENT} THEN v.value END),
               MAX(CASE WHEN v.property_id = {PROPERTY_ID_TITLE} THEN v.value END),
               MAX(CASE WHEN v.property_id = {PROPERTY_ID_DATE} THEN v.value END),
               REPLACE(REPLACE(
                   MAX(CASE WHEN v.property_id = {PROPERTY_ID_CONTENT} THEN v.value END),
                   '\\n', '<<NL>>'), '\\r', '')
        ) AS row_data
        FROM item i
        JOIN value v ON i.id = v.resource_id
        WHERE v.property_id IN ({PROPERTY_ID_IDENT}, {PROPERTY_ID_TITLE},
                                {PROPERTY_ID_DATE}, {PROPERTY_ID_CONTENT})
          AND i.id NOT IN (
              SELECT item_id FROM item_item_set
              WHERE item_set_id IN ({excluded_csv})
          )
          {where_item}
        GROUP BY i.id
        HAVING MAX(CASE WHEN v.property_id = {PROPERTY_ID_CONTENT} THEN v.value END) IS NOT NULL
        ORDER BY MAX(CASE WHEN v.property_id = {PROPERTY_ID_IDENT} THEN v.value END);
    """
    lines = db_query(sql).strip().split("\n")
    if len(lines) < 2:
        return []
    items = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split(sep)
        if len(parts) < 5:
            continue
        content = parts[4].replace("<<NL>>", "\n")
        items.append({
            "item_id": int(parts[0]),
            "identifier": parts[1] or "",
            "title": parts[2] or "",
            "date": parts[3] or "",
            "transcription": content,
        })
    return items


# ── Common words that happen to be location names ─────────────────────────
# These are never extracted as locations unless part of a multi-word trigger
# or handled by disambiguation rules.

COMMON_WORDS = frozenset({
    # Words too common in English
    "THE", "AND", "FOR", "NOT", "YOU", "ALL", "CAN", "HER", "WAS", "ONE",
    "OUR", "OUT", "HIS", "HAS", "BUT", "ARE", "WHO", "HOW", "MAN", "ITS",
    "LET", "OLD", "NEW", "NOW", "WAY", "MAY", "DAY", "TOO", "ANY",
    "SAY", "SHE", "TWO", "HIM", "GET", "BIG", "GOT", "DID", "RUN", "USE",
    "HAD", "PUT", "TOP", "SET", "SEE", "END", "WHY", "OWN", "TRY",
    # JMS signatures and common artwork text
    "JMS", "JON", "SARKIN", "JONATHAN", "MARK",
    # Common words that are also place names — skip as single words
    "MOBILE", "NORMAL", "HAVEN", "EDEN", "UNION", "LIBERTY",
    "READING", "BATH", "STERLING", "TROY", "SUMMIT", "AUBURN",
    "FLORENCE", "AURORA", "CANTON", "CLINTON", "BRANDON", "MARION",
    "OXFORD", "SALEM", "TEMPLE", "LINCOLN", "JACKSON", "MADISON",
    "GRANT", "MARSHALL", "WELLINGTON", "VICTORIA", "INDEPENDENCE",
    "PROVIDENCE", "FAITH", "HOPE", "GRACE", "HARMONY", "CONCORD",
    "TURKEY",  # the bird, not the country
})


# ── Ambiguous location words (need multi-word or disambiguation) ──────────

AMBIGUOUS_LOCATION_WORDS = frozenset({
    # US state names that are also common words or cultural references
    "INDIANA",   # also Robert Indiana (artist) — already extracted as cultural ref
    "GEORGIA",   # also Georgia O'Keeffe
    "VIRGINIA",  # also a common first name
    "CAROLINA",  # only as "North Carolina" / "South Carolina"
    "JERSEY",    # only as "New Jersey"
    "MONTANA",   # unambiguous enough — keep in dictionary, not here
    "COLORADO",  # could be a name but uncommon
    "DAKOTA",    # only as "North/South Dakota"
    # City/region names that are also common words
    "BATH",      # too common
    "MOBILE",    # too common
    "NORMAL",    # too common
    "BURY",      # too common
    "NICE",      # too common
    "READING",   # too common
    "LIMA",      # too common (Peruvian city, but also lima bean context)
})

# ── Location disambiguation rules ─────────────────────────────────────────
# word → (canonical_name, location_type, context_words_that_confirm)

LOCATION_DISAMBIGUATION = {
    "INDIANA": ("Indiana", "state",
                {"OHIO", "ILLINOIS", "KENTUCKY", "MIDWEST", "STATE",
                 "TERRE", "SOUTH BEND", "INDIANAPOLIS"}),
    "GEORGIA": ("Georgia", "state",
                {"ATLANTA", "SOUTH", "CAROLINA", "FLORIDA", "ALABAMA",
                 "SAVANNAH", "STATE"}),
    "VIRGINIA": ("Virginia", "state",
                 {"RICHMOND", "CAROLINA", "WASHINGTON", "MARYLAND",
                  "STATE", "NORFOLK", "WEST"}),
}


# ── Supplementary locations ───────────────────────────────────────────────
# Format: (canonical_name, location_type, [single_word_triggers], [multi_word_triggers])
# Only locations NOT already in custom vocab 10 (161 terms from JIM Stories).

SUPPLEMENTARY_LOCATIONS = [
    # ── Countries ──
    ("Australia", "country", ["AUSTRALIA"], []),
    ("Austria", "country", ["AUSTRIA"], []),
    ("Belgium", "country", ["BELGIUM"], []),
    ("Brazil", "country", ["BRAZIL"], []),
    ("Canada", "country", ["CANADA"], []),
    ("Colombia", "country", ["COLOMBIA"], []),
    ("Cuba", "country", ["CUBA"], []),
    ("Denmark", "country", ["DENMARK"], []),
    ("Egypt", "country", ["EGYPT"], []),
    ("Ethiopia", "country", ["ETHIOPIA"], []),
    ("Finland", "country", ["FINLAND"], []),
    ("Germany", "country", ["GERMANY"], []),
    ("Ghana", "country", ["GHANA"], []),
    ("Hungary", "country", ["HUNGARY"], []),
    ("Iceland", "country", ["ICELAND"], []),
    ("India", "country", ["INDIA"], []),
    ("Indonesia", "country", ["INDONESIA"], []),
    ("Iran", "country", ["IRAN"], []),
    ("Iraq", "country", ["IRAQ"], []),
    ("Ireland", "country", ["IRELAND"], []),
    ("Jamaica", "country", ["JAMAICA"], []),
    ("Japan", "country", ["JAPAN"], []),
    ("Kenya", "country", ["KENYA"], []),
    ("Korea", "country", ["KOREA"], []),
    ("Lebanon", "country", ["LEBANON"], []),
    ("Libya", "country", ["LIBYA"], []),
    ("Mali", "country", ["MALI"], []),
    ("Morocco", "country", ["MOROCCO"], []),
    ("Nepal", "country", ["NEPAL"], []),
    ("Netherlands", "country", ["NETHERLANDS"], []),
    ("Norway", "country", ["NORWAY"], []),
    ("Pakistan", "country", ["PAKISTAN"], []),
    ("Peru", "country", ["PERU"], []),
    ("Philippines", "country", ["PHILIPPINES"], []),
    ("Poland", "country", ["POLAND"], []),
    ("Portugal", "country", ["PORTUGAL"], []),
    ("Romania", "country", ["ROMANIA"], []),
    ("Scotland", "country", ["SCOTLAND"], []),
    ("Senegal", "country", ["SENEGAL"], []),
    ("Somalia", "country", ["SOMALIA"], []),
    ("Spain", "country", ["SPAIN"], []),
    ("Sweden", "country", ["SWEDEN"], []),
    ("Switzerland", "country", ["SWITZERLAND"], []),
    ("Syria", "country", ["SYRIA"], []),
    ("Thailand", "country", ["THAILAND"], []),
    ("Tunisia", "country", ["TUNISIA"], []),
    # Turkey omitted — "turkey" (the bird) is too common in transcriptions
    ("Uganda", "country", ["UGANDA"], []),
    ("Ukraine", "country", ["UKRAINE"], []),
    ("Uruguay", "country", ["URUGUAY"], []),
    ("Venezuela", "country", ["VENEZUELA"], []),
    ("Wales", "country", ["WALES"], []),
    ("Zimbabwe", "country", ["ZIMBABWE"], []),

    # ── US States (filling gaps in existing 31) ──
    ("Georgia", "state", [], []),       # handled by disambiguation
    ("Indiana", "state", [], []),       # handled by disambiguation
    ("Maryland", "state", ["MARYLAND"], []),
    ("Michigan", "state", ["MICHIGAN"], []),
    ("Minnesota", "state", ["MINNESOTA"], []),
    ("Mississippi", "state", [], []),   # already in vocab as body_of_water
    ("Nevada", "state", ["NEVADA"], []),
    ("New Hampshire", "state", [], ["NEW HAMPSHIRE"]),
    ("New York", "state", [], []),      # already in vocab
    ("North Dakota", "state", [], ["NORTH DAKOTA"]),
    ("Pennsylvania", "state", ["PENNSYLVANIA"], []),
    ("Rhode Island", "state", [], ["RHODE ISLAND"]),
    ("South Carolina", "state", [], ["SOUTH CAROLINA"]),
    ("South Dakota", "state", [], ["SOUTH DAKOTA"]),
    ("Texas", "state", ["TEXAS"], []),
    ("Virginia", "state", [], []),      # handled by disambiguation
    ("Washington", "state", ["WASHINGTON"], []),
    ("West Virginia", "state", [], ["WEST VIRGINIA"]),
    ("Wisconsin", "state", ["WISCONSIN"], []),
    ("Wyoming", "state", ["WYOMING"], []),

    # ── Cities ──
    ("Athens", "city", ["ATHENS"], []),
    ("Atlanta", "city", ["ATLANTA"], []),
    ("Baghdad", "city", ["BAGHDAD"], []),
    ("Beijing", "city", ["BEIJING"], []),
    ("Berlin", "city", ["BERLIN"], []),
    ("Bethlehem", "city", ["BETHLEHEM"], []),
    ("Bombay", "city", ["BOMBAY"], []),
    ("Brussels", "city", ["BRUSSELS"], []),
    ("Buenos Aires", "city", [], ["BUENOS AIRES"]),
    ("Cairo", "city", ["CAIRO"], []),
    ("Calcutta", "city", ["CALCUTTA"], []),
    ("Cape Town", "city", [], ["CAPE TOWN"]),
    ("Chicago", "city", ["CHICAGO"], []),
    ("Copenhagen", "city", ["COPENHAGEN"], []),
    ("Denver", "city", ["DENVER"], []),
    ("Dublin", "city", ["DUBLIN"], []),
    ("Edinburgh", "city", ["EDINBURGH"], []),
    ("Elko", "city", ["ELKO"], []),
    ("Geneva", "city", ["GENEVA"], []),
    ("Harlem", "city", ["HARLEM"], []),
    ("Helsinki", "city", ["HELSINKI"], []),
    ("Hiroshima", "city", ["HIROSHIMA"], []),
    ("Hong Kong", "city", [], ["HONG KONG"]),
    ("Houston", "city", ["HOUSTON"], []),
    ("Istanbul", "city", ["ISTANBUL"], []),
    ("Jerusalem", "city", ["JERUSALEM"], []),
    ("Johannesburg", "city", ["JOHANNESBURG"], []),
    ("Kyoto", "city", ["KYOTO"], []),
    ("Las Vegas", "city", [], ["LAS VEGAS"]),
    ("Lisbon", "city", ["LISBON"], []),
    ("London", "city", ["LONDON"], []),
    ("Los Angeles", "city", [], ["LOS ANGELES"]),
    ("Madrid", "city", ["MADRID"], []),
    ("Manchester", "city", ["MANCHESTER"], []),
    ("Manila", "city", ["MANILA"], []),
    ("Marrakech", "city", ["MARRAKECH"], []),
    ("Marseille", "city", ["MARSEILLE"], []),
    ("Mecca", "city", ["MECCA"], []),
    ("Milan", "city", ["MILAN"], []),
    ("Montreal", "city", ["MONTREAL"], []),
    ("Moscow", "city", ["MOSCOW"], []),
    ("Munich", "city", ["MUNICH"], []),
    ("Nagasaki", "city", ["NAGASAKI"], []),
    ("Nairobi", "city", ["NAIROBI"], []),
    ("Nashville", "city", ["NASHVILLE"], []),
    ("Philadelphia", "city", ["PHILADELPHIA"], []),
    ("Pittsburgh", "city", ["PITTSBURGH"], []),
    ("Prague", "city", ["PRAGUE"], []),
    ("Reno", "city", ["RENO"], []),
    ("Rio de Janeiro", "city", [], ["RIO DE JANEIRO"]),
    ("Rockport", "city", ["ROCKPORT"], []),
    ("Saigon", "city", ["SAIGON"], []),
    ("Salt Lake City", "city", [], ["SALT LAKE CITY", "SALT LAKE"]),
    ("Sarajevo", "city", ["SARAJEVO"], []),
    ("Shanghai", "city", ["SHANGHAI"], []),
    ("Singapore", "city", ["SINGAPORE"], []),
    ("Stockholm", "city", ["STOCKHOLM"], []),
    ("St. Petersburg", "city", [], ["ST PETERSBURG", "SAINT PETERSBURG"]),
    ("Sydney", "city", ["SYDNEY"], []),
    ("Tehran", "city", ["TEHRAN"], []),
    ("Tel Aviv", "city", [], ["TEL AVIV"]),
    ("Timbuktu", "city", ["TIMBUKTU"], []),
    ("Tokyo", "city", ["TOKYO"], []),
    ("Toronto", "city", ["TORONTO"], []),
    ("Venice", "city", ["VENICE"], []),
    ("Vienna", "city", ["VIENNA"], []),
    ("Warsaw", "city", ["WARSAW"], []),
    ("Zurich", "city", ["ZURICH"], []),

    # ── Regions ──
    ("Antarctica", "region", ["ANTARCTICA"], []),
    ("Arctic", "region", ["ARCTIC"], []),
    ("Asia", "region", ["ASIA"], []),
    ("Balkans", "region", ["BALKANS"], []),
    ("Catalonia", "region", ["CATALONIA"], []),
    ("Central America", "region", [], ["CENTRAL AMERICA"]),
    ("Europe", "region", ["EUROPE"], []),
    ("Harlem", "region", ["HARLEM"], []),
    ("Latin America", "region", [], ["LATIN AMERICA"]),
    ("Middle East", "region", [], ["MIDDLE EAST"]),
    ("New England", "region", [], ["NEW ENGLAND"]),
    ("Sahara", "region", ["SAHARA"], []),
    ("Siberia", "region", ["SIBERIA"], []),
    ("South America", "region", [], ["SOUTH AMERICA"]),
    ("Tibet", "region", ["TIBET"], []),
    ("Tuscany", "region", ["TUSCANY"], []),

    # ── Landmarks ──
    ("Alcatraz", "landmark", ["ALCATRAZ"], []),
    ("Central Park", "landmark", [], ["CENTRAL PARK"]),
    ("Colosseum", "landmark", ["COLOSSEUM"], []),
    ("Eiffel Tower", "landmark", [], ["EIFFEL TOWER"]),
    ("Ellis Island", "landmark", [], ["ELLIS ISLAND"]),
    ("Empire State Building", "landmark", [], ["EMPIRE STATE"]),
    ("Golden Gate", "landmark", [], ["GOLDEN GATE"]),
    ("Great Wall", "landmark", [], ["GREAT WALL"]),
    ("Kremlin", "landmark", ["KREMLIN"], []),
    ("Louvre", "landmark", ["LOUVRE"], []),
    ("Mount Everest", "landmark", [], ["MOUNT EVEREST"]),
    ("Pompeii", "landmark", ["POMPEII"], []),
    ("Statue of Liberty", "landmark", [], ["STATUE OF LIBERTY"]),
    ("Stonehenge", "landmark", ["STONEHENGE"], []),
    ("Taj Mahal", "landmark", [], ["TAJ MAHAL"]),
    ("Times Square", "landmark", [], ["TIMES SQUARE"]),
    ("Tower of Babel", "landmark", [], ["TOWER OF BABEL"]),
    ("Vatican", "landmark", ["VATICAN"], []),
    ("Versailles", "landmark", ["VERSAILLES"], []),
    ("Wall Street", "landmark", [], ["WALL STREET"]),
    ("Westminster", "landmark", ["WESTMINSTER"], []),
    ("White House", "landmark", [], ["WHITE HOUSE"]),

    # ── Bodies of water ──
    ("Atlantic", "body_of_water", ["ATLANTIC"], []),
    ("Mediterranean", "body_of_water", ["MEDITERRANEAN"], []),
    ("Nile", "body_of_water", ["NILE"], []),
    ("Pacific", "body_of_water", ["PACIFIC"], []),
    ("Red Sea", "body_of_water", [], ["RED SEA"]),
    ("Tigris", "body_of_water", ["TIGRIS"], []),
]


# ── LocationDictionary ────────────────────────────────────────────────────

class LocationDictionary:
    """Builds a lookup dictionary from JIM locations + supplementary locations."""

    def __init__(self):
        # canonical_name → location_type
        self.types: dict[str, str] = {}
        # uppercased multi-word pattern → canonical_name
        self.multi_word: dict[str, str] = {}
        # uppercased single word → canonical_name (only non-ambiguous)
        self.single_word: dict[str, str] = {}
        # word → (canonical, type, context_words)
        self.disambiguation: dict[str, tuple] = dict(LOCATION_DISAMBIGUATION)

    def build(self, jim_locations_cache_path: str) -> None:
        """Build dictionary from JIM locations cache + supplementary."""

        # ── Source A: JIM Stories locations (from cache) ──
        jim_locs: dict[str, str] = {}  # canonical → type
        if os.path.exists(jim_locations_cache_path):
            with open(jim_locations_cache_path) as f:
                jim_cache = json.load(f)
            for item in jim_cache:
                for loc in item.get("locations", []):
                    name = loc["location_name"]
                    ltype = loc["location_type"]
                    if name not in jim_locs:
                        jim_locs[name] = ltype

            for canonical, ltype in jim_locs.items():
                self.types[canonical] = ltype
                upper = canonical.upper()
                words = canonical.split()
                if len(words) >= 2:
                    self.multi_word[upper] = canonical
                    # Also register last word if not ambiguous
                    # (e.g., "New York" → don't register YORK alone)
                elif (upper not in AMBIGUOUS_LOCATION_WORDS
                      and upper not in COMMON_WORDS
                      and len(upper) >= 3):
                    self.single_word[upper] = canonical

            print(f"  Source A: {len(jim_locs)} JIM Stories locations loaded.")
        else:
            print(f"  Source A: jim_locations_cache.json not found — skipping.")

        # Also load from custom vocab 10 (may have terms not in cache)
        try:
            sql = f"SELECT terms FROM custom_vocab WHERE id = {CUSTOM_VOCAB_ID};"
            out = db_query(sql).strip().split("\n")
            if len(out) >= 2:
                vocab_terms = json.loads(out[1].strip())
                for term in vocab_terms:
                    if term not in self.types:
                        self.types[term] = "region"  # default type for vocab-only terms
                        upper = term.upper()
                        words = term.split()
                        if len(words) >= 2:
                            self.multi_word[upper] = term
                        elif (upper not in AMBIGUOUS_LOCATION_WORDS
                              and upper not in COMMON_WORDS
                              and len(upper) >= 3):
                            self.single_word[upper] = term
                print(f"  Vocab 10: {len(vocab_terms)} terms (merged with cache).")
        except Exception as e:
            print(f"  WARNING: Could not read custom vocab 10: {e}")

        # ── Source B: Supplementary locations ──
        added = 0
        for canonical, ltype, singles, multis in SUPPLEMENTARY_LOCATIONS:
            if canonical not in self.types:
                self.types[canonical] = ltype
            for trigger in singles:
                trigger_upper = trigger.upper()
                if (trigger_upper not in AMBIGUOUS_LOCATION_WORDS
                        and trigger_upper not in COMMON_WORDS):
                    if trigger_upper not in self.single_word:
                        self.single_word[trigger_upper] = canonical
                        added += 1
            for trigger in multis:
                trigger_upper = trigger.upper()
                if trigger_upper not in self.multi_word:
                    self.multi_word[trigger_upper] = canonical
                    added += 1
            # Register full canonical as multi-word if 2+ words
            upper_full = canonical.upper()
            if len(canonical.split()) >= 2 and upper_full not in self.multi_word:
                self.multi_word[upper_full] = canonical
        print(f"  Source B: {len(SUPPLEMENTARY_LOCATIONS)} supplementary locations ({added} new triggers).")

        print(f"  Dictionary: {len(self.multi_word)} multi-word, "
              f"{len(self.single_word)} single-word, "
              f"{len(self.disambiguation)} disambiguation rules.")

    def scan(self, text: str) -> list[dict]:
        """Scan text for location references. Returns list of matches."""
        text_upper = text.upper()
        text_words = set(re.findall(r"[A-Z]{2,}(?:'[A-Z]+)?", text_upper))
        matches: list[dict] = []
        matched_spans: set[str] = set()

        # ── Tier 1: Multi-word exact (case-insensitive) ──
        for pattern, canonical in self.multi_word.items():
            if pattern in text_upper:
                count = len(re.findall(re.escape(pattern), text_upper))
                if count > 0 and canonical not in matched_spans:
                    m = re.search(re.escape(pattern), text_upper)
                    actual = text[m.start():m.end()] if m else pattern
                    matches.append({
                        "location_name": canonical,
                        "location_as_mentioned": actual,
                        "location_type": self.types.get(canonical, "region"),
                        "repetition_count": count,
                        "match_tier": "multi_word",
                    })
                    matched_spans.add(canonical)

        # ── Tier 2: Single-word exact ──
        for word in text_words:
            if word in COMMON_WORDS:
                continue
            if word in AMBIGUOUS_LOCATION_WORDS:
                continue  # handled by disambiguation
            canonical = self.single_word.get(word)
            if canonical and canonical not in matched_spans:
                count = len(re.findall(r"\b" + re.escape(word) + r"\b", text_upper))
                matches.append({
                    "location_name": canonical,
                    "location_as_mentioned": word,
                    "location_type": self.types.get(canonical, "region"),
                    "repetition_count": count,
                    "match_tier": "exact",
                })
                matched_spans.add(canonical)

        # ── Tier 3: Disambiguation (context-dependent) ──
        for word, (canonical, ltype, context_words) in self.disambiguation.items():
            if canonical in matched_spans:
                continue
            if word not in text_words:
                continue
            context_hits = sum(1 for cw in context_words if cw in text_words
                               or any(cw in w for w in text_words))
            if context_hits >= 2:
                count = len(re.findall(r"\b" + re.escape(word) + r"\b", text_upper))
                matches.append({
                    "location_name": canonical,
                    "location_as_mentioned": word,
                    "location_type": ltype,
                    "repetition_count": count,
                    "match_tier": "disambiguated",
                    "notes": f"disambiguated with {context_hits} context words",
                })
                matched_spans.add(canonical)

        # No fuzzy matching for locations — too many false positives

        return matches


# ── Output helpers ─────────────────────────────────────────────────────────

def format_item_summary(item: dict, locations: list[dict]) -> str:
    header = f"{item['identifier']} | {item['title']}"
    if not locations:
        return f"{header}\n  (no locations)\n"
    lines = [header]
    for loc in locations:
        notes = f" [{loc['notes']}]" if loc.get("notes") else ""
        rep = f" x{loc['repetition_count']}" if loc.get("repetition_count", 1) > 1 else ""
        lines.append(
            f"  - {loc['location_name']} ({loc['location_type']}, {loc['match_tier']})"
            f"{rep}{notes} — as \"{loc['location_as_mentioned']}\""
        )
    return "\n".join(lines) + "\n"


def build_aggregate_table(all_results: list[dict]) -> list[dict]:
    agg = defaultdict(lambda: {
        "location_name": "",
        "location_type": "",
        "works_count": 0,
        "total_repetitions": 0,
        "works": set(),
        "match_tiers": Counter(),
    })
    for result in all_results:
        ident = result["identifier"]
        for loc in result.get("locations", []):
            key = loc["location_name"].lower()
            entry = agg[key]
            entry["location_name"] = loc["location_name"]
            entry["location_type"] = loc["location_type"]
            entry["total_repetitions"] += loc.get("repetition_count", 1)
            entry["match_tiers"][loc["match_tier"]] += 1
            if ident not in entry["works"]:
                entry["works"].add(ident)
                entry["works_count"] += 1

    rows = sorted(agg.values(), key=lambda x: (-x["works_count"], -x["total_repetitions"]))
    for row in rows:
        row["work_identifiers"] = ", ".join(sorted(row["works"]))
        avg = row["total_repetitions"] / row["works_count"] if row["works_count"] else 0
        row["avg_reps_per_work"] = round(avg, 1)
        row["primary_tier"] = row["match_tiers"].most_common(1)[0][0] if row["match_tiers"] else ""
        del row["works"]
        del row["match_tiers"]
    return rows


def build_type_breakdown(all_results: list[dict]) -> list[dict]:
    type_locs = defaultdict(set)
    type_reps = Counter()
    for result in all_results:
        for loc in result.get("locations", []):
            ltype = loc["location_type"]
            type_locs[ltype].add(loc["location_name"])
            type_reps[ltype] += loc.get("repetition_count", 1)
    rows = []
    for ltype in sorted(type_reps, key=lambda t: -type_reps[t]):
        rows.append({
            "location_type": ltype,
            "unique_locations": len(type_locs[ltype]),
            "total_repetitions": type_reps[ltype],
        })
    return rows


def write_csv(rows: list[dict], path: str, fieldnames: list[str]) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_aggregate(rows: list[dict], limit: int = 30) -> None:
    print(f"\n{'Location':<40} {'Type':<15} {'Works':>5} {'Total Reps':>10} {'Avg':>5}  Tier")
    print("-" * 95)
    for row in rows[:limit]:
        print(f"{row['location_name']:<40} {row['location_type']:<15} "
              f"{row['works_count']:>5} {row['total_repetitions']:>10} "
              f"{row['avg_reps_per_work']:>5}  {row['primary_tier']}")
    if len(rows) > limit:
        print(f"  ... and {len(rows) - limit} more (see CSV)")


def print_type_breakdown(rows: list[dict]) -> None:
    print(f"\n{'Type':<25} {'Unique':>6} {'Total Reps':>10}")
    print("-" * 45)
    for row in rows:
        print(f"{row['location_type']:<25} {row['unique_locations']:>6} "
              f"{row['total_repetitions']:>10}")


# ── Cross-media reports ───────────────────────────────────────────────────

def generate_cross_media_reports(visual_cache_path: str, jim_cache_path: str,
                                 scripts_dir: str) -> None:
    """Generate cross-media location match reports."""
    if not os.path.exists(visual_cache_path):
        print("ERROR: visual_locations_cache.json not found. Run extraction first.")
        return
    if not os.path.exists(jim_cache_path):
        print("ERROR: jim_locations_cache.json not found.")
        return

    with open(visual_cache_path) as f:
        visual_cache = json.load(f)
    with open(jim_cache_path) as f:
        jim_cache = json.load(f)

    # Collect locations from each corpus
    visual_locs: dict[str, dict] = {}
    for item in visual_cache:
        for loc in item.get("locations", []):
            name = loc["location_name"]
            if name not in visual_locs:
                visual_locs[name] = {"type": loc["location_type"], "works": set(), "total_reps": 0}
            visual_locs[name]["works"].add(item["identifier"])
            visual_locs[name]["total_reps"] += loc.get("repetition_count", 1)

    jim_locs: dict[str, dict] = {}
    for item in jim_cache:
        for loc in item.get("locations", []):
            name = loc["location_name"]
            if name not in jim_locs:
                jim_locs[name] = {"type": loc["location_type"], "stories": set(), "mentions": 0}
            jim_locs[name]["stories"].add(item["identifier"])
            jim_locs[name]["mentions"] += 1

    visual_names = set(visual_locs.keys())
    jim_names = set(jim_locs.keys())
    both = visual_names & jim_names
    visual_only = visual_names - jim_names
    jim_only = jim_names - visual_names

    # Cross-media locations (in both)
    cross_rows = []
    for name in sorted(both, key=str.casefold):
        v = visual_locs[name]
        j = jim_locs[name]
        cross_rows.append({
            "location_name": name,
            "location_type": v["type"],
            "visual_works_count": len(v["works"]),
            "visual_total_reps": v["total_reps"],
            "jim_stories_count": len(j["stories"]),
            "jim_mentions": j["mentions"],
        })
    cross_path = os.path.join(scripts_dir, "cross_media_locations.csv")
    write_csv(cross_rows, cross_path, [
        "location_name", "location_type",
        "visual_works_count", "visual_total_reps",
        "jim_stories_count", "jim_mentions",
    ])

    # Visual-only
    vo_rows = []
    for name in sorted(visual_only, key=str.casefold):
        v = visual_locs[name]
        vo_rows.append({
            "location_name": name,
            "location_type": v["type"],
            "visual_works_count": len(v["works"]),
            "visual_total_reps": v["total_reps"],
        })
    vo_path = os.path.join(scripts_dir, "locations_visual_only.csv")
    write_csv(vo_rows, vo_path, [
        "location_name", "location_type", "visual_works_count", "visual_total_reps",
    ])

    # JIM-only
    jo_rows = []
    for name in sorted(jim_only, key=str.casefold):
        j = jim_locs[name]
        jo_rows.append({
            "location_name": name,
            "location_type": j["type"],
            "jim_stories_count": len(j["stories"]),
            "jim_mentions": j["mentions"],
        })
    jo_path = os.path.join(scripts_dir, "locations_jim_only.csv")
    write_csv(jo_rows, jo_path, [
        "location_name", "location_type", "jim_stories_count", "jim_mentions",
    ])

    print(f"\n{'=' * 70}")
    print("CROSS-MEDIA LOCATION REPORT")
    print(f"{'=' * 70}")
    print(f"Locations in BOTH visual works + JIM Stories:  {len(both)}")
    print(f"Locations in visual works ONLY:                {len(visual_only)}")
    print(f"Locations in JIM Stories ONLY:                 {len(jim_only)}")
    print(f"\nFiles:")
    print(f"  {cross_path}")
    print(f"  {vo_path}")
    print(f"  {jo_path}")

    if cross_rows:
        cross_sorted = sorted(cross_rows,
                              key=lambda r: -(r["visual_works_count"] + r["jim_stories_count"]))
        print(f"\nTop cross-media locations:")
        print(f"{'Location':<40} {'Type':<15} {'Visual':>6} {'JIM':>4}")
        print("-" * 70)
        for row in cross_sorted[:25]:
            print(f"{row['location_name']:<40} {row['location_type']:<15} "
                  f"{row['visual_works_count']:>6} {row['jim_stories_count']:>4}")


# ── Vocab & facet extension ───────────────────────────────────────────────

def extend_custom_vocab(new_names: list[str]) -> int:
    """Extend custom vocab 10 with new terms (merge, don't replace)."""
    sql = f"SELECT terms FROM custom_vocab WHERE id = {CUSTOM_VOCAB_ID};"
    out = db_query(sql).strip().split("\n")
    if len(out) < 2:
        print(f"  WARNING: Custom vocab {CUSTOM_VOCAB_ID} not found.")
        return 0
    existing_json = out[1].strip()
    try:
        existing_terms = json.loads(existing_json)
    except json.JSONDecodeError:
        existing_terms = []

    existing_set = {t.lower() for t in existing_terms}
    added = []
    for name in new_names:
        if name.lower() not in existing_set:
            added.append(name)
            existing_set.add(name.lower())

    if not added:
        return 0

    merged = sorted(existing_terms + added, key=str.casefold)
    terms_json = json.dumps(merged, ensure_ascii=False)
    escaped = terms_json.replace("\\", "\\\\").replace("'", "\\'")
    sql = f"UPDATE custom_vocab SET terms = '{escaped}' WHERE id = {CUSTOM_VOCAB_ID};"
    db_execute(sql)
    return len(added)


def update_facet_values(facet_id: int, all_names: list[str]) -> None:
    """Update an existing facet's value list."""
    sql = f"SELECT data FROM faceted_browse_facet WHERE id = {facet_id};"
    out = db_query(sql).strip().split("\n")
    if len(out) < 2:
        print(f"  WARNING: Facet {facet_id} not found.")
        return
    try:
        data = json.loads(out[1].strip())
    except json.JSONDecodeError:
        print(f"  WARNING: Could not parse facet {facet_id} data.")
        return

    data["values"] = "\n".join(sorted(all_names, key=str.casefold))
    data_json = json.dumps(data, separators=(",", ":"))
    escaped = data_json.replace("\\", "\\\\").replace("'", "\\'")
    sql = f"UPDATE faceted_browse_facet SET data = '{escaped}' WHERE id = {facet_id};"
    db_execute(sql)


def check_existing_locations() -> set[int]:
    """Check which visual work items already have customvocab:10 spatial values."""
    sql = f"""
        SELECT DISTINCT v.resource_id
        FROM value v
        WHERE v.property_id = {PROPERTY_ID_SPATIAL}
          AND v.type = '{CUSTOM_VOCAB_TYPE}'
          AND v.resource_id NOT IN (
              SELECT item_id FROM item_item_set
              WHERE item_set_id IN ({','.join(str(s) for s in EXCLUDED_ITEM_SETS)})
          );
    """
    out = db_query(sql).strip().split("\n")
    if len(out) < 2:
        return set()
    return {int(line.strip()) for line in out[1:] if line.strip().isdigit()}


def insert_spatial_values(all_results: list[dict], existing: set[int]) -> int:
    """Insert dcterms:spatial values for each unique location per item."""
    total = 0
    for result in all_results:
        item_id = result["item_id"]
        if item_id in existing:
            continue
        locs = result.get("locations", [])
        if not locs:
            continue
        seen = set()
        for loc in locs:
            name = loc["location_name"]
            if name in seen:
                continue
            seen.add(name)
            escaped = name.replace("\\", "\\\\").replace("'", "\\'")
            sql = (
                f"INSERT INTO `value` (resource_id, property_id, type, `value`, is_public) "
                f"VALUES ({item_id}, {PROPERTY_ID_SPATIAL}, '{CUSTOM_VOCAB_TYPE}', "
                f"'{escaped}', 1);"
            )
            db_execute(sql)
            total += 1
    return total


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract geographic locations from visual work transcriptions"
    )
    parser.add_argument("--apply", action="store_true",
                        help="Write results to DB (default: dry-run)")
    parser.add_argument("--item-id", type=int,
                        help="Process a single item by DB ID")
    parser.add_argument("--cross-media", action="store_true",
                        help="Generate cross-media location reports")
    args = parser.parse_args()

    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    jim_locations_cache_path = os.path.join(scripts_dir, "jim_locations_cache.json")
    visual_cache_path = os.path.join(scripts_dir, "visual_locations_cache.json")

    # ── Cross-media report mode ──
    if args.cross_media:
        generate_cross_media_reports(visual_cache_path, jim_locations_cache_path, scripts_dir)
        return

    # ── Build dictionary ──
    print("Building location dictionary...")
    dictionary = LocationDictionary()
    dictionary.build(jim_locations_cache_path)

    # ── Fetch visual works ──
    print("\nFetching visual works from DB...")
    items = fetch_visual_works(args.item_id)
    if not items:
        print("No visual works found.")
        sys.exit(1)
    print(f"Fetched {len(items)} visual works.")

    # ── Scan ──
    print("\nScanning transcriptions for locations...")
    all_results: list[dict] = []
    items_with_locs = 0
    total_instances = 0
    errors = 0

    for i, item in enumerate(items, 1):
        text = item["transcription"]
        if len(text.strip()) < MIN_TRANSCRIPTION_LENGTH:
            all_results.append({
                "item_id": item["item_id"],
                "identifier": item["identifier"],
                "title": item["title"],
                "date": item["date"],
                "locations": [],
            })
            continue

        try:
            matches = dictionary.scan(text)
            if matches:
                items_with_locs += 1
                total_instances += len(matches)
        except Exception as e:
            print(f"  ERROR on {item['identifier']}: {e}")
            matches = []
            errors += 1

        all_results.append({
            "item_id": item["item_id"],
            "identifier": item["identifier"],
            "title": item["title"],
            "date": item["date"],
            "locations": matches,
        })

        if i % 500 == 0:
            print(f"  ... {i}/{len(items)} processed")

    # ── Collect unique location names ──
    all_location_names = set()
    for result in all_results:
        for loc in result.get("locations", []):
            all_location_names.add(loc["location_name"])

    print(f"\nDone. {len(all_location_names)} unique locations, "
          f"{items_with_locs} items with locations, "
          f"{total_instances} instances, {errors} errors.")

    # ── Write cache ──
    with open(visual_cache_path, "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f"Cache: {visual_cache_path}")

    # ── Write review file ──
    review_path = os.path.join(scripts_dir, "visual_locations_review.txt")
    with open(review_path, "w") as f:
        for result in all_results:
            if result.get("locations"):
                f.write(format_item_summary(result, result["locations"]) + "\n")
    print(f"Review: {review_path}")

    # ── Aggregate ──
    agg_rows = build_aggregate_table(all_results)
    agg_path = os.path.join(scripts_dir, "visual_locations_aggregate.csv")
    write_csv(agg_rows, agg_path, [
        "location_name", "location_type", "works_count", "total_repetitions",
        "avg_reps_per_work", "primary_tier", "work_identifiers",
    ])
    print(f"Aggregate: {agg_path}")
    print_aggregate(agg_rows)

    type_rows = build_type_breakdown(all_results)
    print_type_breakdown(type_rows)

    # ── Cross-media reports ──
    generate_cross_media_reports(visual_cache_path, jim_locations_cache_path, scripts_dir)

    # ── Apply to DB ──
    if args.apply:
        print(f"\n{'=' * 70}")
        print("APPLYING TO DATABASE")
        print(f"{'=' * 70}")

        # 1. Extend custom vocab 10
        new_names = sorted(all_location_names, key=str.casefold)
        added = extend_custom_vocab(new_names)
        print(f"  Custom vocab {CUSTOM_VOCAB_ID}: {added} new terms added.")

        # 2. Read updated vocab for facet
        sql = f"SELECT terms FROM custom_vocab WHERE id = {CUSTOM_VOCAB_ID};"
        out = db_query(sql).strip().split("\n")
        all_vocab_terms = json.loads(out[1].strip()) if len(out) >= 2 else []
        print(f"  Vocab now has {len(all_vocab_terms)} total terms.")

        # 3. Update facet value list
        update_facet_values(FACET_ID_LOCATION, all_vocab_terms)
        print(f"  Location facet (ID {FACET_ID_LOCATION}) updated with {len(all_vocab_terms)} values.")

        # 4. Insert spatial values
        existing = check_existing_locations()
        print(f"  Existing visual works with locations: {len(existing)}")
        inserted = insert_spatial_values(all_results, existing)
        print(f"  Inserted {inserted} new dcterms:spatial values.")

        print("\nDone. Restart Omeka to clear Doctrine cache:")
        print("  docker compose restart omeka")
    else:
        print(f"\nDry run complete. Use --apply to write to DB.")


if __name__ == "__main__":
    main()
