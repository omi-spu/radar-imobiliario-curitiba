"""Command line entry point for collection, training and local dashboard."""

from __future__ import annotations

from datetime import timedelta
import argparse
import json
from pathlib import Path
import subprocess
import sys

from sqlalchemy import select

from radar_imobiliario.analysis import artifact_directory, train_model
from radar_imobiliario.collection import collect
from radar_imobiliario.database import CrawlRun, Source, create_session_factory, utc_now


def _print_runs(runs: list[CrawlRun]) -> None:
    if not runs:
        print("Nenhuma fonte executada (fontes desativadas podem exigir --retry-blocked).")
    for run in runs:
        print(f"{run.source_slug}: {run.status}; descobertos={run.discovered}; salvos={run.saved}; falhas={run.failed}")
        if run.error:
            print(f"  {run.error}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Radar Imobiliário Curitiba")
    commands = parser.add_subparsers(dest="command", required=True)
    collect_parser = commands.add_parser("collect", help="Coletar anúncios públicos")
    collect_parser.add_argument("--source", action="append", choices=["apolar", "innova", "habitec"])
    collect_parser.add_argument("--limit", type=int, help="Amostra por fonte; não retira anúncios ausentes")
    collect_parser.add_argument("--retry-blocked", action="store_true")
    commands.add_parser("daily", help="Coleta completa e treino semanal, para o agendador")
    commands.add_parser("train", help="Treinar e validar o modelo")
    commands.add_parser("status", help="Ver situação das fontes")
    commands.add_parser("dashboard", help="Abrir painel local")
    args = parser.parse_args()

    if args.command == "collect":
        _print_runs(collect(args.source, limit=args.limit, retry_blocked=args.retry_blocked))
    elif args.command == "daily":
        factory = create_session_factory()
        with factory() as session:
            due = []
            for slug in ("apolar", "innova", "habitec"):
                source = session.get(Source, slug)
                if source is not None and not source.enabled:
                    continue
                recent_inventory = session.scalar(
                    select(CrawlRun)
                    .where(
                        CrawlRun.source_slug == slug,
                        CrawlRun.status.in_(["complete", "partial"]),
                        CrawlRun.discovered > 0,
                        CrawlRun.saved + CrawlRun.failed == CrawlRun.discovered,
                        CrawlRun.failed * 20 <= CrawlRun.discovered,
                    )
                    .order_by(CrawlRun.finished_at.desc())
                    .limit(1)
                )
                last_inventory = recent_inventory.finished_at if recent_inventory else None
                if last_inventory is None or utc_now() - last_inventory >= timedelta(hours=20):
                    due.append(slug)
        _print_runs(collect(due)) if due else print("Coleta diária já concluída nas últimas 20 horas.")
        metadata_path = artifact_directory() / "model-metadata.json"
        stale = True
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            trained_at = metadata.get("trained_at")
            if trained_at:
                from datetime import datetime

                stale = (
                    not metadata.get("available")
                    or utc_now() - datetime.fromisoformat(trained_at.rstrip("Z")) >= timedelta(days=7)
                )
        if stale:
            print(json.dumps(train_model(), ensure_ascii=False, indent=2))
    elif args.command == "train":
        print(json.dumps(train_model(), ensure_ascii=False, indent=2))
    elif args.command == "status":
        factory = create_session_factory()
        with factory() as session:
            for source in session.scalars(select(Source).order_by(Source.name)):
                run = session.scalar(select(CrawlRun).where(CrawlRun.source_slug == source.slug).order_by(CrawlRun.started_at.desc()).limit(1))
                print(f"{source.name}: {'ativa' if source.enabled else 'desativada'}; última coleta: {run.status if run else 'nenhuma'}")
                if source.last_error:
                    print(f"  {source.last_error}")
    elif args.command == "dashboard":
        script = Path(__file__).with_name("dashboard.py")
        raise SystemExit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(script)]))


if __name__ == "__main__":
    main()
