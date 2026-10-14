"""Shared parsing and conservative matching rules."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import re
import unicodedata


@dataclass(frozen=True)
class ListingCandidate:
    source_slug: str
    source_ref: str
    url: str
    title: str
    subtype: str
    neighborhood: str | None
    address: str | None
    address_precision: str
    price_cents: int
    bedrooms: int | None
    bathrooms: int | None
    built_area_m2: float | None
    land_area_m2: float | None
    reserved: bool = False


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def fold(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", clean_text(value))
    return "".join(c for c in normalized if not unicodedata.combining(c)).casefold()


def parse_brl(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(?:R\$\s*)?([\d.]+(?:,\d{1,2})?)", value)
    if not match:
        return None
    raw = match.group(1)
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif raw.count(".") > 1 or ("." in raw and len(raw.rsplit(".", 1)[1]) == 3):
        raw = raw.replace(".", "")
    try:
        amount = Decimal(raw)
        return int(amount * 100) if amount > 0 else None
    except InvalidOperation:
        return None


def parse_area(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"(\d[\d.]*,?\d*)\s*(?:m²|m2|metros? quadrados?)?", value, re.I)
    if not match:
        return None
    raw = match.group(1)
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif raw.count(".") > 1 or ("." in raw and len(raw.rsplit(".", 1)[1]) == 3):
        raw = raw.replace(".", "")
    try:
        area = float(raw)
        return area if 0 < area < 100_000 else None
    except ValueError:
        return None


def parse_int(value: str | None) -> int | None:
    match = re.search(r"\d+", value or "")
    return int(match.group()) if match else None


def classify_subtype(title: str) -> str:
    lowered = fold(title)
    if "condominio" in lowered:
        return "condominio"
    if "sobrado" in lowered or "triplex" in lowered:
        return "sobrado"
    return "casa"


def address_precision(address: str | None) -> str:
    if not address:
        return "bairro"
    lowered = fold(address)
    if re.search(r"\b(?:rua|r\.?|avenida|av\.?|travessa|estrada|rodovia|alameda)\b", lowered):
        return "numero" if re.search(r",\s*\d{1,5}\b", lowered) else "rua"
    return "bairro"


def property_key(candidate: ListingCandidate) -> str | None:
    """Only group different agencies for a numbered non-condominium address."""
    if candidate.address_precision != "numero" or candidate.subtype == "condominio":
        return None
    if candidate.built_area_m2 is None or candidate.bedrooms is None or candidate.bathrooms is None:
        return None
    canonical_address = fold(candidate.address)
    canonical_address = re.sub(r"\b(?:rua|r\.|avenida|av\.|travessa|estrada|rodovia|alameda)\b", "", canonical_address)
    canonical_address = re.sub(r"[^a-z0-9]+", " ", canonical_address).strip()
    payload = "|".join(
        [
            fold(candidate.neighborhood),
            canonical_address,
            candidate.subtype,
            str(candidate.bedrooms),
            str(candidate.bathrooms),
            str(round(candidate.built_area_m2)),
            str(round(candidate.land_area_m2)) if candidate.land_area_m2 is not None else "?",
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()
