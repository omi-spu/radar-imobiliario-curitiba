"""SQLite schema and persistence for crawled listings."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utc_now() -> datetime:
    """Use naive UTC timestamps consistently because SQLite has no timezone type."""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    slug: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    listings: Mapped[list[Listing]] = relationship(back_populates="source")


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("source_slug", "source_ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_slug: Mapped[str] = mapped_column(ForeignKey("sources.slug"), index=True)
    source_ref: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(String(500))
    subtype: Mapped[str] = mapped_column(String(30))
    neighborhood: Mapped[str | None] = mapped_column(String(150), index=True)
    address: Mapped[str | None] = mapped_column(String(300))
    address_precision: Mapped[str] = mapped_column(String(20), default="bairro")
    price_cents: Mapped[int] = mapped_column(Integer)
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    bathrooms: Mapped[int | None] = mapped_column(Integer)
    built_area_m2: Mapped[float | None] = mapped_column(Float)
    land_area_m2: Mapped[float | None] = mapped_column(Float)
    reserved: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    missing_runs: Mapped[int] = mapped_column(Integer, default=0)
    property_key: Mapped[str | None] = mapped_column(String(64), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    source: Mapped[Source] = relationship(back_populates="listings")
    price_changes: Mapped[list[PriceChange]] = relationship(back_populates="listing", cascade="all, delete-orphan")


class PriceChange(Base):
    __tablename__ = "price_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), index=True)
    price_cents: Mapped[int] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    listing: Mapped[Listing] = relationship(back_populates="price_changes")


class CrawlRun(Base):
    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_slug: Mapped[str] = mapped_column(ForeignKey("sources.slug"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")
    discovered: Mapped[int] = mapped_column(Integer, default=0)
    saved: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class CrawlError(Base):
    __tablename__ = "crawl_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("crawl_runs.id"), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    message: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


def database_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "radar.sqlite3"


def create_session_factory(path: Path | None = None) -> sessionmaker:
    path = path or database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path.as_posix()}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
