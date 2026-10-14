from dataclasses import replace

import pandas as pd
from sqlalchemy import select

from radar_imobiliario import analysis
from radar_imobiliario import collection
from radar_imobiliario.collection import mark_missing, save_candidate
from radar_imobiliario.database import CrawlError, Listing, PriceChange, Source, create_session_factory, utc_now
from radar_imobiliario.normalization import ListingCandidate, property_key


def candidate(source="innova", ref="1", price=50000000):
    return ListingCandidate(
        source_slug=source, source_ref=ref, url=f"https://example.com/{source}/{ref}",
        title="Casa à venda", subtype="casa", neighborhood="Xaxim", address="Rua Exemplo, 10",
        address_precision="numero", price_cents=price, bedrooms=3, bathrooms=2,
        built_area_m2=100, land_area_m2=200,
    )


def test_price_history_reactivation_and_two_missing_runs(tmp_path):
    factory = create_session_factory(tmp_path / "data.sqlite3")
    with factory() as session:
        session.add(Source(slug="innova", name="Innova", base_url="https://example.com"))
        session.commit()
        save_candidate(session, candidate())
        save_candidate(session, candidate())
        save_candidate(session, candidate(price=55000000))
        session.commit()
        listing = session.scalar(select(Listing))
        assert len(session.scalars(select(PriceChange)).all()) == 2
        mark_missing(session, "innova", set())
        assert listing.active and listing.missing_runs == 1
        mark_missing(session, "innova", set())
        assert not listing.active and listing.missing_runs == 2
        save_candidate(session, candidate(price=55000000))
        assert listing.active and listing.missing_runs == 0


def test_confident_cross_source_duplicates_count_once(tmp_path):
    factory = create_session_factory(tmp_path / "data.sqlite3")
    with factory() as session:
        session.add_all([
            Source(slug="innova", name="Innova", base_url="https://example.com"),
            Source(slug="habitec", name="Habitec", base_url="https://example.com"),
        ])
        session.commit()
        save_candidate(session, candidate())
        save_candidate(session, candidate(source="habitec", ref="2"))
        session.commit()
    assert property_key(candidate()) == property_key(candidate(source="habitec", ref="2"))
    assert len(analysis.load_listings(tmp_path / "data.sqlite3")) == 1
    assert property_key(replace(candidate(), address_precision="bairro")) is None


def test_same_agency_listings_with_same_visible_attributes_remain_distinct(tmp_path):
    factory = create_session_factory(tmp_path / "data.sqlite3")
    with factory() as session:
        session.add(Source(slug="innova", name="Innova", base_url="https://example.com"))
        session.commit()
        save_candidate(session, candidate(ref="1"))
        save_candidate(session, candidate(ref="2"))
        session.commit()
    assert len(analysis.load_listings(tmp_path / "data.sqlite3")) == 2


def test_partial_run_cannot_retire_a_listing(tmp_path, monkeypatch):
    class OfflineCollector:
        slug = "offline"
        name = "Offline"
        base_url = "https://example.com"
        links = ["one", "two"]
        failing = set()

        def __init__(self, **_):
            pass

        def start(self):
            pass

        def close(self):
            pass

        def discover_urls(self):
            return self.links

        def fetch_listing(self, url):
            return url

        def parse_listing(self, url, _html):
            return None if url in self.failing else candidate(source="offline", ref=url)

    monkeypatch.setitem(collection.COLLECTORS, "offline", OfflineCollector)
    path = tmp_path / "data.sqlite3"
    assert collection.collect(["offline"], db_path=path)[0].status == "complete"
    OfflineCollector.failing = {"two"}
    factory = create_session_factory(path)
    with factory() as session:
        session.get(Source, "offline").enabled = False
        session.commit()
    assert collection.collect(["offline"], db_path=path, retry_blocked=True)[0].status == "partial"
    with factory() as session:
        second = session.scalar(select(Listing).where(Listing.source_ref == "two"))
        assert second.active and second.missing_runs == 0
        assert len(session.scalars(select(CrawlError)).all()) == 1
        assert session.get(Source, "offline").enabled
    OfflineCollector.failing = set()
    OfflineCollector.links = ["one"]
    assert collection.collect(["offline"], db_path=path)[0].status == "complete"
    assert collection.collect(["offline"], db_path=path)[0].status == "complete"
    with factory() as session:
        second = session.scalar(select(Listing).where(Listing.source_ref == "two"))
        assert not second.active and second.missing_runs == 2


def test_filters_statistics_and_model_gates(tmp_path, monkeypatch):
    rows = []
    for index in range(120):
        neighborhood = "Xaxim" if index < 60 else "Portão"
        built = 80 + index % 40
        bedrooms = 2 + index % 3
        bathrooms = 1 + index % 3
        land = 150 + index % 50
        rows.append({
            "neighborhood": neighborhood, "subtype": "casa", "bedrooms": bedrooms,
            "bathrooms": bathrooms, "built_area_m2": built, "land_area_m2": land,
            "price_brl": 250000 + built * 3500 + bedrooms * 20000 + bathrooms * 8000 + land * 300,
            "last_seen_at": utc_now(), "source_inventory_at": utc_now(),
        })
    frame = pd.DataFrame(rows)
    filtered = analysis.filter_listings(frame, neighborhood="Xaxim", bedrooms=3, price_range=(0, 1_000_000))
    stats = analysis.summary(filtered)
    assert stats["count"] == len(filtered) and stats["median"] > 0 and stats["median_m2"] > 0

    monkeypatch.setattr(analysis, "load_listings", lambda *_: frame.iloc[:99])
    assert not analysis.train_model(output_dir=tmp_path)["available"]
    monkeypatch.setattr(analysis, "load_listings", lambda *_: frame)
    metadata = analysis.train_model(output_dir=tmp_path)
    assert metadata["available"] and metadata["median_ape"] <= 0.25
    query = {"neighborhood": "Xaxim", "subtype": "casa", "bedrooms": 3, "bathrooms": 2, "built_area_m2": 100, "land_area_m2": 180}
    estimate = analysis.estimate_price(frame, query, output_dir=tmp_path)
    assert estimate["available"] and estimate["low"] < estimate["estimate"] < estimate["high"]
    assert not analysis.estimate_price(frame, {**query, "neighborhood": "Sem anúncios"}, output_dir=tmp_path)["available"]
