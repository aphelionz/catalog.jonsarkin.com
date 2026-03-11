#!/usr/bin/env python3
"""
extract_visual_references.py — Extract cultural references from visual work transcriptions.

Two-pass architecture:
  Pass 1: Dictionary + regex scan ($0, handles all ~3,675 items)
  Pass 2: Claude Haiku for selective deeper analysis (~$1-3)

Uses the SAME canonical name normalization and custom vocab as JIM Stories
for cross-media matching.

Usage:
  python scripts/extract_visual_references.py                       # Pass 1 only
  python scripts/extract_visual_references.py --pass2               # Pass 1 + 2
  python scripts/extract_visual_references.py --pass2 --apply       # + write to DB
  python scripts/extract_visual_references.py --item-id 7154        # single item
  python scripts/extract_visual_references.py --cross-media          # cross-media reports

Environment:
  ANTHROPIC_API_KEY — Required for --pass2 only.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────────────

PROPERTY_ID_CONTENT = 91    # bibo:content (transcription)
PROPERTY_ID_IDENT = 10      # dcterms:identifier
PROPERTY_ID_TITLE = 1       # dcterms:title
PROPERTY_ID_DATE = 7        # dcterms:date
PROPERTY_ID_RELATION = 13   # dcterms:relation (cultural references)

EXCLUDED_ITEM_SETS = (8020, 8021, 7502)  # JIM Stories, Press & Docs, jsarkin.com Writings
MIN_TRANSCRIPTION_LENGTH = 10  # Skip trivial transcriptions (JMS signatures, etc.)

CUSTOM_VOCAB_ID = 11
CUSTOM_VOCAB_TYPE = "customvocab:11"
CUSTOM_VOCAB_LABEL = "JIM Story Cultural References"

FACET_ID_CULTURAL = 23      # existing Cultural Reference facet
FACET_ID_LOCATION = 22      # existing Location facet

PASS2_MODEL = "claude-haiku-4-5"

VALID_REFERENCE_TYPES = [
    "author", "work", "fictional_character", "musician", "band",
    "song", "album", "film", "tv", "visual_artist", "art_movement",
    "historical_figure", "historical_event", "philosopher",
    "religious", "sports", "venue", "other",
]


# ── Name normalization (shared with extract_jim_references.py) ────────────

_INITIALS_RE = re.compile(r"([A-Z]\.) +([A-Z]\.)")

_NAME_OVERRIDES = {
    "t.s. eliot": "T.S. Eliot",
    "j.d. salinger": "J.D. Salinger",
    "j. d. salinger": "J.D. Salinger",
    "t. s. eliot": "T.S. Eliot",
}


def normalize_name(name: str) -> str:
    """Standardise canonical reference names so aggregates don't split."""
    name = _INITIALS_RE.sub(r"\1\2", name)
    override = _NAME_OVERRIDES.get(name.lower())
    if override:
        return override
    return name


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


def check_existing_references() -> set[int]:
    """Return item IDs that already have dcterms:relation custom vocab values."""
    sql = (
        f"SELECT DISTINCT resource_id FROM `value` "
        f"WHERE property_id = {PROPERTY_ID_RELATION} AND type = '{CUSTOM_VOCAB_TYPE}';"
    )
    out = db_query(sql).strip().split("\n")
    if len(out) < 2:
        return set()
    return {int(row) for row in out[1:] if row.strip()}


# ── Text preprocessing ────────────────────────────────────────────────────

def preprocess_text(text: str) -> str:
    """Normalize transcription text for matching."""
    # Replace newlines with spaces
    text = text.replace("\n", " ").replace("\r", "")
    # Rejoin hyphenated line breaks: RAUSCH- ENBERG → RAUSCHENBERG
    text = re.sub(r"([A-Za-z]{2,})-\s+([A-Za-z]{2,})", r"\1\2", text)
    # Normalize multiple spaces
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ── Common words (NEVER extract as references) ───────────────────────────

COMMON_WORDS = frozenset("""
    THE AND FOR ARE BUT NOT YOU ALL ANY CAN HAD HER WAS ONE OUR OUT
    DAY HAS HIS HOW ITS MAY NEW NOW OLD SEE WAY WHO BOY DID GET HAS
    LET SAY SHE TOO USE DAD MOM SON MAN MEN RAN SIT SET RUN CUT
    HOME LOVE RAIN MONEY POWER DREAM OCEAN FISH EYES FACE HAND HEAD
    HEART TREE FIRE WATER EARTH WIND TIME LIFE DEAD DEATH NIGHT LIGHT
    DARK WORLD PLACE HOUSE CITY TOWN ROAD DOOR WINDOW WALL FLOOR
    ROOM HALL DOOR STAR MOON ROCK STONE BONE BLOOD SKIN BODY BACK
    MIND SOUL WORD NAME SONG PLAY GAME WORK BOOK READ WRITE PAINT
    DRAW LINE FORM SHAPE COLOR BLUE GREEN YELLOW WHITE BLACK BROWN
    PINK GREY GOLD MAKE TAKE GIVE COME LEFT RIGHT SIDE EDGE MOVE
    TURN WALK RIDE FALL DOWN AWAY FROM INTO OVER UPON WITH JUST LIKE
    GOOD WELL VERY MUCH MORE MOST LESS SOME MANY EACH EVERY ONLY
    WANT NEED KEEP LONG LAST LIVE HERE THEM THEN THAN THAT THIS
    WHAT WHEN WHERE WHICH WHILE WILL WITH YOUR BEEN DOES DONE FELT
    FIND GIVE GOES GONE KNEW KNOW LOOK LOST MADE MEAN MUST NEXT
    ONCE PART REAL REST SAID SAME SEEM SHOW SIDE SUCH SURE TELL
    THINK TOLD TRUE TURN USED WAIT WENT WERE UPON ALSO EVEN EVER
    HIGH DEEP WIDE OPEN FULL HALF FOUR FIVE HARD SOFT WARM COLD
    NICE BEST EVER STILL NEVER ALWAYS OFTEN AGAIN
    ART ARTS ARTIST TOP BOTTOM LEFT RIGHT BORDER SYMBOLS TEXT
    CARD BOARD CARDBOARD PAPER CANVAS
    PLANET SATURN DESERT TRAIN STATION ALIEN CREATURE
    COMIC COMIX PATTERN SPIRAL MOUTH GRID CIRCLE
    BOAT SHIP VEHICLE CAR TRUCK BUS
    PORTRAIT LADY LADIES FIGURE PENCIL SKULL NIPPLE
    SATURDAY SUNDAY MONDAY TUESDAY WEDNESDAY THURSDAY FRIDAY
    JANUARY FEBRUARY MARCH APRIL JUNE JULY AUGUST SEPTEMBER
    OCTOBER NOVEMBER DECEMBER
    SUPER HERO QUEEN PRINCE PRINCESS LORD MASTER
    BIG SMALL LITTLE GREAT PRETTY BEAUTIFUL WONDERFUL AMAZING
    ALOE ALOES PLANT PLANTS FLOWER FLOWERS
    MYSTERY ZERO HUNDRED THOUSAND MILLION
    REPEATED REPEAT AMORE
    GOD GODS HEAVEN HELL ANGEL ANGELS DEVIL PRAYER TEMPLE CHURCH CROSS
    SARKIN JON JONATHAN MARK JMS JSARKIN SARK
""".split())


# ── Supplementary names (NOT in JIM Stories cache) ────────────────────────
# (canonical_name, reference_type, [single_word_triggers], [multi_word_triggers])

