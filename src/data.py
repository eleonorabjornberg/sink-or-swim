import json
import math
import re
from pathlib import Path

import pandas as pd


BOROUGH_ORDER = ["Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"]

COUNTY_TO_BOROUGH = {
    "36005": "Bronx",
    "36047": "Brooklyn",
    "36061": "Manhattan",
    "36081": "Queens",
    "36085": "Staten Island",
}

BOROUGH_TO_COUNTY = {borough: county for county, borough in COUNTY_TO_BOROUGH.items()}

FVI_COLUMNS = {
    ("Storm surge", "Present"): "FVI_storm_surge_present",
    ("Storm surge", "2050s"): "FVI_storm_surge_2050s",
    ("Storm surge", "2080s"): "FVI_storm_surge_2080s",
    ("Tidal flooding", "2020s"): "FVI_tidal_2020s",
    ("Tidal flooding", "2050s"): "FVI_tidal_2050s",
    ("Tidal flooding", "2080s"): "FVI_tidal_2080s",
}

RISK_COMPONENT_WEIGHTS = {
    "historical_score": 20,
    "nfip_loss_score": 25,
    "severity_score": 15,
    "risk_2020s_score": 15,
    "risk_2050s_score": 15,
    "risk_2080s_score": 10,
}

NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def parse_multipolygon(wkt_value: str) -> list:
    """Convert the dataset's MULTIPOLYGON WKT into GeoJSON coordinates."""
    if not isinstance(wkt_value, str) or not wkt_value.startswith("MULTIPOLYGON"):
        raise ValueError("Expected MULTIPOLYGON geometry")
    body = wkt_value[len("MULTIPOLYGON") :].strip()

    def coordinate(match: re.Match) -> str:
        return json.dumps([round(float(match.group(1)), 6), round(float(match.group(2)), 6)])

    body = re.sub(rf"({NUMBER})\s+({NUMBER})", coordinate, body)
    return json.loads(body.replace("(", "[").replace(")", "]"))


def load_fvi(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"GEOID": "string"})
    required = {"the_geom", "GEOID", "FSHRI", *FVI_COLUMNS.values()}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    frame["GEOID"] = frame["GEOID"].str.zfill(11)
    frame["borough"] = frame["GEOID"].str[:5].map(COUNTY_TO_BOROUGH)
    if frame["borough"].isna().any():
        raise ValueError("The dataset contains census tracts outside NYC")

    numeric_columns = ["FSHRI", *FVI_COLUMNS.values()]
    frame[numeric_columns] = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
    return frame


