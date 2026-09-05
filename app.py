from pathlib import Path
from io import BytesIO
import html
import re

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab import rl_config
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.data import (
    BOROUGH_ORDER,
    build_geojson,
    build_zip_profiles,
    geocode_nyc,
    load_aggregate_paid_claims,
    load_floodnet,
    load_fvi,
    load_multiple_loss,
    load_nfip_zip_claims,
    nearest_zip_profiles,
)


ROOT = Path(__file__).parent
FVI_PATH = ROOT / "data" / "nyc_flood_vulnerability.csv"
FLOODNET_EVENTS_PATH = ROOT / "data" / "floodnet_events.csv"
FLOODNET_SENSORS_PATH = ROOT / "data" / "floodnet_sensors.csv"
MULTIPLE_LOSS_PATH = ROOT / "data" / "nyc_nfip_multiple_loss_by_zip.csv"
AGGREGATE_CLAIMS_PATH = ROOT / "data" / "aggregate_paid_claims.csv"
ZIP_CLAIMS_PATH = ROOT / "data" / "nyc_nfip_claims_by_zip.csv"

EVIDENCE_PROVENANCE = [
    ("NYC Flood Vulnerability Index", "Modeled census-tract scenarios for the 2020s, 2050s and 2080s", "Retrieved 2026-08-23"),
    ("NYC FloodNet events", "Observed sensor events through the latest event in the included snapshot", "Retrieved 2026-08-23"),
    ("NYC FloodNet sensor metadata", "Installation, removal, location and tidal status used to calculate active sensor-years", "Retrieved 2026-08-22"),
    ("FEMA NFIP Redacted Claims V3", "Historical claims aggregated to reported ZIP; source dates are not retained in the small ZIP extract", "Retrieved 2026-08-23"),
    ("FEMA NFIP Multiple Loss Properties V1", "Repetitive-loss status as of 2026-08-02", "Retrieved 2026-08-22"),
]


@st.cache_data(show_spinner=False)
def cached_load_fvi(path_text: str) -> pd.DataFrame:
    return load_fvi(Path(path_text))


@st.cache_data(show_spinner=False)
def cached_load_floodnet(events_path: str, sensors_path: str) -> dict:
    return load_floodnet(Path(events_path), Path(sensors_path))


@st.cache_data(show_spinner=False)
def cached_load_multiple_loss(path_text: str) -> pd.DataFrame:
    return load_multiple_loss(Path(path_text))


@st.cache_data(show_spinner=False)
def cached_load_aggregate_claims(path_text: str) -> pd.DataFrame:
    return load_aggregate_paid_claims(Path(path_text))


@st.cache_data(show_spinner=False)
def cached_load_zip_claims(path_text: str) -> pd.DataFrame:
    return load_nfip_zip_claims(Path(path_text))


@st.cache_data(show_spinner=False)
def cached_build_geojson(frame: pd.DataFrame) -> dict:
    return build_geojson(frame)


@st.cache_data(show_spinner=False)
def cached_build_zip_profiles(fvi, floodnet, losses, claims, zip_claims):
    return build_zip_profiles(fvi, floodnet, losses, claims, zip_claims)


@st.cache_data(show_spinner=False, ttl=86400)
def cached_geocode_nyc(query: str) -> list[dict]:
    return geocode_nyc(query)


def _number(value, digits=0, missing="Not available"):
    return missing if pd.isna(value) else f"{value:,.{digits}f}"


