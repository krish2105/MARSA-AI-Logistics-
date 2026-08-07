"""Cross-corpus join tables.

The four corpora share no keys. CROSS speaks HTS codes, Comtrade speaks UN M49
numeric country codes, DataCo speaks Spanish country names and merchandising
categories, and the World Bank speaks ISO3 plus UN/LOCODE. Connecting them is
the actual work of graph construction, and every join below is a *decision*
this project is making rather than a fact it is reading.

They live here, in one file, so the honest-limitations section can be generated
from code instead of remembered — and so that a reviewer can audit every
assumption by reading a single module.
"""

from __future__ import annotations

import unicodedata

# ─────────────────────────────────────────────────────────────────────────────
# DataCo country names → ISO3
#
# The real DataCo dataset stores country names in Spanish. Without this map,
# every shipment is an island: it cannot reach a Comtrade flow or an LPI score.
# Unmatched names are dropped rather than guessed — a wrong country edge is
# worse than a missing one, because it silently produces a confident answer
# about a corridor that does not exist.
# ─────────────────────────────────────────────────────────────────────────────

COUNTRY_NAME_TO_ISO3: dict[str, str] = {
    # Spanish (DataCo's own spelling)
    "estados unidos": "USA",
    "ee. uu.": "USA",
    "eeuu": "USA",
    "alemania": "DEU",
    "francia": "FRA",
    "reino unido": "GBR",
    "paises bajos": "NLD",
    "holanda": "NLD",
    "espana": "ESP",
    "italia": "ITA",
    "mexico": "MEX",
    "brasil": "BRA",
    "argentina": "ARG",
    "honduras": "HND",
    "republica dominicana": "DOM",
    "cuba": "CUB",
    "china": "CHN",
    "japon": "JPN",
    "india": "IND",
    "pakistan": "PAK",
    "vietnam": "VNM",
    "singapur": "SGP",
    "australia": "AUS",
    "canada": "CAN",
    "nigeria": "NGA",
    "ghana": "GHA",
    "kenia": "KEN",
    "etiopia": "ETH",
    "egipto": "EGY",
    "marruecos": "MAR",
    "corea del sur": "KOR",
    "turquia": "TUR",
    "sudafrica": "ZAF",
    "arabia saudita": "SAU",
    "emiratos arabes unidos": "ARE",
    "puerto rico": "PRI",
    # English, for corpora that are not localised
    "united states": "USA",
    "usa": "USA",
    "germany": "DEU",
    "france": "FRA",
    "united kingdom": "GBR",
    "netherlands": "NLD",
    "spain": "ESP",
    "italy": "ITA",
    "japan": "JPN",
    "south korea": "KOR",
    "republic of korea": "KOR",
    "singapore": "SGP",
    "united arab emirates": "ARE",
    "saudi arabia": "SAU",
    "south africa": "ZAF",
    "turkey": "TUR",
    "egypt": "EGY",
    "morocco": "MAR",
    "kenya": "KEN",
    "ethiopia": "ETH",
    "brazil": "BRA",
    "mexico ": "MEX",
    "dominican republic": "DOM",
}

# UN M49 numeric (Comtrade) → ISO3, for the reporters/partners actually pulled.
M49_TO_ISO3: dict[int, str] = {
    784: "ARE", 156: "CHN", 699: "IND", 840: "USA", 276: "DEU",
    392: "JPN", 410: "KOR", 702: "SGP", 826: "GBR", 682: "SAU",
    512: "OMN", 634: "QAT", 414: "KWT", 48: "BHR", 528: "NLD",
    56: "BEL", 792: "TUR", 818: "EGY", 710: "ZAF", 76: "BRA",
}


def normalise_country(name: str) -> str:
    """Strip accents and case so 'Japón' and 'Japon' resolve identically."""
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


def country_to_iso3(name: str | None) -> str | None:
    """Resolve a country name to ISO3, or None if it is not in the lookup."""
    if not name:
        return None
    key = normalise_country(name)
    if key in COUNTRY_NAME_TO_ISO3:
        return COUNTRY_NAME_TO_ISO3[key]
    # Already an ISO3 code?
    if len(key) == 3 and key.isalpha():
        return key.upper()
    return None


# ─────────────────────────────────────────────────────────────────────────────
# DataCo merchandising category → HS chapter
#
# This is an *editorial* mapping, not a customs classification. A broker
# classifies per article under the General Rules of Interpretation; DataCo's
# categories are retail merchandising buckets. The mapping exists so shipment
# data can reach the CROSS ruling corpus at all, and any answer that leans on
# it should say so — which the narrator does.
# ─────────────────────────────────────────────────────────────────────────────

CATEGORY_TO_HS_CHAPTER: dict[str, str] = {
    "electronics": "85",
    "cameras": "90",
    "computers": "84",
    "consumer electronics": "85",
    "music": "85",
    "cleats": "64",
    "men's footwear": "64",
    "women's footwear": "64",
    "shop by sport": "61",
    "women's apparel": "62",
    "men's clothing": "62",
    "children's clothing": "62",
    "garden": "82",
    "fishing": "95",
    "camping & hiking": "63",
    "indoor/outdoor games": "95",
    "water sports": "95",
    "fitness accessories": "95",
    "golf": "95",
    "furniture": "94",
    "book shop": "49",
    "pet supplies": "23",
    "health and beauty": "33",
}


def category_to_chapter(category: str | None) -> str | None:
    if not category:
        return None
    return CATEGORY_TO_HS_CHAPTER.get(category.strip().lower())


def hts_to_chapter(code: str | None) -> str | None:
    """First two digits of an HTS code are its HS chapter. Definitional."""
    if not code:
        return None
    digits = code.strip().replace(".", "")
    return digits[:2] if len(digits) >= 2 and digits[:2].isdigit() else None


# ─────────────────────────────────────────────────────────────────────────────
# Country → gateway port
#
# DataCo has no port field, so shipments would never touch the port network —
# which is precisely the network the graph path exists to reason about. Each
# country is linked to its best-ranked CPPI port as an assumed gateway.
#
# The distortion is real and worth naming: countries genuinely use many ports,
# and transhipment through a third country is routine in this trade. Collapsing
# to one gateway *overstates* how concentrated exposure is at that port, which
# is the direction that flatters the analysis, so it must be disclosed rather
# than buried.
# ─────────────────────────────────────────────────────────────────────────────


def build_gateway_map(ports: list) -> dict[str, str]:
    """Pick one gateway port per country: best (lowest) CPPI rank wins."""
    best: dict[str, tuple[int, str]] = {}
    for port in ports:
        iso3 = (port.country_iso3 or "").upper()
        if not iso3 or not port.unlocode:
            continue
        # Unranked ports sort last rather than being treated as rank 0.
        rank = port.cppi_rank if port.cppi_rank is not None else 10_000
        current = best.get(iso3)
        if current is None or rank < current[0]:
            best[iso3] = (rank, port.unlocode)
    return {iso3: unlocode for iso3, (_, unlocode) in best.items()}