def build_geojson(frame: pd.DataFrame) -> dict:
    features = []
    for row in frame[["GEOID", "the_geom"]].itertuples(index=False):
        features.append(
            {
                "type": "Feature",
                "properties": {"GEOID": row.GEOID},
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": parse_multipolygon(row.the_geom),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def summarize_layer(frame: pd.DataFrame, column: str) -> dict:
    exposed = frame[frame[column].notna()].copy()
    if exposed.empty:
        borough_summary = pd.DataFrame(
            columns=["borough", "exposed_tracts", "average_vulnerability"]
        )
    else:
        borough_summary = (
            exposed.groupby("borough", as_index=False, observed=True)
            .agg(
                exposed_tracts=("GEOID", "size"),
                average_vulnerability=(column, "mean"),
            )
            .sort_values("exposed_tracts", ascending=False)
        )
    return {
        "exposed_tracts": len(exposed),
        "score_5_tracts": int((exposed[column] == 5).sum()),
        "leading_borough": borough_summary.iloc[0]["borough"] if not borough_summary.empty else None,
        "borough_summary": borough_summary,
    }


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype("string").str.replace(",", "", regex=False), errors="coerce")


def load_floodnet(events_path: Path, sensors_path: Path) -> dict:
    """Load FloodNet events and calculate exposure-adjusted tract metrics."""
    events = pd.read_csv(events_path, dtype={"Sensor ID": "string"})
    sensors = pd.read_csv(
        sensors_path,
        dtype={"Sensor ID": "string", "Census Tract 2020": "string", "Zipcode": "string"},
    )

    event_fields = {
        "Sensor ID",
        "Flood Start Datetime (GMT)",
        "Flood End Datetime (GMT)",
        "Maximum Flood Depth (inches)",
        "Time to Drain From Peak (minutes)",
        "Total Duration (minutes)",
        "Duration of Flooding Greater Than 4 Inches (minutes)",
        "Duration of Flooding Greater Than 12 Inches (minutes)",
        "Duration of Flooding Greater Than 24 Inches (minutes)",
    }
    sensor_fields = {
        "Sensor ID",
        "Sensor Name",
        "Date Installed",
        "Date Removed",
        "Tidally Influenced",
        "Borough",
        "Census Tract 2020",
        "Latitude",
        "Longitude",
    }
    missing_events = event_fields.difference(events.columns)
    missing_sensors = sensor_fields.difference(sensors.columns)
    if missing_events or missing_sensors:
        raise ValueError(
            f"Missing FloodNet fields. Events: {sorted(missing_events)}; "
            f"Sensors: {sorted(missing_sensors)}"
        )

    events["flood_start"] = pd.to_datetime(
        events["Flood Start Datetime (GMT)"],
        format="%m/%d/%Y %I:%M:%S %p",
        errors="coerce",
        utc=True,
    )
    events["flood_end"] = pd.to_datetime(
        events["Flood End Datetime (GMT)"],
        format="%m/%d/%Y %I:%M:%S %p",
        errors="coerce",
        utc=True,
    )
    numeric_event_fields = {
        "max_depth": "Maximum Flood Depth (inches)",
        "drain_minutes": "Time to Drain From Peak (minutes)",
        "duration_minutes": "Total Duration (minutes)",
        "duration_over_4": "Duration of Flooding Greater Than 4 Inches (minutes)",
        "duration_over_12": "Duration of Flooding Greater Than 12 Inches (minutes)",
        "duration_over_24": "Duration of Flooding Greater Than 24 Inches (minutes)",
    }
    for clean_name, source_name in numeric_event_fields.items():
        events[clean_name] = _numeric(events[source_name])

    cutoff = events["flood_end"].max()
    if pd.isna(cutoff):
        raise ValueError("FloodNet contains no valid event dates")

    sensors["installed"] = pd.to_datetime(
        sensors["Date Installed"], format="%m/%d/%Y", errors="coerce", utc=True
    )
    sensors["removed"] = pd.to_datetime(
        sensors["Date Removed"], format="%m/%d/%Y", errors="coerce", utc=True
    )
    sensors["monitoring_end"] = sensors["removed"].fillna(cutoff).clip(upper=cutoff)
    sensors["active_sensor_years"] = (
        (sensors["monitoring_end"] - sensors["installed"]).dt.total_seconds()
        / (365.25 * 24 * 60 * 60)
    ).clip(lower=0)
    sensors["tidal_sensor"] = sensors["Tidally Influenced"].eq("Yes")
    sensors["Latitude"] = _numeric(sensors["Latitude"])
    sensors["Longitude"] = _numeric(sensors["Longitude"])

    tract_code = (
        sensors["Census Tract 2020"]
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(7)
        .str[-6:]
    )
    sensors["GEOID"] = sensors["Borough"].map(BOROUGH_TO_COUNTY) + tract_code
    active_sensors = sensors[sensors["active_sensor_years"] > 0].copy()

    exposure = (
        active_sensors.groupby(["GEOID", "Borough"], as_index=False, observed=True)
        .agg(
            sensor_count=("Sensor ID", "nunique"),
            tidal_sensor_count=("tidal_sensor", "sum"),
            active_sensor_years=("active_sensor_years", "sum"),
            latitude=("Latitude", "mean"),
            longitude=("Longitude", "mean"),
        )
    )

    event_join = events.merge(
        active_sensors[["Sensor ID", "GEOID", "Borough", "Zipcode", "tidal_sensor"]],
        on="Sensor ID",
        how="inner",
        validate="many_to_one",
    )
    event_join["over_4"] = event_join["duration_over_4"].gt(0)
    event_join["over_12"] = event_join["duration_over_12"].gt(0)
    event_join["over_24"] = event_join["duration_over_24"].gt(0)

    consequences = (
        event_join.groupby(["GEOID", "Borough"], as_index=False, observed=True)
        .agg(
            observed_events=("Sensor ID", "size"),
            maximum_depth_inches=("max_depth", "max"),
            median_depth_inches=("max_depth", "median"),
            median_event_duration_minutes=("duration_minutes", "median"),
            median_drainage_minutes=("drain_minutes", "median"),
            events_over_4_inches=("over_4", "sum"),
            events_over_12_inches=("over_12", "sum"),
            events_over_24_inches=("over_24", "sum"),
        )
    )

    tracts = exposure.merge(consequences, on=["GEOID", "Borough"], how="left")
    count_columns = [
        "observed_events",
        "events_over_4_inches",
        "events_over_12_inches",
        "events_over_24_inches",
    ]
    tracts[count_columns] = tracts[count_columns].fillna(0).astype(int)
    tracts["events_per_sensor_year"] = (
        tracts["observed_events"] / tracts["active_sensor_years"]
    )
    event_sensor_ids = set(events["Sensor ID"].dropna())
    metadata_sensor_ids = set(sensors["Sensor ID"].dropna())
    return {
        "tracts": tracts,
        "sensors": active_sensors,
        "events": event_join,
        "cutoff": cutoff,
        "unmatched_event_sensor_ids": sorted(event_sensor_ids - metadata_sensor_ids),
        "unmatched_event_rows": int((~events["Sensor ID"].isin(metadata_sensor_ids)).sum()),
    }


def load_multiple_loss(path: Path) -> pd.DataFrame:
    """Load the public NYC NFIP multiple-loss ZIP aggregate."""
    frame = pd.read_csv(path, dtype={"zip_code": "string"})
    required = {
        "borough",
        "zip_code",
        "multiple_loss_properties",
        "total_repeat_losses",
        "severe_repetitive_loss_properties",
        "post_firm_construction_count",
        "currently_insured_count",
        "mitigated_count",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing multiple-loss fields: {sorted(missing)}")

    frame["zip_code"] = frame["zip_code"].str.zfill(5)
    count_columns = [
        "multiple_loss_properties",
        "total_repeat_losses",
        "severe_repetitive_loss_properties",
        "post_firm_construction_count",
        "currently_insured_count",
        "mitigated_count",
    ]
    frame[count_columns] = frame[count_columns].apply(_numeric).fillna(0).astype(int)
    denominator = frame["multiple_loss_properties"].replace(0, pd.NA)
    frame["post_firm_construction_share"] = (
        frame["post_firm_construction_count"] / denominator
    )
    frame["currently_insured_share"] = (
        frame["currently_insured_count"] / denominator
    )
    frame["mitigation_share"] = frame["mitigated_count"] / denominator
    return frame


def summarize_multiple_loss(frame: pd.DataFrame, level: str) -> pd.DataFrame:
    """Aggregate multiple-loss concentration to borough or ZIP code."""
    if level == "Borough":
        group_columns = ["borough"]
    elif level == "ZIP code":
        group_columns = ["borough", "zip_code"]
    else:
        raise ValueError("level must be 'Borough' or 'ZIP code'")

    summary = frame.groupby(
        group_columns,
        as_index=False,
        dropna=False,
        observed=True,
    ).agg(
        multiple_loss_properties=("multiple_loss_properties", "sum"),
        total_repeat_losses=("total_repeat_losses", "sum"),
        severe_repetitive_loss_properties=(
            "severe_repetitive_loss_properties",
            "sum",
        ),
        post_firm_construction_count=("post_firm_construction_count", "sum"),
        currently_insured_count=("currently_insured_count", "sum"),
        mitigated_count=("mitigated_count", "sum"),
    )
    denominator = summary["multiple_loss_properties"].replace(0, pd.NA)
    summary["post_firm_construction_share"] = (
        summary["post_firm_construction_count"] / denominator
    )
    summary["currently_insured_share"] = (
        summary["currently_insured_count"] / denominator
    )
    summary["mitigation_share"] = summary["mitigated_count"] / denominator
    return summary


def load_aggregate_paid_claims(path: Path) -> pd.DataFrame:
    """Load borough-level paid NFIP claim counts and dollars from a NY extract."""
    frame = pd.read_csv(path, dtype={"Name": "string"})
    required = {"Name", "Total Paid Claims", "Total Claim Dollars Paid"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing aggregate paid-claims fields: {sorted(missing)}")

    county_to_borough = {
        "Bronx": "Bronx",
        "Kings": "Brooklyn",
        "New York": "Manhattan",
        "Queens": "Queens",
        "Richmond": "Staten Island",
    }
    frame = frame[frame["Name"].isin(county_to_borough)].copy()
    frame["borough"] = frame["Name"].map(county_to_borough)
    frame["paid_claim_count"] = _numeric(frame["Total Paid Claims"]).fillna(0).astype(int)
    frame["total_claim_dollars_paid"] = _numeric(
        frame["Total Claim Dollars Paid"].astype("string").str.replace("$", "", regex=False)
    ).fillna(0.0)
    frame["average_payment_per_paid_claim"] = (
        frame["total_claim_dollars_paid"] / frame["paid_claim_count"].replace(0, pd.NA)
    )
    return frame[
        [
            "borough",
            "Name",
            "paid_claim_count",
            "total_claim_dollars_paid",
            "average_payment_per_paid_claim",
        ]
    ].sort_values("borough")


def load_nfip_zip_claims(path: Path) -> pd.DataFrame:
    """Load and reconcile the NYC ZIP-level NFIP claims extract."""
    frame = pd.read_csv(path, dtype={"zip_code": "string", "borough": "string"})
    required = {
        "zip_code", "borough", "claim_count", "paid_claim_count",
        "building_payments", "contents_payments", "icc_payments",
        "total_claim_dollars_paid",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing ZIP-claims fields: {sorted(missing)}")
    frame["zip_code"] = frame["zip_code"].str.extract(r"(\d{5})", expand=False)
    numeric = list(required.difference({"zip_code", "borough"}))
    frame[numeric] = frame[numeric].apply(_numeric).fillna(0)
    frame = frame[frame["zip_code"].notna()].copy()
    summary = (
        frame.groupby("zip_code", as_index=False, observed=True)
        .agg(
            claim_count=("claim_count", "sum"),
            paid_claim_count=("paid_claim_count", "sum"),
            building_payments=("building_payments", "sum"),
            contents_payments=("contents_payments", "sum"),
            icc_payments=("icc_payments", "sum"),
            total_claim_dollars_paid=("total_claim_dollars_paid", "sum"),
            claims_borough=("borough", lambda x: x.mode().sort_values().iloc[0]),
            claims_source_boroughs=("borough", lambda x: ", ".join(sorted(set(x.dropna())))),
        )
    )
    summary["average_payment_per_paid_claim"] = (
        summary["total_claim_dollars_paid"]
        / summary["paid_claim_count"].replace(0, pd.NA)
    )
    return summary


def summarize_preparedness(
    fvi: pd.DataFrame,
    floodnet_events: pd.DataFrame,
    multiple_losses: pd.DataFrame,
) -> pd.DataFrame:
    """Create borough-level preparedness indicators without collapsing grains."""
    vulnerability = (
        fvi.groupby("borough", as_index=False, observed=True)
        .agg(
            average_fshri=("FSHRI", "mean"),
            high_fshri_share=("FSHRI", lambda values: values.ge(4).mean()),
        )
    )
    drainage = (
        floodnet_events.groupby("Borough", as_index=False, observed=True)
        .agg(
            median_drainage_minutes=("drain_minutes", "median"),
            events_with_drainage=("drain_minutes", "count"),
        )
        .rename(columns={"Borough": "borough"})
    )
    property_indicators = summarize_multiple_loss(multiple_losses, "Borough")
    return (
        vulnerability.merge(drainage, on="borough", how="left")
        .merge(
            property_indicators[
                [
                    "borough",
                    "multiple_loss_properties",
                    "post_firm_construction_share",
                    "currently_insured_share",
                    "mitigation_share",
                ]
            ],
            on="borough",
            how="left",
        )
    )


def geocode_nyc(query: str) -> list[dict]:
    """Resolve an NYC address with the public NYC Planning GeoSearch service."""
    import requests
    response = requests.get(
        "https://geosearch.planninglabs.nyc/v2/search",
        params={"text": query, "size": 5},
        headers={"User-Agent": "NYC-Flood-Risk-Explorer/1.0"},
        timeout=10,
    )
    response.raise_for_status()
    candidates = []
    for feature in response.json().get("features", []):
        properties = feature.get("properties", {})
        coordinates = feature.get("geometry", {}).get("coordinates", [None, None])
        postal = str(
            properties.get("postalcode")
            or properties.get("postal_code")
            or properties.get("postcode")
            or ""
        )
        match = re.search(r"\b(\d{5})\b", postal)
        if not match:
            match = re.search(r"\b(\d{5})\b", str(properties.get("label", "")))
        if match:
            candidates.append(
                {
                    "label": properties.get("label") or query,
                    "zip_code": match.group(1),
                    "longitude": coordinates[0],
                    "latitude": coordinates[1],
                    "confidence": properties.get("confidence"),
                }
            )
    return candidates


def _percentile_score(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.rank(method="average", pct=True).mul(100).where(numeric.notna())


def weighted_risk_index(frame: pd.DataFrame, weights: dict[str, float]) -> tuple[pd.Series, pd.Series]:
    """Calculate a missing-aware weighted score and its available-weight coverage."""
    if not weights or sum(weights.values()) <= 0:
        raise ValueError("Risk weights must have a positive total")
    numerator = pd.Series(0.0, index=frame.index)
    denominator = pd.Series(0.0, index=frame.index)
    for column, weight in weights.items():
        if column not in frame:
            raise ValueError(f"Missing risk component: {column}")
        available = frame[column].notna()
        numerator = numerator.add(frame[column].fillna(0).mul(weight))
        denominator = denominator.add(available.mul(weight))
    score = numerator.div(denominator.replace(0, pd.NA)).clip(0, 100)
    coverage = denominator.div(sum(weights.values()))
    return score, coverage


def risk_weight_sensitivity(frame: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    """Compare the baseline index with plausible loss-forward and physical-forward weights."""
    scenarios = {
        "Baseline": RISK_COMPONENT_WEIGHTS,
        "Loss-forward": {
            "historical_score": 15, "nfip_loss_score": 35, "severity_score": 10,
            "risk_2020s_score": 15, "risk_2050s_score": 15, "risk_2080s_score": 10,
        },
        "Physical-forward": {
            "historical_score": 25, "nfip_loss_score": 20, "severity_score": 20,
            "risk_2020s_score": 15, "risk_2050s_score": 12, "risk_2080s_score": 8,
        },
    }
    scores = pd.DataFrame(index=frame.index)
    for name, weights in scenarios.items():
        scores[name], _ = weighted_risk_index(frame, weights)
    baseline_top = set(scores["Baseline"].nlargest(min(top_n, scores["Baseline"].notna().sum())).index)
    rows = []
    for name in ["Loss-forward", "Physical-forward"]:
        valid = scores[["Baseline", name]].dropna()
        comparison_top = set(scores[name].nlargest(min(top_n, scores[name].notna().sum())).index)
        rows.append({
            "scenario": name,
            "spearman_rank_correlation": (
                valid["Baseline"].rank().corr(valid[name].rank())
            ),
            "top_20_overlap": len(baseline_top & comparison_top),
            "mean_absolute_score_change": (valid["Baseline"] - valid[name]).abs().mean(),
        })
    return pd.DataFrame(rows)


def build_zip_profiles(
    fvi: pd.DataFrame,
    floodnet: dict,
    multiple_losses: pd.DataFrame,
    aggregate_claims: pd.DataFrame,
    zip_claims: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build transparent ZIP profiles and a coverage-aware relative risk index."""
    sensors = floodnet["sensors"].copy()
    sensors["zip_code"] = sensors["Zipcode"].astype("string").str.extract(r"(\d{5})", expand=False)
    sensors = sensors[sensors["zip_code"].notna()].copy()
    sensor_summary = (
        sensors.groupby("zip_code", as_index=False, observed=True)
        .agg(
            borough=("Borough", lambda x: x.mode().sort_values().iloc[0]),
            sensor_count=("Sensor ID", "nunique"),
            tidal_sensor_count=("tidal_sensor", "sum"),
            active_sensor_years=("active_sensor_years", "sum"),
            latitude=("Latitude", "mean"),
            longitude=("Longitude", "mean"),
        )
    )

    events = floodnet["events"].copy()
    events["zip_code"] = events["Zipcode"].astype("string").str.extract(r"(\d{5})", expand=False)
    event_summary = (
        events[events["zip_code"].notna()]
        .groupby("zip_code", as_index=False, observed=True)
        .agg(
            observed_events=("Sensor ID", "size"),
            maximum_depth_inches=("max_depth", "max"),
            median_depth_inches=("max_depth", "median"),
            median_event_duration_minutes=("duration_minutes", "median"),
            median_drainage_minutes=("drain_minutes", "median"),
            events_over_4_inches=("over_4", "sum"),
            events_over_12_inches=("over_12", "sum"),
            events_over_24_inches=("over_24", "sum"),
        )
    )
    flood = sensor_summary.merge(event_summary, on="zip_code", how="left")
    event_counts = ["observed_events", "events_over_4_inches", "events_over_12_inches", "events_over_24_inches"]
    flood[event_counts] = flood[event_counts].fillna(0)
    flood["events_per_sensor_year"] = flood["observed_events"] / flood["active_sensor_years"]

    tract_zip = (
        sensors.groupby("GEOID", observed=True)["zip_code"]
        .agg(lambda values: values.mode().sort_values().iloc[0])
        .rename("zip_code")
        .reset_index()
    )
    modeled = fvi.merge(tract_zip, on="GEOID", how="inner")
    # Preserve missing FVI values. A tract outside the modeled footprint is not
    # evidence of zero vulnerability and must not lower a ZIP average.
    modeled["risk_2020s_raw"] = modeled[["FVI_storm_surge_present", "FVI_tidal_2020s"]].max(axis=1)
    modeled["risk_2050s_raw"] = modeled[["FVI_storm_surge_2050s", "FVI_tidal_2050s"]].max(axis=1)
    modeled["risk_2080s_raw"] = modeled[["FVI_storm_surge_2080s", "FVI_tidal_2080s"]].max(axis=1)
    projected = (
        modeled.groupby("zip_code", as_index=False, observed=True)
        .agg(
            borough=("borough", lambda x: x.mode().sort_values().iloc[0]),
            linked_fvi_tracts=("GEOID", "nunique"),
            mapped_fvi_tracts=("risk_2020s_raw", "count"),
            average_fshri=("FSHRI", "mean"),
            high_fshri_share=("FSHRI", lambda x: x.ge(4).mean()),
            risk_2020s_raw=("risk_2020s_raw", "mean"),
            risk_2050s_raw=("risk_2050s_raw", "mean"),
            risk_2080s_raw=("risk_2080s_raw", "mean"),
        )
    )
    losses = (
        multiple_losses[multiple_losses["zip_code"].notna()]
        .groupby("zip_code", as_index=False, observed=True)
        .agg(
            borough=("borough", lambda x: x.mode().sort_values().iloc[0]),
            multiple_loss_properties=("multiple_loss_properties", "sum"),
            total_repeat_losses=("total_repeat_losses", "sum"),
            severe_repetitive_loss_properties=(
                "severe_repetitive_loss_properties",
                "sum",
            ),
            post_firm_construction_count=("post_firm_construction_count", "sum"),
            currently_insured_count=("currently_insured_count", "sum"),
            mitigated_count=("mitigated_count", "sum"),
        )
    )
    loss_denominator = losses["multiple_loss_properties"].replace(0, pd.NA)
    losses["post_firm_construction_share"] = (
        losses["post_firm_construction_count"] / loss_denominator
    )
    losses["currently_insured_share"] = (
        losses["currently_insured_count"] / loss_denominator
    )
    losses["mitigation_share"] = losses["mitigated_count"] / loss_denominator
    # ZIP claims use reported mailing ZIPs and include a small number of records
    # outside the NYC decision geography. Build the displayed population only
    # from ZIPs anchored by an NYC geography source, then attach claim evidence.
    key_frames = [flood[["zip_code"]], projected[["zip_code"]], losses[["zip_code"]]]
    keys = pd.concat(
        key_frames,
        ignore_index=True,
    ).drop_duplicates()
    borough_claims = aggregate_claims.rename(columns={
        "paid_claim_count": "borough_paid_claim_count",
        "total_claim_dollars_paid": "borough_claim_dollars_paid",
        "average_payment_per_paid_claim": "borough_average_payment_per_paid_claim",
    })
    profile = (
        keys.merge(flood, on="zip_code", how="left")
        .merge(projected, on="zip_code", how="left", suffixes=("", "_projected"))
        .merge(losses, on="zip_code", how="left", suffixes=("", "_losses"))
    )
    profile["borough"] = profile["borough"].fillna(profile["borough_projected"]).fillna(profile["borough_losses"])
    profile = profile.drop(columns=["borough_projected", "borough_losses"])
    if zip_claims is not None:
        profile = profile.merge(zip_claims, on="zip_code", how="left")
        profile["borough"] = profile["borough"].fillna(profile["claims_borough"])
        profile["claims_multiple_borough_labels"] = (
            profile["claims_source_boroughs"].astype("string").str.contains(",", na=False)
        )
        profile["claims_borough_conflict"] = (
            profile["claims_borough"].notna()
            & profile["borough"].notna()
            & profile["claims_borough"].ne(profile["borough"])
        )
    profile = profile.merge(borough_claims, on="borough", how="left")
    if zip_claims is not None:
        zip_claim_fields = [
            "claim_count", "paid_claim_count", "building_payments", "contents_payments",
            "icc_payments", "total_claim_dollars_paid",
        ]
        profile[zip_claim_fields] = profile[zip_claim_fields].fillna(0)
    else:
        profile["claim_count"] = pd.NA
        profile["paid_claim_count"] = profile["borough_paid_claim_count"]
        profile["total_claim_dollars_paid"] = profile["borough_claim_dollars_paid"]
        profile["average_payment_per_paid_claim"] = profile["borough_average_payment_per_paid_claim"]
    loss_counts = ["multiple_loss_properties", "total_repeat_losses", "severe_repetitive_loss_properties"]
    profile[loss_counts] = profile[loss_counts].fillna(0)

    profile["historical_score"] = _percentile_score(profile["events_per_sensor_year"])
    profile["severity_score"] = pd.concat(
        [_percentile_score(profile["median_depth_inches"]), _percentile_score(profile["median_drainage_minutes"])], axis=1
    ).mean(axis=1, skipna=True)
    profile.loc[profile[["median_depth_inches", "median_drainage_minutes"]].isna().all(axis=1), "severity_score"] = pd.NA
    profile["nfip_loss_score"] = pd.concat(
        [_percentile_score(profile["total_repeat_losses"]), _percentile_score(profile["total_claim_dollars_paid"])], axis=1
    ).mean(axis=1, skipna=True)
    for horizon in ["2020s", "2050s", "2080s"]:
        profile[f"risk_{horizon}_score"] = profile[f"risk_{horizon}_raw"].clip(0, 5).mul(20)

    profile["risk_index"], profile["data_coverage"] = weighted_risk_index(
        profile, RISK_COMPONENT_WEIGHTS
    )
    profile["confidence"] = pd.cut(
        profile["data_coverage"], bins=[-0.01, 0.65, 0.85, 1.01], labels=["Limited", "Moderate", "High"], right=False
    ).astype("string")
    profile["risk_category"] = pd.cut(
        profile["risk_index"], bins=[-0.01, 25, 50, 75, 100.01], labels=["Lower", "Moderate", "High", "Very high"], right=False
    ).astype("string")
    return profile.sort_values(["borough", "zip_code"]).reset_index(drop=True)


def nearest_zip_profiles(
    profiles: pd.DataFrame, target_zip: str, latitude: float | None = None,
    longitude: float | None = None, count: int = 5,
) -> pd.DataFrame:
    """Return nearby ZIP profiles using FloodNet sensor-centroid distance."""
    target = profiles[profiles["zip_code"].eq(target_zip)]
    if target.empty:
        return profiles.iloc[0:0].copy()
    if latitude is None or longitude is None:
        latitude, longitude = target.iloc[0][["latitude", "longitude"]]
    candidates = profiles.dropna(subset=["latitude", "longitude"]).copy()
    if pd.isna(latitude) or pd.isna(longitude):
        return candidates.iloc[0:0].copy()
    lat1 = math.radians(float(latitude))
    lon1 = math.radians(float(longitude))
    candidates["distance_miles"] = candidates.apply(
        lambda row: 3958.8 * 2 * math.asin(math.sqrt(
            math.sin((math.radians(row["latitude"]) - lat1) / 2) ** 2
            + math.cos(lat1) * math.cos(math.radians(row["latitude"]))
            * math.sin((math.radians(row["longitude"]) - lon1) / 2) ** 2
        )), axis=1
    )
    return candidates[~candidates["zip_code"].eq(target_zip)].nsmallest(count, "distance_miles")