def _percentile_composite(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    """Return an equal-weight 0-100 percentile composite using available fields."""
    parts = pd.DataFrame(index=frame.index)
    for column in columns:
        values = pd.to_numeric(frame[column], errors="coerce")
        parts[column] = values.rank(method="average", pct=True) * 100
    result = parts.mean(axis=1, skipna=True)
    return result.where(parts.notna().any(axis=1))


def render_risk_dial(value: float, category: str) -> None:
    """Render the ZIP screening score as a compact, accessible dial."""
    category_colors = {
        "Lower": "#2E8B57",
        "Moderate": "#D99C17",
        "High": "#EF6C00",
        "Very high": "#D7263D",
    }
    dial_color = category_colors.get(category, "#0052FF")
    figure = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=float(value),
            number={"valueformat": ".1f", "font": {"size": 32, "color": "#0F172A"}},
            gauge={
                "shape": "angular",
                "axis": {
                    "range": [0, 100],
                    "tickmode": "array",
                    "tickvals": [0, 25, 50, 75, 100],
                    "ticktext": ["0", "25", "50", "75", "100"],
                    "tickfont": {"size": 9, "color": "#64748B"},
                },
                "bar": {"color": dial_color, "thickness": 0.28},
                "bgcolor": "#FFFFFF",
                "borderwidth": 0,
                "steps": [
                    {"range": [0, 25], "color": "#A7DCC7"},
                    {"range": [25, 50], "color": "#FFD166"},
                    {"range": [50, 75], "color": "#F4A261"},
                    {"range": [75, 100], "color": "#E76F73"},
                ],
                "threshold": {
                    "line": {"color": "#0F172A", "width": 3},
                    "thickness": 0.75,
                    "value": float(value),
                },
            },
        )
    )
    figure.update_layout(
        height=172,
        margin={"l": 18, "r": 18, "t": 10, "b": 0},
        paper_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter, Helvetica, Arial, sans-serif"},
    )
    st.plotly_chart(
        figure,
        use_container_width=True,
        config={"displayModeBar": False, "staticPlot": True},
        key="zip_risk_dial",
    )
    st.markdown(
        """
        <div class="risk-legend" aria-label="Risk index bands">
          <span><i class="risk-dot lower"></i>Lower 0-24</span>
          <span><i class="risk-dot moderate"></i>Moderate 25-49</span>
          <span><i class="risk-dot high"></i>High 50-74</span>
          <span><i class="risk-dot very-high"></i>Very high 75-100</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def score_drivers(row: pd.Series) -> list[dict]:
    """Group the six weighted index inputs into three interpretable drivers."""
    groups = [
        (
            "NFIP loss concentration",
            [("nfip_loss_score", 25)],
            "Combines repeat-loss concentration and total NFIP payments. It is not normalized by the number of insured properties.",
        ),
        (
            "Observed flooding",
            [("historical_score", 20), ("severity_score", 15)],
            "Combines FloodNet event frequency with measured depth and drainage severity.",
        ),
        (
            "Modeled FVI",
            [("risk_2020s_score", 15), ("risk_2050s_score", 15), ("risk_2080s_score", 10)],
            "Combines modeled vulnerability signals for the 2020s, 2050s and 2080s.",
        ),
    ]
    available_weight = sum(
        weight
        for _, components, _ in groups
        for column, weight in components
        if pd.notna(row.get(column))
    )
    drivers = []
    for label, components, explanation in groups:
        available = [(float(row[column]), weight) for column, weight in components if pd.notna(row.get(column))]
        if not available:
            continue
        component_weight = sum(weight for _, weight in available)
        grouped_score = sum(score * weight for score, weight in available) / component_weight
        contribution = sum(score * weight for score, weight in available) / available_weight
        if grouped_score >= 75:
            signal = "high relative component score"
        elif grouped_score >= 50:
            signal = "above-midpoint relative component score"
        elif grouped_score >= 25:
            signal = "moderate relative component score"
        else:
            signal = "low relative component score"
        if label == "NFIP loss concentration":
            raw_summary = (
                f"{_number(row.get('total_repeat_losses'))} repeat losses and "
                f"${_number(row.get('total_claim_dollars_paid'), 0)} paid"
            )
        elif label == "Observed flooding":
            raw_summary = (
                f"{_number(row.get('events_per_sensor_year'), 2)} events per sensor-year; "
                f"median depth {_number(row.get('median_depth_inches'), 1)} inches"
            )
        else:
            raw_summary = (
                f"2020s {_number(row.get('risk_2020s_score'), 1)}, "
                f"2050s {_number(row.get('risk_2050s_score'), 1)}, "
                f"2080s {_number(row.get('risk_2080s_score'), 1)}"
            )
        drivers.append({
            "label": label,
            "score": grouped_score,
            "contribution": contribution,
            "signal": signal,
            "explanation": explanation,
            "weight": component_weight,
            "effective_weight": component_weight / available_weight,
            "raw_summary": raw_summary,
        })
    return sorted(drivers, key=lambda driver: driver["contribution"], reverse=True)[:3]


def peer_context(row: pd.Series, profiles: pd.DataFrame) -> str | None:
    """Describe a ZIP's position among comparable, sufficiently complete profiles."""
    if pd.isna(row.get("risk_index")) or row.get("data_coverage", 0) < 0.5:
        return None
    represented = profiles[
        profiles["risk_index"].notna() & profiles["data_coverage"].ge(0.5)
    ].copy()
    if represented.empty:
        return None
    score = float(row["risk_index"])
    lower_share = represented["risk_index"].lt(score).mean()
    borough_peers = represented[represented["borough"].eq(row["borough"])].sort_values(
        "risk_index", ascending=False
    )
    borough_rank = borough_peers["risk_index"].rank(method="min", ascending=False)
    row_rank = borough_rank.loc[borough_peers["zip_code"].eq(row["zip_code"])]
    borough_text = ""
    if not row_rank.empty:
        borough_text = (
            f" It ranks {int(row_rank.iloc[0])} of {len(borough_peers)} represented "
            f"ZIP profiles in {row['borough']}."
        )
    return (
        f"The index is higher than {lower_share:.0%} of represented NYC ZIP profiles."
        f"{borough_text}"
    )


def metric_peer_comparison(
    profiles: pd.DataFrame,
    row: pd.Series,
    column: str,
    formatter,
    include_borough: bool = True,
) -> str | None:
    """Benchmark one ZIP metric against represented NYC ZIPs and its borough peers."""
    current = pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").iloc[0]
    peers = profiles.assign(
        _peer_value=pd.to_numeric(profiles[column], errors="coerce")
    ).dropna(subset=["_peer_value"])
    if pd.isna(current) or len(peers) < 5:
        return None
    lower_share = peers["_peer_value"].lt(current).mean()
    comparison = (
        f"Higher than {lower_share:.0%} of {len(peers)} represented NYC ZIP profiles; "
        f"NYC median {formatter(peers['_peer_value'].median())}."
    )
    if include_borough:
        borough_peers = peers[peers["borough"].eq(row.get("borough"))]
        if len(borough_peers) >= 3:
            comparison += (
                f" {row['borough']} median "
                f"{formatter(borough_peers['_peer_value'].median())}."
            )
    return comparison


def render_provenance() -> None:
    """Expose source periods and snapshot dates without crowding the main report."""
    with st.expander("Evidence periods and source snapshots", expanded=False):
        st.caption(
            "The sources cover different periods. Snapshot presence does not mean complete "
            "geographic or temporal observation for every ZIP."
        )
        for source, period, retrieved in EVIDENCE_PROVENANCE:
            st.markdown(f"**{source}.** {period}. {retrieved}.")


def executive_summary(row: pd.Series) -> tuple[list[str], list[str]]:
    """Translate the strongest available signals into decision-oriented language."""
    findings, actions = [], []
    if pd.notna(row.get("claim_count")) and row["claim_count"] > 0:
        findings.append(f"NFIP records show a historical volume of {row['claim_count']:,.0f} claims and ${row['total_claim_dollars_paid']:,.0f} paid in this ZIP. This is not a claim rate because the extract has no complete insured-property denominator.")
    if pd.notna(row.get("observed_events")) and row.get("sensor_count", 0) > 0:
        findings.append(f"FloodNet recorded {row['observed_events']:,.0f} events across {row['sensor_count']:,.0f} monitored sensor locations; this is observed street evidence, not complete ZIP coverage.")
    if pd.notna(row.get("total_repeat_losses")) and row["total_repeat_losses"] > 0:
        findings.append(f"Repeated-loss records contain {row['multiple_loss_properties']:,.0f} properties associated with {row['total_repeat_losses']:,.0f} losses, indicating recurring concentration.")
    if pd.notna(row.get("risk_2050s_score")):
        direction = "higher" if row.get("risk_2050s_score", 0) > row.get("risk_2020s_score", 0) else "similar or lower"
        findings.append(f"The modeled 2050s vulnerability signal is {direction} than the 2020s signal; this is relative vulnerability, not annual flood probability.")
    category = str(row.get("risk_category", "Limited"))
    if category in {"High", "Very high"}:
        actions.append("Prioritize enhanced property-level review: verify elevation, flood-zone designation, basement exposure, construction type and mitigation history before any underwriting decision.")
        actions.append("Target mitigation outreach and resilience incentives where repeat losses or slow drainage are present.")
    elif category == "Moderate":
        actions.append("Use this ZIP as a screening flag and verify parcel-level hazard, elevation and building characteristics before changing terms or pricing.")
    else:
        actions.append("Maintain standard property-level verification. A lower relative index or sparse monitoring does not establish low parcel risk.")
    if pd.notna(row.get("average_fshri")) and row["average_fshri"] >= 4:
        actions.append("Consider additional preparedness and recovery support because FSHRI is elevated; do not use this social-vulnerability measure to increase premiums.")
    return findings or ["Available evidence is too limited for a substantive ZIP-wide finding."], actions


def render_area_search() -> None:
    """Render the persistent address/ZIP search that can open a ZIP report from any view."""
    def search_form() -> tuple[str, bool]:
        with st.form("global_location_search", border=False):
            search_col, button_col = st.columns([5, 1])
            query_value = search_col.text_input(
                "NYC address or ZIP code",
                placeholder="Example: 120 Broadway, New York, NY or 10007",
                label_visibility="collapsed",
            )
            was_submitted = button_col.form_submit_button(
                "Open report", type="primary", use_container_width=True
            )
        return query_value, was_submitted

    pending_candidates = st.session_state.get("location_candidates", [])
    if pending_candidates:
        _, confirm_panel, _ = st.columns([1, 4, 1])
        with confirm_panel:
            with st.container(border=True):
                st.markdown("<div class='confirm-kicker'>Address verification</div>", unsafe_allow_html=True)
                st.markdown("### Confirm location")
                st.caption("More than one NYC match was found. Select the intended address before opening its ZIP-level report.")
                labels = [candidate["label"] for candidate in pending_candidates]
                selected_label = st.selectbox("Address match", labels)
                action_space, confirm_col, cancel_col = st.columns([1.2, 1.1, .55])
                if confirm_col.button("Open ZIP report", type="primary", use_container_width=True):
                    selected_index = labels.index(selected_label)
                    st.session_state["location_result"] = pending_candidates[selected_index]
                    st.session_state.pop("location_candidates", None)
                    st.rerun()
                if cancel_col.button("Cancel", use_container_width=True):
                    st.session_state.pop("location_candidates", None)
                    st.rerun()
        return

    if st.session_state.get("location_result"):
        with st.expander("Search another address", expanded=False):
            query, submitted = search_form()
    else:
        st.markdown("### Look for your desired area directly")
        query, submitted = search_form()
    if not submitted:
        return
    cleaned = query.strip()
    if not cleaned:
        st.warning("Enter an NYC address or five-digit ZIP code.")
        return
    direct_zip = re.fullmatch(r"\d{5}", cleaned)
    try:
        candidates = (
            [{"label": f"ZIP {cleaned}", "zip_code": cleaned, "latitude": None, "longitude": None}]
            if direct_zip
            else cached_geocode_nyc(cleaned)
        )
    except Exception as exc:
        st.error(f"The address lookup service is temporarily unavailable: {exc}")
        return
    if not candidates:
        st.error("No NYC ZIP code could be resolved. Include the borough and state, or enter the ZIP directly.")
        return
    st.session_state.pop("explorer_focus_zip", None)
    if direct_zip or len(candidates) == 1:
        st.session_state["location_result"] = candidates[0]
    else:
        st.session_state["location_candidates"] = candidates
    st.rerun()


def _selected_zip_from_event(event) -> str | None:
    """Extract the ZIP embedded in Plotly custom data for a selected map feature."""
    if event is None:
        return None
    try:
        points = event.selection.points
    except (AttributeError, KeyError, TypeError):
        try:
            points = event.get("selection", {}).get("points", [])
        except AttributeError:
            return None
    if not points:
        return None
    point = points[0]
    custom = point.get("customdata") if isinstance(point, dict) else getattr(point, "customdata", None)
    if isinstance(custom, (list, tuple)) and custom:
        custom = custom[0]
    match = re.search(r"\b(\d{5})\b", str(custom or ""))
    return match.group(1) if match else None


def render_map_zip_option(event, profiles: pd.DataFrame, key: str) -> None:
    """Offer the full ZIP workflow after a user selects a mapped ZIP."""
    zip_code = _selected_zip_from_event(event)
    if not zip_code:
        st.caption("Select a mapped ZIP or tract to open its ZIP Code Report.")
        return
    match = profiles[profiles["zip_code"].eq(zip_code)]
    if match.empty:
        st.info(f"ZIP {zip_code} is visible in this layer but does not have a combined ZIP profile.")
        return
    borough = match.iloc[0]["borough"]
    selected_result = {
        "label": f"ZIP {zip_code} selected from Explorer",
        "zip_code": zip_code,
        "latitude": match.iloc[0].get("latitude"),
        "longitude": match.iloc[0].get("longitude"),
    }

    def open_selected_zip() -> None:
        st.session_state.pop("explorer_focus_zip", None)
        st.session_state["location_result"] = selected_result

    with st.container(border=True):
        st.markdown(f"**Selected ZIP {zip_code}, {borough}.** Open the same report produced by the search bar.")
        st.button(
            f"Explore ZIP {zip_code}",
            key=f"explore_{key}_{zip_code}",
            type="primary",
            on_click=open_selected_zip,
        )


def _build_flood_risk_pdf(
    row: pd.Series,
    findings: list[str],
    actions: list[str],
    zip_losses: pd.DataFrame,
    neighbors: pd.DataFrame,
    peer_text: str | None,
) -> bytes:
    """Create a self-contained, interpretation-first PDF report."""
    regular_font = "Helvetica"
    bold_font = "Helvetica-Bold"
    for font_directory in rl_config.TTFSearchPath:
        regular_path = Path(font_directory) / "Vera.ttf"
        bold_path = Path(font_directory) / "VeraBd.ttf"
        if regular_path.exists() and bold_path.exists():
            if "SinkSans" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("SinkSans", str(regular_path)))
            if "SinkSans-Bold" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("SinkSans-Bold", str(bold_path)))
            regular_font = "SinkSans"
            bold_font = "SinkSans-Bold"
            break
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
        title=f"Sink or Swim flood risk report - ZIP {row['zip_code']}",
    )
    styles = getSampleStyleSheet()
    styles["BodyText"].fontName = regular_font
    styles.add(ParagraphStyle(name="ReportTitle", parent=styles["Title"], fontName=bold_font, fontSize=25, leading=29, textColor=colors.HexColor("#0F172A"), alignment=TA_CENTER, spaceAfter=8))
    styles.add(ParagraphStyle(name="Section", parent=styles["Heading2"], fontName=bold_font, fontSize=15, leading=19, textColor=colors.HexColor("#0F172A"), spaceBefore=16, spaceAfter=8))
    styles.add(ParagraphStyle(name="MetricLabel", parent=styles["BodyText"], fontName=bold_font, fontSize=8, leading=10, textColor=colors.HexColor("#0052FF"), spaceAfter=3))
    styles.add(ParagraphStyle(name="MetricValue", parent=styles["BodyText"], fontName=bold_font, fontSize=17, leading=20, textColor=colors.HexColor("#0F172A"), spaceAfter=4))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontName=regular_font, fontSize=8.5, leading=11, textColor=colors.HexColor("#475569")))
    styles.add(ParagraphStyle(name="Meta", parent=styles["BodyText"], fontName=regular_font, fontSize=9, leading=12, textColor=colors.HexColor("#64748B"), alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="RiskValue", parent=styles["BodyText"], fontName=bold_font, fontSize=21, leading=25, textColor=colors.white, alignment=TA_CENTER))

    def text(value, style="BodyText") -> Paragraph:
        return Paragraph(html.escape(str(value)), styles[style])

    def metric_grid(items: list[tuple[str, str, str]]) -> Table:
        rows = []
        for start in range(0, len(items), 2):
            cells = []
            for label, value, interpretation in items[start : start + 2]:
                cells.append([
                    text(label.upper(), "MetricLabel"),
                    text(value, "MetricValue"),
                    text(interpretation, "Small"),
                ])
            while len(cells) < 2:
                cells.append("")
            rows.append(cells)
        table = Table(rows, colWidths=[3.25 * inch, 3.25 * inch], hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 11),
            ("RIGHTPADDING", (0, 0), (-1, -1), 11),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        return table

    def section(title: str, intro: str, items: list[tuple[str, str, str]]) -> list:
        return [KeepTogether([text(title, "Section"), text(intro), Spacer(1, 7), metric_grid(items)])]

    def pct(value) -> str:
        return "Not available" if pd.isna(value) else f"{value:.1%}"

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont(regular_font, 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(0.65 * inch, 0.35 * inch, "Sink or Swim | ZIP-level screening report")
        canvas.drawRightString(7.85 * inch, 0.35 * inch, f"Page {doc.page}")
        canvas.restoreState()

    score_available = row.get("data_coverage", 0) >= 0.5 and pd.notna(row.get("risk_index"))
    score_text = f"{row['risk_index']:.1f}/100" if score_available else "Limited evidence"
    category_text = str(row.get("risk_category", "Rating unavailable")) if score_available else "Rating unavailable"
    drivers = score_drivers(row)
    summary_lines = findings + ([peer_text] if peer_text else []) + ["Recommended follow-up:"] + actions
    risk_panel = Table(
        [[
            [text("RELATIVE RISK INDEX", "MetricLabel"), text(score_text, "RiskValue"), text(category_text, "Meta")],
            [text("EXECUTIVE SUMMARY", "MetricLabel")] + [text(line, "Small") for line in summary_lines],
        ]],
        colWidths=[2.0 * inch, 4.5 * inch],
    )
    risk_panel.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#0F172A")),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))

    story = [
        text("Sink or Swim", "ReportTitle"),
        text(f"Flood risk report for ZIP {row['zip_code']}, {row['borough']}", "Meta"),
        Spacer(1, 14),
        risk_panel,
        text("The index compares represented NYC ZIPs. It is not annual flood probability, actuarial pricing, or a parcel-level determination.", "Small"),
        text(
            f"Index component completeness: {row.get('data_coverage', 0):.0%}. This is the share of weighted index inputs available, not complete geographic, sensor, property or claims coverage.",
            "Small",
        ),
    ]
    if drivers:
        story.extend(section(
            "Why this rating",
            "The three drivers group the six weighted index inputs. Ordering reflects each group's contribution to the final missing-aware score.",
            [
                (
                    driver["label"],
                    driver["signal"].title(),
                    f"Raw evidence: {driver['raw_summary']}. Grouped input score {driver['score']:.0f}/100; {driver['effective_weight']:.0%} of available index weight. {driver['explanation']}",
                )
                for driver in drivers
            ],
        ))

    if pd.notna(row.get("mapped_fvi_tracts")) and row.get("mapped_fvi_tracts", 0) > 0:
        story.extend(section(
            "Modeled Flood Vulnerability Index",
            "FVI is relative vulnerability within modeled storm-surge or tidal exposure areas, not the probability of flooding in a particular year.",
            [
                ("2020s FVI", f"{row['risk_2020s_score']:.1f}/100", "Near-term relative vulnerability for current review and mitigation planning."),
                ("2050s FVI", f"{row['risk_2050s_score']:.1f}/100", "Medium-term modeled evidence for long-lived exposure and resilience planning."),
                ("2080s FVI", f"{row['risk_2080s_score']:.1f}/100", "Long-horizon scenario evidence with greater uncertainty."),
                (
                    "Modeled FVI tract coverage",
                    f"{_number(row.get('mapped_fvi_tracts'))} of {_number(row.get('linked_fvi_tracts'))}",
                    "Only linked tracts with a modeled FVI value contribute; missing values are not treated as zero.",
                ),
            ],
        ))
    if pd.notna(row.get("sensor_count")) and row.get("sensor_count", 0) > 0:
        story.extend(section(
            "Observed FloodNet evidence",
            "FloodNet records direct street-level observations at selected sensors. It does not provide complete ZIP coverage.",
            [
                ("Observed events", _number(row.get("observed_events")), "Recorded events at monitored locations."),
                ("Events per sensor-year", _number(row.get("events_per_sensor_year"), 2), "Frequency adjusted for unequal monitoring time."),
                ("Maximum depth", _number(row.get("maximum_depth_inches"), 1) + " in", "Severity flag for basement, utility and backflow review."),
                ("Median drainage time", _number(row.get("median_drainage_minutes"), 0) + " min", "Persistence evidence that supports drainage investigation."),
                ("Events over 12 inches", _number(row.get("events_over_12_inches")), "Count of monitored events exceeding 12 inches."),
                ("Monitoring footprint", f"{_number(row.get('sensor_count'))} sensors", "A missing sensor must not be interpreted as no flood risk."),
            ],
        ))
    if pd.notna(row.get("claim_count")) and row.get("claim_count", 0) > 0:
        story.extend(section(
            "NFIP claims and payments",
            "Claims are aggregated by reported ZIP and cannot establish the searched property's loss history.",
            [
                ("Claim records", _number(row.get("claim_count")), "Historical NFIP records in the reported ZIP."),
                ("Paid claims", _number(row.get("paid_claim_count")), "Claims with a positive payment."),
                ("Total paid", "$" + _number(row.get("total_claim_dollars_paid"), 0), "Historical insured consequence, not expected future loss."),
                ("Average paid claim", "$" + _number(row.get("average_payment_per_paid_claim"), 0), "ZIP-level historical severity, not a parcel estimate."),
            ],
        ))
    if pd.notna(row.get("multiple_loss_properties")) and row.get("multiple_loss_properties", 0) > 0:
        story.extend(section(
            "Repeated loss and property indicators",
            "These indicators describe the FEMA multiple-loss extract, not the full building stock in the ZIP.",
            [
                ("Multiple-loss properties", _number(row.get("multiple_loss_properties")), "Property count in the repeated-loss extract."),
                ("Total repeat losses", _number(row.get("total_repeat_losses")), "Historical recurrence associated with those properties."),
                ("Severe repetitive-loss properties", _number(row.get("severe_repetitive_loss_properties")), "FEMA classification supporting targeted review."),
                ("Currently insured share", pct(row.get("currently_insured_share")), "Share within the multiple-loss extract."),
                ("Post-FIRM construction share", pct(row.get("post_firm_construction_share")), "Construction-era indicator, not proof of resilience."),
                ("Recorded mitigation share", pct(row.get("mitigation_share")), "Potentially incomplete; verify at property level."),
            ],
        ))
    if pd.notna(row.get("average_fshri")):
        story.extend(section(
            "Preparedness and recovery support",
            "FSHRI describes susceptibility to harm and recovery capacity. It supports mitigation and preparedness planning only and must not be used to increase premiums or restrict coverage.",
            [
                ("Average FSHRI", _number(row.get("average_fshri"), 2), "Higher values indicate greater support needs, not greater flood probability."),
                ("High-FSHRI tract share", pct(row.get("high_fshri_share")), "Share of linked tracts with FSHRI at or above 4."),
            ],
        ))
    if not neighbors.empty:
        nearby_rows = [[text(x, "MetricLabel") for x in ["ZIP", "Borough", "Miles", "Index", "Category", "Confidence"]]]
        for nearby in neighbors.itertuples():
            nearby_rows.append([
                text(nearby.zip_code, "Small"), text(nearby.borough, "Small"), text(f"{nearby.distance_miles:.1f}", "Small"),
                text(f"{nearby.risk_index:.1f}", "Small"), text(nearby.risk_category, "Small"), text(nearby.confidence, "Small"),
            ])
        nearby_table = Table(nearby_rows, colWidths=[0.65 * inch, 1.15 * inch, 0.55 * inch, 0.55 * inch, 0.9 * inch, 0.85 * inch], repeatRows=1)
        nearby_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(KeepTogether([
            text("Nearby monitored ZIP comparison", "Section"),
            nearby_table,
            text("Distances use FloodNet sensor centroids rather than shared ZIP boundaries.", "Small"),
        ]))

    story.append(KeepTogether([
        text("Method and decision boundary", "Section"),
        text("The screening index combines NFIP loss concentration evidence (25%), FloodNet event frequency (20%), observed depth and drainage severity (15%), modeled 2020s FVI (15%), 2050s FVI (15%), and 2080s FVI (10%). Missing components are omitted and available weights are renormalized. Claims are historical volumes without a complete insured-property denominator. Verify parcel flood zone, elevation, basement and utility exposure, construction, drainage and completed mitigation before acting on price or coverage."),
        Spacer(1, 10),
        text("Evidence periods and source snapshots", "Section"),
        *[
            text(f"{source}: {period}. {retrieved}.", "Small")
            for source, period, retrieved in EVIDENCE_PROVENANCE
        ],
        Spacer(1, 10),
        text("Sources: NYC Flood Vulnerability Index and FloodNet; FEMA NFIP Multiple Loss Properties V1 and NFIP Redacted Claims V3; NYC Planning GeoSearch for address resolution.", "Small"),
    ]))
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def inject_app_styles():
    """Apply the centralized, responsive Minimalist Modern design layer."""
    st.markdown(
        """
        <style>
        :root {
          --swiss-bg: #FAFAFA; --swiss-fg: #0F172A; --swiss-muted: #F1F5F9;
          --swiss-red: #0052FF; --swiss-line: #E2E8F0; --swiss-space: 24px;
        }
        html, body, [class*="css"], .stApp {
          font-family: Inter, Helvetica, Arial, sans-serif;
          color: var(--swiss-fg); background: var(--swiss-bg);
        }
        .stApp {
          background-image: radial-gradient(circle at 85% 5%, rgba(0,82,255,.08), transparent 28%);
        }
        .block-container { max-width: 1440px; padding: 1.35rem 3rem 4rem; }
        h1, h2 { font-family: Calistoga, Georgia, serif !important; color:#0F172A !important; letter-spacing:-.02em; line-height:1.08; }
        h3 { color:#0F172A !important; letter-spacing:-.02em; }
        h1 { font-size:clamp(3rem,7vw,5.25rem) !important; font-weight:400 !important; }
        h2 { font-size:clamp(1.85rem,3.2vw,2.7rem) !important; font-weight:400 !important; border:0; padding-top:1.35rem; }
        p, label, [data-testid="stCaptionContainer"] { line-height: 1.45; }
        .swiss-hero { position:sticky; top:.4rem; z-index:999; border:1px solid #E2E8F0; border-radius:14px; margin-bottom:.85rem; background:rgba(255,255,255,.96); backdrop-filter:blur(12px); box-shadow:0 6px 18px rgba(15,23,42,.08); }
        .swiss-hero-main { padding:.9rem 1.35rem; display:flex; align-items:center; justify-content:space-between; gap:2rem; }
        .swiss-title { font-family:Calistoga,Georgia,serif; font-size:clamp(2rem,3.5vw,3.05rem); line-height:1; letter-spacing:-.04em; font-weight:400; margin:0; white-space:nowrap; background:linear-gradient(135deg,#0F172A 30%,#0052FF); -webkit-background-clip:text; color:transparent; }
        .swiss-deck { max-width:760px; font-size:clamp(.92rem,1.25vw,1.05rem); line-height:1.35; font-weight:500; color:#475569; text-align:right; }
        div[data-testid="stForm"], div[data-testid="stVerticalBlockBorderWrapper"] { border:1px solid #E2E8F0 !important; border-radius:14px !important; background:#fff; box-shadow:0 3px 12px rgba(15,23,42,.045); }
        div[data-testid="stVerticalBlockBorderWrapper"] { padding: .3rem; }
        div[data-testid="stMetric"] { border:1px solid #DCE6F5; border-left:4px solid #0052FF; padding:1rem 1.1rem; background:#FFFFFF; border-radius:12px; box-shadow:0 4px 14px rgba(15,23,42,.05); }
        div[data-testid="stMetricLabel"] { text-transform:uppercase; letter-spacing:.08em; font-weight:700; color:#64748B; }
        div[data-testid="stMetricValue"] { font-weight:800; letter-spacing:-.035em; color:#0F172A; }
        .stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] button {
          min-height:44px; border:1px solid #CBD5E1 !important; border-radius:9px !important;
          background:#FFFFFF !important; color:#0F172A !important; font-weight:650 !important;
          text-transform:none; letter-spacing:.01em; box-shadow:0 2px 8px rgba(15,23,42,.06);
          transition:transform .15s ease, border-color .15s ease, box-shadow .15s ease;
        }
        .stButton > button:hover, .stDownloadButton > button:hover, [data-testid="stFormSubmitButton"] button:hover {
          transform:translateY(-1px); border-color:#0052FF !important; color:#0052FF !important; box-shadow:0 7px 18px rgba(15,23,42,.10);
        }
        [data-testid="stBaseButton-primary"], [data-testid="stFormSubmitButton"] button, .stDownloadButton > button {
          background:#0F172A !important; border-color:#0F172A !important; color:#FFFFFF !important;
        }
        [data-testid="stBaseButton-primary"]:hover, [data-testid="stFormSubmitButton"] button:hover, .stDownloadButton > button:hover {
          background:#0052FF !important; border-color:#0052FF !important; color:#FFFFFF !important;
        }
        input, [data-baseweb="select"] > div { border:1px solid #E2E8F0 !important; border-radius:12px !important; background:#fff !important; }
        input:focus { border-color:var(--swiss-red) !important; box-shadow:none !important; }
        [data-testid="stAlert"] { border:1px solid #E2E8F0; border-radius:12px; background:#F1F5F9; color:#0F172A; }
        [data-testid="stExpander"] { border:1px solid #E2E8F0 !important; border-radius:12px !important; }
        [data-baseweb="tab-list"] { gap:.35rem; border:1px solid #E2E8F0; border-radius:11px; background:#F1F5F9; padding:.3rem; width:max-content; }
        [data-baseweb="tab"] { border-radius:8px; padding:.65rem 1rem; font-weight:650; color:#475569; }
        [aria-selected="true"][data-baseweb="tab"] { color:#0052FF !important; background:#FFFFFF; box-shadow:0 2px 8px rgba(15,23,42,.08); }
        [data-baseweb="tab-highlight"] { background:#0052FF !important; height:2px; }
        [data-baseweb="tag"] { background:#EAF1FF !important; color:#163B78 !important; border:1px solid #C9DAF7 !important; border-radius:7px !important; }
        [data-baseweb="tag"] span { color:#163B78 !important; }
        .report-heading { margin:.3rem 0 .75rem; padding:.9rem 1.25rem; border:1px solid #DCE6F5; border-radius:14px; background:linear-gradient(125deg,#FFFFFF 62%,#EEF4FF); box-shadow:0 6px 18px rgba(15,23,42,.055); }
        .report-kicker, .section-kicker { color:#0052FF; font-size:.72rem; font-weight:900; letter-spacing:.14em; text-transform:uppercase; }
        .report-heading-title { margin:.15rem 0 .2rem; font-family:Calistoga,Georgia,serif; font-size:clamp(2rem,3.5vw,3.15rem); line-height:1; letter-spacing:-.04em; color:#0F172A; }
        .report-heading-copy { margin:0; color:#475569; font-weight:450; }
        .resolved-location { padding:.65rem .85rem; border-radius:9px; background:#F8FAFC; border:1px solid #E2E8F0; color:#475569; font-size:.9rem; font-weight:450; }
        .location-divider { color:#94A3B8; padding:0 .4rem; }
        .decision-boundary { margin:.65rem 0 .9rem; padding:.65rem .85rem; border-radius:9px; border-left:3px solid #7C9AC8; background:#F4F7FB; color:#475569; font-size:.88rem; }
        .summary-list { margin:.35rem 0 .7rem; border-top:1px solid #E2E8F0; }
        .summary-row { display:grid; grid-template-columns:10px 105px 1fr; gap:.7rem; align-items:start; padding:.65rem 0; border-bottom:1px solid #E2E8F0; color:#334155; }
        .summary-label { font-weight:700; color:#0F172A; }
        .summary-dot { width:8px; height:8px; margin-top:.4rem; border-radius:999px; background:#0052FF; }
        .summary-dot.observed { background:#0F9D88; } .summary-dot.loss { background:#7C5CFC; } .summary-dot.future { background:#E39A14; }
        .peer-block { margin:.7rem 0; padding:.75rem .9rem; border-radius:10px; background:#F2F0FF; border-left:4px solid #6D5CE7; color:#2E275F; }
        .recommendation-block { margin:.65rem 0 .75rem; padding:.75rem .9rem; border-radius:10px; background:#EAF8F1; border-left:4px solid #198754; color:#143D2A; font-weight:400; }
        .driver-table { width:100%; border-collapse:collapse; margin:.45rem 0 .7rem; font-size:.9rem; }
        .driver-table th { padding:.55rem .65rem; text-align:left; color:#64748B; font-size:.72rem; letter-spacing:.07em; text-transform:uppercase; border-bottom:1px solid #CBD5E1; }
        .driver-table td { padding:.7rem .65rem; border-bottom:1px solid #E2E8F0; vertical-align:top; color:#334155; }
        .driver-table td:first-child { color:#0F172A; font-weight:650; }
        .driver-signal { display:inline-block; color:#0052FF; background:#EEF4FF; border-radius:999px; padding:.18rem .48rem; font-size:.78rem; font-weight:600; }
        .driver-detail { padding:.35rem 0 .65rem; border-bottom:1px solid #E2E8F0; color:#475569; }
        .tab-intro { margin:.45rem 0 1rem; padding:.85rem 1rem; border-radius:12px; background:#F1F5F9; border-left:4px solid #0052FF; color:#334155; }
        .metric-comparison { margin-top:.55rem; padding:.5rem .6rem; border-radius:8px; background:#EEF4FF; color:#24466F; font-size:.78rem; line-height:1.4; }
        .borough-context { margin:.7rem 0; padding:.65rem .75rem; border-radius:9px; background:#F8FAFC; border:1px solid #E2E8F0; color:#475569; font-size:.84rem; }
        .risk-legend { display:flex; flex-wrap:wrap; gap:.35rem .8rem; justify-content:center; margin:-.25rem 0 .4rem; color:#64748B; font-size:.72rem; }
        .risk-dot { display:inline-block; width:8px; height:8px; border-radius:999px; margin-right:.3rem; }
        .risk-dot.lower { background:#72C7AC; } .risk-dot.moderate { background:#E8AC22; } .risk-dot.high { background:#EA8A3A; } .risk-dot.very-high { background:#DF5E68; }
        .confirm-kicker { color:#0052FF; font-size:.7rem; font-weight:750; letter-spacing:.12em; text-transform:uppercase; }
        .explorer-heading { margin:.75rem 0 .1rem; font-family:Calistoga,Georgia,serif; font-size:clamp(2rem,3vw,2.8rem); letter-spacing:-.035em; color:#0F172A; }
        .explorer-copy { margin:0 0 .8rem; color:#64748B; }
        .context-bar { display:flex; flex-wrap:wrap; align-items:center; gap:.45rem; margin:.65rem 0 .8rem; padding:.55rem .7rem; border:1px solid #DCE6F5; border-radius:9px; background:#F8FAFC; color:#475569; font-size:.82rem; }
        .context-bar strong { color:#0F172A; } .context-sep { color:#94A3B8; }
        [data-testid="stDataFrame"] { border:1px solid #E2E8F0; border-radius:12px; overflow:hidden; }
        section[data-testid="stSidebar"] { border-right:1px solid #E2E8F0; background:#FFFFFF; }
        section[data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding-top:1.25rem; }
        section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2,
        section[data-testid="stSidebar"] h3 { letter-spacing:-.025em; border:0; }
        hr { border:0; border-top:1px solid #E2E8F0; }
        a { color:#000; text-decoration-thickness:2px; text-underline-offset:3px; }
        a:hover { color:var(--swiss-red); }
        @media (prefers-reduced-motion: reduce) { * { transition:none !important; animation:none !important; } }
        @media (max-width: 768px) {
          .block-container { padding:1rem 1rem 4rem; }
          .swiss-hero-main { padding:.8rem 1rem; align-items:flex-start; flex-direction:column; gap:.35rem; }
          .swiss-deck { text-align:left; font-size:.82rem; }
          .swiss-title { font-size:2rem; }
          .summary-row { grid-template-columns:10px 1fr; } .summary-label { grid-column:2; } .summary-text { grid-column:2; }
          [data-baseweb="tab-list"] { width:100%; overflow-x:auto; }
          .stButton > button, .stDownloadButton > button { width:100%; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_zip_report(profiles: pd.DataFrame, losses: pd.DataFrame) -> None:
    """Render a concise on-page decision brief plus a complete downloadable report."""
    st.markdown(
        """
        <div class="report-heading">
          <div class="report-kicker">ZIP-level underwriting screen</div>
          <div class="report-heading-title">ZIP Code Report</div>
          <p class="report-heading-copy">Decision-ready flood evidence with transparent geographic and data limitations.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    result = st.session_state.get("location_result")
    if not result:
        return
    zip_code = result["zip_code"]
    match = profiles[profiles["zip_code"].eq(zip_code)]
    if match.empty:
        st.warning(f"ZIP {zip_code} is not represented in the project datasets. Try a nearby NYC ZIP.")
        return
    row = match.iloc[0]
    projected_available = pd.notna(row.get("mapped_fvi_tracts")) and row.get("mapped_fvi_tracts", 0) > 0
    observed_available = pd.notna(row.get("sensor_count")) and row.get("sensor_count", 0) > 0
    claims_available = pd.notna(row.get("claim_count")) and row.get("claim_count", 0) > 0
    findings, actions = executive_summary(row)
    drivers = score_drivers(row)
    peer_text = peer_context(row, profiles)
    zip_losses = losses[losses["zip_code"].eq(zip_code)]
    neighbors = nearest_zip_profiles(
        profiles, zip_code, result.get("latitude"), result.get("longitude"), 6
    )
    flood_risk_pdf = _build_flood_risk_pdf(
        row, findings, actions, zip_losses, neighbors, peer_text
    )

    def find_in_explorer() -> None:
        st.session_state["explorer_focus_zip"] = zip_code
        st.session_state["explorer_borough_filter"] = [row["borough"]]
        st.session_state["explorer_zip_filter"] = [zip_code]
        st.session_state.pop("location_result", None)

    report_title, explore_col, download_col = st.columns([5.2, .9, 1.25])
    report_title.markdown(
        f"<div class='resolved-location'><strong>{html.escape(str(result['label']))}</strong>"
        f"<span class='location-divider'>•</span>ZIP {html.escape(str(zip_code))}"
        f"<span class='location-divider'>•</span>{html.escape(str(row['borough']))}</div>",
        unsafe_allow_html=True,
    )
    explore_col.button(
        "Find in Explorer",
        key="find_selected_zip_in_explorer",
        on_click=find_in_explorer,
        use_container_width=True,
    )
    download_col.download_button(
        "Download flood risk report",
        flood_risk_pdf,
        f"sink_or_swim_flood_risk_report_{zip_code}.pdf",
        "application/pdf",
        type="primary",
        use_container_width=True,
    )
    st.markdown(
        f"<div class='decision-boundary'><strong>Decision boundary.</strong> This search was "
        f"resolved to ZIP {html.escape(str(zip_code))}. The report uses ZIP- and tract-level "
        "evidence only. It does not use parcel-level characteristics or the searched property's "
        "individual claim history.</div>",
        unsafe_allow_html=True,
    )

    def metric_card(
        label: str,
        value: str,
        interpretation: str,
        comparison: str | None = None,
    ) -> None:
        """Render one evidence value with its decision interpretation."""
        with st.container(border=True):
            st.metric(label, value)
            st.caption(interpretation)
            if comparison:
                st.markdown(
                    f"<div class='metric-comparison'><strong>Peer comparison.</strong> "
                    f"{html.escape(comparison)}</div>",
                    unsafe_allow_html=True,
                )

    def metric_rows(items: list[tuple]) -> None:
        """Render no more than two evidence cards in each row."""
        for start in range(0, len(items), 2):
            columns = st.columns(2)
            for column, item in zip(columns, items[start : start + 2]):
                with column:
                    metric_card(*item)

    rating_col, summary_col = st.columns([.9, 1.7])
    with rating_col:
        with st.container(border=True):
            st.markdown("<div class='section-kicker'>Relative risk index</div>", unsafe_allow_html=True)
            if row["data_coverage"] >= .5 and pd.notna(row["risk_index"]):
                render_risk_dial(row["risk_index"], str(row["risk_category"]))
                st.markdown(f"### {row['risk_category']}")
                st.write(f"{row['confidence']} confidence | {row['data_coverage']:.0%} index component completeness")
                st.caption("This compares represented NYC ZIPs. It is not annual flood probability, an actuarial premium, or a parcel determination.")
            else:
                st.markdown("#### Limited evidence")
                st.metric("Index component completeness", f"{row['data_coverage']:.0%}")
                st.caption("Too few weighted inputs are available for a reliable composite. Review the available evidence and investigate further.")
    with summary_col:
        with st.container(border=True):
            st.markdown("<div class='section-kicker'>Decision brief</div>", unsafe_allow_html=True)
            st.markdown("### Executive summary")
            summary_rows = []
            for item in findings:
                item_text = str(item)
                if item_text.startswith("NFIP"):
                    label, dot_class = "Claims", "claims"
                elif item_text.startswith("FloodNet"):
                    label, dot_class = "Observed", "observed"
                elif item_text.startswith("Repeated"):
                    label, dot_class = "Repeat loss", "loss"
                else:
                    label, dot_class = "Future", "future"
                summary_rows.append(
                    f"<div class='summary-row'><span class='summary-dot {dot_class}'></span>"
                    f"<span class='summary-label'>{label}</span>"
                    f"<span class='summary-text'>{html.escape(item_text)}</span></div>"
                )
            st.markdown(
                f"<div class='summary-list'>{''.join(summary_rows)}</div>",
                unsafe_allow_html=True,
            )
            if peer_text:
                st.markdown(
                    f"<div class='peer-block'><strong>Peer context.</strong> "
                    f"{html.escape(str(peer_text))}</div>",
                    unsafe_allow_html=True,
                )
            for item in actions:
                st.markdown(
                    f"<div class='recommendation-block'><strong>Recommended follow-up.</strong> "
                    f"{html.escape(str(item))}</div>",
                    unsafe_allow_html=True,
                )
            if drivers:
                st.divider()
                st.markdown("<div class='section-kicker'>Index transparency</div>", unsafe_allow_html=True)
                st.markdown("#### Score drivers")
                st.caption("The component score describes relative strength. Effective weight is the share of available index weight. Contribution is the number of points added to the final index.")
                signal_labels = {
                    "high relative component score": "High",
                    "above-midpoint relative component score": "Above midpoint",
                    "moderate relative component score": "Moderate",
                    "low relative component score": "Low",
                }
                driver_rows = []
                for driver in drivers:
                    driver_rows.append(
                        "<tr>"
                        f"<td>{html.escape(str(driver['label']))}</td>"
                        f"<td><span class='driver-signal'>{html.escape(signal_labels.get(driver['signal'], driver['signal']))}</span></td>"
                        f"<td>{driver['score']:.0f}/100</td>"
                        f"<td>{driver['effective_weight']:.0%}</td>"
                        f"<td><strong>{driver['contribution']:.1f} pts</strong></td>"
                        "</tr>"
                    )
                st.markdown(
                    "<table class='driver-table'><thead><tr><th>Driver</th><th>Signal</th>"
                    "<th>Score</th><th>Weight</th><th>Contribution</th></tr></thead><tbody>"
                    f"{''.join(driver_rows)}</tbody></table>",
                    unsafe_allow_html=True,
                )
                with st.expander("How the score drivers were calculated", expanded=False):
                    for driver in drivers:
                        st.markdown(
                            f"<div class='driver-detail'><strong>{html.escape(str(driver['label']))}</strong><br>"
                            f"{html.escape(str(driver['explanation']))}<br>"
                            f"<small>Raw evidence: {html.escape(str(driver['raw_summary']))}</small></div>",
                            unsafe_allow_html=True,
                        )

    st.caption("The downloadable PDF includes all relevant available indicators, interpretations, nearby monitored ZIP context, methodology and decision limitations.")
    render_provenance()

    repeated_loss_available = pd.notna(row.get("multiple_loss_properties")) and row.get("multiple_loss_properties", 0) > 0
    preparedness_available = any(pd.notna(row.get(column)) for column in [
        "average_fshri", "high_fshri_share", "post_firm_construction_share",
        "currently_insured_share", "mitigation_share", "median_drainage_minutes",
    ])
    report_sections = []
    if projected_available:
        report_sections.append(("Modeled vulnerability", "fvi"))
    if observed_available:
        report_sections.append(("Observed flooding", "observed"))
    if claims_available or repeated_loss_available:
        report_sections.append(("Claims & repeat loss", "claims"))
    if preparedness_available:
        report_sections.append(("Preparedness", "preparedness"))

    if not report_sections:
        st.info("No additional ZIP-level indicators are available. Use the Explorer for surrounding-area context and complete a property-level review.")
        return

    report_tabs = st.tabs([label for label, _ in report_sections])
    for report_tab, (_, section_key) in zip(report_tabs, report_sections):
        with report_tab:
            if section_key == "fvi":
                st.markdown("<div class='section-kicker'>Modeled exposure</div>", unsafe_allow_html=True)
                st.markdown("### Flood Vulnerability Index")
                st.markdown("<div class='tab-intro'><strong>What this shows.</strong> FVI expresses relative vulnerability within modeled storm-surge or tidal exposure areas. It is not the probability that this ZIP floods in a particular year.</div>", unsafe_allow_html=True)
                horizon_copy = {
                    "2020s": "Use this near-term signal to prioritize current property review and mitigation planning.",
                    "2050s": "Use this medium-term signal when considering long-lived property exposure and resilience investment.",
                    "2080s": "Treat this as strategic scenario evidence with greater long-horizon uncertainty.",
                }
                fvi_items = []
                for horizon in ["2020s", "2050s", "2080s"]:
                    value = row.get(f"risk_{horizon}_score")
                    if pd.notna(value):
                        fvi_items.append((
                            f"{horizon} FVI",
                            f"{value:.1f}/100",
                            horizon_copy[horizon],
                            metric_peer_comparison(
                                profiles, row, f"risk_{horizon}_score", lambda x: f"{x:.1f}/100"
                            ),
                        ))
                fvi_items.append((
                    "Modeled FVI tract coverage",
                    f"{_number(row.get('mapped_fvi_tracts'))} of {_number(row.get('linked_fvi_tracts'))}",
                    "Only linked tracts with modeled FVI values contribute. Missing values are preserved rather than converted to zero.",
                    metric_peer_comparison(
                        profiles, row, "mapped_fvi_tracts", lambda x: f"{x:,.0f} mapped tracts"
                    ),
                ))
                metric_rows(fvi_items)
                st.caption("Interpret higher FVI values as stronger relative vulnerability within NYC's modeled exposure areas. Confirm parcel flood zone, elevation and building characteristics before making an underwriting decision.")
            elif section_key == "observed":
                st.markdown("<div class='section-kicker'>Sensor evidence</div>", unsafe_allow_html=True)
                st.markdown("### FloodNet observed flooding summary")
                st.markdown("<div class='tab-intro'><strong>What this shows.</strong> FloodNet provides direct street-level observations from selected sensors. The figures show what happened where monitoring existed, not complete flood coverage for the ZIP.</div>", unsafe_allow_html=True)
                flood_items = [
                    (
                        "Observed events", _number(row["observed_events"]),
                        f"Recorded across {_number(row['sensor_count'])} monitored sensors. Unmonitored streets may have different experience.",
                        metric_peer_comparison(profiles, row, "observed_events", lambda x: f"{x:,.0f} events"),
                    ),
                    (
                        "Events per sensor-year", _number(row["events_per_sensor_year"], 2),
                        "Adjusts for unequal monitoring time but cannot remove sensor-placement bias.",
                        metric_peer_comparison(profiles, row, "events_per_sensor_year", lambda x: f"{x:.2f}"),
                    ),
                ]
                if pd.notna(row.get("maximum_depth_inches")):
                    flood_items.append((
                        "Maximum depth", f"{_number(row['maximum_depth_inches'], 1)} in",
                        "Use this severity flag to prompt basement, utility-location and backflow-prevention review.",
                        metric_peer_comparison(profiles, row, "maximum_depth_inches", lambda x: f"{x:.1f} in"),
                    ))
                if pd.notna(row.get("median_drainage_minutes")):
                    flood_items.append((
                        "Median drainage time", f"{_number(row['median_drainage_minutes'], 0)} min",
                        "Longer persistence supports local drainage investigation, but does not prove sewer failure.",
                        metric_peer_comparison(profiles, row, "median_drainage_minutes", lambda x: f"{x:,.0f} min"),
                    ))
                metric_rows(flood_items)
                st.write(f"**Depth thresholds:** FloodNet recorded {_number(row['events_over_4_inches'])} monitored events over 4 inches, {_number(row['events_over_12_inches'])} over 12 inches and {_number(row['events_over_24_inches'])} over 24 inches. Review deeper events first when prioritizing physical inspections.")
                tidal_count = int(row.get("tidal_sensor_count", 0) or 0)
                sensor_count = int(row.get("sensor_count", 0) or 0)
                st.caption(
                    f"Monitoring footprint: {tidal_count} tidal and "
                    f"{max(sensor_count - tidal_count, 0)} non-tidal sensors in this ZIP."
                )
            elif section_key == "claims":
                st.markdown("<div class='section-kicker'>Historical consequence</div>", unsafe_allow_html=True)
                st.markdown("### NFIP claims and repeated losses")
                st.markdown("<div class='tab-intro'><strong>What this shows.</strong> Claims and repeated-loss records provide ZIP-level historical context. They do not establish the searched property's loss history or predict its future loss.</div>", unsafe_allow_html=True)
                claim_items = []
                if claims_available:
                    claim_items.extend([
                        (
                            "Claim records", _number(row["claim_count"]),
                            "Filed in the reported ZIP and appropriate for area context only.",
                            metric_peer_comparison(
                                profiles, row, "claim_count", lambda x: f"{x:,.0f} records", include_borough=False
                            ),
                        ),
                        (
                            "Total paid", f"${_number(row['total_claim_dollars_paid'], 0)}",
                            "Cumulative historical payment evidence, not an expected-loss estimate.",
                            metric_peer_comparison(
                                profiles, row, "total_claim_dollars_paid", lambda x: f"${x:,.0f}", include_borough=False
                            ),
                        ),
                    ])
                if repeated_loss_available:
                    claim_items.extend([
                        (
                            "Multiple-loss properties", _number(row["multiple_loss_properties"]),
                            "Property count in FEMA's multiple-loss extract, not the complete ZIP building stock.",
                            metric_peer_comparison(profiles, row, "multiple_loss_properties", lambda x: f"{x:,.0f} properties"),
                        ),
                        (
                            "Total repeat losses", _number(row["total_repeat_losses"]),
                            "Historical recurrence associated with properties in the multiple-loss extract.",
                            metric_peer_comparison(profiles, row, "total_repeat_losses", lambda x: f"{x:,.0f} losses"),
                        ),
                    ])
                metric_rows(claim_items)
                if claims_available and pd.notna(row.get("borough_claim_dollars_paid")):
                    st.markdown(
                        f"<div class='borough-context'><strong>{html.escape(str(row['borough']))} context.</strong> "
                        f"The separate borough aggregate reports {_number(row.get('borough_paid_claim_count'))} paid claims "
                        f"and ${_number(row.get('borough_claim_dollars_paid'), 0)} paid. It is a borough benchmark from a "
                        "separate extract and is not allocated to individual ZIPs or properties.</div>",
                        unsafe_allow_html=True,
                    )
                if bool(row.get("claims_multiple_borough_labels", False)):
                    st.caption(
                        "The redacted claims source associates this reported ZIP with multiple borough labels. "
                        "The app retains the ZIP aggregate and uses the non-claims geography sources for borough assignment."
                    )
            elif section_key == "preparedness":
                st.markdown("<div class='section-kicker'>Resilience support</div>", unsafe_allow_html=True)
                st.markdown("### Preparedness and recovery support")
                st.markdown("<div class='tab-intro'><strong>What this shows.</strong> FSHRI describes susceptibility to harm and recovery capacity. Use it for mitigation support and preparedness planning, never to increase premiums or restrict coverage.</div>", unsafe_allow_html=True)
                prep_items = []
                if pd.notna(row.get("average_fshri")):
                    prep_items.append((
                        "Average FSHRI", _number(row["average_fshri"], 2),
                        "Higher values indicate greater support needs, not greater flood probability.",
                        metric_peer_comparison(profiles, row, "average_fshri", lambda x: f"{x:.2f}"),
                    ))
                if pd.notna(row.get("post_firm_construction_share")):
                    prep_items.append((
                        "Post-FIRM share", f"{row['post_firm_construction_share']:.1%}",
                        "Construction-era indicator within the multiple-loss extract, not proof of resilience.",
                        metric_peer_comparison(profiles, row, "post_firm_construction_share", lambda x: f"{x:.1%}"),
                    ))
                if pd.notna(row.get("mitigation_share")):
                    prep_items.append((
                        "Recorded mitigation share", f"{row['mitigation_share']:.1%}",
                        "May be incomplete and must be verified at property level.",
                        metric_peer_comparison(profiles, row, "mitigation_share", lambda x: f"{x:.1%}"),
                    ))
                if pd.notna(row.get("median_drainage_minutes")):
                    prep_items.append((
                        "Median drainage time", f"{_number(row['median_drainage_minutes'], 0)} min",
                        "Physical persistence evidence that supports local drainage and backflow review.",
                        metric_peer_comparison(profiles, row, "median_drainage_minutes", lambda x: f"{x:,.0f} min"),
                    ))
                metric_rows(prep_items)


def render_explorer(profiles: pd.DataFrame, fvi: pd.DataFrame, floodnet: dict) -> None:
    st.markdown("<div class='explorer-heading'>Explorer</div>", unsafe_allow_html=True)
    st.markdown("<p class='explorer-copy'>Filter the evidence in the side panel, then select a mapped ZIP or tract to open its ZIP Code Report.</p>", unsafe_allow_html=True)

    focused_zip = st.session_state.get("explorer_focus_zip")
    if focused_zip:
        focus_match = profiles[profiles["zip_code"].eq(focused_zip)]
        focus_borough = focus_match.iloc[0]["borough"] if not focus_match.empty else "NYC"

        def clear_explorer_focus() -> None:
            st.session_state.pop("explorer_focus_zip", None)
            st.session_state["explorer_zip_filter"] = []

        focus_text, focus_button = st.columns([5, 1])
        focus_text.info(f"Explorer is focused on ZIP {focused_zip}, {focus_borough}. All map tabs use this ZIP filter.")
        focus_button.button(
            "Show all ZIPs",
            key="clear_explorer_zip_focus",
            on_click=clear_explorer_focus,
            use_container_width=True,
        )

    with st.sidebar:
        st.markdown("### Explorer filters")
        st.caption("These shared population filters apply across every map tab.")
        boroughs = st.multiselect(
            "Borough",
            BOROUGH_ORDER,
            default=BOROUGH_ORDER,
            key="explorer_borough_filter",
        )
        if not boroughs:
            boroughs = BOROUGH_ORDER
        available_zips = profiles[profiles["borough"].isin(boroughs)]["zip_code"].dropna().sort_values().tolist()
        stored_zips = [zip_value for zip_value in st.session_state.get("explorer_zip_filter", []) if zip_value in available_zips]
        if stored_zips != st.session_state.get("explorer_zip_filter", []):
            st.session_state["explorer_zip_filter"] = stored_zips
        selected_zips = st.multiselect(
            "ZIP codes",
            available_zips,
            placeholder="All ZIP codes",
            key="explorer_zip_filter",
        )
        st.caption(
            "Borough and ZIP are shared geography filters. Risk category appears only inside "
            "the Risk map because applying it to other evidence layers could hide relevant claims, "
            "observations or preparedness needs."
        )
        st.caption("Blank values remain blank; they are not converted to zero unless the source is a complete count extract.")

    borough_context = "All boroughs" if len(boroughs) == len(BOROUGH_ORDER) else ", ".join(boroughs)
    zip_context = "All ZIPs" if not selected_zips else f"{len(selected_zips)} selected ZIP" + ("s" if len(selected_zips) != 1 else "")

    def render_context_bar(layer: str, metric: str) -> None:
        st.markdown(
            f"<div class='context-bar'><strong>{html.escape(layer)}</strong>"
            f"<span class='context-sep'>•</span>{html.escape(borough_context)}"
            f"<span class='context-sep'>•</span>{html.escape(zip_context)}"
            f"<span class='context-sep'>•</span>{html.escape(metric)}</div>",
            unsafe_allow_html=True,
        )

    flood_metrics = {
        "Flood frequency": "events_per_sensor_year",
        "Depth severity composite": "depth_severity_composite",
        "Drainage persistence": "median_drainage_minutes",
    }
    flood_notes = {
        "Flood frequency": "Observed events divided by active sensor-years. This adjusts for unequal monitoring time but not sensor-placement bias.",
        "Depth severity composite": "Equal-weight mean of NYC-wide percentile ranks for median depth, maximum depth, events over 12 inches and events over 24 inches. The result ranges from 0 to 100.",
        "Drainage persistence": "Median measured drainage time in minutes. It is physical persistence evidence, not proof of a specific sewer or infrastructure cause.",
    }
    claim_metrics = {
        "Historical claim volume": "claim_count",
        "Financial loss composite": "financial_loss_composite",
        "Repeat-loss composite": "repeat_loss_composite",
    }
    claim_notes = {
        "Historical claim volume": "Historical NFIP claim records aggregated by reported ZIP. It is not a claim rate because the project does not contain a complete insured-property denominator.",
        "Financial loss composite": "Equal-weight mean of NYC-wide percentile ranks for total NFIP payments and average payment per paid claim. It measures historical financial concentration, not exposure-normalized property risk.",
        "Repeat-loss composite": "Equal-weight mean of NYC-wide percentile ranks for multiple-loss properties, total repeat losses and severe repetitive-loss properties. It measures historical concentration and is not normalized by ZIP building stock.",
    }
    prep_metrics = {
        "FSHRI": "average_fshri",
        "Property preparedness composite": "property_preparedness_composite",
        "Drainage persistence": "median_drainage_minutes",
    }
    prep_notes = {
        "FSHRI": "Flood-Specific Social Resilience Index at census-tract level. Use it to guide mitigation and recovery support only, never premium increases or coverage restrictions.",
        "Property preparedness composite": "Equal-weight mean of post-FIRM construction, current insurance and recorded mitigation shares within FEMA's multiple-loss extract. The 0-100 result does not describe the full ZIP building stock.",
        "Drainage persistence": "Median FloodNet drainage time in minutes. Longer times support drainage and backflow investigation but do not establish the cause.",
    }
    profile_metrics = profiles.copy()
    financial_columns = ["total_claim_dollars_paid", "average_payment_per_paid_claim"]
    profile_metrics["financial_loss_composite"] = _percentile_composite(profile_metrics, financial_columns)
    profile_metrics.loc[profile_metrics[financial_columns].fillna(0).sum(axis=1).le(0), "financial_loss_composite"] = pd.NA
    repeat_columns = ["multiple_loss_properties", "total_repeat_losses", "severe_repetitive_loss_properties"]
    profile_metrics["repeat_loss_composite"] = _percentile_composite(profile_metrics, repeat_columns)
    profile_metrics.loc[profile_metrics[repeat_columns].fillna(0).sum(axis=1).le(0), "repeat_loss_composite"] = pd.NA
    preparedness_columns = ["post_firm_construction_share", "currently_insured_share", "mitigation_share"]
    profile_metrics["property_preparedness_composite"] = profile_metrics[preparedness_columns].mean(axis=1, skipna=True) * 100
    profile_metrics.loc[profile_metrics[preparedness_columns].isna().all(axis=1), "property_preparedness_composite"] = pd.NA
    data = profile_metrics[profile_metrics["borough"].isin(boroughs)].copy()
    if selected_zips: data = data[data["zip_code"].isin(selected_zips)]
    if data.empty:
        st.warning("No ZIP profiles match these filters."); return
    filtered_zips = set(data["zip_code"].dropna())
    sensors = floodnet["sensors"].copy()
    sensors["zip_code"] = sensors["Zipcode"].astype("string").str.extract(r"(\d{5})", expand=False)
    tract_zip = sensors.dropna(subset=["zip_code"]).groupby("GEOID")["zip_code"].agg(lambda x: x.mode().sort_values().iloc[0]).rename("zip_code").reset_index()
    risk_tab, flood_tab, claims_tab, prep_tab = st.tabs(["Risk map","Observed flooding","Claims & losses","Preparedness"])
    with risk_tab:
        st.write("Explore modeled storm-surge and tidal vulnerability across three planning horizons. This is relative vulnerability, not annual flood probability.")
        categories = st.multiselect(
            "Risk categories for this map",
            ["Lower", "Moderate", "High", "Very high"],
            default=["Lower", "Moderate", "High", "Very high"],
            key="explorer_risk_categories",
            help="This filter applies only to the Risk map.",
        )
        if not categories:
            categories = ["Lower", "Moderate", "High", "Very high"]
        risk_filtered_zips = set(
            data[data["risk_category"].isin(categories)]["zip_code"].dropna()
        )
        horizon = st.selectbox(
            "Projection period",
            ["2020s", "2050s", "2080s"],
            index=0,
            key="risk_projection_period",
            help="This control applies only to the modeled Flood Vulnerability Index map.",
        )
        render_context_bar("Risk map", f"{horizon} FVI | {len(categories)} risk categories")
        risk_notes = {
            "2020s": "Uses the higher of present-day storm-surge FVI and 2020s tidal FVI for each modeled tract.",
            "2050s": "Uses the higher of 2050s storm-surge FVI and 2050s tidal FVI for each modeled tract.",
            "2080s": "Uses the higher of 2080s storm-surge FVI and 2080s tidal FVI for each modeled tract.",
        }
        st.caption(f"Layer definition: {risk_notes[horizon]}")
        st.write(f"The map shows the stronger storm-surge or tidal FVI value for the {horizon}. Scores range from 1 to 5 within modeled exposure areas. Blank tracts are outside the modeled exposure footprint or cannot be linked to the selected ZIP filter.")
        map_data = fvi.merge(tract_zip, on="GEOID", how="left")
        map_data = map_data[map_data["borough"].isin(boroughs)].copy()
        map_data = map_data[map_data["zip_code"].isin(risk_filtered_zips)].copy()
        horizon_columns = {
            "2020s": ["FVI_storm_surge_present", "FVI_tidal_2020s"],
            "2050s": ["FVI_storm_surge_2050s", "FVI_tidal_2050s"],
            "2080s": ["FVI_storm_surge_2080s", "FVI_tidal_2080s"],
        }
        map_data["modeled_vulnerability"] = map_data[horizon_columns[horizon]].max(axis=1, skipna=True)
        if map_data.empty:
            st.info("No mapped FVI tracts match the selected filters.")
        else:
            geojson = cached_build_geojson(map_data)
            fig = px.choropleth_mapbox(map_data, geojson=geojson, locations="GEOID", featureidkey="properties.GEOID", color="modeled_vulnerability", color_continuous_scale=["#F1F5F9", "#4D7CFF", "#0052FF", "#0F172A"], range_color=(1,5), hover_name="borough", hover_data={"GEOID":True,"zip_code":True,"FSHRI":True,"modeled_vulnerability":True}, custom_data=["zip_code"], labels={"modeled_vulnerability":"FVI score","zip_code":"ZIP code","FSHRI":"FSHRI"}, mapbox_style="carto-positron", center={"lat":40.70,"lon":-73.94}, zoom=9.25, opacity=.75)
            fig.update_layout(height=680, margin={"l":0,"r":0,"t":20,"b":0}, coloraxis_colorbar={"title":f"{horizon} FVI"})
            event = st.plotly_chart(fig, use_container_width=True, key="risk_map", on_select="rerun", selection_mode="points")
            render_map_zip_option(event, profiles, "risk")
        st.info("Interpretation: use higher FVI scores to prioritize property-level review and mitigation outreach. The map is not a flood probability surface, and FSHRI must not be used to increase premiums or restrict coverage.")
    with flood_tab:
        st.write("Explore street flooding observed by FloodNet sensors, including normalized frequency, depth severity and drainage persistence.")
        flood_label = st.selectbox(
            "Observed flooding metric",
            list(flood_metrics),
            key="flood_metric",
        )
        render_context_bar("Observed flooding", flood_label)
        flood_column = flood_metrics[flood_label]
        st.caption(f"Layer definition: {flood_notes[flood_label]}")
        observed_source = floodnet["tracts"].copy()
        depth_columns = ["median_depth_inches", "maximum_depth_inches", "events_over_12_inches", "events_over_24_inches"]
        observed_source["depth_severity_composite"] = _percentile_composite(observed_source, depth_columns)
        observed = observed_source.merge(tract_zip, on="GEOID", how="left")
        observed = observed[observed["Borough"].isin(boroughs)].copy()
        observed = observed[observed["zip_code"].isin(filtered_zips)].copy()
        flood_map = fvi[fvi["borough"].isin(boroughs)].merge(observed, on="GEOID", how="inner")
        if flood_map.empty or flood_map[flood_column].dropna().empty:
            st.info("No mapped FloodNet tract evidence matches the selected filters.")
        else:
            geojson = cached_build_geojson(flood_map)
            fig = px.choropleth_mapbox(flood_map, geojson=geojson, locations="GEOID", featureidkey="properties.GEOID", color=flood_column, color_continuous_scale=["#F1F5F9","#4D7CFF","#0052FF","#0F172A"], hover_name="borough", hover_data={"GEOID":True,"zip_code":True,"sensor_count":True,"observed_events":True,"events_per_sensor_year":":.2f","maximum_depth_inches":":.1f","median_drainage_minutes":":.0f"}, custom_data=["zip_code"], labels={flood_column:flood_label,"zip_code":"ZIP code"}, mapbox_style="carto-positron", center={"lat":40.70,"lon":-73.94}, zoom=9.25, opacity=.75)
            fig.update_layout(height=680, margin={"l":0,"r":0,"t":20,"b":0}, coloraxis_colorbar={"title":flood_label})
            event = st.plotly_chart(fig, use_container_width=True, key="flood_map", on_select="rerun", selection_mode="points")
            render_map_zip_option(event, profiles, "flood")
        st.info("Interpretation: FloodNet is direct street-level evidence from selected sensors. Higher rates, depths or drainage times can flag areas for property inspection and infrastructure review, but unmonitored areas are not flood-free.")
    with claims_tab:
        st.write("Explore ZIP-level NFIP claim volume, financial consequences and repeated-loss concentration. These values cannot be assigned to individual properties.")
        claim_label = st.selectbox(
            "Claims and losses metric",
            list(claim_metrics),
            key="claim_metric",
        )
        render_context_bar("Claims and losses", claim_label)
        claim_column = claim_metrics[claim_label]
        st.caption(f"Layer definition: {claim_notes[claim_label]}")
        eligible_claim_zips = data[
            data[claim_column].notna() & data[claim_column].gt(0)
        ]["zip_code"].nunique()
        claim_map = data.dropna(subset=["latitude","longitude",claim_column]).copy()
        claim_map = claim_map[claim_map[claim_column].gt(0)]
        mapped_claim_zips = claim_map["zip_code"].nunique()
        st.caption(
            f"Map-location coverage: {mapped_claim_zips} of {eligible_claim_zips} filtered ZIPs "
            "with positive values have a FloodNet-derived centroid and can appear as markers."
        )
        if claim_map.empty:
            st.info("No monitored ZIP centroids with this claims metric match the selected filters.")
        else:
            fig = px.scatter_mapbox(claim_map, lat="latitude", lon="longitude", color=claim_column, size=claim_column, size_max=38, color_continuous_scale=["#4D7CFF","#0052FF","#0F172A"], hover_name="zip_code", hover_data={"borough":True,"claim_count":True,"paid_claim_count":True,"total_claim_dollars_paid":":$,.0f","multiple_loss_properties":True,"latitude":False,"longitude":False}, custom_data=["zip_code"], labels={claim_column:claim_label}, mapbox_style="carto-positron", center={"lat":40.70,"lon":-73.94}, zoom=9.25)
            fig.update_layout(height=680, margin={"l":0,"r":0,"t":20,"b":0}, coloraxis_colorbar={"title":claim_label})
            event = st.plotly_chart(fig, use_container_width=True, key="claims_map", on_select="rerun", selection_mode="points")
            render_map_zip_option(event, profiles, "claims")
        st.warning("Claims are aggregated by ZIP, but authoritative ZIP boundary polygons are not in the project. Markers use the average location of FloodNet sensors assigned to each ZIP. ZIPs without a sensor centroid cannot appear, so absence from this map is not evidence of no claims.")
    with prep_tab:
        st.write("Explore recovery-support needs, property preparedness indicators and drainage evidence. FSHRI is for mitigation support, not pricing.")
        prep_label = st.selectbox(
            "Preparedness metric",
            list(prep_metrics),
            key="prep_metric",
        )
        render_context_bar("Preparedness", prep_label)
        prep_column = prep_metrics[prep_label]
        st.caption(f"Layer definition: {prep_notes[prep_label]}")
        if prep_label == "FSHRI":
            prep_map = fvi[fvi["borough"].isin(boroughs)].merge(tract_zip,on="GEOID",how="left")
            prep_map=prep_map[prep_map["zip_code"].isin(filtered_zips)]
            geojson=cached_build_geojson(prep_map)
            fig=px.choropleth_mapbox(prep_map,geojson=geojson,locations="GEOID",featureidkey="properties.GEOID",color="FSHRI",range_color=(1,5),color_continuous_scale=["#F1F5F9","#4D7CFF","#0052FF","#0F172A"],hover_name="borough",hover_data={"GEOID":True,"zip_code":True,"FSHRI":True},custom_data=["zip_code"],mapbox_style="carto-positron",center={"lat":40.70,"lon":-73.94},zoom=9.25,opacity=.75)
        else:
            prep_map=data.dropna(subset=["latitude","longitude",prep_column]).copy()
            eligible_prep_zips = data[data[prep_column].notna()]["zip_code"].nunique()
            st.caption(
                f"Map-location coverage: {prep_map['zip_code'].nunique()} of {eligible_prep_zips} "
                "filtered ZIPs with values have a FloodNet-derived centroid."
            )
            fig=px.scatter_mapbox(prep_map,lat="latitude",lon="longitude",color=prep_column,size=prep_column,size_max=38,color_continuous_scale=["#F1F5F9","#4D7CFF","#0052FF"],hover_name="zip_code",hover_data={"borough":True,prep_column:True,"latitude":False,"longitude":False},custom_data=["zip_code"],labels={prep_column:prep_label},mapbox_style="carto-positron",center={"lat":40.70,"lon":-73.94},zoom=9.25)
        fig.update_layout(height=680,margin={"l":0,"r":0,"t":20,"b":0},coloraxis_colorbar={"title":prep_label})
        event = st.plotly_chart(fig, use_container_width=True, key="prep_map", on_select="rerun", selection_mode="points")
        render_map_zip_option(event, profiles, "prep")
        st.warning("FSHRI maps census tracts and supports mitigation and recovery planning only. Other preparedness layers use monitored ZIP centroids, not ZIP boundaries. None of these indicators should be used to increase premiums or restrict coverage.")

    render_provenance()


st.set_page_config(page_title="Sink or Swim", page_icon="🌊", layout="wide", initial_sidebar_state="expanded")
inject_app_styles()
px.defaults.template = "plotly_white"
px.defaults.color_discrete_sequence = ["#0052FF", "#4D7CFF", "#0F172A", "#64748B", "#94A3B8"]
st.markdown(
    """
    <header class="swiss-hero">
      <div class="swiss-hero-main">
        <div class="swiss-title">Sink or Swim</div>
        <div class="swiss-deck">Analyze NYC flood-risk exposure: from your couch to your portfolio.</div>
      </div>
    </header>
    """,
    unsafe_allow_html=True,
)
render_area_search()

try:
    fvi = cached_load_fvi(str(FVI_PATH))
    floodnet = cached_load_floodnet(str(FLOODNET_EVENTS_PATH), str(FLOODNET_SENSORS_PATH))
except (FileNotFoundError, ValueError) as exc:
    st.error(f"The project data could not be loaded: {exc}")
    st.stop()

try:
    multiple_losses = cached_load_multiple_loss(str(MULTIPLE_LOSS_PATH))
    aggregate_claims = cached_load_aggregate_claims(str(AGGREGATE_CLAIMS_PATH))
    zip_claims = cached_load_zip_claims(str(ZIP_CLAIMS_PATH))
except (FileNotFoundError, ValueError) as exc:
    st.error(f"The project data could not be loaded: {exc}")
    st.stop()

profiles = cached_build_zip_profiles(fvi, floodnet, multiple_losses, aggregate_claims, zip_claims)
if st.session_state.get("location_result"):
    render_zip_report(profiles, multiple_losses)
    st.caption("Sources: NYC Flood Vulnerability Index and FloodNet (NYC Open Data); FEMA OpenFEMA NFIP Multiple Loss Properties and NFIP Redacted Claims v3 ZIP aggregate; project borough claims extract; NYC Planning GeoSearch for address resolution.")
else:
    render_explorer(profiles, fvi, floodnet)
    st.caption("Explorer values retain their documented geographic and coverage limitations. Blank values are not treated as zero unless the source is a complete count extract.")
