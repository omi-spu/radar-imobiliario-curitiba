"""Comparable listings, descriptive statistics and validated asking-price model."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sqlalchemy import select

from radar_imobiliario.database import CrawlRun, Listing, Source, create_session_factory, database_path, utc_now
from radar_imobiliario.normalization import fold


FEATURES = ["neighborhood", "subtype", "bedrooms", "bathrooms", "built_area_m2", "land_area_m2"]
NUMERIC = ["bedrooms", "bathrooms", "built_area_m2", "land_area_m2"]
CATEGORICAL = ["neighborhood", "subtype"]


def artifact_directory() -> Path:
    return database_path().parent


def load_listings(db_path: Path | None = None) -> pd.DataFrame:
    factory = create_session_factory(db_path)
    with factory() as session:
        rows = session.execute(
            select(Listing, Source.name).join(Source).where(
                Listing.active.is_(True), Listing.reserved.is_(False), Listing.price_cents > 0
            )
        ).all()
        inventories = {}
        for run in session.scalars(select(CrawlRun).where(CrawlRun.status.in_(["complete", "partial"]))):
            if (
                run.discovered > 0
                and run.saved + run.failed == run.discovered
                and run.failed / run.discovered <= 0.05
                and run.finished_at is not None
            ):
                previous = inventories.get(run.source_slug)
                if previous is None or run.finished_at > previous:
                    inventories[run.source_slug] = run.finished_at
    values = [
        {
            "id": listing.id,
            "source": name,
            "source_slug": listing.source_slug,
            "source_ref": listing.source_ref,
            "url": listing.url,
            "title": listing.title,
            "subtype": listing.subtype,
            "neighborhood": listing.neighborhood,
            "address": listing.address,
            "address_precision": listing.address_precision,
            "price_brl": listing.price_cents / 100,
            "bedrooms": listing.bedrooms,
            "bathrooms": listing.bathrooms,
            "built_area_m2": listing.built_area_m2,
            "land_area_m2": listing.land_area_m2,
            "property_key": listing.property_key,
            "last_seen_at": listing.last_seen_at,
            "source_inventory_at": inventories.get(listing.source_slug),
        }
        for listing, name in rows
    ]
    if not values:
        return pd.DataFrame(columns=["id", "source", "url", "title", *FEATURES, "price_brl"])
    frame = pd.DataFrame(values).sort_values("last_seen_at", ascending=False)
    labels = frame["neighborhood"].dropna().value_counts()
    preferred = {}
    for label in labels.index:
        preferred.setdefault(fold(label), label)
    frame["neighborhood"] = frame["neighborhood"].map(
        lambda label: preferred.get(fold(label), label) if pd.notna(label) else label
    )
    # Group only one-to-one matches across different agencies. Repeated keys within
    # an agency may describe distinct units with identical visible attributes.
    per_source = frame.dropna(subset=["property_key"]).groupby(["property_key", "source"]).size()
    safe_keys = {
        key for key, counts in per_source.groupby(level=0)
        if len(counts) > 1 and counts.max() == 1
    }
    frame["dedup_key"] = frame.apply(
        lambda row: row["property_key"] if row["property_key"] in safe_keys else f"listing:{row['id']}", axis=1
    )
    return frame.drop_duplicates("dedup_key").reset_index(drop=True)


def filter_listings(
    frame: pd.DataFrame,
    *,
    neighborhood: str | None = None,
    subtype: str | None = None,
    bedrooms: int | None = None,
    bathrooms: int | None = None,
    price_range: tuple[float, float] | None = None,
    built_range: tuple[float, float] | None = None,
    land_range: tuple[float, float] | None = None,
) -> pd.DataFrame:
    result = frame
    for column, value in (("neighborhood", neighborhood), ("subtype", subtype), ("bedrooms", bedrooms), ("bathrooms", bathrooms)):
        if value is not None:
            result = result[result[column] == value]
    for column, bounds in (("price_brl", price_range), ("built_area_m2", built_range), ("land_area_m2", land_range)):
        if bounds is not None:
            result = result[result[column].between(*bounds, inclusive="both")]
    return result.copy()


def summary(frame: pd.DataFrame) -> dict[str, float | int | None]:
    if frame.empty:
        return {"count": 0, "median": None, "p25": None, "p75": None, "median_m2": None}
    price = frame["price_brl"].astype(float)
    valid_area = frame["built_area_m2"].notna() & (frame["built_area_m2"] > 0)
    price_per_m2 = price[valid_area] / frame.loc[valid_area, "built_area_m2"].astype(float)
    return {
        "count": len(frame),
        "median": float(price.median()),
        "p25": float(price.quantile(0.25)),
        "p75": float(price.quantile(0.75)),
        "median_m2": float(price_per_m2.median()) if not price_per_m2.empty else None,
    }


def _model() -> Pipeline:
    transformer = ColumnTransformer(
        [
            ("categories", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL),
            ("numbers", SimpleImputer(strategy="median", add_indicator=True), NUMERIC),
        ]
    )
    return Pipeline([("features", transformer), ("regression", Ridge(alpha=10.0))])


def train_model(db_path: Path | None = None, output_dir: Path | None = None) -> dict:
    output_dir = output_dir or artifact_directory()
    output_dir.mkdir(parents=True, exist_ok=True)
    frame = load_listings(db_path)
    if not frame.empty:
        cutoff = utc_now() - timedelta(days=8)
        frame = frame[
            frame["source_inventory_at"].notna()
            & (frame["source_inventory_at"] >= cutoff)
            & (frame["last_seen_at"] >= cutoff)
        ]
    frame = frame.dropna(subset=["neighborhood", "subtype", "bedrooms", "bathrooms", "built_area_m2", "price_brl"])
    frame = frame[(frame["built_area_m2"] > 0) & (frame["price_brl"] > 0)]
    metadata: dict = {"trained_at": utc_now().isoformat() + "Z", "samples": len(frame), "available": False}
    model_path = output_dir / "model.joblib"
    if len(frame) < 100:
        metadata["reason"] = "São necessários pelo menos 100 imóveis recentes de fontes com varredura integral."
    else:
        data = frame[FEATURES].copy()
        data[CATEGORICAL] = data[CATEGORICAL].astype(str)
        data[NUMERIC] = data[NUMERIC].apply(pd.to_numeric, errors="coerce")
        target = np.log(frame["price_brl"].astype(float).to_numpy())
        folds = KFold(n_splits=5, shuffle=True, random_state=42)
        predicted_log = cross_val_predict(_model(), data, target, cv=folds)
        actual = np.exp(target)
        predicted = np.exp(predicted_log)
        median_ape = float(np.median(np.abs(actual - predicted) / actual))
        metadata["median_ape"] = median_ape
        if median_ape <= 0.25:
            model = _model().fit(data, target)
            residual_ratio = actual / predicted
            metadata.update(
                available=True,
                ratio_low=min(1.0, float(np.quantile(residual_ratio, 0.1))),
                ratio_high=max(1.0, float(np.quantile(residual_ratio, 0.9))),
            )
            joblib.dump(model, model_path)
        else:
            metadata["reason"] = "Erro de validação acima de 25%."
    if not metadata["available"] and model_path.exists():
        model_path.unlink()
    (output_dir / "model-metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def comparable_count(frame: pd.DataFrame, query: dict) -> int:
    matches = frame[
        (frame["neighborhood"] == query["neighborhood"])
        & (frame["subtype"] == query["subtype"])
        & (frame["bedrooms"].sub(query["bedrooms"]).abs() <= 1)
        & (frame["bathrooms"].sub(query["bathrooms"]).abs() <= 1)
        & frame["built_area_m2"].between(query["built_area_m2"] * 0.75, query["built_area_m2"] * 1.25)
    ]
    return len(matches)


def estimate_price(frame: pd.DataFrame, query: dict, output_dir: Path | None = None) -> dict:
    output_dir = output_dir or artifact_directory()
    metadata_path = output_dir / "model-metadata.json"
    model_path = output_dir / "model.joblib"
    count = comparable_count(frame, query)
    if count < 5:
        return {"available": False, "reason": "Menos de cinco imóveis comparáveis.", "comparables": count}
    if not metadata_path.exists() or not model_path.exists():
        return {"available": False, "reason": "Modelo ainda não treinado.", "comparables": count}
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    trained_at = pd.Timestamp(metadata["trained_at"]).to_pydatetime().replace(tzinfo=None)
    if not metadata.get("available") or utc_now() - trained_at > timedelta(days=8):
        return {"available": False, "reason": "Modelo indisponível ou desatualizado.", "comparables": count}
    model = joblib.load(model_path)
    data = pd.DataFrame([{name: query.get(name) for name in FEATURES}])
    data[CATEGORICAL] = data[CATEGORICAL].astype(str)
    data[NUMERIC] = data[NUMERIC].apply(pd.to_numeric, errors="coerce")
    price = float(np.exp(model.predict(data)[0]))
    return {
        "available": True,
        "estimate": price,
        "low": price * metadata["ratio_low"],
        "high": price * metadata["ratio_high"],
        "comparables": count,
        "median_ape": metadata["median_ape"],
    }
