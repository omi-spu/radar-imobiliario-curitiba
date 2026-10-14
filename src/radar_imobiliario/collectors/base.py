"""Source access, robots rules and XML sitemap discovery."""

from __future__ import annotations

from time import monotonic, sleep
from urllib.parse import urljoin, urlparse
import re
import xml.etree.ElementTree as ET

import httpx

from radar_imobiliario.normalization import ListingCandidate


USER_AGENT = "RadarImobiliarioCuritiba/0.1 (pesquisa local de anuncios publicos)"


class SourceBlocked(RuntimeError):
    """The published access rules or HTTP status prevent collection."""

    def __init__(self, message: str, *, disable: bool = True):
        super().__init__(message)
        self.disable = disable


class RobotsRules:
    """Longest-match Allow/Disallow evaluation, including '*' and '$'."""

    def __init__(self, lines: list[str], user_agent: str):
        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        for raw in lines:
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.casefold()
            if key == "user-agent":
                if rules:
                    groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(value.casefold())
            elif key in ("allow", "disallow") and agents and value:
                rules.append((key == "allow", value))
        if agents:
            groups.append((agents, rules))
        product = user_agent.split("/", 1)[0].casefold()
        specific = [group for group in groups if any(agent != "*" and agent in product for agent in group[0])]
        selected = specific or [group for group in groups if "*" in group[0]]
        self.rules = [rule for _, group_rules in selected for rule in group_rules]

    def can_fetch(self, url: str) -> bool:
        parsed = urlparse(url)
        target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        matches: list[tuple[int, bool]] = []
        for allow, pattern in self.rules:
            anchored = pattern.endswith("$")
            body = pattern[:-1] if anchored else pattern
            expression = "^" + re.escape(body).replace(r"\*", ".*") + ("$" if anchored else "")
            if re.search(expression, target):
                matches.append((len(body.replace("*", "")), allow))
        return max(matches, default=(0, True))[1]


class Fetcher:
    def __init__(self, base_url: str, delay_seconds: float = 1.0):
        self.base_url = base_url
        self.delay_seconds = delay_seconds
        self.last_request = 0.0
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9"},
            timeout=30,
            follow_redirects=False,
        )
        self.robots: RobotsRules | None = None

    def close(self) -> None:
        self.client.close()

    def wait_turn(self) -> None:
        remaining = self.delay_seconds - (monotonic() - self.last_request)
        if remaining > 0:
            sleep(remaining)
        self.last_request = monotonic()

    def check_robots(self) -> None:
        robots_url = f"{self.base_url}/robots.txt"
        response = self.client.get(robots_url)
        if response.status_code != 200:
            raise SourceBlocked(f"robots.txt indisponível: HTTP {response.status_code}")
        self.robots = RobotsRules(response.text.splitlines(), USER_AGENT)

    def allowed(self, url: str) -> bool:
        if self.robots is None:
            raise RuntimeError("robots.txt ainda não verificado")
        source_host = urlparse(self.base_url).hostname
        target_host = urlparse(url).hostname
        return target_host in {source_host, source_host.removeprefix("www."), f"www.{source_host}"} and self.robots.can_fetch(url)

    def get(self, url: str) -> str:
        current = url
        for _ in range(6):
            if not self.allowed(current):
                if current != url:
                    raise RuntimeError(f"Redirecionamento para rota vedada por robots.txt: {current}")
                raise SourceBlocked(f"Acesso não permitido por robots.txt: {current}")
            for attempt in range(3):
                self.wait_turn()
                response = self.client.get(current)
                if response.status_code in (401, 403):
                    raise SourceBlocked(f"HTTP {response.status_code}: {current}")
                if response.status_code == 429:
                    raise SourceBlocked(f"HTTP 429 (limite de requisições): {current}", disable=False)
                if response.status_code >= 500 and attempt < 2:
                    sleep(2 ** attempt)
                    continue
                if not response.is_redirect:
                    response.raise_for_status()
                break
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise RuntimeError(f"Redirecionamento sem destino: {current}")
                current = urljoin(current, location)
                continue
            return response.text
        raise RuntimeError(f"Redirecionamentos excessivos: {url}")


class BaseCollector:
    slug: str
    name: str
    base_url: str
    sitemap_urls: tuple[str, ...]

    def __init__(self, delay_seconds: float = 1.0):
        self.fetcher = Fetcher(self.base_url, delay_seconds)

    def close(self) -> None:
        self.fetcher.close()

    def start(self) -> None:
        self.fetcher.check_robots()

    def accepts_url(self, url: str) -> bool:
        raise NotImplementedError

    def parse_listing(self, url: str, html: str) -> ListingCandidate | None:
        raise NotImplementedError

    def fetch_listing(self, url: str) -> str:
        return self.fetcher.get(url)

    def discover_urls(self) -> list[str]:
        pending = list(self.sitemap_urls)
        seen_xml: set[str] = set()
        found: set[str] = set()
        while pending:
            url = pending.pop(0)
            if url in seen_xml:
                continue
            seen_xml.add(url)
            root = ET.fromstring(self.fetcher.get(url))
            if root.tag.endswith("sitemapindex"):
                pending.extend(
                    node.text for node in root.findall(".//{*}sitemap/{*}loc") if node.text
                )
            elif root.tag.endswith("urlset"):
                found.update(
                    node.text
                    for node in root.findall(".//{*}url/{*}loc")
                    if node.text and self.accepts_url(node.text)
                )
            else:
                raise ValueError(f"Formato inesperado de sitemap: {url}")
        return sorted(found)
