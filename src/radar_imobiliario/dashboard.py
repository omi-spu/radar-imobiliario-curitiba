"""Local Streamlit dashboard."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from radar_imobiliario.analysis import estimate_price, filter_listings, load_listings, summary
from radar_imobiliario.database import CrawlError, CrawlRun, PriceChange, Source, create_session_factory


def brl(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return "R$ " + f"{value:,.0f}".replace(",", ".")


@st.cache_data(ttl=300)
def data() -> pd.DataFrame:
    return load_listings()


def source_status() -> pd.DataFrame:
    factory = create_session_factory()
    with factory() as session:
        sources = session.scalars(select(Source).order_by(Source.name)).all()
        rows = []
        for source in sources:
            run = session.scalar(
                select(CrawlRun).where(CrawlRun.source_slug == source.slug).order_by(CrawlRun.started_at.desc()).limit(1)
            )
            rows.append(
                {
                    "Fonte": source.name,
                    "Ativa": "Sim" if source.enabled else "Não",
                    "Última coleta": run.started_at.strftime("%d/%m/%Y %H:%M UTC") if run else "—",
                    "Resultado": run.status if run else "Sem coleta",
                    "Inventário completo": "Sim" if source.last_success_at else "Não",
                    "Salvos": run.saved if run else 0,
                    "Falhas": run.failed if run else 0,
                    "Detalhe": source.last_error or (run.error if run else ""),
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    st.set_page_config(page_title="Radar Imobiliário Curitiba", page_icon="🏠", layout="wide")
    st.title("Radar Imobiliário Curitiba")
    st.caption("Preços pedidos em anúncios de casas à venda. Não representam preços efetivos de venda.")
    frame = data()
    status = source_status()
    if not status.empty and (status["Inventário completo"] == "Não").any():
        st.warning("A cobertura da cidade ainda está em formação: uma ou mais fontes não concluíram uma varredura sem falhas.")
    if frame.empty:
        st.info("Ainda não há anúncios. Execute `uv run radar collect --limit 3` para testar a coleta.")
        if not status.empty:
            st.dataframe(status, hide_index=True, width="stretch")
        return

    neighborhoods = sorted(str(x) for x in frame["neighborhood"].dropna().unique())
    subtypes = sorted(str(x) for x in frame["subtype"].dropna().unique())
    with st.sidebar:
        st.header("Filtros")
        neighborhood = st.selectbox("Bairro", ["Todos", *neighborhoods])
        subtype = st.selectbox("Tipo", ["Todos", *subtypes])
        bedrooms = st.selectbox("Quartos", ["Todos", *sorted(int(x) for x in frame["bedrooms"].dropna().unique())])
        bathrooms = st.selectbox("Banheiros", ["Todos", *sorted(int(x) for x in frame["bathrooms"].dropna().unique())])
        min_price = st.number_input("Preço mínimo (R$)", min_value=0, value=0, step=50_000)
        max_price = st.number_input("Preço máximo (R$; 0 = sem limite)", min_value=0, value=0, step=50_000)
        min_built = st.number_input("Área construída mínima (m²)", min_value=0, value=0, step=10)
        max_built = st.number_input("Área construída máxima (m²; 0 = sem limite)", min_value=0, value=0, step=10)
        min_land = st.number_input("Área do terreno mínima (m²)", min_value=0, value=0, step=10)
        max_land = st.number_input("Área do terreno máxima (m²; 0 = sem limite)", min_value=0, value=0, step=10)

    filtered = filter_listings(
        frame,
        neighborhood=None if neighborhood == "Todos" else neighborhood,
        subtype=None if subtype == "Todos" else subtype,
        bedrooms=None if bedrooms == "Todos" else bedrooms,
        bathrooms=None if bathrooms == "Todos" else bathrooms,
        price_range=(float(min_price), float(max_price or frame["price_brl"].max())) if min_price or max_price else None,
        built_range=(float(min_built), float(max_built or float("inf"))) if min_built or max_built else None,
        land_range=(float(min_land), float(max_land or float("inf"))) if min_land or max_land else None,
    )
    stats = summary(filtered)
    one, two, three, four = st.columns(4)
    one.metric("Imóveis únicos", stats["count"])
    two.metric("Preço mediano", brl(stats["median"]))
    three.metric("Faixa central (25%–75%)", f"{brl(stats['p25'])} – {brl(stats['p75'])}")
    four.metric("Mediana por m² construído", brl(stats["median_m2"]))

    st.subheader("Anúncios comparáveis")
    if filtered.empty:
        st.info("Nenhum anúncio atende a estes filtros.")
    else:
        display = filtered.sort_values("price_brl")[
            ["source", "title", "neighborhood", "address", "bedrooms", "bathrooms", "built_area_m2", "land_area_m2", "price_brl", "url"]
        ].rename(columns={
            "source": "Fonte", "title": "Anúncio", "neighborhood": "Bairro", "address": "Endereço informado",
            "bedrooms": "Quartos", "bathrooms": "Banheiros", "built_area_m2": "Área construída (m²)",
            "land_area_m2": "Terreno (m²)", "price_brl": "Preço pedido (R$)", "url": "Origem",
        })
        st.dataframe(display, hide_index=True, width="stretch", column_config={"Origem": st.column_config.LinkColumn("Origem")})
        with st.expander("Histórico de preço de um anúncio"):
            choices = {
                f"{row.source} · {row.source_ref} · {row.neighborhood or 'bairro não informado'}": row.id
                for row in filtered.itertuples()
            }
            chosen = st.selectbox("Anúncio", list(choices))
            factory = create_session_factory()
            with factory() as session:
                changes = session.scalars(
                    select(PriceChange)
                    .where(PriceChange.listing_id == choices[chosen])
                    .order_by(PriceChange.observed_at)
                ).all()
            history = pd.DataFrame(
                [{"Data": change.observed_at, "Preço pedido (R$)": change.price_cents / 100} for change in changes]
            )
            if len(history) < 2:
                st.info("Ainda não houve mudança de preço observada para este anúncio.")
            else:
                st.line_chart(history.set_index("Data"))
        by_neighborhood = filtered.groupby("neighborhood", dropna=True)["price_brl"].median().sort_values().tail(20)
        if len(by_neighborhood) > 1:
            st.subheader("Preço mediano por bairro no filtro")
            st.bar_chart(by_neighborhood, horizontal=True, x_label="Preço pedido (R$)")

    st.subheader("Estimativa de preço pedido")
    st.caption("A previsão aparece somente com dados e precisão de validação suficientes.")
    with st.form("estimate"):
        col1, col2, col3 = st.columns(3)
        chosen_neighborhood = col1.selectbox("Bairro do imóvel", neighborhoods)
        chosen_subtype = col2.selectbox("Tipo do imóvel", subtypes)
        chosen_bedrooms = col3.number_input("Quartos do imóvel", min_value=1, max_value=20, value=3)
        col4, col5, col6 = st.columns(3)
        chosen_bathrooms = col4.number_input("Banheiros do imóvel", min_value=1, max_value=20, value=2)
        chosen_built = col5.number_input("Área construída (m²)", min_value=1.0, value=100.0, step=10.0)
        chosen_land = col6.number_input("Terreno (m²; 0 = desconhecido)", min_value=0.0, value=0.0, step=10.0)
        submitted = st.form_submit_button("Calcular estimativa")
    if submitted:
        query = {
            "neighborhood": chosen_neighborhood,
            "subtype": chosen_subtype,
            "bedrooms": chosen_bedrooms,
            "bathrooms": chosen_bathrooms,
            "built_area_m2": chosen_built,
            "land_area_m2": chosen_land or None,
        }
        result = estimate_price(frame, query)
        if result["available"]:
            st.metric("Preço pedido estimado", brl(result["estimate"]))
            st.write(f"Faixa indicativa: {brl(result['low'])} a {brl(result['high'])}. Comparáveis: {result['comparables']}. Erro mediano de validação: {result['median_ape']:.1%}.")
        else:
            st.info(f"Previsão indisponível: {result['reason']} Comparáveis: {result['comparables']}.")

    with st.expander("Situação das fontes"):
        if not status.empty:
            st.dataframe(status, hide_index=True, width="stretch")
        factory = create_session_factory()
        with factory() as session:
            errors = session.execute(
                select(CrawlError, CrawlRun.source_slug)
                .join(CrawlRun, CrawlError.run_id == CrawlRun.id)
                .order_by(CrawlError.observed_at.desc())
                .limit(20)
            ).all()
        if errors:
            st.caption("Falhas recentes em anúncios individuais")
            st.dataframe(
                pd.DataFrame([
                    {"Fonte": slug, "Quando (UTC)": error.observed_at, "URL": error.url, "Erro": error.message}
                    for error, slug in errors
                ]),
                hide_index=True,
                width="stretch",
                column_config={"URL": st.column_config.LinkColumn("URL")},
            )


if __name__ == "__main__":
    main()
