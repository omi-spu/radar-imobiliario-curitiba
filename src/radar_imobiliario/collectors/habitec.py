"""Habitec's public property pages and structured listing metadata."""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from radar_imobiliario.collectors.base import BaseCollector
from radar_imobiliario.normalization import (
    ListingCandidate,
    address_precision,
    classify_subtype,
    clean_text,
    fold,
    parse_area,
    parse_brl,
    parse_int,
)


class HabitecCollector(BaseCollector):
    slug = "habitec"
    name = "Habitec Imóveis"
    base_url = "https://habitec.com.br"
    sitemap_urls = (f"{base_url}/sitemap-imoveis-1.xml",)

    def accepts_url(self, url: str) -> bool:
        slug = url.casefold()
        return (
            slug.startswith(f"{self.base_url}/imovel/comprar-")
            and "curitiba" in slug
            and any(word in slug for word in ("casa", "sobrado", "triplex"))
        )

    def parse_listing(self, url: str, html: str) -> ListingCandidate | None:
        soup = BeautifulSoup(html, "html.parser")
        listing = None
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "{}")
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("@type") == "RealEstateListing":
                listing = data
                break
        if not listing:
            return None
        entity = listing.get("mainEntity") or {}
        offer = listing.get("offers") or {}
        price = parse_brl(str(offer.get("price") or ""))
        title = clean_text((soup.find("h1") or {}).get_text(" ", strip=True)) if soup.find("h1") else clean_text(listing.get("name"))
        code_tag = soup.select_one(".tipoCodigo")
        code_match = re.search(r"Ref:\s*([\w.-]+)", code_tag.get_text(" ", strip=True) if code_tag else "", re.I)
        if not title or not price or not code_match:
            return None
        address_data = entity.get("address") or {}
        if fold(address_data.get("addressLocality")) != "curitiba":
            return None
        address = clean_text(address_data.get("streetAddress")) or None
        city_tag = soup.select_one(".localizacao .cidade")
        location_text = clean_text(city_tag.get_text(" ", strip=True)) if city_tag else ""
        neighborhood = re.split(r"\s+-\s+Curitiba", location_text, flags=re.I)[0].strip() or None

        features: dict[str, str] = {}
        for p in soup.select(".caracteristicas p, .caracteristicas-imovel p, .lista-caracteristicas p"):
            strong = p.find("span", class_="fw-bold")
            if strong:
                label = p.get_text(" ", strip=True).split(":", 1)[0]
                features[fold(label)] = strong.get_text(" ", strip=True)
        # Habitec has changed container names, so fall back to the labeled span itself.
        if not features:
            for strong in soup.select("span.fw-bold"):
                parent = strong.find_parent("p")
                if parent and ":" in parent.get_text(" ", strip=True):
                    label = parent.get_text(" ", strip=True).split(":", 1)[0]
                    features.setdefault(fold(label), strong.get_text(" ", strip=True))

        floor = entity.get("floorSize") or {}
        built = parse_area(features.get("area privativa") or features.get("area util") or features.get("area construida"))
        if built is None and isinstance(floor, dict):
            built = parse_area(str(floor.get("value") or ""))
        availability = fold(offer.get("availability"))
        return ListingCandidate(
            source_slug=self.slug,
            source_ref=code_match.group(1),
            url=url,
            title=title,
            subtype=classify_subtype(title),
            neighborhood=neighborhood,
            address=address,
            address_precision=address_precision(address),
            price_cents=price,
            bedrooms=parse_int(str(entity.get("numberOfBedrooms") or features.get("quartos") or "")),
            bathrooms=parse_int(str(entity.get("numberOfBathroomsTotal") or features.get("banheiros") or "")),
            built_area_m2=built,
            land_area_m2=parse_area(features.get("area do terreno") or features.get("area do lote")),
            reserved="outofstock" in availability or "reserved" in availability,
        )
