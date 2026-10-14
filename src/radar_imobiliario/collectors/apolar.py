"""Apolar's JavaScript-rendered public property pages."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from playwright.sync_api import Browser, Playwright, sync_playwright

from radar_imobiliario.collectors.base import BaseCollector, SourceBlocked, USER_AGENT
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


class ApolarCollector(BaseCollector):
    slug = "apolar"
    name = "Apolar"
    base_url = "https://www.apolar.com.br"
    sitemap_urls = (f"{base_url}/sitemap.xml",)

    def __init__(self, delay_seconds: float = 1.0):
        super().__init__(delay_seconds)
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.page = None

    def start(self) -> None:
        super().start()
        self.playwright = sync_playwright().start()
        try:
            self.browser = self.playwright.chromium.launch(channel="msedge", headless=True)
            self.page = self.browser.new_page(user_agent=USER_AGENT, locale="pt-BR")
            self.page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in {"image", "font", "media"}
                else route.continue_(),
            )
        except Exception:
            self.playwright.stop()
            self.playwright = None
            raise

    def close(self) -> None:
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        super().close()

    def accepts_url(self, url: str) -> bool:
        slug = url.casefold()
        return (
            slug.startswith(f"{self.base_url}/venda/curitiba/")
            and "-residencial-" in slug
            and any(f"-{kind}-curitiba-" in slug for kind in ("casa", "sobrado"))
        )

    def fetch_listing(self, url: str) -> str:
        if not self.fetcher.allowed(url):
            raise SourceBlocked(f"Acesso não permitido por robots.txt: {url}")
        if self.page is None:
            raise RuntimeError("Navegador da Apolar não iniciado")
        self.fetcher.wait_turn()
        response = self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        if response and response.status in (401, 403, 429):
            raise SourceBlocked(f"HTTP {response.status}: {url}")
        reference = url.rstrip("/").rsplit("-", 1)[-1]
        try:
            self.page.wait_for_function(
                "ref => document.body.innerText.includes(ref) && "
                "!!document.querySelector('.price-new') && "
                "!document.querySelector('.price-new').innerText.includes('R$ 0,00')",
                arg=reference,
                timeout=20_000,
            )
        except Exception as exc:
            raise RuntimeError(f"Anúncio não carregou na Apolar: {url}") from exc
        return self.page.content()

    def parse_listing(self, url: str, html: str) -> ListingCandidate | None:
        soup = BeautifulSoup(html, "html.parser")
        title_tag = soup.select_one(".property-title")
        reference_tag = soup.select_one(".property-title .property-ref")
        price_tag = soup.select_one(".price-box .price-new")
        address_tag = soup.select_one(".property-address")
        if not title_tag or not reference_tag or not price_tag:
            return None
        reference = parse_int(reference_tag.get_text(" ", strip=True))
        price = parse_brl(price_tag.get_text(" ", strip=True))
        title = clean_text(title_tag.get_text(" ", strip=True))
        title = re.sub(r"\s*ref\.\s*\d+\s*$", "", title, flags=re.I)
        if not reference or not price or not title:
            return None

        details: dict[str, str] = {}
        for item in soup.select("ul.property-details li"):
            strong = item.find("strong")
            if strong:
                label = item.get_text(" ", strip=True).split(":", 1)[0]
                details[fold(label)] = strong.get_text(" ", strip=True)
        if fold(details.get("cidade")) != "curitiba":
            return None

        full_address = clean_text(address_tag.get_text(" ", strip=True)) if address_tag else ""
        neighborhood = None
        if address_tag and address_tag.find("span"):
            neighborhood = clean_text(address_tag.find("span").get_text(" ", strip=True)).rstrip(" -") or None
        street_parts = full_address.split(",")
        address = clean_text(", ".join(part.strip() for part in street_parts[:2])) if len(street_parts) >= 2 else full_address or None
        description = soup.select_one(".description")
        summary = clean_text(description.get_text(" ", strip=True))[:180] if description else ""
        bathrooms = None
        for image in soup.select(".property-highlights img.bathrooms"):
            bathrooms = parse_int(image.get("alt"))
            if bathrooms is not None:
                break
        return ListingCandidate(
            source_slug=self.slug,
            source_ref=str(reference),
            url=url,
            title=title,
            subtype=classify_subtype(title + " " + summary),
            neighborhood=neighborhood,
            address=address,
            address_precision=address_precision(address),
            price_cents=price,
            bedrooms=parse_int(details.get("dormitorios")),
            bathrooms=bathrooms,
            built_area_m2=parse_area(details.get("area util") or details.get("area construida")),
            land_area_m2=parse_area(details.get("area terreno") or details.get("area do terreno")),
            reserved="reservado" in fold(title),
        )
