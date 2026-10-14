import json
import httpx
import pytest
from radar_imobiliario.collectors import ApolarCollector, HabitecCollector, InnovaCollector
from radar_imobiliario.collectors.base import Fetcher, RobotsRules, USER_AGENT
from radar_imobiliario.normalization import parse_brl


APOLAR_URL = "https://www.apolar.com.br/venda/curitiba/portao/venda-residencial-casa-curitiba-portao-221275"
INNOVA_URL = "https://www.innovaimobiliaria.com.br/imovel/casa-a-venda-3-quartos-xaxim-curitiba-cod-90969-001"
HABITEC_URL = "https://habitec.com.br/imovel/comprar-sobrado-4-quartos-sao-braz-curitiba-94254002/129113"


def test_apolar_extracts_built_and_land_separately():
    html = """
    <h1 class="property-title">Casa Residencial para Venda no Bairro Portão<div class="property-ref">ref. 221275</div></h1>
    <a class="property-address">Rua Itacolomi, 1680, <span>Portão -</span> Curitiba</a>
    <div class="description">Casa/Sobrado tríplex com 3 quartos</div>
    <ul class="property-details"><li>Cidade: <strong>Curitiba</strong></li>
      <li>Área Terreno: <strong>178.2m²</strong></li><li>Área Total: <strong>211m²</strong></li>
      <li>Área Útil: <strong>211m²</strong></li><li>Dormitórios: <strong>3</strong></li></ul>
    <ul class="property-highlights"><li><img class="bathrooms" alt="4 banheiros"></li></ul>
    <div class="price-box"><div class="price-new">R$ 1.400.000,00</div></div>
    """
    result = ApolarCollector().parse_listing(APOLAR_URL, html)
    assert (result.price_cents, result.built_area_m2, result.land_area_m2) == (140000000, 211, 178.2)
    assert (result.bedrooms, result.bathrooms, result.subtype, result.address_precision) == (3, 4, "sobrado", "numero")


def test_innova_does_not_assume_total_area_is_land():
    listing = {
        "@type": "RealEstateListing", "name": "Casa para venda - Xaxim",
        "description": "Casa com quintal.",
        "address": {"streetAddress": "Rua Exemplo, 174", "addressLocality": "Xaxim", "addressRegion": "Curitiba/PR"},
        "offers": {"price": 1690000, "availability": "https://schema.org/InStock"},
    }
    html = f"""
    <script type="application/ld+json">{json.dumps(listing)}</script>
    <h1 class="propertyTitle">Casa para venda - Xaxim</h1><p class="propertyCode">Cod. 90969-001</p>
    <div class="featureItem"><p>Área total</p><p class="featureValue">336 m²</p></div>
    <div class="featureItem"><p>Área útil</p><p class="featureValue">169 m²</p></div>
    <div class="featureItem"><p>Quartos</p><p class="featureValue">3</p></div>
    <div class="featureItem"><p>Banheiros</p><p class="featureValue">2</p></div>
    """
    result = InnovaCollector().parse_listing(INNOVA_URL, html)
    assert result.built_area_m2 == 169
    assert result.land_area_m2 is None
    assert result.price_cents == 169000000
    assert InnovaCollector().parse_listing(INNOVA_URL, html + '<div class="reservedOverlay">Reservado</div>').reserved


def test_habitec_reads_explicit_land_area():
    listing = {
        "@type": "RealEstateListing", "offers": {"price": 950000, "availability": "https://schema.org/InStock"},
        "mainEntity": {
            "address": {"streetAddress": "Rua José Miguel dos Santos, 372", "addressLocality": "Curitiba"},
            "floorSize": {"value": 226.07}, "numberOfBedrooms": 4, "numberOfBathroomsTotal": 4,
        },
    }
    html = f"""
    <script type="application/ld+json">{json.dumps(listing)}</script>
    <h1>Sobrado à venda em São Braz</h1><p class="tipoCodigo">Ref: 94254.002-KD</p>
    <div class="localizacao"><p class="cidade">São Braz - Curitiba - PR</p></div>
    <p>Área do terreno: <span class="fw-bold">336m²</span></p>
    <p>Área privativa: <span class="fw-bold">226.07m²</span></p>
    """
    result = HabitecCollector().parse_listing(HABITEC_URL, html)
    assert (result.built_area_m2, result.land_area_m2, result.bedrooms, result.bathrooms) == (226.07, 336, 4, 4)


def test_sitemap_discovers_only_curitiba_houses():
    collector = InnovaCollector()
    collector.fetcher.get = lambda _: f"""<urlset><url><loc>{INNOVA_URL}</loc></url>
       <url><loc>https://www.innovaimobiliaria.com.br/imovel/apartamento-a-venda-curitiba</loc></url></urlset>"""
    assert collector.discover_urls() == [INNOVA_URL]


def test_robots_rules_block_restricted_paths():
    fetcher = Fetcher("https://www.innovaimobiliaria.com.br")
    fetcher.robots = RobotsRules(["User-agent: *", "Allow: /", "Disallow: /api/"], USER_AGENT)
    assert fetcher.allowed(INNOVA_URL)
    assert not fetcher.allowed("https://www.innovaimobiliaria.com.br/api/imoveis")


def test_price_parser_accepts_json_number_and_brazilian_currency():
    assert parse_brl("1690000.0") == parse_brl("R$ 1.690.000,00") == 169000000


def test_redirect_to_disallowed_path_is_skipped_without_fetching_it():
    requested = []

    def respond(request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"Location": "/api/imoveis"})

    fetcher = Fetcher("https://www.innovaimobiliaria.com.br", delay_seconds=0)
    fetcher.robots = RobotsRules(["User-agent: *", "Allow: /", "Disallow: /api/"], USER_AGENT)
    fetcher.client.close()
    fetcher.client = httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False)
    with pytest.raises(RuntimeError, match="Redirecionamento para rota vedada"):
        fetcher.get(INNOVA_URL)
    assert requested == [INNOVA_URL]
    fetcher.close()
