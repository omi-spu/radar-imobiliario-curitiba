"""Innova's public property pages, using listing JSON-LD and visible labels."""

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


class InnovaCollector(BaseCollector):
    slug = "innova"
    name = "Innova Imobiliária"
    base_url = "https://www.innovaimobiliaria.com.br"
    sitemap_urls = (f"{base_url}/sitemap.xml",)

    def accepts_url(self, url: str) -> bool:
        slug = url.rsplit("/", 1)[-1].casefold()
        return (
            url.startswith(f"{self.base_url}/imovel/")
            and "curitiba" in slug
            and any(word in slug for word in ("casa", "sobrado", "triplex"))
            and any(word in slug for word in ("venda", "comprar"))
            and "alugar" not in slug
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
        offer = listing.get("offers") or {}
        price = parse_brl(str(offer.get("price") or ""))
        title_tag = soup.select_one("h1[class*='propertyTitle']")
        title = clean_text(title_tag.get_text(" ", strip=True) if title_tag else listing.get("name"))
        code_tag = soup.select_one("[class*='propertyCode']")
        code_text = code_tag.get_text(" ", strip=True) if code_tag else url
        code_match = re.search(r"(?:Cod\.\s*|cod-)([\w.-]+)", code_text, re.I)
        if not title or not price or not code_match:
            return None

        features: dict[str, str] = {}
        for item in soup.select("div[class*='featureItem']"):
            label = item.find("p")
            value = item.select_one("[class*='featureValue']")
            if label and value:
                features[fold(label.get_text(" ", strip=True))] = value.get_text(" ", strip=True)

        address_data = listing.get("address") or {}
        street = clean_text(address_data.get("streetAddress")) or None
        neighborhood = clean_text(address_data.get("addressLocality")) or None
        region = fold(address_data.get("addressRegion"))
        if "curitiba" not in region and "curitiba" not in fold(url):
            return None

        description = listing.get("description") or ""
        built = parse_area(features.get("area util") or features.get("area construida"))
        if built is None:
            match = re.search(r"([\d.,]+)\s*m[²2]\s+de\s+[áa]rea constru[íi]da", description, re.I)
            built = parse_area(match.group(1)) if match else None
        land = parse_area(features.get("area do terreno") or features.get("area terreno"))
        if land is None:
            match = re.search(r"terreno\s+(?:de|com)\s+(?:aproximadamente\s+)?([\d.,]+)\s*m[²2]", description, re.I)
            land = parse_area(match.group(1)) if match else None

        availability = fold(offer.get("availability"))
        return ListingCandidate(
            source_slug=self.slug,
            source_ref=code_match.group(1),
            url=url,
            title=title,
            subtype=classify_subtype(title),
            neighborhood=neighborhood,
            address=street,
            address_precision=address_precision(street),
            price_cents=price,
            bedrooms=parse_int(features.get("quartos") or features.get("dormitorios")),
            bathrooms=parse_int(features.get("banheiros")),
            built_area_m2=built,
            land_area_m2=land,
            reserved=(
                "outofstock" in availability
                or "reserved" in availability
                or soup.select_one("div[class*='reservedOverlay']") is not None
            ),
        )