SUPPLEMENTARY_NAMES = [
    # ── Visual artists ──
    ("Pablo Picasso", "visual_artist", ["PICASSO"], []),
    ("Andy Warhol", "visual_artist", ["WARHOL"], []),
    ("Robert Rauschenberg", "visual_artist", ["RAUSCHENBERG"], []),
    ("Jean-Michel Basquiat", "visual_artist", ["BASQUIAT"], []),
    ("Jasper Johns", "visual_artist", [], ["JASPER JOHNS"]),
    ("Cy Twombly", "visual_artist", ["TWOMBLY"], ["CY TWOMBLY"]),
    ("Willem de Kooning", "visual_artist", [], ["DE KOONING", "DEKOONING"]),
    ("Mark Rothko", "visual_artist", ["ROTHKO"], []),
    ("Roy Lichtenstein", "visual_artist", ["LICHTENSTEIN"], []),
    ("Claes Oldenburg", "visual_artist", ["OLDENBURG"], []),
    ("Henri Matisse", "visual_artist", ["MATISSE"], []),
    ("Paul Cezanne", "visual_artist", ["CEZANNE", "CEZANNE"], []),
    ("Claude Monet", "visual_artist", ["MONET"], []),
    ("Pierre-Auguste Renoir", "visual_artist", ["RENOIR"], []),
    ("Edouard Manet", "visual_artist", ["MANET"], []),
    ("Paul Gauguin", "visual_artist", ["GAUGUIN"], []),
    ("Vincent van Gogh", "visual_artist", ["GOGH"], ["VAN GOGH"]),
    ("Rembrandt van Rijn", "visual_artist", ["REMBRANDT"], []),
    ("Johannes Vermeer", "visual_artist", ["VERMEER"], []),
    ("Michelangelo", "visual_artist", ["MICHELANGELO"], []),
    ("Leonardo da Vinci", "visual_artist", ["LEONARDO"], ["DA VINCI", "DAVINCI"]),
    ("Raphael", "visual_artist", ["RAPHAEL"], []),
    ("Sandro Botticelli", "visual_artist", ["BOTTICELLI"], []),
    ("Caravaggio", "visual_artist", ["CARAVAGGIO"], []),
    ("Titian", "visual_artist", ["TITIAN"], []),
    ("Tintoretto", "visual_artist", ["TINTORETTO"], []),
    ("Giotto", "visual_artist", ["GIOTTO"], []),
    ("Albrecht Durer", "visual_artist", ["DURER"], []),
    ("Hans Holbein", "visual_artist", ["HOLBEIN"], []),
    ("Wassily Kandinsky", "visual_artist", ["KANDINSKY"], []),
    ("Paul Klee", "visual_artist", ["KLEE"], []),
    ("Piet Mondrian", "visual_artist", ["MONDRIAN"], []),
    ("Rene Magritte", "visual_artist", ["MAGRITTE"], []),
    ("Salvador Dali", "visual_artist", ["DALI"], []),
    ("Joan Miro", "visual_artist", ["MIRO"], []),
    ("Max Ernst", "visual_artist", ["ERNST"], ["MAX ERNST"]),
    ("Alberto Giacometti", "visual_artist", ["GIACOMETTI"], []),
    ("Constantin Brancusi", "visual_artist", ["BRANCUSI"], []),
    ("Auguste Rodin", "visual_artist", ["RODIN"], []),
    ("Joseph Beuys", "visual_artist", ["BEUYS"], []),
    ("Richard Serra", "visual_artist", ["SERRA"], []),
    ("Donald Judd", "visual_artist", ["JUDD"], []),
    ("Dan Flavin", "visual_artist", ["FLAVIN"], []),
    ("Sol LeWitt", "visual_artist", ["LEWITT"], ["SOL LEWITT"]),
    ("Bruce Nauman", "visual_artist", ["NAUMAN"], []),
    ("Francis Bacon", "visual_artist", [], ["FRANCIS BACON"]),
    ("Lucian Freud", "visual_artist", [], ["LUCIAN FREUD"]),
    ("David Hockney", "visual_artist", ["HOCKNEY"], []),
    ("Gerhard Richter", "visual_artist", ["RICHTER"], []),
    ("Anselm Kiefer", "visual_artist", ["KIEFER"], []),
    ("Georg Baselitz", "visual_artist", ["BASELITZ"], []),
    ("Jean Dubuffet", "visual_artist", ["DUBUFFET"], []),
    ("Jean Arp", "visual_artist", ["ARP"], ["JEAN ARP"]),
    ("Man Ray", "visual_artist", [], ["MAN RAY"]),
    ("Joseph Cornell", "visual_artist", ["CORNELL"], []),
    ("Louise Nevelson", "visual_artist", ["NEVELSON"], []),
    ("Robert Motherwell", "visual_artist", ["MOTHERWELL"], []),
    ("Franz Kline", "visual_artist", ["KLINE"], ["FRANZ KLINE"]),
    ("Barnett Newman", "visual_artist", ["NEWMAN"], ["BARNETT NEWMAN"]),
    ("Ad Reinhardt", "visual_artist", ["REINHARDT"], ["AD REINHARDT"]),
    ("Clyfford Still", "visual_artist", [], ["CLYFFORD STILL"]),
    ("Philip Guston", "visual_artist", ["GUSTON"], []),
    ("Larry Rivers", "visual_artist", [], ["LARRY RIVERS"]),
    ("Robert Indiana", "visual_artist", [], ["ROBERT INDIANA"]),
    ("James Rosenquist", "visual_artist", ["ROSENQUIST"], []),
    ("Tom Wesselmann", "visual_artist", ["WESSELMANN"], []),
    ("Edward Hopper", "visual_artist", ["HOPPER"], ["EDWARD HOPPER"]),
    ("Georges Seurat", "visual_artist", ["SEURAT"], []),
    ("Paul Signac", "visual_artist", ["SIGNAC"], []),
    ("Robert Smithson", "visual_artist", ["SMITHSON"], []),
    ("Jean Tinguely", "visual_artist", ["TINGUELY"], []),
    ("James Turrell", "visual_artist", ["TURRELL"], []),
    ("Maurice Utrillo", "visual_artist", ["UTRILLO"], []),
    ("Francis Picabia", "visual_artist", ["PICABIA"], []),
    ("Egon Schiele", "visual_artist", ["SCHIELE"], []),
    ("Gustav Klimt", "visual_artist", ["KLIMT"], []),
    ("Edvard Munch", "visual_artist", ["MUNCH"], []),
    ("Marc Chagall", "visual_artist", ["CHAGALL"], []),
    ("Amedeo Modigliani", "visual_artist", ["MODIGLIANI"], []),
    ("El Greco", "visual_artist", [], ["EL GRECO"]),
    ("Diego Velazquez", "visual_artist", ["VELAZQUEZ"], []),
    ("Francisco Goya", "visual_artist", ["GOYA"], []),
    ("J.M.W. Turner", "visual_artist", [], ["JMW TURNER"]),
    ("John Constable", "visual_artist", ["CONSTABLE"], ["JOHN CONSTABLE"]),
    ("William Blake", "visual_artist", [], ["WILLIAM BLAKE"]),
    ("Frida Kahlo", "visual_artist", ["KAHLO"], ["FRIDA KAHLO"]),
    ("Diego Rivera", "visual_artist", ["RIVERA"], ["DIEGO RIVERA"]),
    ("Yves Klein", "visual_artist", [], ["YVES KLEIN"]),
    ("Christo", "visual_artist", ["CHRISTO"], []),
    ("Nam June Paik", "visual_artist", ["PAIK"], ["NAM JUNE PAIK"]),
    ("Wolf Vostell", "visual_artist", ["VOSTELL"], []),
    ("Allan Kaprow", "visual_artist", ["KAPROW"], []),
    ("Yoko Ono", "visual_artist", [], ["YOKO ONO"]),
    ("Keith Haring", "visual_artist", ["HARING"], ["KEITH HARING"]),
    ("Jeff Koons", "visual_artist", ["KOONS"], []),
    ("Damien Hirst", "visual_artist", ["HIRST"], []),
    ("Banksy", "visual_artist", ["BANKSY"], []),
    ("R. Crumb", "visual_artist", ["CRUMB"], []),

    # ── Musicians / Composers (NOT in JIM Stories) ──
    ("John Coltrane", "musician", ["COLTRANE"], []),
    ("Charles Mingus", "musician", ["MINGUS"], []),
    ("Thelonious Monk", "musician", [], []),  # disambiguation only
    ("Elvis Presley", "musician", ["ELVIS", "PRESLEY"], []),
    ("Ludwig van Beethoven", "musician", ["BEETHOVEN"], []),
    ("Wolfgang Amadeus Mozart", "musician", ["MOZART"], []),
    ("Frederic Chopin", "musician", ["CHOPIN"], []),
    ("Richard Wagner", "musician", ["WAGNER"], []),
    ("Giuseppe Verdi", "musician", ["VERDI"], []),
    ("Gustav Mahler", "musician", ["MAHLER"], []),
    ("Sergei Rachmaninoff", "musician", ["RACHMANINOFF"], []),
    ("Luciano Pavarotti", "musician", ["PAVAROTTI"], []),
    ("Lou Reed", "musician", [], ["LOU REED"]),
    ("Mick Jagger", "musician", ["JAGGER"], []),
    ("Pete Townshend", "musician", ["TOWNSHEND", "TOWNSEND"], []),
    ("Paul McCartney", "musician", ["MCCARTNEY"], []),
    ("George Harrison", "musician", [], ["GEORGE HARRISON"]),
    ("Eric Clapton", "musician", ["CLAPTON"], []),
    ("Muddy Waters", "musician", [], ["MUDDY WATERS"]),
    ("Howlin' Wolf", "musician", [], ["HOWLIN WOLF"]),
    ("B.B. King", "musician", [], ["BB KING", "B B KING"]),
    ("Robert Johnson", "musician", [], ["ROBERT JOHNSON"]),
    ("Smokey Robinson", "musician", [], ["SMOKEY ROBINSON"]),
    ("Otis Redding", "musician", ["REDDING"], ["OTIS REDDING"]),
    ("Ray Charles", "musician", [], ["RAY CHARLES"]),
    ("Sam Cooke", "musician", [], ["SAM COOKE"]),
    ("Marvin Gaye", "musician", [], ["MARVIN GAYE"]),
    ("Dizzy Gillespie", "musician", ["GILLESPIE"], ["DIZZY GILLESPIE"]),
    ("Duke Ellington", "musician", ["ELLINGTON"], ["DUKE ELLINGTON"]),
    ("Count Basie", "musician", ["BASIE"], ["COUNT BASIE"]),
    ("Billie Holiday", "musician", [], ["BILLIE HOLIDAY"]),
    ("Ella Fitzgerald", "musician", [], ["ELLA FITZGERALD"]),
    ("Louis Armstrong", "musician", ["ARMSTRONG"], ["LOUIS ARMSTRONG"]),
    ("Benny Goodman", "musician", [], ["BENNY GOODMAN"]),
    ("Ornette Coleman", "musician", ["COLEMAN"], ["ORNETTE COLEMAN"]),
    ("Max Roach", "musician", [], ["MAX ROACH"]),
    ("Art Blakey", "musician", ["BLAKEY"], ["ART BLAKEY"]),
    ("McCoy Tyner", "musician", ["TYNER"], ["MCCOY TYNER"]),
    ("Sonny Rollins", "musician", ["ROLLINS"], ["SONNY ROLLINS"]),
    ("Cannonball Adderley", "musician", ["CANNONBALL"], ["CANNONBALL ADDERLEY"]),
    ("Pharoah Sanders", "musician", ["PHAROAH"], ["PHAROAH SANDERS"]),
    ("Albert Ayler", "musician", ["AYLER"], ["ALBERT AYLER"]),
    ("Janis Joplin", "musician", ["JOPLIN"], ["JANIS JOPLIN"]),
    ("Frank Zappa", "musician", ["ZAPPA"], ["FRANK ZAPPA"]),
    ("Captain Beefheart", "musician", ["BEEFHEART"], []),
    ("David Bowie", "musician", ["BOWIE"], []),
    ("Patti Smith", "musician", [], ["PATTI SMITH"]),
    ("Iggy Pop", "musician", [], ["IGGY POP"]),
    ("Joey Ramone", "musician", ["RAMONE"], []),
    ("Debbie Harry", "musician", [], ["DEBBIE HARRY"]),
    ("Talking Heads", "band", [], ["TALKING HEADS"]),
    ("Velvet Underground", "band", [], ["VELVET UNDERGROUND"]),
    ("Led Zeppelin", "band", ["ZEPPELIN"], ["LED ZEPPELIN"]),
    ("Black Sabbath", "band", [], ["BLACK SABBATH"]),
    ("Pink Floyd", "band", [], ["PINK FLOYD"]),
    ("Blondie", "band", ["BLONDIE"], []),
    ("Ramones", "band", ["RAMONES"], []),

    # ── Authors (NOT in JIM Stories) ──
    ("William Faulkner", "author", ["FAULKNER"], []),
    ("Louis-Ferdinand Celine", "author", ["CELINE"], []),
    ("Michael McClure", "author", ["MCCLURE", "MCLURE"], []),
    ("Flannery O'Connor", "author", [], ["FLANNERY OCONNOR"]),
    ("Virginia Woolf", "author", ["WOOLF"], ["VIRGINIA WOOLF"]),
    ("James Joyce", "author", ["JOYCE"], ["JAMES JOYCE"]),
    ("Saki", "author", ["SAKI"], []),
    ("Truman Capote", "author", ["CAPOTE"], []),
    ("Norman Mailer", "author", ["MAILER"], ["NORMAN MAILER"]),
    ("Charles Bukowski", "author", ["BUKOWSKI"], []),
    ("Henry Miller", "author", [], ["HENRY MILLER"]),
    ("Rainer Maria Rilke", "author", ["RILKE"], []),
    ("Arthur Rimbaud", "author", ["RIMBAUD"], []),
    ("Charles Baudelaire", "author", ["BAUDELAIRE"], []),
    ("Walt Whitman", "author", ["WHITMAN"], ["WALT WHITMAN"]),
    ("Ralph Waldo Emerson", "author", ["EMERSON"], []),
    ("Oscar Wilde", "author", ["WILDE"], ["OSCAR WILDE"]),

    # ── Film / TV (NOT in JIM Stories) ──
    ("Martin Scorsese", "film", ["SCORSESE"], []),
    ("Alfred Hitchcock", "author", ["HITCHCOCK"], []),
    ("Federico Fellini", "film", ["FELLINI"], []),
    ("Akira Kurosawa", "film", ["KUROSAWA"], []),
    ("Willem Dafoe", "historical_figure", ["DAFOE"], ["WILLEM DAFOE"]),
    ("The Sopranos", "tv", ["SOPRANOS"], []),

    # ── Other cultural touchstones ──
    ("Alfred E. Neuman", "fictional_character", ["NEUMAN"], ["ALFRED E NEUMAN"]),
    ("Ubu Roi", "work", [], ["UBU ROI"]),
    ("Tombstone Blues", "song", [], ["TOMBSTONE BLUES"]),
    ("Edward Abbey", "author", ["ABBEY"], ["EDWARD ABBEY"]),
    ("Desert Solitaire", "work", [], ["DESERT SOLITAIRE", "DESERT SOLITUDE"]),
]


