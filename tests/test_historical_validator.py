"""Unit and integration tests for historical temperature validation."""

import textwrap
from datetime import datetime
from pathlib import Path
import pytest

from freightpact.historical_validator import (
    ThermalReading,
    classify_nepal_file,
    detect_episodes,
    parse_nepal_csv,
    summarize_cohort,
    validate_file,
)


# -----------------------------------------------------------------------------
# 1. Self-contained Unit Tests: Equipment Classification
# -----------------------------------------------------------------------------

def test_classify_phase_1a_carriers_and_freezers():
    # Freeze-Preventive Carrier (LI)
    pvc = Path("Phase 1a (May-December 2018)/Round 1/Sunsari/Itahari/1000821006SUNIT01LI Started 5-8-2018.csv")
    assert classify_nepal_file(pvc) == "freeze_preventive_carrier"

    # Standard Vaccine Carrier (LS)
    svc = Path("Phase 1a (May-December 2018)/Round 1/Sunsari/Itahari/1000821005SUNIT01LS Started 5-8-2018.csv")
    assert classify_nepal_file(svc) == "standard_vaccine_carrier"

    # Ambient (LA)
    amb = Path("Phase 1a (May-December 2018)/Round 1/Sunsari/Itahari/1000821049SUNIT01LA Started 5-8-2018.csv")
    assert classify_nepal_file(amb) == "ambient"

    # LogTag Freezer (LF in Round 3)
    freezer = Path("Phase 1a (May-December 2018)/Round 3/Sunsari/Harinagara/1000821014SUNHA01LF Started 30-Jul-18.csv")
    assert classify_nepal_file(freezer) == "ice_pack_freezer"


def test_classify_distinguishes_lf_sn_as_freezer():
    # Based on Phase 1a Round 3 documentary evidence at Chumbang post (where LI, LS, LA
    # were already deployed), the 4th logger was LogTag Freezer (LF), labeled "lf-SN 1000820977"
    freezer_sn = Path("Phase 1a/Round 3/Dhankuta/Pakhribas/lf-SN 1000820977   1000820977 Started 8-2-2018.csv")
    assert classify_nepal_file(freezer_sn) == "ice_pack_freezer"

    # DHAPA 06 LF is also LogTag Freezer
    freezer = Path("Phase 1a/Round 3/Dhankuta/Pakhribas/DHAPA 06 LF  1000510595 Started 8-2-2018.csv")
    assert classify_nepal_file(freezer) == "ice_pack_freezer"


def test_classify_handles_typos_and_malformed_filenames():
    # Typo: LItarted instead of LI Started
    typo_pvc = Path("Phase 1a/Round 1/Sunsari/1000821007 S SUNHA03LItarted 09-May-18.csv")
    assert classify_nepal_file(typo_pvc) == "freeze_preventive_carrier"

    # Typo: LAa instead of LA
    typo_amb = Path("Phase 1a/Round 2/Dhankuta/DHAPA 05 LAa 1000821038 Started 5-9-2018.csv")
    assert classify_nepal_file(typo_amb) == "ambient"

    # Compound name: DA6DMahabharat
    compound_freezer = Path("Phase 2/Round I/Dandabazar/DA6DMahabharatStarted 11-14-2019.csv")
    assert classify_nepal_file(compound_freezer) == "ice_pack_freezer"

    # Compound name: BhedetarDA3PI
    compound_pvc = Path("Phase 2/Round II/Dandabazar/1000930105 Started 11-14-2019, BhedetarDA3PIFinished.csv")
    assert classify_nepal_file(compound_pvc) == "freeze_preventive_carrier"


def test_classify_explicitly_reports_unlabelled_file_as_unknown():
    # The single unlabelled file with no equipment prefix and unlisted serial
    unlabelled = Path("Phase 1a/Round 3/Dhankuta/Dandabazar/Mahabarat/1000821023 Started 7-20-2018, Finished 7-25-2018.csv")
    assert classify_nepal_file(unlabelled) == "unknown"


