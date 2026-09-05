import argparse
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------
# Command-line arguments
# ---------------------------------------------------------------------

parser = argparse.ArgumentParser(
    description="Aggregate OpenFEMA NFIP claims into NYC ZIP-level metrics."
)
parser.add_argument(
    "input_file",
    type=Path,
    help="Path to the source NfipClaimsV3.csv file.",
)
parser.add_argument(
    "--output",
    type=Path,
    default=Path("data/nyc_nfip_claims_by_zip.csv"),
    help=(
        "Destination CSV path "
        "(default: data/nyc_nfip_claims_by_zip.csv)."
    ),
)
args = parser.parse_args()

INPUT_FILE = args.input_file.expanduser().resolve()
OUTPUT_FILE = args.output.expanduser().resolve()
OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------
# NYC county identifiers
# ---------------------------------------------------------------------

COUNTY_TO_BOROUGH = {
    "36005": "Bronx",
    "36047": "Brooklyn",
    "36061": "Manhattan",
    "36081": "Queens",
    "36085": "Staten Island",
}


# ---------------------------------------------------------------------
# Validate the input file and its columns
# ---------------------------------------------------------------------

if not INPUT_FILE.exists():
    raise FileNotFoundError(
        f"Input file not found:\n{INPUT_FILE}\n\n"
        "Confirm that the filename and capitalization are correct."
    )

header = pd.read_csv(INPUT_FILE, nrows=0)
available_columns = set(header.columns)

required_columns = {
    "reportedZipCode",
    "countyCode",
    "amountPaidOnBuildingClaim",
    "amountPaidOnContentsClaim",
    "amountPaidOnIncreasedCostOfComplianceClaim",
}

missing_columns = required_columns - available_columns

if missing_columns:
    raise ValueError(
        "The NFIP file is missing these expected columns:\n"
        f"{sorted(missing_columns)}\n\n"
        "Available columns are:\n"
        f"{sorted(available_columns)}"
    )


# Read only the fields needed for the ZIP-level report.
columns_to_read = [
    "reportedZipCode",
    "countyCode",
    "amountPaidOnBuildingClaim",
    "amountPaidOnContentsClaim",
    "amountPaidOnIncreasedCostOfComplianceClaim",
]

# Include useful optional fields when present.
optional_columns = [
    "state",
    "dateOfLoss",
    "floodZone",
    "occupancyType",
]

for column in optional_columns:
    if column in available_columns:
        columns_to_read.append(column)


# ---------------------------------------------------------------------
# Process the large file in chunks
# ---------------------------------------------------------------------

payment_columns = [
    "amountPaidOnBuildingClaim",
    "amountPaidOnContentsClaim",
    "amountPaidOnIncreasedCostOfComplianceClaim",
]

chunk_summaries = []
rows_processed = 0
nyc_rows_found = 0

print(f"Reading: {INPUT_FILE.name}")
print("Processing the file in chunks. This may take several minutes.")

for chunk_number, chunk in enumerate(
    pd.read_csv(
        INPUT_FILE,
        usecols=columns_to_read,
        dtype=str,
        chunksize=100_000,
        low_memory=False,
        encoding_errors="replace",
    ),
    start=1,
):
    rows_processed += len(chunk)

    # Normalize FEMA county codes.
    chunk["countyCode"] = (
        chunk["countyCode"]
        .astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(5)
    )

    # Normalize ZIP codes to five digits.
    chunk["reportedZipCode"] = (
        chunk["reportedZipCode"]
        .astype("string")
        .str.extract(r"(\d{5})", expand=False)
    )

    # Retain claims located in NYC's five counties.
    nyc = chunk[
        chunk["countyCode"].isin(COUNTY_TO_BOROUGH)
        & chunk["reportedZipCode"].notna()
    ].copy()

    if nyc.empty:
        print(
            f"Chunk {chunk_number}: "
            f"{rows_processed:,} total rows processed"
        )
        continue

    nyc_rows_found += len(nyc)
    nyc["borough"] = nyc["countyCode"].map(COUNTY_TO_BOROUGH)

    # Convert FEMA payment fields to numeric values.
    for column in payment_columns:
        nyc[column] = pd.to_numeric(
            nyc[column]
            .astype("string")
            .str.replace(",", "", regex=False)
            .str.replace("$", "", regex=False),
            errors="coerce",
        ).fillna(0.0)

    nyc["total_claim_payment"] = nyc[payment_columns].sum(axis=1)
    nyc["paid_claim"] = nyc["total_claim_payment"] > 0

    # Summarize this chunk before continuing.
    summary = (
        nyc.groupby(
            ["borough", "reportedZipCode"],
            as_index=False,
            observed=True,
        )
        .agg(
            claim_count=("reportedZipCode", "size"),
            paid_claim_count=("paid_claim", "sum"),
            building_payments=(
                "amountPaidOnBuildingClaim",
                "sum",
            ),
            contents_payments=(
                "amountPaidOnContentsClaim",
                "sum",
            ),
            icc_payments=(
                "amountPaidOnIncreasedCostOfComplianceClaim",
                "sum",
            ),
            total_claim_dollars_paid=(
                "total_claim_payment",
                "sum",
            ),
        )
    )

    chunk_summaries.append(summary)

    print(
        f"Chunk {chunk_number}: "
        f"{rows_processed:,} total rows processed; "
        f"{nyc_rows_found:,} NYC records found"
    )


# ---------------------------------------------------------------------
# Combine the chunk-level results
# ---------------------------------------------------------------------

if not chunk_summaries:
    raise ValueError(
        "No NYC claim records were found. Check the countyCode values "
        "in the source file."
    )

combined = pd.concat(chunk_summaries, ignore_index=True)

final = (
    combined.groupby(
        ["borough", "reportedZipCode"],
        as_index=False,
        observed=True,
    )
    .agg(
        claim_count=("claim_count", "sum"),
        paid_claim_count=("paid_claim_count", "sum"),
        building_payments=("building_payments", "sum"),
        contents_payments=("contents_payments", "sum"),
        icc_payments=("icc_payments", "sum"),
        total_claim_dollars_paid=("total_claim_dollars_paid", "sum"),
    )
    .rename(columns={"reportedZipCode": "zip_code"})
)

final["average_payment_per_paid_claim"] = (
    final["total_claim_dollars_paid"]
    / final["paid_claim_count"].replace(0, pd.NA)
)

# Arrange the fields for the Streamlit application.
final = final[
    [
        "zip_code",
        "borough",
        "claim_count",
        "paid_claim_count",
        "building_payments",
        "contents_payments",
        "icc_payments",
        "total_claim_dollars_paid",
        "average_payment_per_paid_claim",
    ]
].sort_values(["borough", "zip_code"])

# Keep ZIP codes as five-character identifiers.
final["zip_code"] = final["zip_code"].astype("string").str.zfill(5)

final.to_csv(OUTPUT_FILE, index=False)


# ---------------------------------------------------------------------
# Completion report
# ---------------------------------------------------------------------

print("\nFinished successfully.")
print(f"Output file: {OUTPUT_FILE}")
print(f"NYC ZIP codes: {len(final):,}")
print(f"Total claim records: {final['claim_count'].sum():,.0f}")
print(f"Paid claims: {final['paid_claim_count'].sum():,.0f}")
print(
    "Total claim payments: "
    f"${final['total_claim_dollars_paid'].sum():,.2f}"
)