# ── OCR variant mappings ─────────────────────────────────────────────────
# Maps common misspellings/OCR errors → the correct trigger word.

OCR_VARIANTS = {
    "PICASO": "PICASSO",
    "PICSSO": "PICASSO",
    "PICASSO'S": "PICASSO",
    "VORMEER": "VERMEER",
    "VEMEER": "VERMEER",
    "BEUTS": "BEUYS",
    "BUEYS": "BEUYS",
    "KANDINSAY": "KANDINSKY",
    "KANDISNKY": "KANDINSKY",
    "KANDNISKY": "KANDINSKY",
    "BRACHMANINOFF": "RACHMANINOFF",
    "RACHMANNINOFF": "RACHMANINOFF",
    "RACHMANINOV": "RACHMANINOFF",
    "RAUCHENBERG": "RAUSCHENBERG",
    "RAUSCHBERG": "RAUSCHENBERG",
    "RAUSCHEN": "RAUSCHENBERG",
    "RAUSHENBERG": "RAUSCHENBERG",
    "WARHOLE": "WARHOL",
    "WAHROL": "WARHOL",
    "BASQUAIT": "BASQUIAT",
    "BASQIAT": "BASQUIAT",
    "DUSCHAMP": "DUCHAMP",
    "DUCHMAP": "DUCHAMP",
    "POLLACK": "POLLOCK",
    "POLOK": "POLLOCK",
    "LITCHENSTEIN": "LICHTENSTEIN",
    "LICHENSTEIN": "LICHTENSTEIN",
    "LICTENSTEIN": "LICHTENSTEIN",
    "TWOBLY": "TWOMBLY",
    "TWOMBY": "TWOMBLY",
    "MICHELANGLEO": "MICHELANGELO",
    "MICHAELANGELO": "MICHELANGELO",
    "MICHAELANGLEO": "MICHELANGELO",
    "REMBRANT": "REMBRANDT",
    "REMBRANDT'S": "REMBRANDT",
    "GINBURG": "GINSBERG",
    "GINSBURGH": "GINSBERG",
    "GINBURG": "GINSBERG",
    "FERLINGETTI": "FERLINGHETTI",
    "FERLINGHETI": "FERLINGHETTI",
    "HEMMINGWAY": "HEMINGWAY",
    "HEMMINGWAY'S": "HEMINGWAY",
    "HEMINGWAY'S": "HEMINGWAY",
    "DOSTOEVSKI": "DOSTOEVSKY",
    "DOSTOEVSKY'S": "DOSTOEVSKY",
    "DOSTOYEVSKY": "DOSTOEVSKY",
    "KEROAUC": "KEROUAC",
    "KEROUAK": "KEROUAC",
    "BROCH": "BRAQUE",
    "MATISE": "MATISSE",
    "MATISS": "MATISSE",
    "MODIGLANI": "MODIGLIANI",
    "GAUGAIN": "GAUGUIN",
    "GAUGIN": "GAUGUIN",
    "CEZANE": "CEZANNE",
    "CEZZANE": "CEZANNE",
    "GIOCOMETTI": "GIACOMETTI",
    "JACOMETTI": "GIACOMETTI",
    "MONDRAIN": "MONDRIAN",
    "MONDRAN": "MONDRIAN",
    "SERRRA": "SERRA",
    "COLLTRANE": "COLTRANE",
    "COLRANE": "COLTRANE",
    "COLLTRAINE": "COLTRANE",
    "JAGER": "JAGGER",
    "TOWSEND": "TOWNSHEND",
    "MCLURE": "MCCLURE",
}