def test_classify_phase_1b_and_phase_2_conventions():
    # Phase 1b: PI (PVC), S (Standard), PA (Ambient), D (Deep freezer)
    assert classify_nepal_file(Path("Phase 1b/Round I/Itahari/IT1PI Started.csv")) == "freeze_preventive_carrier"
    assert classify_nepal_file(Path("Phase 1b/Round I/Itahari/IT1S Started.csv")) == "standard_vaccine_carrier"
    assert classify_nepal_file(Path("Phase 1b/Round I/Itahari/IT1PA Started.csv")) == "ambient"
    assert classify_nepal_file(Path("Phase 1b/Round I/Itahari/IT1D Started.csv")) == "ice_pack_freezer"

    # Phase 1b Pakhribas block: PA5S must be standard carrier, PA6DI deep freezer, PA5PA ambient, PA5PI PVC
    assert classify_nepal_file(Path("Phase 1b/Round I/Pakhribas/Chumbang/PA5S-Started 4-29-2019.csv")) == "standard_vaccine_carrier"
    assert classify_nepal_file(Path("Phase 1b/Round II/Pakhribas/Pakhribas/PA6DI-1000820990 Started.csv")) == "ice_pack_freezer"
    assert classify_nepal_file(Path("Phase 1b/Round I/Pakhribas/Chumbang/PA5PA- Started 4-29-2019.csv")) == "ambient"
    assert classify_nepal_file(Path("Phase 1b/Round I/Pakhribas/Chumbang/PA5PI-Started 4-29-2019.csv")) == "freeze_preventive_carrier"

    # Phase 2: PI (PVC), PA (Ambient), D (Deep freezer)
    assert classify_nepal_file(Path("Phase 2/Round I/Sitaganj/SI1PI Started.csv")) == "freeze_preventive_carrier"
    assert classify_nepal_file(Path("Phase 2/Round I/Sitaganj/SI1PA Started.csv")) == "ambient"
    assert classify_nepal_file(Path("Phase 2/Round I/Sitaganj/SI1D Started.csv")) == "ice_pack_freezer"


def test_dataset_provenance_and_licensing_metadata():
    from freightpact.historical_validator import DATASET_PROVENANCE

    dataset = DATASET_PROVENANCE["dataset"]
    pub = DATASET_PROVENANCE["related_publication"]

    # Verify Mendeley dataset metadata
    assert "Long-range freeze-preventive" in dataset["title"]
    assert dataset["contributors"] == "Alexandra Sibole, Steven Diesburg and Sandeep Kumar"
    assert dataset["doi"] == "10.17632/j277dvs69g.1"
    assert dataset["licence"] == "CC BY 4.0"

    # Verify related journal publication metadata
    assert pub["authors"] == "Sandeep Kumar et al., with the full author list available in the publication"
    assert pub["journal"] == "Vaccine: X, volume 10 (2022), article 100146"
    assert pub["doi"] == "10.1016/j.jvacx.2022.100146"


# -----------------------------------------------------------------------------
# 2. Self-contained Unit Tests: CSV Parsing & Header Variations
# -----------------------------------------------------------------------------

def test_parse_csv_format_a_explicit_header(tmp_path: Path):
    csv_content = textwrap.dedent("""\
        Index,Date,Time,Readings (°C),Type
        1,"5/9/2018","10:54:44 AM","4.2",
        2,"5/9/2018","11:04:44 AM","4.8","Inspection Mark"
        3,"5/9/2018","11:14:44 AM","5.1","Download Mark"
    """)
    file_path = tmp_path / "format_a.csv"
    file_path.write_text(csv_content, encoding="utf-8")

    readings, audit = parse_nepal_csv(file_path)
    assert len(readings) == 3
    assert audit.valid_readings_count == 3
    assert audit.header_rows_skipped == 1
    assert readings[0].temperature_c == 4.2
    assert readings[1].temperature_c == 4.8
    assert readings[1].annotation == "Inspection Mark"
    assert audit.operational_markers_retained["Inspection Mark"] == 1
    assert audit.operational_markers_retained["Download Mark"] == 1


def test_parse_csv_format_b_logtag_note_header(tmp_path: Path):
    csv_content = textwrap.dedent("""\

        Note: All Date-Time values are in: UTC +05:45
        1,2019-11-18,11:14:17,6.5,
        2,2019-11-18,11:24:17,6.8, Download Mark
    """)
    file_path = tmp_path / "format_b.csv"
    file_path.write_text(csv_content, encoding="utf-8")

    readings, audit = parse_nepal_csv(file_path)
    assert len(readings) == 2
    assert audit.header_rows_skipped == 1
    assert readings[0].timestamp == datetime(2019, 11, 18, 11, 14, 17)
    assert readings[0].temperature_c == 6.5
    assert readings[1].annotation == "Download Mark"


