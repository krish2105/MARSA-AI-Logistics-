"""Deterministic synthetic corpora.

Why this exists
---------------
Phases B–F (indexing, graph construction, ML, the router, the evaluation
harness) all need data with the right *shape* to be built and tested. Waiting
on network access to start them would serialise the whole project behind one
blocker.

So: seeded generators producing records that validate against the exact same
schemas as the live ingestors, letting every downstream phase be developed and
unit-tested offline.

Two hard rules, enforced rather than documented:

1. Every record carries ``origin=SYNTHETIC``. Nothing downstream can mistake
   these for real data, and the manifest says so in bold.
2. **No benchmark number in RESULTS.md may ever be computed from this data.**
   Fixtures prove the pipeline runs; they cannot prove the router works. The
   evaluation harness refuses to publish figures from a synthetic corpus.

The DataCo generator embeds a genuinely learnable late-delivery signal (mode,
scheduled window, corridor, and a congestion interaction) with realistic label
noise, so Phase D's LogReg/XGBoost/LightGBM comparison exercises real model
behaviour instead of fitting pure noise.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

from marsa.ingestion.schemas import (
    ComtradeFlow,
    CountryLogistics,
    CrossRuling,
    DataCoOrder,
    Origin,
    PortPerformance,
    Provenance,
    SourceKind,
)

SYNTHETIC_NOTE = (
    "SYNTHETIC FIXTURE — generated locally to unblock downstream development. "
    "Not real data. Must never be used to produce reported benchmark figures."
)


def _prov(source: SourceKind) -> Provenance:
    return Provenance(
        source=source,
        origin=Origin.SYNTHETIC,
        retrieved_at=datetime.now(UTC),
        note=SYNTHETIC_NOTE,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CROSS rulings
# ─────────────────────────────────────────────────────────────────────────────

_PRODUCTS = [
    ("lithium-ion power bank", "8507.60.0020", "electronics"),
    ("semiconductor light-emitting diode", "8541.41.0000", "electronics"),
    ("printed circuit assembly", "8534.00.0000", "electronics"),
    ("cellular smartphone", "8517.13.0000", "electronics"),
    ("brushless DC electric motor", "8501.31.4000", "machinery"),
    ("air conditioning unit, split system", "8415.10.9000", "machinery"),
    ("centrifugal pump, industrial", "8413.70.2004", "machinery"),
    ("knitted cotton t-shirt", "6109.10.0012", "textiles"),
    ("woven polyester trousers", "6203.43.4010", "textiles"),
    ("leather upper footwear", "6403.99.9065", "textiles"),
    ("passenger vehicle brake pad", "8708.30.5090", "vehicles"),
    ("wooden office desk", "9403.30.8000", "furniture"),
    ("LED luminaire, ceiling-mounted", "9405.11.4010", "furniture"),
    ("stainless steel vacuum flask", "9617.00.1000", "household"),
]

_REASONING = [
    "The article's principal function is {function}, which governs classification "
    "under GRI 1. Competing headings covering {alternative} were considered and "
    "rejected because the {feature} is subsidiary to that principal function.",
    "Applying GRI 3(b), the essential character of the composite good is imparted "
    "by the {feature}. The presence of {alternative} does not alter that analysis, "
    "as it serves only to {function}.",
    "Classification turns on whether the article is 'parts of general use' within "
    "Section XV Note 2. Because the {feature} is dedicated solely to {function}, "
    "it falls outside that exclusion and is classified with the machine it serves.",
]

_FUNCTIONS = [
    "energy storage", "signal conversion", "mechanical transmission",
    "thermal regulation", "protection of the enclosed components",
]
_FEATURES = [
    "integrated charging circuit", "moulded polymer housing",
    "rectifying junction", "textile outer surface", "bearing assembly",
]
_ALTERNATIVES = [
    "static converters of heading 8504", "parts of heading 8548",
    "articles of plastics under Chapter 39", "made-up textile articles of Chapter 63",
]


def generate_cross_rulings(count: int = 400, *, seed: int = 42) -> Iterator[CrossRuling]:
    rng = random.Random(seed)
    provenance = _prov(SourceKind.CROSS)

    # Pre-allocate numbers so rulings can cite each other — those citations are
    # what the graph path later turns into `classified_under` / cross-ref edges.
    numbers = [
        f"{'NY' if rng.random() < 0.7 else 'HQ'} "
        f"{'N' if rng.random() < 0.8 else 'H'}{rng.randint(100000, 999999)}"
        for _ in range(count)
    ]

    for i in range(count):
        product, hts, category = _PRODUCTS[i % len(_PRODUCTS)]
        number = numbers[i]
        variant = rng.choice(["", " with integrated controller", " in retail packaging",
                              " imported in bulk", ", unassembled"])
        origin_country = rng.choice(["China", "India", "Vietnam", "Korea", "Germany"])
        subject = f"Tariff classification of {product}{variant} from {origin_country}"

        # Clamp to the pool: `rng.sample` raises when k exceeds the population,
        # so generating a tiny corpus (count <= 3) would crash outright.
        pool = [n for n in numbers if n != number]
        cited = rng.sample(pool, k=min(rng.randint(0, 3), len(pool)))
        reasoning = rng.choice(_REASONING).format(
            function=rng.choice(_FUNCTIONS),
            feature=rng.choice(_FEATURES),
            alternative=rng.choice(_ALTERNATIVES),
        )
        citation_text = (
            f" This determination is consistent with {', '.join(cited)}."
            if cited
            else ""
        )

        body = (
            f"DESCRIPTION OF MERCHANDISE:\n"
            f"The merchandise under consideration is a {product}{variant}. "
            f"The item is imported for retail distribution and re-export.\n\n"
            f"ISSUE:\nWhat is the correct classification of the {product}?\n\n"
            f"LAW AND ANALYSIS:\n{reasoning}{citation_text}\n\n"
            f"HOLDING:\nThe applicable subheading for the {product} will be "
            f"{hts}. The general rate of duty will be "
            f"{rng.choice(['free', '2.5%', '3.7%', '6.5%', '9%'])} ad valorem."
        )

        yield CrossRuling(
            ruling_number=number,
            collection="NY" if number.startswith("NY") else "HQ",
            ruling_date=date(2019, 1, 1) + timedelta(days=rng.randint(0, 2100)),
            subject=subject,
            body=body,
            hts_codes=[hts],
            related_rulings=cited,
            category=category,
            url=f"https://rulings.cbp.gov/ruling/{number.replace(' ', '')}",
            provenance=provenance,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Comtrade flows
# ─────────────────────────────────────────────────────────────────────────────

_COUNTRIES = {
    784: ("ARE", "United Arab Emirates"),
    156: ("CHN", "China"),
    699: ("IND", "India"),
    840: ("USA", "USA"),
    276: ("DEU", "Germany"),
    392: ("JPN", "Japan"),
    410: ("KOR", "Republic of Korea"),
    702: ("SGP", "Singapore"),
    826: ("GBR", "United Kingdom"),
}
_CHAPTERS = {
    "85": "Electrical machinery and equipment",
    "84": "Machinery, mechanical appliances",
    "62": "Apparel, not knitted",
    "61": "Apparel, knitted",
    "87": "Vehicles other than railway",
    "94": "Furniture, bedding, lamps",
}


def generate_comtrade_flows(
    *, seed: int = 42, years: tuple[int, ...] = (2021, 2022, 2023)
) -> Iterator[ComtradeFlow]:
    rng = random.Random(seed)
    provenance = _prov(SourceKind.COMTRADE)
    reporter = 784  # UAE

    for year in years:
        for partner, (iso, name) in _COUNTRIES.items():
            if partner == reporter:
                continue
            for chapter, chapter_desc in _CHAPTERS.items():
                for flow, flow_desc in (("M", "Import"), ("X", "Export")):
                    # Log-normal-ish magnitudes: trade values are heavy-tailed,
                    # and a uniform draw would make every partner look alike.
                    base = rng.lognormvariate(17.5, 1.3)
                    growth = 1.0 + (year - 2021) * rng.uniform(-0.08, 0.22)
                    value = base * growth * (1.0 if flow == "M" else rng.uniform(0.3, 0.9))

                    yield ComtradeFlow(
                        period=str(year),
                        ref_year=year,
                        reporter_code=reporter,
                        reporter_iso="ARE",
                        reporter_desc="United Arab Emirates",
                        partner_code=partner,
                        partner_iso=iso,
                        partner_desc=name,
                        flow_code=flow,
                        flow_desc=flow_desc,
                        cmd_code=chapter,
                        cmd_desc=chapter_desc,
                        primary_value=round(value, 2),
                        net_weight_kg=round(value / rng.uniform(3, 40), 1),
                        qty=round(value / rng.uniform(50, 400), 1),
                        qty_unit="kg",
                        provenance=provenance,
                    )


# ─────────────────────────────────────────────────────────────────────────────
# DataCo orders
# ─────────────────────────────────────────────────────────────────────────────

_SHIPPING_MODES = {
    "Standard Class": 0.0,
    "Second Class": 0.35,
    "First Class": 0.62,
    "Same Day": 0.80,
}
_MARKETS = ["Africa", "Europe", "LATAM", "Pacific Asia", "USCA"]
# Market → region → (country, city). Nested rather than sampled independently:
# drawing each level separately produced records like "Alemania / Mumbai / East
# Africa", which build a nonsense supply graph in Phase C. Country names are
# Spanish because that is how the real DataCo dataset stores them.
_GEOGRAPHY: dict[str, dict[str, list[tuple[str, str]]]] = {
    "Africa": {
        "North Africa": [("Egipto", "Cairo"), ("Marruecos", "Casablanca")],
        "West Africa": [("Nigeria", "Lagos"), ("Ghana", "Accra")],
        "East Africa": [("Kenia", "Nairobi"), ("Etiopía", "Addis Abeba")],
    },
    "Europe": {
        "Western Europe": [("Alemania", "Berlin"), ("Francia", "Paris")],
        "Northern Europe": [("Reino Unido", "London"), ("Países Bajos", "Rotterdam")],
        "Southern Europe": [("España", "Madrid"), ("Italia", "Milan")],
    },
    "LATAM": {
        "Central America": [("México", "Ciudad de Mexico"), ("Honduras", "Tegucigalpa")],
        "South America": [("Brasil", "Sao Paulo"), ("Argentina", "Buenos Aires")],
        "Caribbean": [("República Dominicana", "Santo Domingo"), ("Cuba", "La Habana")],
    },
    "Pacific Asia": {
        "Southeast Asia": [("Vietnam", "Ho Chi Minh"), ("Singapur", "Singapore")],
        "Eastern Asia": [("China", "Shanghai"), ("Japón", "Tokyo")],
        "South Asia": [("India", "Mumbai"), ("Pakistán", "Karachi")],
        "Oceania": [("Australia", "Sydney")],
    },
    "USCA": {
        "West of USA": [("Estados Unidos", "Los Angeles")],
        "US Center": [("Estados Unidos", "Chicago")],
        "East of USA": [("Estados Unidos", "New York")],
        "Canada": [("Canadá", "Toronto")],
    },
}
_CATEGORIES = [
    ("Electronics", "Technology"), ("Cameras", "Technology"),
    ("Cleats", "Footwear"), ("Men's Footwear", "Footwear"),
    ("Shop By Sport", "Apparel"), ("Women's Apparel", "Apparel"),
    ("Garden", "Outdoors"), ("Fishing", "Outdoors"),
]
# Corridors with structurally worse reliability — gives the graph path a real
# congestion signal to propagate rather than uniform risk.
_CONGESTED_REGIONS = {"South Asia", "West Africa", "Caribbean"}


def generate_dataco_orders(count: int = 5000, *, seed: int = 42) -> Iterator[DataCoOrder]:
    rng = random.Random(seed)
    provenance = _prov(SourceKind.DATACO)
    start = datetime(2021, 1, 1, tzinfo=UTC)

    for i in range(count):
        mode = rng.choice(list(_SHIPPING_MODES))
        market = rng.choice(_MARKETS)
        region = rng.choice(list(_GEOGRAPHY[market]))
        origin_country, origin_city = rng.choice(_GEOGRAPHY[market][region])
        category, department = rng.choice(_CATEGORIES)

        scheduled = {"Same Day": 0, "First Class": 1, "Second Class": 2, "Standard Class": 4}[mode]

        # Latent risk: expedited modes are tighter and fail more often; a short
        # scheduled window leaves no slack; congested corridors add pressure.
        logit = (
            -1.15
            + 2.4 * _SHIPPING_MODES[mode]
            + (0.85 if scheduled <= 1 else 0.0)
            + (0.65 if region in _CONGESTED_REGIONS else 0.0)
            + rng.gauss(0, 0.55)  # irreducible noise — keeps AUC realistic
        )
        probability = 1 / (1 + pow(2.718281828, -logit))
        late = 1 if rng.random() < probability else 0

        real_days = scheduled + (rng.randint(1, 4) if late else rng.randint(-1, 0))
        real_days = max(0, real_days)

        order_date = start + timedelta(days=rng.randint(0, 900), hours=rng.randint(0, 23))

        yield DataCoOrder(
            order_id=100000 + i,
            order_item_id=1 + (i % 5),
            order_date=order_date,
            shipping_date=order_date + timedelta(days=real_days),
            shipping_mode=mode,
            days_for_shipping_real=float(real_days),
            days_for_shipment_scheduled=float(scheduled),
            late_delivery_risk=late,
            delivery_status="Late delivery" if late else "Shipping on time",
            customer_id=rng.randint(1, 1200),
            customer_country=rng.choice(["EE. UU.", "Puerto Rico", "France", "Mexico"]),
            customer_city=rng.choice(["Caguas", "Chicago", "Los Angeles", "Tegucigalpa"]),
            customer_segment=rng.choice(["Consumer", "Corporate", "Home Office"]),
            product_card_id=rng.randint(1, 120),
            product_name=f"{category} item {rng.randint(1, 400)}",
            category_name=category,
            department_name=department,
            order_country=origin_country,
            order_city=origin_city,
            order_region=region,
            market=market,
            order_item_quantity=rng.randint(1, 5),
            sales=round(rng.lognormvariate(4.6, 0.7), 2),
            order_profit_per_order=round(rng.gauss(22, 60), 2),
            provenance=provenance,
        )


# ─────────────────────────────────────────────────────────────────────────────
# World Bank LPI + ports
# ─────────────────────────────────────────────────────────────────────────────

_LPI_COUNTRIES = {
    "ARE": ("United Arab Emirates", 4.05), "SGP": ("Singapore", 4.30),
    "DEU": ("Germany", 4.20), "NLD": ("Netherlands", 4.15),
    "CHN": ("China", 3.70), "USA": ("USA", 3.90), "GBR": ("United Kingdom", 3.85),
    "JPN": ("Japan", 4.00), "KOR": ("Republic of Korea", 3.80),
    "IND": ("India", 3.40), "SAU": ("Saudi Arabia", 3.30), "OMN": ("Oman", 3.20),
    "QAT": ("Qatar", 3.35), "TUR": ("Turkey", 3.45), "EGY": ("Egypt", 3.00),
    "ZAF": ("South Africa", 3.25), "BRA": ("Brazil", 3.10), "NGA": ("Nigeria", 2.60),
}

_PORTS = [
    ("Jebel Ali", "AEJEA", "ARE", 12, 14.2e6, 21.4),
    ("Khalifa Port", "AEKHL", "ARE", 24, 4.5e6, 24.1),
    ("Singapore", "SGSIN", "SGP", 5, 37.3e6, 18.2),
    ("Shanghai", "CNSHA", "CHN", 8, 47.0e6, 19.6),
    ("Ningbo", "CNNGB", "CHN", 3, 33.4e6, 17.1),
    ("Rotterdam", "NLRTM", "NLD", 30, 13.4e6, 26.3),
    ("Hamburg", "DEHAM", "DEU", 55, 8.3e6, 31.8),
    ("Los Angeles", "USLAX", "USA", 340, 9.9e6, 62.4),
    ("Nhava Sheva", "INNSA", "IND", 26, 6.4e6, 25.0),
    ("Busan", "KRPUS", "KOR", 18, 22.8e6, 22.7),
    ("Tanger Med", "MAPTM", "MAR", 4, 7.5e6, 16.9),
    ("Salalah", "OMSLL", "OMN", 15, 4.3e6, 20.8),
]


def generate_country_logistics(
    *, seed: int = 42, years: tuple[int, ...] = (2023, 2025)
) -> Iterator[CountryLogistics]:
    rng = random.Random(seed)
    provenance = _prov(SourceKind.WORLDBANK)

    def jitter(around: float) -> float:
        """Sub-score around the country's baseline.

        Takes the baseline as an argument rather than closing over the loop
        variable — a closure here would bind the *name*, so every country would
        silently score against whichever baseline the loop happened to end on.
        """
        return round(around + rng.uniform(-0.35, 0.35), 2)

    for year in years:
        for iso, (name, base) in _LPI_COUNTRIES.items():
            # Dwell time is inversely related to LPI — stronger logistics, less
            # time sitting at the border. This is the graph's edge weight.
            dwell = max(0.6, round(9.5 - base * 1.75 + rng.uniform(-0.6, 0.9), 2))
            yield CountryLogistics(
                country_iso3=iso,
                country_name=name,
                year=year,
                lpi_score=round(base + rng.uniform(-0.12, 0.12), 2),
                lpi_rank=None,
                customs_score=jitter(base),
                infrastructure_score=jitter(base),
                international_shipments_score=jitter(base),
                logistics_competence_score=jitter(base),
                tracking_tracing_score=jitter(base),
                timeliness_score=jitter(base),
                import_dwell_days=dwell,
                export_dwell_days=max(0.4, round(dwell * rng.uniform(0.6, 0.9), 2)),
                provenance=provenance,
            )


def generate_ports(*, seed: int = 42, year: int = 2024) -> Iterator[PortPerformance]:
    rng = random.Random(seed)
    provenance = _prov(SourceKind.WORLDBANK)
    for name, unlocode, iso, rank, teu, hours in _PORTS:
        yield PortPerformance(
            port_name=name,
            unlocode=unlocode,
            country_iso3=iso,
            year=year,
            cppi_rank=rank,
            cppi_score=round(rng.uniform(-1.5, 2.5), 3),
            annual_teu=teu,
            avg_vessel_hours=hours,
            provenance=provenance,
        )
