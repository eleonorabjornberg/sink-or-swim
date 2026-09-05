# Sink or Swim

**Public app:** https://sinkorswim.streamlit.app/

Analyze NYC flood-risk exposure: from your couch to your portfolio.

Sink or Swim is a Streamlit decision app for climate-risk managers at NYC property insurance companies. It combines projected vulnerability, observed street flooding, NFIP claims, repeated losses and preparedness indicators without claiming to produce a property premium or parcel-level flood determination.

## Author

Designed, implemented, validated, and documented by [Eleonora Björnberg](https://github.com/eleonorabjornberg) as an individual project.

## Decision contract

- **Intended user:** Climate-risk manager at an NYC property insurer.
- **Decision:** Which NYC ZIP areas should be prioritized for mitigation support and enhanced property-level review?
- **Decision question:** Does the available evidence justify escalating a ZIP for property-level investigation, mitigation outreach or resilience incentives?
- **Recommendation:** Use High and Very High ZIP results as screening flags. Verify parcel flood zone, elevation, basement and utility exposure, construction characteristics, drainage conditions and completed mitigation before changing underwriting terms or pricing. Where evidence is limited, investigate further rather than interpreting missing observations as low risk.
- **Most consequential limitation:** FloodNet sensors are selectively deployed and are also used to link some census tracts to ZIPs. Unmonitored areas can therefore have limited evidence, and a parcel-level hazard or elevation assessment may change the recommendation.

FSHRI describes susceptibility to harm and recovery capacity. It is used only to guide mitigation assistance, outreach and recovery support. It must not be used to increase premiums, restrict coverage or penalize residents.

## Application structure

The app uses two focused views without a permanent view selector. The default Explorer contains the maps and sidebar filters. A persistent search bar titled **Look for your desired area directly** accepts an NYC address or five-digit ZIP and opens a dedicated ZIP report view, hiding the Explorer to reduce distraction.

### ZIP Code Report

Enter an NYC address or five-digit ZIP code, or select a mapped ZIP in Explorer and choose **Explore ZIP**. Address lookup resolves the location to a ZIP; it does not create parcel-level evidence.

The report's top action row contains **Find in Explorer** and **Download flood risk report**. Find in Explorer returns to the maps with the report ZIP preselected in the sidebar and identified in a visible focus message. **Show all ZIPs** removes that focus. Keeping export beside navigation prevents the download action from floating beneath the risk card.

While a report is open, the full search form is replaced by a collapsed **Search another address** control so the report begins closer to the top of the page. If NYC Planning GeoSearch returns multiple matches, the app requires the user to confirm the intended result. Every address report states that the address was resolved to a ZIP and that no parcel-level characteristics or individual property claim history were used.

The on-page report is intentionally concise. A compact branded masthead introduces the report, while the opening places a reduced risk dial beside a single consolidated executive decision brief. The dial uses an unclipped numeric scale and a separate four-band legend. Findings appear as scan-friendly rows, peer context and recommended action retain restrained color cues, and score drivers use a compact comparison table instead of stacked cards. The table distinguishes component score, effective weight and point contribution to the final index; raw evidence and calculation notes remain available in an expander. Flat, minimalist controls keep report navigation prominent without competing with the analysis. Supported evidence is then organized into conditional tabs for modeled vulnerability, observed flooding, claims and repeat loss, and preparedness. Tabs without supporting data are omitted. Every tab begins with a short explanation of its evidence and limitations. Each tab uses no more than two metric cards per row, with a decision-focused interpretation and a descriptive peer comparison directly beneath every number. Comparisons state the share of represented NYC ZIP profiles below the selected value and show the NYC median; borough medians appear only when the source geography supports a defensible borough assignment. Claims comparisons remain NYC-wide because reported mailing ZIPs can carry conflicting source borough labels. The report focuses on:

- a 0-100 relative risk-index dial, confidence and index component completeness;
- a decision-oriented executive summary;
- NYC-wide and borough peer context for the composite rating;
- three transparent score drivers grouped into NFIP loss concentration, observed flooding and modeled FVI, including raw evidence, standardized score and effective weight;
- FVI figures for the 2020s, 2050s and 2080s;
- interpreted FloodNet frequency, severity and drainage evidence; and
- historical NFIP claim counts, paid claims and payments.

Each figure is paired with an interpretation rather than presented as a wall of numbers. The **download flood risk report** button creates a PDF containing all relevant evidence available for the ZIP, including repeated-loss properties, flood-zone distribution, construction and insurance indicators, FSHRI, nearby monitored ZIP context, methodology and limitations.

The dial uses four directly labeled, high-contrast bands: Lower (0-24), Moderate (25-49), High (50-74) and Very high (75-100). The score-driver section groups the six actual index inputs and orders the three groups by their contribution to the final missing-aware score. Each driver displays its grouped input score and share of available index weight.

### Explorer

Explorer provides synchronized borough and ZIP filters in the sidebar. Risk category appears only inside the Risk map because applying it to other evidence layers could hide relevant claims, observations or preparedness needs. The filter chips and active navigation use the same restrained blue design system as the rest of the app. Each map tab contains only its own metric control, and the projection-period selector appears only in the Risk map tab because it applies specifically to FVI. A compact context bar above every map states the active layer, borough scope, ZIP scope and metric or planning horizon. The same geography filter carries across four map tabs:

- **Risk map:** Census-tract FVI for the 2020s, 2050s or 2080s
- **Observed flooding:** FloodNet evidence aggregated to linked census tracts
- **Claims and losses:** ZIP aggregates displayed at monitored ZIP centroids
- **Preparedness:** Census-tract FSHRI and monitored ZIP-centroid property indicators

Centroid markers are reference points derived from FloodNet sensors. They are not ZIP boundaries, and ZIPs without a sensor centroid cannot appear on those layers.

Every map tab begins with a short explanation of its content, geographic grain and decision boundary. Map features carry their linked ZIP code when the data support one. Selecting a feature reveals an **Explore ZIP** action that opens the same dedicated ZIP report generated by the search bar.

Each map tab offers exactly three layer choices:

- **Risk map:** 2020s, 2050s or 2080s FVI. Each layer uses the higher storm-surge or tidal FVI signal for the selected period.
- **Observed flooding:** flood frequency, depth severity composite or drainage persistence. The depth composite equally weights NYC-wide percentile ranks for median depth, maximum depth, events over 12 inches and events over 24 inches.
- **Claims and losses:** claim volume, financial loss composite or repeat-loss composite. The financial composite equally weights total-payment and average-paid-claim percentile ranks. The repeat-loss composite equally weights multiple-loss properties, total repeat losses and severe repetitive-loss properties.
- **Preparedness:** FSHRI, property preparedness composite or drainage persistence. The property composite equally weights post-FIRM construction, current insurance and recorded mitigation shares within FEMA's multiple-loss extract.

The active layer's construction note is displayed immediately above its map. Composite scores range from 0 to 100. They are descriptive screening indicators, not fitted actuarial models.

## Relative risk index

The index is a transparent screening score, not a fitted actuarial model.

| Component | Weight | Rationale |
|---|---:|---|
| NFIP loss concentration evidence | 25% | Most direct historical financial-consequence signal available at ZIP level; not normalized by insured-property count |
| Historical FloodNet event rate | 20% | Direct observed frequency normalized by active sensor-years |
| Observed depth and drainage severity | 15% | Captures physical intensity and persistence beyond event count |
| 2020s modeled FVI | 15% | Near-term vulnerability relevant to current portfolio planning |
| 2050s modeled FVI | 15% | Medium-term vulnerability relevant to long-lived property exposure |
| 2080s modeled FVI | 10% | Strategic signal down-weighted for longer-horizon uncertainty |

Empirical components are percentile-ranked among represented ZIPs. FVI values are scaled from 0 to 5 into a 0 to 100 signal. Missing FVI remains missing and is never converted to zero. Missing components are omitted and remaining weights are normalized. The app displays **index component completeness** and confidence beside the score. Component completeness is the share of weighted inputs present; it is not complete geographic, sensor, claims or property coverage.

NFIP claim counts and dollars are historical volumes. The project does not contain a complete insured-property or policy denominator, so they are not claim rates. The NFIP component is therefore described as historical loss concentration, not exposure-normalized parcel risk.

### Weight sensitivity analysis

`risk_weight_sensitivity()` compares the baseline with two plausible alternatives:

- **Loss-forward:** NFIP loss evidence increases from 25% to 35%.
- **Physical-forward:** observed event frequency and severity receive a combined 45%.

| Scenario | Spearman rank correlation | Baseline top-20 overlap | Mean absolute score change |
|---|---:|---:|---:|
| Loss-forward | 0.9747 | 18 of 20 | 2.84 points |
| Physical-forward | 0.9856 | 20 of 20 | 2.06 points |

The ranking is stable under these alternatives. This does not make the weights actuarial. It shows that the main screening result is not driven by one narrow weighting choice. `validate_data.py` recomputes the analysis and enforces minimum stability thresholds.

## Independent headline verification

The ZIP claims snapshot contains 43,996 claim records, 36,528 paid claims and $1,466,518,481.69 in total payments. Validation reads the saved snapshots directly, checks record counts and ZIP uniqueness, and reconciles total payments to building, contents and Increased Cost of Compliance components. It also independently recomputes the complete weighted index for ZIP 11201 from its six displayed components and confirms the result matches the app profile.

Run all checks with `python validate_data.py`.

## Data provenance

| Ref. | Dataset and final use | Accessed | Filters and grain |
|---|---|---|---|
| [1] | NYC Flood Vulnerability Index. Projected FVI and FSHRI maps | 2026-08-23 | NYC census tracts; storm-surge and tidal horizons |
| [2] | FloodNet street-flooding events. Frequency, depth, duration and drainage | 2026-08-23 | Events joined to active sensor metadata by Sensor ID |
| [3] | FloodNet sensor metadata. Monitoring exposure, tract and ZIP linkage | 2026-08-22 | Active sensor-years through latest event date |
| [4] | NFIP Multiple Loss Properties v1. Repeated-loss and preparedness indicators | 2026-08-22 | Five NYC counties; ZIP aggregate; corrupted block-group and rounded coordinates excluded |
| [5] | FloodSmart historical NFIP claims information. Supplementary context | 2026-08-23 | New York, 2025 view; not assigned to tracts or addresses |
| [6] | NFIP Redacted Claims v3. ZIP claim counts and payments | 2026-08-23 | NYC county codes 36005, 36047, 36061, 36081 and 36085; reported-ZIP aggregate |

Downloaded analysis-ready snapshots are included in `data/`. The public repository includes only a ZIP-level aggregate of the NFIP multiple-loss data; the property-level derivative is intentionally excluded. The large NFIP claims source file is also not included. Reproduce its ZIP aggregate with:

```bash
python data_extraction.py /path/to/NfipClaimsV3.csv \
  --output data/nyc_nfip_claims_by_zip.csv
```

The redacted claims snapshot contains reported mailing ZIPs that are not necessarily NYC decision geographies. The app therefore creates its displayed population from ZIPs anchored by FloodNet sensor metadata, FVI linkage or the NYC multiple-loss extract, then joins claims to those ZIPs. This produces 153 represented profiles. Eighty-seven claims-only reported ZIPs, containing 456 of 43,996 claim records and $7,138,410.17 of the complete snapshot payment total, remain in the reproducible claims file but do not create Explorer or report geographies.

## Bibliography

[1] M. O. of Climate and Environmental Justice (MOCEJ), “New York City’s Flood Vulnerability Index,” NYC Open Data. Accessed Aug. 23, 2026. https://data.cityofnewyork.us/Environment/New-York-City-s-Flood-Vulnerability-Index/mrjc-v9pm/about_data

[2] Department of Environmental Protection (DEP), “FloodNet: Street Flooding Events Measured by FloodNet Sensors,” NYC Open Data. Accessed Aug. 23, 2026. https://data.cityofnewyork.us/Environment/FloodNet-Street-Flooding-Events-Measured-by-FloodN/aq7i-eu5q/about_data

[3] Department of Environmental Protection (DEP), “FloodNet: Sensor Deployment Metadata,” NYC Open Data. Accessed Aug. 22, 2026. https://data.cityofnewyork.us/Environment/FloodNet-Sensor-Deployment-Metadata/kb2e-tjy3/about_data

[4] FEMA, “NFIP Multiple Loss Properties - V1.” Accessed Aug. 22, 2026. https://www.fema.gov/openfema-data-page/nfip-multiple-loss-properties-v1

[5] FEMA, “Historical NFIP Claims Information and Trends,” FloodSmart.gov. Accessed Aug. 23, 2026. https://www.floodsmart.gov/historical-nfip-claims-information-and-trends?map=countries/us/us-ny-all&region=us-ny&miny=2025&maxy=2025&county=&gtype=state

[6] FEMA, “NFIP Redacted Claims - V3.” Accessed Aug. 23, 2026. https://www.fema.gov/openfema-data-page/nfip-redacted-claims-v3

Address resolution uses NYC Planning GeoSearch: https://geosearch.planninglabs.nyc/docs/

## Important limitations

- FVI is relative vulnerability within modeled exposure areas, not annual flood probability.
- FVI does not model rainfall-driven street flooding. FloodNet supplies observed evidence only at selected locations.
- Unmonitored areas cannot be treated as flood-free.
- Two FloodNet event rows lack matching sensor metadata and are excluded.
- Thirteen monitored tract identifiers do not match FVI geometry and cannot be shaded.
- Eleven sensor-linked census tracts contain sensors assigned to two ZIPs. The app uses the most common sensor ZIP for each tract and treats the linkage as approximate.
- Multiple-loss counts are concentrations, not rates, because an insured-property or policy-year denominator is unavailable.
- Claims are ZIP aggregates and cannot establish the claim history of a searched address or property.
- Address search resolves to ZIP only. It is not parcel geocoding for underwriting.
- FVI tract-to-ZIP linkage uses the most common ZIP among FloodNet sensors in each tract, so coverage is partial.
- Reported mailing ZIPs in the redacted claims source do not create app geographies by themselves. Claims are displayed only when their ZIP is anchored by FloodNet, FVI linkage or the NYC multiple-loss extract.
- Fourteen represented claim ZIPs contain more than one borough label in the redacted source. ZIP totals are retained, while borough assignment comes from the non-claims geography sources; the report flags this condition when applicable.
- The report separately displays modeled FVI tracts and all sensor-linked tracts. Linked tracts without a modeled FVI value remain missing and do not lower the ZIP score.
- Claims and most property-preparedness layers use monitored ZIP centroids, not ZIP boundaries. The app displays marker-location coverage, and absence from a map is not interpreted as absence of claims or risk.
- Authoritative ZCTA boundaries or a maintained tract-to-ZCTA crosswalk would improve the geography, but the app does not fabricate those data.
- ZIP claim comparisons are not normalized by insured-property or policy counts because a complete denominator is unavailable. Adding an authoritative denominator is required before interpreting historical volumes as rates.
- Longer drainage duration is persistence evidence, not direct proof of sewer-capacity failure.
- Analyst-defined weights support screening only and must not be represented as actuarial coefficients.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python validate_data.py
streamlit run app.py
```

The app requires no secret values. If address lookup is unavailable, enter a five-digit ZIP directly.

## Deploy

Deploy `app.py` from the repository root on Streamlit Community Cloud. Confirm https://sinkorswim.streamlit.app/ opens without login in a private browser and compare at least one displayed ZIP result with `validate_data.py` before submission.

## Project structure

```text
app.py
src/data.py
data_extraction.py
validate_data.py
requirements.txt
data/
README.md
AI_USE_DISCLOSURE.md
```