def test_parse_csv_format_c_raw_data_stream(tmp_path: Path):
    csv_content = textwrap.dedent("""\
        1,2019-12-03,08:37:33,3.5,
        2,2019-12-03,08:47:33,3.8,
    """)
    file_path = tmp_path / "format_c.csv"
    file_path.write_text(csv_content, encoding="utf-8")

    readings, audit = parse_nepal_csv(file_path)
    assert len(readings) == 2
    assert audit.header_rows_skipped == 0
    assert readings[0].temperature_c == 3.5


# -----------------------------------------------------------------------------
# 3. Self-contained Unit Tests: Data Quality Filtering & Annotations
# -----------------------------------------------------------------------------

def test_operational_annotations_retained_with_valid_temps(tmp_path: Path):
    csv_content = textwrap.dedent("""\
        Index,Date,Time,Readings (°C),Type
        1,"5/9/2018","10:00:00 AM","21.8","Pre-Start"
        2,"5/9/2018","10:10:00 AM","4.5","Inspection Mark"
        3,"5/9/2018","10:20:00 AM","4.6","Download Mark"
        4,"5/9/2018","10:30:00 AM","4.7","Inspection Mark Download Mark"
    """)
    file_path = tmp_path / "operational.csv"
    file_path.write_text(csv_content, encoding="utf-8")

    readings, audit = parse_nepal_csv(file_path)
    # All 4 valid numeric readings must be retained
    assert len(readings) == 4
    assert audit.valid_readings_count == 4
    assert audit.missing_blank_count == 0
    assert audit.sensor_saturation_count == 0
    assert "Pre-Start" in audit.operational_markers_retained
    assert "Inspection Mark" in audit.operational_markers_retained


def test_missing_and_saturated_readings_excluded_cleanly(tmp_path: Path):
    csv_content = textwrap.dedent("""\
        Index,Date,Time,Readings (°C),Type
        1,"5/9/2018","10:00:00 AM","4.5",
        2,"5/9/2018","10:10:00 AM","", "INVALID"
        3,"5/9/2018","10:20:00 AM","> 85.0", "Above Limit"
        4,"5/9/2018","10:30:00 AM","< -40.0",
        5,"5/9/2018","10:40:00 AM","99.9",
        6,"5/9/2018","10:50:00 AM","4.8",
    """)
    file_path = tmp_path / "errors.csv"
    file_path.write_text(csv_content, encoding="utf-8")

    readings, audit = parse_nepal_csv(file_path)
    assert len(readings) == 2  # Only records 1 and 6 are valid
    assert audit.valid_readings_count == 2
    assert audit.missing_blank_count == 1  # record 2
    assert audit.sensor_saturation_count == 3  # records 3 (>85), 4 (<-40), 5 (99.9 out of range)


# -----------------------------------------------------------------------------
# 4. Self-contained Unit Tests: Independent Episode Detection & Duration
# -----------------------------------------------------------------------------

def test_independent_freeze_and_heat_episodes():
    readings = [
        ThermalReading(1, datetime(2020, 1, 1, 10, 0), -1.5),  # Freeze 1
        ThermalReading(2, datetime(2020, 1, 1, 10, 10), -2.0), # Freeze 2
        ThermalReading(3, datetime(2020, 1, 1, 10, 20), 4.5),  # Safe
        ThermalReading(4, datetime(2020, 1, 1, 10, 30), 12.0), # Heat 1
        ThermalReading(5, datetime(2020, 1, 1, 10, 40), 13.5), # Heat 2
        ThermalReading(6, datetime(2020, 1, 1, 10, 50), 14.0), # Heat 3
    ]
    freeze_eps, heat_eps = detect_episodes(readings, temp_min=0.0, temp_max=10.0)

    assert len(freeze_eps) == 1
    assert freeze_eps[0].reading_count == 2
    assert freeze_eps[0].estimated_duration_minutes == 20.0
    assert freeze_eps[0].min_temp_c == -2.0

    assert len(heat_eps) == 1
    assert heat_eps[0].reading_count == 3
    assert heat_eps[0].estimated_duration_minutes == 30.0
    assert heat_eps[0].max_temp_c == 14.0