# ── Disambiguation rules ─────────────────────────────────────────────────
# word → (canonical_name, type, context_words)
# Only match if ≥2 context words co-occur in the same transcription.

DISAMBIGUATION = {
    "BIRD": ("Charlie Parker", "musician",
             {"COLTRANE", "MINGUS", "MILES", "MONK", "JAZZ", "BEBOP", "PARKER", "SAXOPHONE", "SAX"}),
    "MONK": ("Thelonious Monk", "musician",
             {"COLTRANE", "MINGUS", "MILES", "BIRD", "JAZZ", "BEBOP", "PIANO", "THELONIOUS"}),
    "MILES": ("Miles Davis", "musician",
              {"COLTRANE", "MINGUS", "MONK", "BIRD", "JAZZ", "BEBOP", "DAVIS", "TRUMPET"}),
    "HOMER": ("Winslow Homer", "visual_artist",
              {"PICASSO", "DEGAS", "MONET", "MANET", "RENOIR", "MATISSE", "CEZANNE",
               "REMBRANDT", "SARGENT", "EAKINS", "PAINT", "PAINTER"}),
}


# ── Ambiguous single words (need multi-word match or disambiguation) ─────

AMBIGUOUS_SINGLE_WORDS = frozenset({
    "YOUNG", "BROWN", "DAVIS", "BURNS", "DARK", "LAKE", "WEST",
    "BIRD", "MONK", "MILES", "HOMER", "CHASE", "STARK",
    "REED", "KING", "RAY", "MAN", "MOORE", "TURNER",
    "LONG", "BLACK", "COLE", "RIVERS", "INDIANA",
    "JOHNS", "ERNST", "BACON", "STILL", "HOPPER", "KLEIN",
    "CONSTABLE", "NEWMAN", "FREUD", "SMITH",
    "COLLINS", "HARRISON", "ARMSTRONG", "COLEMAN", "ROLLINS",
    "MUNCH", "BERRY", "PAIK",
    "WILSON", "THOMPSON", "MITCHELL", "DAVIES", "SORVINO",
    "COLEMAN", "MARSHALL", "GRANT", "BELL", "ROSS", "GORDON",
    "JOHNSON", "WILLIAMS", "WILLIAM",
})


# ── Common English words for fuzzy exclusion ─────────────────────────────
# Words that are common enough in English that they should NEVER participate
# in fuzzy matching — neither as source (text word) nor as target (trigger).
# This prevents FIRST≈HIRST, MIGHT≈NIGHT, DREAMS≈DREAM, etc.

_COMMON_ENGLISH_FUZZY = frozenset("""
    ABOUT ABOVE AFTER AGAIN AGREE ALLOW ALONG AMONG APART ARGUE AWAIT
    BEACH BEGIN BEING BELOW BIRTH BLACK BLANK BLAST BLAZE BLEND BLESS
    BLIND BLOCK BLOOM BLOWN BOARD BOUND BRAND BRAVE BREAD BREAK BREED
    BRICK BRIEF BRING BROAD BROOK BRUSH BUILD BUNCH BURST
    CARRY CATCH CAUSE CHAIN CHAIR CHALK CHEAP CHEAT CHECK CHEEK CHIEF
    CHILD CHOSE CLAIM CLASS CLEAN CLEAR CLICK CLIMB CLOSE CLOUD COACH
    COAST COLOR CORAL COUNT COULD COURT COVER CRACK CRASH CRAFT CREAM
    CREEK CRIME CROSS CROWD CROWN CRUSH CURVE
    DANCE DEALT DECAY DEPTH DEVIL DIRTY DOUBT DRAFT DRAIN DRAMA DRAWN
    DREAM DRESS DRIED DRIFT DRINK DRIVE DROVE DYING
    EAGER EARLY EARTH EIGHT EMPTY ENEMY ENJOY ENTER EQUAL ERROR ESSAY
    ETHER EVENT EVERY EXACT EXIST EXTRA EXULT
    FACED FAITH FALSE FARMS FANCY FAULT FEAST FEWER FIELD FIGHT FINAL
    FINAL FIRST FIXED FLAME FLASH FLESH FLIES FLOAT FLOOD FLOOR FLOWN
    FLUSH FOCUS FORCE FORGE FORTH FOUND FRAME FRESH FRONT FROST FRUIT
    FULLY FUNNY
    GAMES GATES GIANT GIVEN GLASS GLEAN GLOBE GOING GOODS GRACE GRADE
    GRAIN GRAND GRANT GRASP GRASS GRAVE GREAT GREEN GREET GRIEF GRIND
    GROSS GROUP GROWN GUARD GUESS GUIDE GUILD GUILT
    HANDS HAPPY HARSH HASTE HAVEN HEADS HEARD HEART HEAVY HEDGE HERBS
    HILLS HIRED HOLDS HOMES HONOR HOPED HORSE HOTEL HOURS HOUSE HUMAN
    HUMOR
    IDEAL IMAGE INDEX INNER INPUT INTER ISSUE IVORY
    JAMES JOINT JUDGE JUICE
    KEEPS KNACK KNEEL KNIFE KNOCK KNOWN
    LABEL LABOR LARGE LATER LAUGH LAYER LEADS LEARN LEAST LEAVE LEGAL
    LEMON LEVEL LIGHT LIMIT LINES LINKS LIVES LOCAL LOGIC LOOSE LOVER
    LOWER LUNAR LUNCH
    MAGIC MAJOR MANOR MARCH MARKS MATCH MAYOR MEANS MEDIA MERCY MERGE
    METAL MIGHT MINDS MINOR MIXED MODEL MONEY MONTH MORAL MOUNT MOUSE
    MOUTH MOVED MUSIC MYTHS
    NERVE NEVER NEWLY NIGHT NOBLE NOISE NORTH NOTED NOVEL NURSE
    OCEAN OFFER ORDER ORGAN OTHER OUGHT OUTER OWNED OWNER
    PAINT PANEL PAPER PARTY PATCH PAUSE PEACH PEACE PENNY PHASE PHONE
    PIANO PIECE PITCH PLACE PLAIN PLANE PLANT PLATE PLAYS PLAZA PLEAD
    PLUMB POINT POLAR POOLS POUND POWER PRESS PRICE PRIDE PRIME PRINT
    PRIOR PRIZE PROOF PROUD PROVE PSALM PUNCH PURSE QUEST QUICK QUIET
    QUITE QUOTE
    RADIO RAISE RANGE RAPID RATES REACH READS REALM REIGN RELAX REPLY
    RIDER RIDGE RIGHT RISEN RIVAL RIVER ROADS ROBIN ROCKY ROOTS ROUGH
    ROUND ROUTE ROYAL RUINS RULES RURAL RUSTY
    SADLY SAINT SAUCE SAVED SCALE SCENE SCOPE SCORE SEATS SEEDS SEEMS
    SENSE SERVE SHADE SHAKE SHALL SHAME SHAPE SHARE SHARP SHEEP SHELL
    SHIFT SHINE SHIPS SHIRT SHOCK SHOES SHOOT SHORT SHOWN SIDED SIGHT
    SINCE SIXTH SIXTY SKILL SLEEP SLICE SLIDE SLOPE SMALL SMART SMELL
    SMILE SMOKE SNAKE SOLAR SOLID SOLVE SORRY SOUND SOUTH SPACE SPARE
    SPEAK SPEED SPEND SPILL SPINE SPLIT SPOKE SPORT SPRAY STACK STAFF
    STAGE STAIN STAKE STALL STAMP STAND STARK STARS STATE STAYS STEAL
    STEAM STEEL STEEP STEMS STEPS STICK STIFF STOCK STOLE STOOD STORE
    STORM STORY STOVE STRIP STUCK STUFF STYLE SUGAR SUITE SUNNY SUPER
    SURGE SWEET SWEPT SWING SWORN
    TABLE TAKEN TALES TASTE TAXES TEACH TEETH TERMS THANK THEFT THEME
    THICK THING THINK THIRD THOSE THREW THREE THROW TIGHT TIRED TITLE
    TODAY TOKEN TOOLS TOTAL TOUCH TOUGH TOURS TOWER TRACE TRACK TRADE
    TRAIL TRAIN TRAIT TRASH TREAT TREND TRIAL TRIBE TRICK TRIED TROOP
    TRUCK TRULY TRUMP TRUNK TRUST TRUTH TWICE
    ULTRA UNDER UNION UNITE UNITY UNTIL UPPER UPSET URBAN USUAL
    VALID VALUE VAULT VERSE VIDEO VIGOR VIRUS VISIT VITAL VIVID VOCAL
    VOICE VOTED
    WAGES WASTE WATCH WATER WAVES WEEKS WEIGH WEIRD WHALE WHEAT WHEEL
    WHERE WHICH WHILE WHITE WHOLE WHOSE WIDTH WINGS WOMAN WOMEN WOODS
    WORDS WORKS WORLD WORRY WORSE WORST WORTH WOULD WOUND WRATH WRITE
    WRONG WROTE
    YIELD YOURS YOUTH
""".split())


