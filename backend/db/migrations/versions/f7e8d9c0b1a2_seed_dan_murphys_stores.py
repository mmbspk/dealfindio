"""seed Dan Murphy's physical store locations

Revision ID: f7e8d9c0b1a2
Revises: 87632b64f9a1
Create Date: 2026-09-11 00:00:00.000000

Inserts a curated set of Dan Murphy's store locations with lat/lon so they
appear in the /stores/nearby endpoint. Only inserts rows that don't already
exist (matched by name + chain).
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "f7e8d9c0b1a2"
down_revision: str | None = "87632b64f9a1"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_STORES = [
    # New South Wales
    ("Dan Murphy's Alexandria",       "Alexandria",       "NSW", "2015", -33.9053,  151.1938),
    ("Dan Murphy's Bondi Junction",   "Bondi Junction",   "NSW", "2022", -33.8921,  151.2478),
    ("Dan Murphy's Chatswood",        "Chatswood",        "NSW", "2067", -33.7966,  151.1831),
    ("Dan Murphy's Parramatta",       "Parramatta",       "NSW", "2150", -33.8151,  151.0001),
    ("Dan Murphy's Penrith",          "Penrith",          "NSW", "2750", -33.7535,  150.6913),
    ("Dan Murphy's Newcastle",        "Newcastle",        "NSW", "2300", -32.9265,  151.7673),
    # Victoria
    ("Dan Murphy's Melbourne CBD",    "Melbourne",        "VIC", "3000", -37.8162,  144.9554),
    ("Dan Murphy's Richmond",         "Richmond",         "VIC", "3121", -37.8218,  145.0012),
    ("Dan Murphy's Chadstone",        "Chadstone",        "VIC", "3148", -37.8876,  145.0804),
    ("Dan Murphy's Ringwood",         "Ringwood",         "VIC", "3134", -37.8142,  145.2272),
    # Queensland
    ("Dan Murphy's Brisbane City",    "Brisbane City",    "QLD", "4000", -27.4728,  153.0233),
    ("Dan Murphy's Fortitude Valley", "Fortitude Valley", "QLD", "4006", -27.4567,  153.0387),
    ("Dan Murphy's Chermside",        "Chermside",        "QLD", "4032", -27.3867,  153.0287),
    ("Dan Murphy's Gold Coast",       "Southport",        "QLD", "4215", -27.9664,  153.3913),
    # Western Australia
    ("Dan Murphy's Perth CBD",        "Perth",            "WA",  "6000", -31.9543,  115.8611),
    ("Dan Murphy's Cannington",       "Cannington",       "WA",  "6107", -31.9997,  115.9360),
    # South Australia
    ("Dan Murphy's Adelaide CBD",     "Adelaide",         "SA",  "5000", -34.9295,  138.6002),
    ("Dan Murphy's Marion",           "Marion",           "SA",  "5043", -35.0143,  138.5637),
    ("Dan Murphy's Gepps Cross",      "Gepps Cross",      "SA",  "5094", -34.8312,  138.6021),
    # Australian Capital Territory
    ("Dan Murphy's Fyshwick",         "Fyshwick",         "ACT", "2609", -35.3278,  149.1695),
]


def upgrade() -> None:
    conn = op.get_bind()
    for name, suburb, state, postcode, lat, lon in _STORES:
        exists = conn.execute(
            sa.text(
                "SELECT 1 FROM stores WHERE chain = 'dan_murphys' AND name = :name LIMIT 1"
            ),
            {"name": name},
        ).scalar()
        if not exists:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO stores (id, chain, name, suburb, state, postcode, lat, lon, is_active)
                    VALUES (gen_random_uuid(), 'dan_murphys', :name, :suburb, :state, :postcode,
                            :lat, :lon, true)
                    """
                ),
                {
                    "name": name,
                    "suburb": suburb,
                    "state": state,
                    "postcode": postcode,
                    "lat": lat,
                    "lon": lon,
                },
            )


def downgrade() -> None:
    conn = op.get_bind()
    names = [name for name, *_ in _STORES]
    conn.execute(
        sa.text(
            "DELETE FROM stores WHERE chain = 'dan_murphys' AND name = ANY(:names)"
        ),
        {"names": names},
    )
