"""Idempotent collection runs and listing lifecycle."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar_imobiliario.collectors import COLLECTORS
from radar_imobiliario.collectors.base import SourceBlocked
from radar_imobiliario.database import CrawlError, CrawlRun, Listing, PriceChange, Source, create_session_factory, utc_now
from radar_imobiliario.normalization import ListingCandidate, property_key


def save_candidate(session: Session, candidate: ListingCandidate) -> Listing:
    listing = session.scalar(
        select(Listing).where(
            Listing.source_slug == candidate.source_slug,
            Listing.source_ref == candidate.source_ref,
        )
    )
    now = utc_now()
    if listing is None:
        listing = Listing(
            source_slug=candidate.source_slug,
            source_ref=candidate.source_ref,
            first_seen_at=now,
        )
        session.add(listing)
    old_price = listing.price_cents if listing.id is not None else None
    listing.url = candidate.url
    listing.title = candidate.title
    listing.subtype = candidate.subtype
    listing.neighborhood = candidate.neighborhood
    listing.address = candidate.address
    listing.address_precision = candidate.address_precision
    listing.price_cents = candidate.price_cents
    listing.bedrooms = candidate.bedrooms
    listing.bathrooms = candidate.bathrooms
    listing.built_area_m2 = candidate.built_area_m2
    listing.land_area_m2 = candidate.land_area_m2
    listing.reserved = candidate.reserved
    listing.active = True
    listing.missing_runs = 0
    listing.property_key = property_key(candidate)
    listing.last_seen_at = now
    session.flush()
    if old_price != candidate.price_cents:
        session.add(PriceChange(listing_id=listing.id, price_cents=candidate.price_cents, observed_at=now))
    return listing


def mark_missing(session: Session, source_slug: str, seen_refs: set[str]) -> None:
    active = session.scalars(
        select(Listing).where(Listing.source_slug == source_slug, Listing.active.is_(True))
    )
    for listing in active:
        if listing.source_ref not in seen_refs:
            listing.missing_runs += 1
            if listing.missing_runs >= 2:
                listing.active = False


def collect(
    sources: list[str] | None = None,
    *,
    limit: int | None = None,
    db_path: Path | None = None,
    delay_seconds: float = 1.0,
    retry_blocked: bool = False,
) -> list[CrawlRun]:
    if limit is not None and limit < 1:
        raise ValueError("--limit deve ser maior que zero")
    names = sources or list(COLLECTORS)
    unknown = set(names) - set(COLLECTORS)
    if unknown:
        raise ValueError(f"Fontes desconhecidas: {', '.join(sorted(unknown))}")
    session_factory = create_session_factory(db_path)
    summaries: list[CrawlRun] = []
    for name in names:
        collector = COLLECTORS[name](delay_seconds=delay_seconds)
        try:
            with session_factory() as session:
                source = session.get(Source, name)
                if source is None:
                    source = Source(slug=name, name=collector.name, base_url=collector.base_url)
                    session.add(source)
                    session.commit()
                if not source.enabled and not retry_blocked:
                    continue
                run = CrawlRun(source_slug=name)
                session.add(run)
                session.commit()
                summaries.append(run)
                seen_refs: set[str] = set()
                try:
                    collector.start()
                    source.enabled = True
                    urls = collector.discover_urls()
                    run.discovered = len(urls)
                    if limit is not None:
                        urls = urls[:limit]
                    for url in urls:
                        try:
                            candidate = collector.parse_listing(url, collector.fetch_listing(url))
                            if candidate is None:
                                raise ValueError("Campos essenciais ausentes ou anúncio fora do recorte")
                            save_candidate(session, candidate)
                            seen_refs.add(candidate.source_ref)
                            run.saved += 1
                            session.commit()
                        except SourceBlocked:
                            raise
                        except Exception as exc:
                            run.failed += 1
                            run.error = f"{url}: {exc}"[:2000]
                            session.add(CrawlError(run_id=run.id, url=url, message=str(exc)[:2000]))
                            session.commit()
                    if run.failed:
                        run.status = "partial"
                        source.last_error = run.error
                    elif limit is not None:
                        run.status = "sample"
                        source.enabled = True
                        source.last_error = None
                    else:
                        mark_missing(session, name, seen_refs)
                        source.last_success_at = utc_now()
                        source.last_error = None
                        source.enabled = True
                        run.status = "complete"
                except SourceBlocked as exc:
                    run.status = "blocked" if exc.disable else "failed"
                    run.error = str(exc)[:2000]
                    source.last_error = run.error
                    if exc.disable:
                        source.enabled = False
                except Exception as exc:
                    run.status = "failed"
                    run.error = str(exc)[:2000]
                    source.last_error = run.error
                finally:
                    run.finished_at = utc_now()
                    session.commit()
        finally:
            collector.close()
    return summaries