# ── ReferenceDictionary ──────────────────────────────────────────────────

class ReferenceDictionary:
    """Builds a lookup dictionary from Jim Stories names + supplementary + OCR."""

    def __init__(self):
        # canonical_name → type
        self.types: dict[str, str] = {}
        # uppercased multi-word pattern → canonical_name
        self.multi_word: dict[str, str] = {}
        # uppercased single word → canonical_name  (only non-ambiguous)
        self.single_word: dict[str, str] = {}
        # subset of single_word that are safe for fuzzy matching (proper names only)
        self.fuzzy_eligible: dict[str, str] = {}
        # misspelling (upper) → correct trigger (upper)
        self.ocr_variants: dict[str, str] = dict(OCR_VARIANTS)
        # word → (canonical, type, context_words)
        self.disambiguation: dict[str, tuple] = dict(DISAMBIGUATION)

    def _is_proper_name(self, word: str) -> bool:
        """Check if a word looks like a proper name (not a common English word)."""
        return word not in COMMON_WORDS and word not in _COMMON_ENGLISH_FUZZY

    def build(self, jim_cache_path: str) -> None:
        """Build the dictionary from Jim Stories cache + supplementary names."""
        # ── Source A: Jim Stories canonical names ──
        if os.path.exists(jim_cache_path):
            with open(jim_cache_path) as f:
                jim_cache = json.load(f)
            jim_names: dict[str, dict] = {}
            for item in jim_cache:
                for ref in item.get("references", []):
                    n = ref["reference_name"]
                    if n not in jim_names:
                        jim_names[n] = {"type": ref["reference_type"], "mentions": set()}
                    jim_names[n]["mentions"].add(ref["reference_as_mentioned"])

            for canonical, info in jim_names.items():
                rtype = info["type"]
                self.types[canonical] = rtype

                # Register canonical name as multi-word if 2+ words
                words = canonical.split()
                upper_canonical = canonical.upper()
                if len(words) >= 2:
                    self.multi_word[upper_canonical] = canonical
                    # Also register last word as single-word (if not ambiguous)
                    last_word = words[-1].upper()
                    if last_word not in AMBIGUOUS_SINGLE_WORDS and len(last_word) >= 4:
                        self.single_word[last_word] = canonical
                        # Only fuzzy-eligible if it's a proper name
                        if self._is_proper_name(last_word):
                            self.fuzzy_eligible[last_word] = canonical
                elif len(words) == 1 and upper_canonical not in AMBIGUOUS_SINGLE_WORDS:
                    self.single_word[upper_canonical] = canonical
                    if self._is_proper_name(upper_canonical):
                        self.fuzzy_eligible[upper_canonical] = canonical

                # Register mention forms as lookups
                for mention in info["mentions"]:
                    mention_upper = mention.upper()
                    mention_words = mention_upper.split()
                    # Skip very long mention forms (full quotes, etc.)
                    if len(mention_words) > 4:
                        continue
                    if len(mention_words) >= 2:
                        self.multi_word[mention_upper] = canonical
                    elif mention_upper not in AMBIGUOUS_SINGLE_WORDS and len(mention_upper) >= 4:
                        self.single_word[mention_upper] = canonical

            print(f"  Source A: {len(jim_names)} Jim Stories canonical names loaded.")
        else:
            print(f"  Source A: jim_references_cache.json not found — skipping.")

        # ── Source B: Supplementary names ──
        added = 0
        for canonical, rtype, singles, multis in SUPPLEMENTARY_NAMES:
            canonical = normalize_name(canonical)
            if canonical not in self.types:
                self.types[canonical] = rtype
            for trigger in singles:
                trigger_upper = trigger.upper()
                if trigger_upper not in AMBIGUOUS_SINGLE_WORDS:
                    if trigger_upper not in self.single_word:
                        self.single_word[trigger_upper] = canonical
                        added += 1
                    # Supplementary names are always fuzzy-eligible
                    if trigger_upper not in self.fuzzy_eligible:
                        self.fuzzy_eligible[trigger_upper] = canonical
            for trigger in multis:
                trigger_upper = trigger.upper()
                if trigger_upper not in self.multi_word:
                    self.multi_word[trigger_upper] = canonical
                    added += 1
            # Also register full canonical as multi-word
            upper_full = canonical.upper()
            if len(canonical.split()) >= 2 and upper_full not in self.multi_word:
                self.multi_word[upper_full] = canonical
        print(f"  Source B: {len(SUPPLEMENTARY_NAMES)} supplementary names ({added} new triggers).")

        # ── Source C: OCR variants ──
        print(f"  Source C: {len(self.ocr_variants)} OCR variant mappings.")

        print(f"  Dictionary: {len(self.multi_word)} multi-word, "
              f"{len(self.single_word)} single-word, "
              f"{len(self.fuzzy_eligible)} fuzzy-eligible, "
              f"{len(self.disambiguation)} disambiguation rules.")

    def scan(self, text: str) -> tuple[list[dict], list[str]]:
        """Scan preprocessed text for references. Returns (matches, unmatched_candidates)."""
        text_upper = text.upper()
        text_words = set(re.findall(r"[A-Z]{2,}(?:'[A-Z]+)?", text_upper))
        matches: list[dict] = []
        matched_spans: set[str] = set()  # track what we've already matched

        # ── Tier 1: Multi-word exact (case-insensitive) ──
        for pattern, canonical in self.multi_word.items():
            if pattern in text_upper:
                count = len(re.findall(re.escape(pattern), text_upper))
                if count > 0 and canonical not in matched_spans:
                    # Find the actual text form
                    m = re.search(re.escape(pattern), text_upper)
                    actual = text[m.start():m.end()] if m else pattern
                    matches.append({
                        "reference_name": canonical,
                        "reference_as_mentioned": actual,
                        "reference_type": self.types.get(canonical, "other"),
                        "repetition_count": count,
                        "match_tier": "multi_word",
                    })
                    matched_spans.add(canonical)

        # ── Tier 2: Single-word exact ──
        for word in text_words:
            # Check OCR variants first
            corrected = self.ocr_variants.get(word, word)
            if corrected != word:
                # This word is an OCR variant — look up the corrected form
                canonical = self.single_word.get(corrected)
                if not canonical:
                    # Check multi-word dict for the corrected form
                    canonical = self.multi_word.get(corrected)
                if canonical and canonical not in matched_spans:
                    count = len(re.findall(r"\b" + re.escape(word) + r"\b", text_upper))
                    matches.append({
                        "reference_name": canonical,
                        "reference_as_mentioned": word,
                        "reference_type": self.types.get(canonical, "other"),
                        "repetition_count": count,
                        "match_tier": "fuzzy",
                        "notes": f"OCR variant: {word} → {corrected}",
                    })
                    matched_spans.add(canonical)
                continue

            if word in COMMON_WORDS:
                continue
            if word in AMBIGUOUS_SINGLE_WORDS:
                continue  # handled by disambiguation
            canonical = self.single_word.get(word)
            if canonical and canonical not in matched_spans:
                count = len(re.findall(r"\b" + re.escape(word) + r"\b", text_upper))
                matches.append({
                    "reference_name": canonical,
                    "reference_as_mentioned": word,
                    "reference_type": self.types.get(canonical, "other"),
                    "repetition_count": count,
                    "match_tier": "exact",
                })
                matched_spans.add(canonical)

        # ── Tier 3: Fuzzy matching (edit distance ≤ 1, words 7+ chars) ──
        # Only matches against fuzzy_eligible triggers (proper names, not common words).
        # Also skips text words that are themselves common English words.
        for word in text_words:
            if len(word) < 7 or word in COMMON_WORDS or word in _COMMON_ENGLISH_FUZZY:
                continue
            if word in self.single_word or word in self.ocr_variants:
                continue  # already handled
            # Check edit distance against fuzzy-eligible triggers only
            for trigger, canonical in self.fuzzy_eligible.items():
                if canonical in matched_spans:
                    continue
                if len(trigger) < 7:
                    continue
                if abs(len(word) - len(trigger)) > 1:
                    continue
                if _edit_distance(word, trigger) == 1:
                    count = len(re.findall(r"\b" + re.escape(word) + r"\b", text_upper))
                    matches.append({
                        "reference_name": canonical,
                        "reference_as_mentioned": word,
                        "reference_type": self.types.get(canonical, "other"),
                        "repetition_count": count,
                        "match_tier": "fuzzy",
                        "notes": f"fuzzy: {word} ≈ {trigger}",
                    })
                    matched_spans.add(canonical)
                    break  # one match per unknown word

        # ── Tier 4: Disambiguation (context-dependent) ──
        for word, (canonical, rtype, context_words) in self.disambiguation.items():
            if canonical in matched_spans:
                continue
            if word not in text_words:
                continue
            # Count how many context words are present
            context_hits = sum(1 for cw in context_words if cw in text_words)
            if context_hits >= 2:
                count = len(re.findall(r"\b" + re.escape(word) + r"\b", text_upper))
                matches.append({
                    "reference_name": canonical,
                    "reference_as_mentioned": word,
                    "reference_type": rtype,
                    "repetition_count": count,
                    "match_tier": "disambiguated",
                    "notes": f"disambiguated with {context_hits} context words",
                })
                matched_spans.add(canonical)

        # ── Collect unmatched candidates (for Pass 2 review) ──
        all_matched_words = set()
        for m in matches:
            for w in m["reference_as_mentioned"].upper().split():
                all_matched_words.add(w)
        unmatched = []
        for word in text_words:
            if word in all_matched_words or word in COMMON_WORDS:
                continue
            if len(word) < 4:
                continue
            # Heuristic: capitalize-like patterns suggest proper names
            if word.isalpha() and word == word.upper():
                unmatched.append(word)

        return matches, sorted(unmatched)


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return _edit_distance(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(
                prev[j + 1] + 1,     # deletion
                curr[j] + 1,          # insertion
                prev[j] + (0 if ca == cb else 1),  # substitution
            ))
        prev = curr
    return prev[len(b)]