def test_direct_switch_between_freeze_and_heat_creates_separate_episodes():
    # Direct transition from Freeze to Heat without an intermediate Safe reading
    readings = [
        ThermalReading(1, datetime(2020, 1, 1, 10, 0), -3.0),
        ThermalReading(2, datetime(2020, 1, 1, 10, 10), -1.0),
        ThermalReading(3, datetime(2020, 1, 1, 10, 20), 15.0),  # Switched directly to Heat!
        ThermalReading(4, datetime(2020, 1, 1, 10, 30), 16.0),
    ]
    freeze_eps, heat_eps = detect_episodes(readings, temp_min=0.0, temp_max=10.0)

    assert len(freeze_eps) == 1
    assert freeze_eps[0].reading_count == 2
    assert freeze_eps[0].estimated_duration_minutes == 20.0

    assert len(heat_eps) == 1
    assert heat_eps[0].reading_count == 2
    assert heat_eps[0].estimated_duration_minutes == 20.0


def test_timestamp_gap_closes_episode_never_treated_as_safe_recovery():
    # Gap of 60 minutes between readings during a freeze excursion
    readings = [
        ThermalReading(1, datetime(2020, 1, 1, 10, 0), -2.0),
        ThermalReading(2, datetime(2020, 1, 1, 10, 10), -2.5),
        # Gap of 60 minutes
        ThermalReading(3, datetime(2020, 1, 1, 11, 10), -1.5),
    ]
    freeze_eps, heat_eps = detect_episodes(readings, temp_min=0.0, temp_max=10.0)

    # Must be 2 separate freeze episodes, not 1 continuous episode bridging the unobserved hour!
    assert len(freeze_eps) == 2
    assert freeze_eps[0].reading_count == 2
    assert freeze_eps[0].estimated_duration_minutes == 20.0
    assert freeze_eps[1].reading_count == 1
    assert freeze_eps[1].estimated_duration_minutes == 10.0


def test_file_validation_and_cohort_summary(tmp_path: Path):
    csv_1 = textwrap.dedent("""\
        Index,Date,Time,Readings (°C),Type
        1,"5/9/2018","10:00:00 AM","4.0",
        2,"5/9/2018","10:10:00 AM","-1.0",
        3,"5/9/2018","10:20:00 AM","5.0",
        4,"5/9/2018","10:30:00 AM","12.0",
    """)
    file_1 = tmp_path / "test_IT1PI.csv"
    file_1.write_text(csv_1, encoding="utf-8")

    res = validate_file(file_1, temp_min=0.0, temp_max=10.0)
    assert res.total_valid_readings == 4
    assert res.safe_reading_count == 2
    assert res.freeze_reading_count == 1
    assert res.heat_reading_count == 1
    assert res.compliance_rate_percent == 50.0
    assert res.freeze_rate_percent == 25.0
    assert res.heat_rate_percent == 25.0
    assert len(res.freeze_episodes) == 1
    assert len(res.heat_episodes) == 1

    cohort = summarize_cohort("freeze_preventive_carrier", [res])
    assert cohort.file_count == 1
    assert cohort.total_valid_readings == 4
    assert cohort.overall_compliance_rate_percent == 50.0
    assert cohort.overall_freeze_rate_percent == 25.0
    assert cohort.files_with_freeze_excursions == [file_1.name]


# -----------------------------------------------------------------------------
# 5. Integration / Dataset Audit Check (Separated from regular unit tests)
# -----------------------------------------------------------------------------

NEPAL_DATASET_DIR = Path("data/external/nepal_vaccine_carriers")


@pytest.mark.skipif(
    not NEPAL_DATASET_DIR.exists(),
    reason="Full Mendeley Nepal dataset not downloaded locally",
)
def test_nepal_dataset_inventory_reconciliation():
    """Verify that every one of the 526 downloaded CSV files is deterministically accounted for."""
    csv_files = sorted(list(NEPAL_DATASET_DIR.rglob("*.csv")))
    assert len(csv_files) == 526, f"Expected 526 CSV files, found {len(csv_files)}"

    classified = {}
    unknown_files = []
    for f in csv_files:
        cat = classify_nepal_file(f)
        classified[cat] = classified.get(cat, 0) + 1
        if cat == "unknown":
            unknown_files.append(f.name)

    # Reconciled inventory verification:
    assert classified.get("freeze_preventive_carrier") == 167
    assert classified.get("standard_vaccine_carrier") == 118
    assert classified.get("ice_pack_freezer") == 74
    assert classified.get("ambient") == 166
    assert classified.get("unknown") == 1
    assert sum(classified.values()) == 526

    # Verify the exact identity of the single unknown file
    assert unknown_files == ["1000821023 Started 7-20-2018, Finished 7-25-2018.csv"]
