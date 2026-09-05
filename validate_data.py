from pathlib import Path

import pandas as pd

from src.data import (
    FVI_COLUMNS,
    RISK_COMPONENT_WEIGHTS,
    build_zip_profiles,
    load_aggregate_paid_claims,
    load_floodnet,
    load_fvi,
    load_multiple_loss,
    load_nfip_zip_claims,
    risk_weight_sensitivity,
    summarize_layer,
    summarize_multiple_loss,
    summarize_preparedness,
)


frame = load_fvi(Path("data/nyc_flood_vulnerability.csv"))
assert len(frame) == 2209
assert frame["GEOID"].is_unique
assert frame["FSHRI"].dropna().between(1, 5).all()

expected_exposed = {
    ("Storm surge", "Present"): 361,
    ("Storm surge", "2050s"): 517,
    ("Storm surge", "2080s"): 612,
    ("Tidal flooding", "2020s"): 78,
    ("Tidal flooding", "2050s"): 87,
    ("Tidal flooding", "2080s"): 173,
}

for scenario, expected in expected_exposed.items():
    result = summarize_layer(frame, FVI_COLUMNS[scenario])
    assert result["exposed_tracts"] == expected, (scenario, result["exposed_tracts"])

floodnet = load_floodnet(
    Path("data/floodnet_events.csv"),
    Path("data/floodnet_sensors.csv"),
)
tracts = floodnet["tracts"]
assert len(floodnet["sensors"]) == 479
assert floodnet["sensors"]["Sensor ID"].is_unique
assert len(floodnet["events"]) == 2927
assert floodnet["unmatched_event_rows"] == 2
assert int(tracts["observed_events"].sum()) == 2927
assert int(tracts["events_over_4_inches"].sum()) == 1294
assert int(tracts["events_over_12_inches"].sum()) == 217
assert int(tracts["events_over_24_inches"].sum()) == 27
assert round(tracts["maximum_depth_inches"].max(), 2) == 46.14
assert tracts["active_sensor_years"].gt(0).all()
assert tracts[["maximum_depth_inches", "median_depth_inches", "median_event_duration_minutes", "median_drainage_minutes"]].ge(0).all().all()

multiple_losses = load_multiple_loss(Path("data/nyc_nfip_multiple_loss_by_zip.csv"))
assert len(multiple_losses) == 124
assert multiple_losses["zip_code"].dropna().str.fullmatch(r"\d{5}").all()
assert int(multiple_losses["multiple_loss_properties"].sum()) == 4486
assert int(multiple_losses["total_repeat_losses"].sum()) == 11758
assert int(multiple_losses["severe_repetitive_loss_properties"].sum()) == 205
assert int(multiple_losses["currently_insured_count"].sum()) == 1689
assert int(multiple_losses["mitigated_count"].sum()) == 7
assert len(summarize_multiple_loss(multiple_losses, "ZIP code")) == 124
assert len(summarize_multiple_loss(multiple_losses, "Borough")) == 5

aggregate_claims = load_aggregate_paid_claims(Path("data/aggregate_paid_claims.csv"))
assert len(aggregate_claims) == 5
assert int(aggregate_claims["paid_claim_count"].sum()) == 30127
assert round(aggregate_claims["total_claim_dollars_paid"].sum(), 2) == 1446476049.31
assert round(
    aggregate_claims["total_claim_dollars_paid"].sum()
    / aggregate_claims["paid_claim_count"].sum(),
    2,
) == 48012.61

preparedness = summarize_preparedness(fvi=frame, floodnet_events=floodnet["events"], multiple_losses=multiple_losses)
assert len(preparedness) == 5
assert int(frame["FSHRI"].ge(4).sum()) == 883
assert round(floodnet["events"]["drain_minutes"].median(), 2) == 53.83
assert int(preparedness["events_with_drainage"].sum()) == 2926

zip_claims = load_nfip_zip_claims(Path("data/nyc_nfip_claims_by_zip.csv"))
assert int(zip_claims["claim_count"].sum()) == 43996
assert int(zip_claims["paid_claim_count"].sum()) == 36528
assert round(zip_claims["total_claim_dollars_paid"].sum(), 2) == 1466518481.69
assert round(
    zip_claims[["building_payments", "contents_payments", "icc_payments"]].sum().sum(), 2
) == round(zip_claims["total_claim_dollars_paid"].sum(), 2)
assert zip_claims["zip_code"].is_unique

