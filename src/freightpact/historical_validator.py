"""Historical temperature validation module for cold-chain empirical datasets.

Designed to validate passive carrier thermal performance against standard operating
bands (WHO 0-10°C specification) without conflating passive cold-box field data
with live refrigerated-truck telemetry.

PROVENANCE & ATTRIBUTION
------------------------
Dataset:
  • Title: Long-range freeze-preventive vaccine carriers in Nepal: thermal data
  • Contributors listed on Mendeley Data: Alexandra Sibole, Steven Diesburg and Sandeep Kumar
  • Version: 1
  • DOI: 10.17632/j277dvs69g.1
  • URL: https://data.mendeley.com/datasets/j277dvs69g/1
  • Licence: Creative Commons Attribution 4.0 International (CC BY 4.0)

Related Publication:
  • Title: Using long-range freeze-preventive vaccine carriers in Nepal: A study of
           equipment performance, acceptability, systems fit, and cost
  • Authors: Sandeep Kumar et al., with the full author list available in the publication
  • Journal: Vaccine: X, volume 10 (2022), article 100146
  • DOI: 10.1016/j.jvacx.2022.100146
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


DATASET_PROVENANCE = {
    "dataset": {
        "title": "Long-range freeze-preventive vaccine carriers in Nepal: thermal data",
        "contributors": "Alexandra Sibole, Steven Diesburg and Sandeep Kumar",
        "version": "1",
        "doi": "10.17632/j277dvs69g.1",
        "url": "https://data.mendeley.com/datasets/j277dvs69g/1",
        "licence": "CC BY 4.0",
    },
    "related_publication": {
        "title": (
            "Using long-range freeze-preventive vaccine carriers in Nepal: "
            "A study of equipment performance, acceptability, systems fit, and cost"
        ),
        "authors": "Sandeep Kumar et al., with the full author list available in the publication",
        "journal": "Vaccine: X, volume 10 (2022), article 100146",
        "doi": "10.1016/j.jvacx.2022.100146",
    },
}


@dataclass(frozen=True)
class ThermalReading:
    """A single validated timestamped thermal reading."""

    index: int
    timestamp: datetime
    temperature_c: float
    annotation: str = ""


@dataclass
class ExcursionEpisode:
    """A contiguous period of out-of-range temperature readings in a single file."""

    kind: str  # "FREEZE" (< temp_min) or "HEAT" (> temp_max)
    start_index: int
    end_index: int
    start_time: datetime
    end_time: datetime
    reading_count: int
    min_temp_c: float
    max_temp_c: float
    estimated_duration_minutes: float


@dataclass
class DataQualityAudit:
    """Auditable summary of raw records, excluded records, and operational markers."""

    total_records_read: int = 0
    header_rows_skipped: int = 0
    valid_readings_count: int = 0
    missing_blank_count: int = 0
    sensor_saturation_count: int = 0
    corrupted_parse_count: int = 0
    operational_markers_retained: Dict[str, int] = field(default_factory=dict)


@dataclass
class FileValidationResult:
    """Comprehensive validation result for an individual thermal log file."""

    file_name: str
    file_path: str
    equipment_category: str  # "freeze_preventive_carrier", "standard_vaccine_carrier", "ice_pack_freezer", "ambient", "unknown"
    data_quality: DataQualityAudit
    total_valid_readings: int
    temp_min_threshold: float
    temp_max_threshold: float
    safe_reading_count: int
    freeze_reading_count: int
    heat_reading_count: int
    min_temperature_c: Optional[float]
    max_temperature_c: Optional[float]
    mean_temperature_c: Optional[float]
    compliance_rate_percent: float
    freeze_rate_percent: float
    heat_rate_percent: float
    freeze_episodes: List[ExcursionEpisode] = field(default_factory=list)
    heat_episodes: List[ExcursionEpisode] = field(default_factory=list)

    @property
    def total_freeze_duration_minutes(self) -> float:
        return sum(ep.estimated_duration_minutes for ep in self.freeze_episodes)

    @property
    def total_heat_duration_minutes(self) -> float:
        return sum(ep.estimated_duration_minutes for ep in self.heat_episodes)


@dataclass
class CohortSummary:
    """Aggregated empirical statistics across a group of files."""

    category: str
    file_count: int
    total_valid_readings: int
    total_safe_readings: int
    total_freeze_readings: int
    total_heat_readings: int
    total_freeze_episodes: int
    total_heat_episodes: int
    total_freeze_duration_hours: float
    total_heat_duration_hours: float
    overall_compliance_rate_percent: float
    overall_freeze_rate_percent: float
    overall_heat_rate_percent: float
    min_observed_temperature_c: Optional[float]
    max_observed_temperature_c: Optional[float]
    files_with_freeze_excursions: List[str] = field(default_factory=list)
    files_with_heat_excursions: List[str] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Equipment Classification
# -----------------------------------------------------------------------------

def classify_nepal_file(file_path: Path) -> str:
    """Deterministically classify a Nepal study file by equipment role and phase.

    Returns one of:
      - 'freeze_preventive_carrier'
      - 'standard_vaccine_carrier'
      - 'ice_pack_freezer'
      - 'ambient'
      - 'unknown'
    """
    fname = file_path.name
    fname_upper = fname.upper()
    path_norm = str(file_path).replace("\\", "/").upper()

    # Explicit check for known unlabelled file (no equipment code, unlisted serial in Coding List Phase 1a)
    if fname == "1000821023 Started 7-20-2018, Finished 7-25-2018.csv":
        return "unknown"

    # Documentary evidence check for lf-SN 1000820977 in Phase 1a Round 3:
    # In Chumbang post, the 3 carrier/ambient loggers were already present (DHAPA 05 LA, LI, LS).
    # The 4th logger introduced in Round 3 was LF (LogTag Freezer), here labeled "lf-SN"
    # (LogTag Freezer - Serial Number 1000820977). It is physically and administratively an ice-pack freezer logger.
    if "LF-SN" in fname_upper or "LF_SN" in fname_upper or "LF - SN" in fname_upper:
        return "ice_pack_freezer"

    is_phase_1a = "PHASE 1A" in path_norm
    is_phase_1b = "PHASE 1B" in path_norm
    is_phase_2 = "PHASE 2" in path_norm

    if is_phase_1a:
        # Phase 1a conventions:
        # LF = LogTag Freezer (deployed in freezers in Round 3)
        # LI = LogTag Internal (PVC)
        # LS = LogTag Standard (SVC)
        # LA = LogTag Ambient
        if re.search(r'(\b|_|\d)LF(\b|_|\d|\s)', fname_upper) or fname_upper.endswith("LF.CSV") or "LF STARTED" in fname_upper:
            return "ice_pack_freezer"
        if re.search(r'(\b|_|\d)LI(\b|_|\d|\s|TARTED|STARTED)', fname_upper) or "LITARTED" in fname_upper:
            return "freeze_preventive_carrier"
        if re.search(r'(\b|_|\d)LS(\b|_|\d|\s|STARTED)', fname_upper) or "LS STARTED" in fname_upper or "SUNSI03L STARTED" in fname_upper:
            return "standard_vaccine_carrier"
        if re.search(r'(\b|_|\d)LA(\b|_|\d|\s|STARTED|A)', fname_upper) or "LA STARTED" in fname_upper or "LAA" in fname_upper:
            return "ambient"

    elif is_phase_1b:
        # Phase 1b conventions:
        # Block codes: IT, HA, SI, DA, PA (Pakhribas).
        # Check deep freezer first (including PA\d+DI and Dhankuta_deepfreezer):
        if (
            "DEEPFREEZER" in path_norm or
            "DEEP FREEZER" in path_norm or
            "DVS-" in fname_upper or
            re.search(r'(\b|_|\d)DI?(\b|_|\d|\s|-|STARTED)', fname_upper) or
            re.search(r'PA\d+DI?', fname_upper)
        ):
            return "ice_pack_freezer"

        # Check PVC Internal (PI)
        if re.search(r'(\b|_|\d)PI(\b|_|\d|\s|STARTED)', fname_upper) or re.search(r'PA\d+PI', fname_upper):
            return "freeze_preventive_carrier"

        # Check Standard Vaccine Carrier (S / PS / SVC):
        # Must evaluate PA\d+S before generic PA ambient check!
        if (
            re.search(r'PA\d+S(\b|_|\d|\s|-|STARTED)', fname_upper) or
            re.search(r'(\b|_|\d)PS(\b|_|\d|\s|STARTED)', fname_upper) or
            re.search(r'(\b|_|\d)S(\b|_|\d|\s|STARTED)', fname_upper) or
            "SVC" in fname_upper or
            "STANDARD" in path_norm
        ):
            return "standard_vaccine_carrier"

        # Check Ambient (PA):
        # In Pakhribas, ambient is PA\d+PA (e.g. PA5PA)
        if (
            re.search(r'PA\d+PA', fname_upper) or
            re.search(r'(\b|_|\d)PA(\b|_|\d|\s|STARTED)', fname_upper)
        ):
            return "ambient"

    elif is_phase_2:
        # Phase 2 conventions (only PVCs, Ambient, and Freezers deployed):
        # D = Deep Freezer
        # PI = PVC Internal
        # PA = PVC Ambient
        if (
            "DEEPFREEZER" in path_norm or
            "DEEP" in path_norm or
            "DVS-" in fname_upper or
            re.search(r'(\b|_|\d)D(\b|_|\d|\s|-|STARTED|MAHABHARAT)', fname_upper) or
            "DSTARTED" in fname_upper
        ):
            return "ice_pack_freezer"
        if re.search(r'(\b|_|\d)PI(\b|_|\d|\s|STARTED|FINISHED)', fname_upper) or "PIFINISHED" in fname_upper:
            return "freeze_preventive_carrier"
        if re.search(r'(\b|_|\d)PA(\b|_|\d|\s|STARTED|11-)', fname_upper) or "PA11-" in fname_upper:
            return "ambient"

    # Contextual fallbacks based on standalone filename patterns (useful for isolated unit test fixtures)
    if "FREEZER" in path_norm or "DEEP" in path_norm or re.search(r'(\b|_|\d)DI?(\b|_|\d|\s)', fname_upper):
        return "ice_pack_freezer"
    if "PVC" in path_norm or "PVC" in fname_upper or re.search(r'(\b|_|\d)(PI|LI)(\b|_|\d|\s)', fname_upper):
        return "freeze_preventive_carrier"
    if "STANDARD" in path_norm or "SVC" in fname_upper or re.search(r'(\b|_|\d)(PS|LS|S)(\b|_|\d|\s)', fname_upper):
        return "standard_vaccine_carrier"
    if "AMBIENT" in path_norm or re.search(r'(\b|_|\d)(PA|LA)(\b|_|\d|\s)', fname_upper):
        return "ambient"

    return "unknown"

    return "unknown"


# -----------------------------------------------------------------------------
# File Parsing & Data Quality Filtering
# -----------------------------------------------------------------------------

def parse_datetime(date_str: str, time_str: str) -> Optional[datetime]:
    """Parse date and time strings supporting US, ISO, 12h, and 24h representations."""
    dt_str = f"{date_str.strip()} {time_str.strip()}"
    candidates = (
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
        "%d/%m/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%d-%b-%y %H:%M:%S",
    )
    for fmt in candidates:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            pass
    return None


def parse_nepal_csv(file_path: Path) -> Tuple[List[ThermalReading], DataQualityAudit]:
    """Read a Nepal dataset CSV file, filter invalid records, and return valid readings with audit."""
    lines: List[str] = []
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            with open(file_path, "r", encoding=enc) as fp:
                lines = fp.readlines()
            break
        except UnicodeDecodeError:
            continue

    audit = DataQualityAudit()
    readings: List[ThermalReading] = []

    if not lines:
        return readings, audit

    # Identify header or note lines to skip
    start_row = 0
    for idx, raw_line in enumerate(lines[:10]):
        stripped = raw_line.strip()
        if not stripped:
            start_row = idx + 1
            continue
        if "Index" in stripped and "Readings" in stripped:
            start_row = idx + 1
            audit.header_rows_skipped += 1
            break
        if "Note: All Date-Time" in stripped:
            start_row = idx + 1
            audit.header_rows_skipped += 1
            # Skip subsequent blank lines
            while start_row < len(lines) and not lines[start_row].strip():
                start_row += 1
            break
        if re.match(r"^\d+,", stripped):
            start_row = idx
            break

    data_lines = lines[start_row:]
    reader = csv.reader(data_lines)

    for row_idx, row in enumerate(reader):
        if not row:
            continue
        audit.total_records_read += 1
        if len(row) < 4:
            audit.corrupted_parse_count += 1
            continue

        raw_idx = row[0].strip()
        raw_date = row[1].strip().strip('"')
        raw_time = row[2].strip().strip('"')
        raw_temp = row[3].strip().strip('"')
        raw_ann = row[4].strip().strip('"') if len(row) > 4 else ""

        # Parse index
        try:
            record_idx = int(raw_idx)
        except ValueError:
            record_idx = row_idx + 1

        # Check for missing temperature
        if raw_temp == "":
            audit.missing_blank_count += 1
            continue

        # Check for sensor saturation
        if raw_temp.startswith(">") or raw_temp.startswith("<") or raw_ann == "Above Limit":
            audit.sensor_saturation_count += 1
            continue

        # Convert temperature float
        try:
            temp_val = float(raw_temp)
        except ValueError:
            audit.corrupted_parse_count += 1
            continue

        # Validate sensor hardware limits (-40°C to +85°C)
        if temp_val < -40.0 or temp_val > 85.0:
            audit.sensor_saturation_count += 1
            continue

        # Parse timestamp
        dt = parse_datetime(raw_date, raw_time)
        if dt is None:
            audit.corrupted_parse_count += 1
            continue

        # Track operational annotations retained
        if raw_ann:
            audit.operational_markers_retained[raw_ann] = (
                audit.operational_markers_retained.get(raw_ann, 0) + 1
            )

        readings.append(
            ThermalReading(
                index=record_idx,
                timestamp=dt,
                temperature_c=temp_val,
                annotation=raw_ann,
            )
        )

    audit.valid_readings_count = len(readings)
    return readings, audit


# -----------------------------------------------------------------------------
# Excursion Episode Detection & Metric Calculation
# -----------------------------------------------------------------------------

def detect_episodes(
    readings: Sequence[ThermalReading],
    temp_min: float = 0.0,
    temp_max: float = 10.0,
    cadence_minutes: float = 10.0,
) -> Tuple[List[ExcursionEpisode], List[ExcursionEpisode]]:
    """Detect freeze and heat excursion episodes independently.

    Rules:
    - Readings < temp_min belong to FREEZE episodes.
    - Readings > temp_max belong to HEAT episodes.
    - Switching directly between FREEZE and HEAT starts a new episode of the new kind.
    - Invalid/missing readings are not present in `readings` (cleaned upstream);
      if an unobserved gap > 30 minutes occurs between readings, any active episode closes.
    - Duration convention: reading_count * cadence_minutes.
    """
    freeze_episodes: List[ExcursionEpisode] = []
    heat_episodes: List[ExcursionEpisode] = []

    current_kind: Optional[str] = None
    current_readings: List[ThermalReading] = []

    def close_current_episode():
        nonlocal current_kind, current_readings
        if not current_kind or not current_readings:
            current_kind = None
            current_readings = []
            return

        temps = [r.temperature_c for r in current_readings]
        start_r = current_readings[0]
        end_r = current_readings[-1]
        count = len(current_readings)
        duration = count * cadence_minutes

        ep = ExcursionEpisode(
            kind=current_kind,
            start_index=start_r.index,
            end_index=end_r.index,
            start_time=start_r.timestamp,
            end_time=end_r.timestamp,
            reading_count=count,
            min_temp_c=min(temps),
            max_temp_c=max(temps),
            estimated_duration_minutes=duration,
        )
        if current_kind == "FREEZE":
            freeze_episodes.append(ep)
        elif current_kind == "HEAT":
            heat_episodes.append(ep)

        current_kind = None
        current_readings = []

    for idx, reading in enumerate(readings):
        t = reading.temperature_c

        # Check timestamp gap from previous reading
        if current_readings and idx > 0:
            prev_r = readings[idx - 1]
            gap = (reading.timestamp - prev_r.timestamp).total_seconds()
            if gap > 35 * 60:  # Gap exceeding 35 mins indicates unobserved gap
                close_current_episode()

        if t < temp_min:
            reading_kind = "FREEZE"
        elif t > temp_max:
            reading_kind = "HEAT"
        else:
            reading_kind = "SAFE"

        if reading_kind == "SAFE":
            close_current_episode()
        elif reading_kind == current_kind:
            current_readings.append(reading)
        else:
            # Switched directly from FREEZE to HEAT or vice versa, or started a new one
            close_current_episode()
            current_kind = reading_kind
            current_readings.append(reading)

    close_current_episode()
    return freeze_episodes, heat_episodes


def validate_file(
    file_path: Path,
    temp_min: float = 0.0,
    temp_max: float = 10.0,
) -> FileValidationResult:
    """Validate a single thermal CSV file against the target band."""
    readings, audit = parse_nepal_csv(file_path)
    category = classify_nepal_file(file_path)

    total_valid = len(readings)
    if total_valid == 0:
        return FileValidationResult(
            file_name=file_path.name,
            file_path=str(file_path),
            equipment_category=category,
            data_quality=audit,
            total_valid_readings=0,
            temp_min_threshold=temp_min,
            temp_max_threshold=temp_max,
            safe_reading_count=0,
            freeze_reading_count=0,
            heat_reading_count=0,
            min_temperature_c=None,
            max_temperature_c=None,
            mean_temperature_c=None,
            compliance_rate_percent=0.0,
            freeze_rate_percent=0.0,
            heat_rate_percent=0.0,
            freeze_episodes=[],
            heat_episodes=[],
        )

    temps = [r.temperature_c for r in readings]
    freeze_count = sum(1 for t in temps if t < temp_min)
    heat_count = sum(1 for t in temps if t > temp_max)
    safe_count = sum(1 for t in temps if temp_min <= t <= temp_max)

    freeze_eps, heat_eps = detect_episodes(readings, temp_min=temp_min, temp_max=temp_max)

    return FileValidationResult(
        file_name=file_path.name,
        file_path=str(file_path),
        equipment_category=category,
        data_quality=audit,
        total_valid_readings=total_valid,
        temp_min_threshold=temp_min,
        temp_max_threshold=temp_max,
        safe_reading_count=safe_count,
        freeze_reading_count=freeze_count,
        heat_reading_count=heat_count,
        min_temperature_c=min(temps),
        max_temperature_c=max(temps),
        mean_temperature_c=sum(temps) / total_valid,
        compliance_rate_percent=(safe_count / total_valid) * 100.0,
        freeze_rate_percent=(freeze_count / total_valid) * 100.0,
        heat_rate_percent=(heat_count / total_valid) * 100.0,
        freeze_episodes=freeze_eps,
        heat_episodes=heat_eps,
    )


def summarize_cohort(
    category_name: str,
    results: Sequence[FileValidationResult],
) -> CohortSummary:
    """Aggregate individual file validation results into a transparent cohort summary."""
    matching = [r for r in results if r.equipment_category == category_name]
    file_count = len(matching)

    total_valid = sum(r.total_valid_readings for r in matching)
    total_safe = sum(r.safe_reading_count for r in matching)
    total_freeze = sum(r.freeze_reading_count for r in matching)
    total_heat = sum(r.heat_reading_count for r in matching)

    total_freeze_eps = sum(len(r.freeze_episodes) for r in matching)
    total_heat_eps = sum(len(r.heat_episodes) for r in matching)

    total_freeze_mins = sum(r.total_freeze_duration_minutes for r in matching)
    total_heat_mins = sum(r.total_heat_duration_minutes for r in matching)

    all_mins = [r.min_temperature_c for r in matching if r.min_temperature_c is not None]
    all_maxs = [r.max_temperature_c for r in matching if r.max_temperature_c is not None]

    files_with_freeze = [r.file_name for r in matching if r.freeze_reading_count > 0]
    files_with_heat = [r.file_name for r in matching if r.heat_reading_count > 0]

    return CohortSummary(
        category=category_name,
        file_count=file_count,
        total_valid_readings=total_valid,
        total_safe_readings=total_safe,
        total_freeze_readings=total_freeze,
        total_heat_readings=total_heat,
        total_freeze_episodes=total_freeze_eps,
        total_heat_episodes=total_heat_eps,
        total_freeze_duration_hours=round(total_freeze_mins / 60.0, 2),
        total_heat_duration_hours=round(total_heat_mins / 60.0, 2),
        overall_compliance_rate_percent=(
            round((total_safe / total_valid) * 100.0, 2) if total_valid > 0 else 0.0
        ),
        overall_freeze_rate_percent=(
            round((total_freeze / total_valid) * 100.0, 2) if total_valid > 0 else 0.0
        ),
        overall_heat_rate_percent=(
            round((total_heat / total_valid) * 100.0, 2) if total_valid > 0 else 0.0
        ),
        min_observed_temperature_c=min(all_mins) if all_mins else None,
        max_observed_temperature_c=max(all_maxs) if all_maxs else None,
        files_with_freeze_excursions=files_with_freeze,
        files_with_heat_excursions=files_with_heat,
    )