# ── Pass 2: Claude Haiku (selective) ──────────────────────────────────────

PASS2_SYSTEM_PROMPT = """\
You are analyzing text transcribed from visual artworks by artist Jon Sarkin.
The text is fragmentary, often ALL CAPS, with repeated words and OCR errors.

Given a transcription from an artwork, identify cultural references (people, works,
songs, films, etc.) that a dictionary scan might have missed:

1. Names with unusual spellings or OCR errors not caught by dictionary
2. Song lyrics or literary quotations embedded in the text
3. Slang or abbreviated references to cultural figures
4. Less well-known cultural references

Already-identified references will be provided; do NOT repeat them.

Respond with a JSON array only. No markdown fences, no explanation.
Empty array [] if no additional references found.

Each object must have:
- reference_name: Canonical form (e.g., "Bob Dylan")
- reference_as_mentioned: Exact text from transcription
- reference_type: One of: author, work, fictional_character, musician, band, song, album, film, tv, visual_artist, art_movement, historical_figure, historical_event, philosopher, religious, sports, venue, other
- repetition_count: How many times this reference appears
- notes: Brief note on why this was identified

Be conservative — only extract references you're confident about."""


def pass2_claude(item: dict, pass1_matches: list[dict],
                 client, model: str) -> list[dict]:
    """Run Claude Haiku on items that need deeper analysis."""
    transcription = item["transcription"]
    already = ", ".join(m["reference_name"] for m in pass1_matches) or "(none)"

    user_msg = (
        f"Artwork: {item['identifier']} — {item['title']}\n\n"
        f"Already identified: {already}\n\n"
        f"Transcription:\n{transcription}"
    )
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=PASS2_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    start = raw.find("[")
    if start == -1:
        return []
    depth = 0
    end = start
    for i, ch in enumerate(raw[start:], start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    try:
        refs = json.loads(raw[start:end])
    except json.JSONDecodeError:
        return []
    if not isinstance(refs, list):
        return []

    valid = []
    existing_names = {m["reference_name"].lower() for m in pass1_matches}
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        name = ref.get("reference_name", "")
        if not name or name.lower() in existing_names:
            continue
        name = normalize_name(name)
        if ref.get("reference_type") not in VALID_REFERENCE_TYPES:
            continue
        ref["reference_name"] = name
        ref["match_tier"] = "claude"
        ref.setdefault("repetition_count", 1)
        valid.append(ref)
    return valid


def needs_pass2(item: dict, pass1_matches: list[dict], unmatched: list[str]) -> bool:
    """Decide whether an item needs Pass 2 Claude analysis."""
    text = item["transcription"]
    if len(text) < 50:
        return False
    # Items with pass1 matches AND unmatched candidates
    if pass1_matches and len(unmatched) > 3:
        return True
    # Items with many unmatched capitalized words
    if len(unmatched) > 8:
        return True
    return False


# ── Output helpers ─────────────────────────────────────────────────────────

def format_item_summary(item: dict, references: list[dict]) -> str:
    """Format a single item's references for the review file."""
    header = f"{item['identifier']} | {item['title']}"
    if not references:
        return f"{header}\n  (no references)\n"
    lines = [header]
    for ref in references:
        notes = f" [{ref['notes']}]" if ref.get("notes") else ""
        rep = f" x{ref['repetition_count']}" if ref.get("repetition_count", 1) > 1 else ""
        lines.append(
            f"  - {ref['reference_name']} ({ref['reference_type']}, {ref['match_tier']})"
            f"{rep}{notes} — as \"{ref['reference_as_mentioned']}\""
        )
    return "\n".join(lines) + "\n"


def build_aggregate_table(all_results: list[dict]) -> list[dict]:
    """Build aggregate reference frequency table."""
    agg = defaultdict(lambda: {
        "reference_name": "",
        "reference_type": "",
        "works_count": 0,
        "total_repetitions": 0,
        "works": set(),
        "match_tiers": Counter(),
    })
    for result in all_results:
        ident = result["identifier"]
        for ref in result.get("references", []):
            key = ref["reference_name"].lower()
            entry = agg[key]
            entry["reference_name"] = ref["reference_name"]
            entry["reference_type"] = ref["reference_type"]
            entry["total_repetitions"] += ref.get("repetition_count", 1)
            entry["match_tiers"][ref["match_tier"]] += 1
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
    """Build reference type breakdown table."""
    type_refs = defaultdict(set)
    type_reps = Counter()
    for result in all_results:
        for ref in result.get("references", []):
            rtype = ref["reference_type"]
            type_refs[rtype].add(ref["reference_name"])
            type_reps[rtype] += ref.get("repetition_count", 1)
    rows = []
    for rtype in sorted(type_reps, key=lambda t: -type_reps[t]):
        rows.append({
            "reference_type": rtype,
            "unique_references": len(type_refs[rtype]),
            "total_repetitions": type_reps[rtype],
        })
    return rows


def write_csv(rows: list[dict], path: str, fieldnames: list[str]) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_aggregate(rows: list[dict], limit: int = 30) -> None:
    print(f"\n{'Reference':<40} {'Type':<18} {'Works':>5} {'Total Reps':>10} {'Avg':>5}  Tier")
    print("-" * 100)
    for row in rows[:limit]:
        print(f"{row['reference_name']:<40} {row['reference_type']:<18} "
              f"{row['works_count']:>5} {row['total_repetitions']:>10} "
              f"{row['avg_reps_per_work']:>5}  {row['primary_tier']}")
    if len(rows) > limit:
        print(f"  ... and {len(rows) - limit} more (see CSV)")


def print_type_breakdown(rows: list[dict]) -> None:
    print(f"\n{'Type':<25} {'Unique':>6} {'Total Reps':>10}")
    print("-" * 45)
    for row in rows:
        print(f"{row['reference_type']:<25} {row['unique_references']:>6} "
              f"{row['total_repetitions']:>10}")


# ── Cross-media reports ───────────────────────────────────────────────────

def generate_cross_media_reports(visual_cache_path: str, jim_cache_path: str,
                                 scripts_dir: str) -> None:
    """Generate cross-media match reports comparing visual works and JIM Stories."""
    if not os.path.exists(visual_cache_path):
        print("ERROR: visual_references_cache.json not found. Run extraction first.")
        return
    if not os.path.exists(jim_cache_path):
        print("ERROR: jim_references_cache.json not found.")
        return

    with open(visual_cache_path) as f:
        visual_cache = json.load(f)
    with open(jim_cache_path) as f:
        jim_cache = json.load(f)

    # Collect references from each corpus
    visual_refs: dict[str, dict] = {}  # canonical → {type, works, total_reps}
    for item in visual_cache:
        for ref in item.get("references", []):
            name = ref["reference_name"]
            if name not in visual_refs:
                visual_refs[name] = {"type": ref["reference_type"], "works": set(), "total_reps": 0}
            visual_refs[name]["works"].add(item["identifier"])
            visual_refs[name]["total_reps"] += ref.get("repetition_count", 1)

    jim_refs: dict[str, dict] = {}
    for item in jim_cache:
        for ref in item.get("references", []):
            name = ref["reference_name"]
            if name not in jim_refs:
                jim_refs[name] = {"type": ref["reference_type"], "stories": set(), "mentions": 0}
            jim_refs[name]["stories"].add(item["identifier"])
            jim_refs[name]["mentions"] += 1

    visual_names = set(visual_refs.keys())
    jim_names = set(jim_refs.keys())
    both = visual_names & jim_names
    visual_only = visual_names - jim_names
    jim_only = jim_names - visual_names

    # Cross-media references (in both)
    cross_rows = []
    for name in sorted(both, key=str.casefold):
        v = visual_refs[name]
        j = jim_refs[name]
        cross_rows.append({
            "reference_name": name,
            "reference_type": v["type"],
            "visual_works_count": len(v["works"]),
            "visual_total_reps": v["total_reps"],
            "jim_stories_count": len(j["stories"]),
            "jim_mentions": j["mentions"],
        })
    cross_path = os.path.join(scripts_dir, "cross_media_references.csv")
    write_csv(cross_rows, cross_path, [
        "reference_name", "reference_type",
        "visual_works_count", "visual_total_reps",
        "jim_stories_count", "jim_mentions",
    ])

    # Visual-only
    vo_rows = []
    for name in sorted(visual_only, key=str.casefold):
        v = visual_refs[name]
        vo_rows.append({
            "reference_name": name,
            "reference_type": v["type"],
            "visual_works_count": len(v["works"]),
            "visual_total_reps": v["total_reps"],
        })
    vo_path = os.path.join(scripts_dir, "references_visual_only.csv")
    write_csv(vo_rows, vo_path, [
        "reference_name", "reference_type", "visual_works_count", "visual_total_reps",
    ])

    # JIM-only
    jo_rows = []
    for name in sorted(jim_only, key=str.casefold):
        j = jim_refs[name]
        jo_rows.append({
            "reference_name": name,
            "reference_type": j["type"],
            "jim_stories_count": len(j["stories"]),
            "jim_mentions": j["mentions"],
        })
    jo_path = os.path.join(scripts_dir, "references_jim_only.csv")
    write_csv(jo_rows, jo_path, [
        "reference_name", "reference_type", "jim_stories_count", "jim_mentions",
    ])

    print(f"\n{'=' * 70}")
    print("CROSS-MEDIA REPORT")
    print(f"{'=' * 70}")
    print(f"References in BOTH visual works + JIM Stories:  {len(both)}")
    print(f"References in visual works ONLY:                {len(visual_only)}")
    print(f"References in JIM Stories ONLY:                 {len(jim_only)}")
    print(f"\nFiles:")
    print(f"  {cross_path}")
    print(f"  {vo_path}")
    print(f"  {jo_path}")

    # Print top cross-media references
    if cross_rows:
        cross_sorted = sorted(cross_rows,
                              key=lambda r: -(r["visual_works_count"] + r["jim_stories_count"]))
        print(f"\nTop cross-media references:")
        print(f"{'Reference':<40} {'Type':<18} {'Visual':>6} {'JIM':>4}")
        print("-" * 72)
        for row in cross_sorted[:25]:
            print(f"{row['reference_name']:<40} {row['reference_type']:<18} "
                  f"{row['visual_works_count']:>6} {row['jim_stories_count']:>4}")


# ── Vocab & facet extension ───────────────────────────────────────────────

def extend_custom_vocab(new_names: list[str]) -> int:
    """Extend custom vocab 11 with new terms (merge, don't replace)."""
    # Read existing terms
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

    # Merge
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
    # Read current facet data
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

    # Update values field
    data["values"] = "\n".join(sorted(all_names, key=str.casefold))
    data_json = json.dumps(data, separators=(",", ":"))
    escaped = data_json.replace("\\", "\\\\").replace("'", "\\'")
    sql = f"UPDATE faceted_browse_facet SET data = '{escaped}' WHERE id = {facet_id};"
    db_execute(sql)


def insert_relation_values(all_results: list[dict], existing: set[int]) -> int:
    """Insert dcterms:relation values for each unique reference per item."""
    total = 0
    for result in all_results:
        item_id = result["item_id"]
        if item_id in existing:
            continue
        refs = result.get("references", [])
        if not refs:
            continue
        seen = set()
        for ref in refs:
            name = ref["reference_name"]
            if name in seen:
                continue
            seen.add(name)
            escaped = name.replace("\\", "\\\\").replace("'", "\\'")
            sql = (
                f"INSERT INTO `value` (resource_id, property_id, type, `value`, is_public) "
                f"VALUES ({item_id}, {PROPERTY_ID_RELATION}, '{CUSTOM_VOCAB_TYPE}', "
                f"'{escaped}', 1);"
            )
            db_execute(sql)
            total += 1
    return total


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract cultural references from visual work transcriptions"
    )
    parser.add_argument("--apply", action="store_true",
                        help="Write results to DB (default: dry-run)")
    parser.add_argument("--pass2", action="store_true",
                        help="Run Pass 2 Claude analysis on selected items")
    parser.add_argument("--item-id", type=int,
                        help="Process a single item by DB ID")
    parser.add_argument("--cross-media", action="store_true",
                        help="Generate cross-media match reports")
    parser.add_argument("--model", default=PASS2_MODEL,
                        help=f"Claude model for Pass 2 (default: {PASS2_MODEL})")
    args = parser.parse_args()

    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    jim_cache_path = os.path.join(scripts_dir, "jim_references_cache.json")
    visual_cache_path = os.path.join(scripts_dir, "visual_references_cache.json")

    # ── Cross-media report mode ──
    if args.cross_media:
        generate_cross_media_reports(visual_cache_path, jim_cache_path, scripts_dir)
        return

    # ── Build dictionary ──
    print("Building reference dictionary...")
    dictionary = ReferenceDictionary()
    dictionary.build(jim_cache_path)

    # ── Fetch visual works ──
    print("\nFetching visual works from DB...")
    items = fetch_visual_works(args.item_id)
    if not items:
        print("No visual works found.")
        sys.exit(1)
    print(f"Fetched {len(items)} visual works.")

    # ── Load cache ──
    cache: dict[int, dict] = {}
    if os.path.exists(visual_cache_path) and not args.item_id:
        with open(visual_cache_path) as f:
            for entry in json.load(f):
                cache[entry["item_id"]] = entry

    # ── Pass 2 setup ──
    client = None
    if args.pass2:
        if not os.getenv("ANTHROPIC_API_KEY"):
            print("ERROR: Set ANTHROPIC_API_KEY for --pass2.")
            sys.exit(1)
        import anthropic
        client = anthropic.Anthropic()

    # ── Processing ──
    cached_count = sum(1 for it in items if it["item_id"] in cache)
    need_scan = len(items) - cached_count
    if cached_count:
        print(f"Using cache for {cached_count} items, {need_scan} need scanning.")
    print(f"Processing {len(items)} items...\n")

    all_results = []
    errors = []
    review_lines = []
    pass2_count = 0
    skipped_short = 0

    for i, item in enumerate(items):
        item_id = item["item_id"]
        ident = item["identifier"]
        transcription = item["transcription"]

        try:
            # Use cache if available
            if item_id in cache:
                result = cache[item_id]
                refs = result.get("references", [])
                if (i + 1) % 500 == 0 or i == 0:
                    print(f"  [{i+1}/{len(items)}] {ident} — cached ({len(refs)} refs)")
                all_results.append(result)
                review_lines.append(format_item_summary(item, refs))
                continue

            # Skip trivially short transcriptions
            if len(transcription.strip()) < MIN_TRANSCRIPTION_LENGTH:
                skipped_short += 1
                result = {
                    "item_id": item_id,
                    "identifier": ident,
                    "title": item["title"],
                    "references": [],
                    "skipped": True,
                }
                all_results.append(result)
                cache[item_id] = result
                continue

            # ── Pass 1: Dictionary scan ──
            processed = preprocess_text(transcription)
            matches, unmatched = dictionary.scan(processed)

            # ── Pass 2: Claude (selective) ──
            if args.pass2 and client and needs_pass2(item, matches, unmatched):
                try:
                    extra = pass2_claude(item, matches, client, args.model)
                    if extra:
                        matches.extend(extra)
                    pass2_count += 1
                    if pass2_count < len(items) - 1:
                        time.sleep(0.3)
                except Exception as e:
                    # Pass 2 failure is non-fatal
                    pass

            result = {
                "item_id": item_id,
                "identifier": ident,
                "title": item["title"],
                "references": matches,
                "unmatched_candidates": unmatched[:20],  # cap for cache size
                "pass2_complete": args.pass2 and needs_pass2(item, matches, unmatched),
            }
            all_results.append(result)
            cache[item_id] = result
            review_lines.append(format_item_summary(item, matches))

            # Progress
            ref_count = len(matches)
            if ref_count > 0:
                names = ", ".join(m["reference_name"] for m in matches[:3])
                if ref_count > 3:
                    names += f", ... (+{ref_count - 3})"
                print(f"  [{i+1}/{len(items)}] {ident} — {ref_count} refs: {names}")
            elif (i + 1) % 500 == 0:
                print(f"  [{i+1}/{len(items)}] {ident} — no refs")

        except Exception as e:
            print(f"  [{i+1}/{len(items)}] {ident} — ERROR: {e}")
            errors.append({"identifier": ident, "item_id": item_id, "error": str(e)})

    # ── Save cache ──
    with open(visual_cache_path, "w") as f:
        json.dump(list(cache.values()), f, separators=(",", ":"))
    print(f"\nCache saved to: {visual_cache_path}")

    # ── Review file ──
    review_path = os.path.join(scripts_dir, "visual_references_review.txt")
    with open(review_path, "w") as f:
        f.write("# Visual Works — Cultural Reference Extraction\n")
        f.write(f"# {len(all_results)} items processed, {skipped_short} skipped (short), "
                f"{len(errors)} errors\n")
        if args.pass2:
            f.write(f"# Pass 2 (Claude {args.model}): {pass2_count} items\n")
        f.write("\n")
        for line in review_lines:
            f.write(line + "\n")
    print(f"Review saved to: {review_path}")

    # ── Aggregate CSV ──
    agg_rows = build_aggregate_table(all_results)
    agg_path = os.path.join(scripts_dir, "visual_references_aggregate.csv")
    write_csv(agg_rows, agg_path, [
        "reference_name", "reference_type", "works_count",
        "total_repetitions", "avg_reps_per_work", "primary_tier",
        "work_identifiers",
    ])
    print(f"Aggregate CSV: {agg_path}")
    print_aggregate(agg_rows)

    # ── Type breakdown ──
    type_rows = build_type_breakdown(all_results)
    type_path = os.path.join(scripts_dir, "visual_references_type_breakdown.csv")
    write_csv(type_rows, type_path, [
        "reference_type", "unique_references", "total_repetitions",
    ])
    print(f"\nType breakdown: {type_path}")
    print_type_breakdown(type_rows)

    # ── Summary ──
    items_with = sum(1 for r in all_results if r.get("references"))
    items_without = sum(1 for r in all_results if not r.get("references"))
    total_refs = sum(len(r.get("references", [])) for r in all_results)
    total_reps = sum(
        ref.get("repetition_count", 1)
        for r in all_results for ref in r.get("references", [])
    )

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    print(f"Items processed:         {len(all_results)}")
    print(f"Items skipped (short):   {skipped_short}")
    print(f"Items with references:   {items_with}")
    print(f"Items without:           {items_without}")
    print(f"Unique references:       {len(agg_rows)}")
    print(f"Total ref instances:     {total_refs}")
    print(f"Total repetitions:       {total_reps}")
    if args.pass2:
        print(f"Pass 2 items:            {pass2_count}")
    print(f"Errors:                  {len(errors)}")

    if errors:
        print(f"\nERRORS:")
        for e in errors[:20]:
            print(f"  {e['identifier']}: {e['error']}")

    # ── Apply to DB ──
    if args.apply:
        print(f"\n{'=' * 70}")
        print("APPLYING TO DATABASE")
        print(f"{'=' * 70}")

        # Collect all unique reference names from visual works
        visual_names = set()
        for result in all_results:
            for ref in result.get("references", []):
                visual_names.add(ref["reference_name"])
        visual_names_sorted = sorted(visual_names, key=str.casefold)

        # Step 1: Extend custom vocab
        print("\n--- Step 1: Extend Custom Vocabulary ---")
        added = extend_custom_vocab(visual_names_sorted)
        print(f"  Added {added} new terms to custom vocab {CUSTOM_VOCAB_ID}.")

        # Step 2: Insert dcterms:relation values
        print("\n--- Step 2: dcterms:relation values ---")
        existing = check_existing_references()
        if existing:
            print(f"  {len(existing)} items already tagged — skipping those.")
        total_inserted = insert_relation_values(all_results, existing)
        print(f"  Inserted {total_inserted} relation values.")

        # Step 3: Update Cultural Reference facet
        print("\n--- Step 3: Update Cultural Reference Facet ---")
        # Read current vocab terms (now includes new ones) for the facet
        sql = f"SELECT terms FROM custom_vocab WHERE id = {CUSTOM_VOCAB_ID};"
        out = db_query(sql).strip().split("\n")
        if len(out) >= 2:
            try:
                all_terms = json.loads(out[1].strip())
                update_facet_values(FACET_ID_CULTURAL, all_terms)
                print(f"  Updated Cultural Reference facet with {len(all_terms)} total values.")
            except json.JSONDecodeError:
                print("  WARNING: Could not read vocab terms for facet update.")

        print(f"\nDone. Run: docker compose restart omeka")
    else:
        print("\nDry run complete. Use --apply to write results to DB.")
        print("Use --cross-media to generate cross-media reports.")


if __name__ == "__main__":
    main()