profiles = build_zip_profiles(frame, floodnet, multiple_losses, aggregate_claims, zip_claims)
assert len(profiles) == 153
assert profiles["risk_index"].between(0, 100).all()
assert profiles["zip_code"].str.fullmatch(r"\d{5}").all()
assert profiles["zip_code"].is_unique
assert profiles["data_coverage"].between(0, 1).all()
assert profiles["mapped_fvi_tracts"].fillna(0).le(
    profiles["linked_fvi_tracts"].fillna(0)
).all()
# Linked tracts without modeled exposure must remain missing, not become zero-risk evidence.
linked_but_unmodeled = profiles["linked_fvi_tracts"].gt(0) & profiles["mapped_fvi_tracts"].eq(0)
assert profiles.loc[linked_but_unmodeled, "risk_2020s_score"].isna().all()
assert profiles.loc[profiles["mapped_fvi_tracts"].gt(0), "risk_2020s_score"].notna().all()

# The displayed decision population must be anchored by at least one NYC
# geography source. Reported mailing ZIPs from the redacted claims extract do
# not create new Explorer geographies by themselves.
sensor_zips = set(
    floodnet["sensors"]["Zipcode"].astype("string").str.extract(r"(\d{5})", expand=False).dropna()
)
loss_zips = set(multiple_losses["zip_code"].dropna())
anchored_zips = sensor_zips | loss_zips | set(profiles.loc[profiles["linked_fvi_tracts"].notna(), "zip_code"])
assert set(profiles["zip_code"]).issubset(anchored_zips)
excluded_claim_zips = zip_claims[~zip_claims["zip_code"].isin(profiles["zip_code"])]
assert len(excluded_claim_zips) == 87
assert int(excluded_claim_zips["claim_count"].sum()) == 456
assert round(float(excluded_claim_zips["total_claim_dollars_paid"].sum()), 2) == 7138410.17

# Document and lock the two remaining approximate geography decisions. Eleven
# sensor-linked tracts span more than one reported ZIP, so the app uses the
# modal sensor ZIP. Fourteen represented claim ZIPs contain more than one
# source borough label, and nine disagree with the non-claims borough anchor.
sensor_zip_links = floodnet["sensors"].assign(
    zip_code=floodnet["sensors"]["Zipcode"].astype("string").str.extract(r"(\d{5})", expand=False)
)
assert int(sensor_zip_links.groupby("GEOID")["zip_code"].nunique().gt(1).sum()) == 11
assert int(profiles["claims_multiple_borough_labels"].sum()) == 14
assert int(profiles["claims_borough_conflict"].sum()) == 9

# Independent headline checks read the saved snapshots directly rather than
# relying on the project loader or aggregation functions.
raw_events = pd.read_csv("data/floodnet_events.csv", dtype={"Sensor ID": "string"})
raw_sensors = pd.read_csv("data/floodnet_sensors.csv", dtype={"Sensor ID": "string"})
assert len(raw_events) == 2929
assert int(raw_events["Sensor ID"].isin(set(raw_sensors["Sensor ID"])).sum()) == 2927
raw_zip_claims = pd.read_csv("data/nyc_nfip_claims_by_zip.csv", dtype={"zip_code": "string"})
assert int(raw_zip_claims["claim_count"].sum()) == 43996
assert round(raw_zip_claims["total_claim_dollars_paid"].sum(), 2) == 1466518481.69
assert round(
    raw_zip_claims[["building_payments", "contents_payments", "icc_payments"]].sum().sum(), 2
) == 1466518481.69
representative = profiles.loc[profiles["zip_code"].eq("11201")].iloc[0]
available_components = [
    (float(representative[column]), weight)
    for column, weight in RISK_COMPONENT_WEIGHTS.items()
    if pd.notna(representative[column])
]
manual_index = sum(score * weight for score, weight in available_components) / sum(
    weight for _, weight in available_components
)
assert round(manual_index, 10) == round(float(representative["risk_index"]), 10)
assert int(representative["claim_count"]) == 47
assert round(float(representative["total_claim_dollars_paid"]), 2) == 13551811.82

sensitivity = risk_weight_sensitivity(profiles)
assert sensitivity["spearman_rank_correlation"].ge(0.90).all()
assert sensitivity["top_20_overlap"].ge(15).all()
assert sensitivity["mean_absolute_score_change"].lt(8).all()
print(sensitivity.to_string(index=False))

print("Validation passed for all five decision modules.")
