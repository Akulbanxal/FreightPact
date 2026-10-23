"""CLI tool for historical validation of Nepal vaccine carrier thermal data.

Evaluates passive vaccine carrier empirical data against standard WHO specifications
(0.0°C to 10.0°C) with transparent data quality tracking and honest exception reporting.

PROVENANCE & CITATION:
  Dataset:
    • Title: Long-range freeze-preventive vaccine carriers in Nepal: thermal data
    • Contributors listed on Mendeley Data: Alexandra Sibole, Steven Diesburg and Sandeep Kumar
    • Version: 1
    • DOI: 10.17632/j277dvs69g.1
    • URL: https://data.mendeley.com/datasets/j277dvs69g/1
    • Licence: CC BY 4.0

  Related Publication:
    • Title: Using long-range freeze-preventive vaccine carriers in Nepal: A study of
             equipment performance, acceptability, systems fit, and cost
    • Authors: Sandeep Kumar et al., with the full author list available in the publication
    • Journal: Vaccine: X, volume 10 (2022), article 100146
    • DOI: 10.1016/j.jvacx.2022.100146
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add src to sys.path so freightpact can be imported without installation
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from freightpact.historical_validator import (
    DATASET_PROVENANCE,
    classify_nepal_file,
    parse_nepal_csv,
    summarize_cohort,
    validate_file,
)


def run_audit(dataset_dir: Path, temp_min: float = 0.0, temp_max: float = 10.0, limit: int = 0):
    if not dataset_dir.exists():
        print(f"Error: Dataset directory '{dataset_dir}' does not exist.")
        sys.exit(1)

    csv_files = sorted(list(dataset_dir.rglob("*.csv")))
    total_found = len(csv_files)
    print("=" * 80)
    print("  NEPAL VACCINE CARRIER HISTORICAL THERMAL VALIDATION AUDIT")
    print("=" * 80)
    print("PROVENANCE & CITATION:")
    print(f"  Dataset: {DATASET_PROVENANCE['dataset']['title']}")
    print(f"  Contributors (Mendeley Data): {DATASET_PROVENANCE['dataset']['contributors']}")
    print(f"  DOI: {DATASET_PROVENANCE['dataset']['doi']} | Licence: {DATASET_PROVENANCE['dataset']['licence']}")
    print(f"  URL: {DATASET_PROVENANCE['dataset']['url']}")
    print(f"  Related Publication: {DATASET_PROVENANCE['related_publication']['title']}")
    print(f"  Journal: {DATASET_PROVENANCE['related_publication']['journal']} (DOI: {DATASET_PROVENANCE['related_publication']['doi']})")
    print("-" * 80)
    print(f"Dataset root: {dataset_dir}")
    print(f"Total CSV log files discovered: {total_found}")
    print(f"Evaluation target band: [{temp_min:.1f}°C to {temp_max:.1f}°C] (WHO Passive Carrier Specification)")
    print("-" * 80)

    # 1. Classification & Inventory Reconciliation
    inventory = {
        "freeze_preventive_carrier": [],
        "standard_vaccine_carrier": [],
        "ice_pack_freezer": [],
        "ambient": [],
        "unknown": [],
    }

    for f in csv_files:
        cat = classify_nepal_file(f)
        inventory[cat].append(f)

    print("\n📦 RECONCILED EQUIPMENT INVENTORY:")
    print(f"  • Freeze-Preventive Carriers (PVC): {len(inventory['freeze_preventive_carrier'])} files")
    print(f"  • Standard Vaccine Carriers (SVC):  {len(inventory['standard_vaccine_carrier'])} files")
    print(f"  • Ice-Pack Freezers (Deep Freezers):{len(inventory['ice_pack_freezer'])} files")
    print(f"  • Ambient Air Sensors:             {len(inventory['ambient'])} files")
    print(f"  • Unknown / Unclassified Files:    {len(inventory['unknown'])} files")
    print(f"  --------------------------------------------------")
    print(f"  TOTAL RECONCILED:                  {sum(len(v) for v in inventory.values())} files")

    if inventory["unknown"]:
        print("\n⚠️ UNKNOWN / UNCLASSIFIED FILES AUDIT:")
        for u in inventory["unknown"]:
            print(f"  - {u.name}")
            print(f"    Path: {u.relative_to(dataset_dir)}")
            print(f"    Reason: Missing equipment prefix in filename and absent from master coding list.")

    # 2. File Validation
    files_to_validate = csv_files if limit <= 0 else csv_files[:limit]
    print(f"\n⏳ Processing and validating {len(files_to_validate)} files...")

    results = []
    total_records = 0
    total_valid = 0
    total_missing = 0
    total_saturated = 0
    total_operational = 0

    for idx, f in enumerate(files_to_validate):
        res = validate_file(f, temp_min=temp_min, temp_max=temp_max)
        results.append(res)
        total_records += res.data_quality.total_records_read
        total_valid += res.total_valid_readings
        total_missing += res.data_quality.missing_blank_count
        total_saturated += res.data_quality.sensor_saturation_count
        total_operational += sum(res.data_quality.operational_markers_retained.values())

    print("\n🔍 DATA QUALITY AUDIT SUMMARY:")
    print(f"  • Total raw records read:         {total_records:,}")
    print(f"  • Total valid numeric readings:   {total_valid:,}")
    print(f"  • Missing / blank records:        {total_missing:,} (excluded from denominator)")
    print(f"  • Sensor saturation (>85°C / <-40°C): {total_saturated:,} (excluded from denominator)")
    print(f"  • Operational markers retained:   {total_operational:,} (download/inspection marks with valid temps)")

    # 3. Cohort Summaries
    pvc_summary = summarize_cohort("freeze_preventive_carrier", results)
    svc_summary = summarize_cohort("standard_vaccine_carrier", results)

    print("\n📊 EMPIRICAL COHORT ANALYSIS (0.0°C to 10.0°C Standard):")
    print("-" * 80)
    print(f"{'Metric':<35} | {'PVC (Freeze-Preventive)':<18} | {'SVC (Standard Carrier)':<18}")
    print("-" * 80)
    print(f"{'Analyzed File Count':<35} | {pvc_summary.file_count:<18} | {svc_summary.file_count:<18}")
    print(f"{'Total Valid Readings':<35} | {pvc_summary.total_valid_readings:<18,d} | {svc_summary.total_valid_readings:<18,d}")
    print(f"{'Safe In-Range Readings':<35} | {pvc_summary.total_safe_readings:<18,d} | {svc_summary.total_safe_readings:<18,d}")
    print(f"{'Freeze Excursion Readings (< 0°C)':<35} | {pvc_summary.total_freeze_readings:<18,d} | {svc_summary.total_freeze_readings:<18,d}")
    print(f"{'Heat Excursion Readings (> 10°C)':<35} | {pvc_summary.total_heat_readings:<18,d} | {svc_summary.total_heat_readings:<18,d}")
    print(f"{'Overall Compliance Rate':<35} | {pvc_summary.overall_compliance_rate_percent:<17.2f}% | {svc_summary.overall_compliance_rate_percent:<17.2f}%")
    print(f"{'Freeze Excursion Rate':<35} | {pvc_summary.overall_freeze_rate_percent:<17.2f}% | {svc_summary.overall_freeze_rate_percent:<17.2f}%")
    print(f"{'Heat Excursion Rate':<35} | {pvc_summary.overall_heat_rate_percent:<17.2f}% | {svc_summary.overall_heat_rate_percent:<17.2f}%")
    print(f"{'Discrete Freeze Episodes':<35} | {pvc_summary.total_freeze_episodes:<18,d} | {svc_summary.total_freeze_episodes:<18,d}")
    print(f"{'Total Freeze Duration (hours)':<35} | {pvc_summary.total_freeze_duration_hours:<18.1f} | {svc_summary.total_freeze_duration_hours:<18.1f}")
    print(f"{'Min Observed Temp':<35} | {pvc_summary.min_observed_temperature_c:<17.1f}°C | {svc_summary.min_observed_temperature_c:<17.1f}°C")
    print(f"{'Max Observed Temp':<35} | {pvc_summary.max_observed_temperature_c:<17.1f}°C | {svc_summary.max_observed_temperature_c:<17.1f}°C")
    print("-" * 80)

    # 4. Honest Exception Reporting
    print("\n⚠️ HONEST EMPIRICAL EXCEPTIONS & LIMITATIONS AUDIT:")
    if pvc_summary.files_with_freeze_excursions:
        print(f"  Freeze-Preventive Carriers are not 100% immune from sub-zero readings.")
        print(f"  Found {len(pvc_summary.files_with_freeze_excursions)} PVC files exhibiting sub-zero readings (< 0.0°C):")
        for fname in pvc_summary.files_with_freeze_excursions:
            matching_res = next(r for r in results if r.file_name == fname)
            print(f"    • {fname}")
            print(f"      Freeze readings: {matching_res.freeze_reading_count}/{matching_res.total_valid_readings} "
                  f"({matching_res.freeze_rate_percent:.2f}%), Min Temp: {matching_res.min_temperature_c:.1f}°C, "
                  f"Episodes: {len(matching_res.freeze_episodes)}")
    else:
        print("  No sub-zero freeze excursions observed in analyzed PVC cohort.")

    print("\n🏁 Validation audit completed successfully.")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="Validate Nepal vaccine carrier thermal dataset.")
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path("data/external/nepal_vaccine_carriers"),
        help="Path to extracted Nepal dataset root directory.",
    )
    parser.add_argument(
        "--temp-min",
        type=float,
        default=0.0,
        help="Lower threshold in °C for freeze excursions (default: 0.0°C).",
    )
    parser.add_argument(
        "--temp-max",
        type=float,
        default=10.0,
        help="Upper threshold in °C for heat excursions (default: 10.0°C).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of files to process (default: 0 for all).",
    )
    args = parser.parse_args()

    # Reconfigure stdout for utf-8 on Windows
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    run_audit(
        dataset_dir=args.dataset_dir,
        temp_min=args.temp_min,
        temp_max=args.temp_max,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
