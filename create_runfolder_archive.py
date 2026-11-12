#!/usr/bin/env python3
"""Create a tarball of a run directory restricted to one project's lanes.

Only an allowlist of top-level run folder entries is archived (see
INCLUDED_TOP_LEVEL_ENTRIES); anything else, including Demultiplexing output
and sample sheets, is left out so that data from other projects cannot leak
into the archive.
"""

import argparse
import csv
import hashlib
import os
import re
import sys
import tarfile
from pathlib import Path

SAMPLESHEET_DIR_NAME = "Samplesheets"
LANE_SAMPLESHEET_PATTERN = re.compile(r"SampleSheet_lane_(\d+)_settings_\d+")
LANE_DIR_PATTERN = re.compile(r"L\d{3}")

# Top-level run folder entries that are copied into the archive. Everything
# else is skipped (and reported), so new/unknown output never ends up in a
# delivery by accident.
INCLUDED_TOP_LEVEL_ENTRIES = {
    # Directories
    "Config",
    "Data",
    "InterOp",
    "Logs",
    "Recipe",
    "Thumbnail_Images",
    # Files
    "CopyComplete.txt",
    "RTA3.cfg",
    "RTAComplete.txt",
    "RunCompletionStatus.xml",
    "RunInfo.xml",
    "RunParameters.xml",
    "runParameters.xml",
    "SequenceComplete.txt",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a tarball of a run directory for one project, containing only "
            "the selected lanes. All samples in the selected lanes' sample sheets "
            "must belong to the project. Only standard run folder entries (Data, "
            "InterOp, RunInfo.xml, etc.) are included; lane directories for "
            "unselected lanes, Demultiplexing output and sample sheets are excluded. "
            "The archive and its .md5 file are written to the current directory."
        )
    )
    parser.add_argument(
        "--run", "-r", required=True, help="Run directory name to archive"
    )
    parser.add_argument(
        "--project", "-p", required=True, help="Project ID to keep, e.g. P12345"
    )
    parser.add_argument(
        "--include_lanes",
        "-l",
        type=lambda s: [int(item) for item in s.split(",")],
        required=True,
        help="Comma-separated list of lane numbers to include, e.g. 1,2,4",
    )
    return parser.parse_args()


def find_lane_samplesheets(
    run_dir: Path, lane_numbers: set[int]
) -> dict[int, list[Path]]:
    samplesheet_dir = run_dir / SAMPLESHEET_DIR_NAME
    if not samplesheet_dir.is_dir():
        raise FileNotFoundError(f"Samplesheets directory not found: {samplesheet_dir}")

    lane_samplesheets: dict[int, list[Path]] = {lane: [] for lane in lane_numbers}
    for path in sorted(samplesheet_dir.iterdir()):
        match = LANE_SAMPLESHEET_PATTERN.match(path.name)
        if match and path.is_file() and int(match.group(1)) in lane_numbers:
            lane_samplesheets[int(match.group(1))].append(path)

    missing = sorted(lane for lane, paths in lane_samplesheets.items() if not paths)
    if missing:
        raise FileNotFoundError(f"No lane sample sheet found for lane(s): {missing}")
    return lane_samplesheets


def read_bclconvert_sample_names(samplesheet_path: Path) -> list[str]:
    in_data_section = False
    name_index: int | None = None
    sample_names: list[str] = []
    for line in samplesheet_path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        stripped = line.strip()
        if stripped == "[BCLConvert_Data]":
            in_data_section = True
            continue
        if not in_data_section or not stripped:
            continue
        if stripped.startswith("["):
            break

        row = next(csv.reader([stripped]))
        if name_index is None:
            if "Sample_Name" not in row:
                raise ValueError(
                    f"'Sample_Name' column not found in {samplesheet_path}"
                )
            name_index = row.index("Sample_Name")
            continue

        if len(row) <= name_index:
            raise ValueError(f"Malformed row in {samplesheet_path}: {stripped}")
        sample_names.append(row[name_index].strip())

    if name_index is None:
        raise ValueError(f"[BCLConvert_Data] section not found in {samplesheet_path}")
    if not sample_names:
        raise ValueError(f"No samples listed in {samplesheet_path}")
    return sample_names


def check_lane_samplesheets_match_project(
    run_dir: Path, project_id: str, lane_numbers: set[int]
) -> None:
    foreign_samples = []
    for lane, samplesheets in find_lane_samplesheets(run_dir, lane_numbers).items():
        for samplesheet in samplesheets:
            for sample_name in read_bclconvert_sample_names(samplesheet):
                if not sample_name.startswith(f"{project_id}_"):
                    foreign_samples.append(
                        f"lane {lane} ({samplesheet.name}): {sample_name}"
                    )

    if foreign_samples:
        raise ValueError(
            f"Samples not belonging to project {project_id} in selected lanes:\n"
            + "\n".join(foreign_samples)
        )


def is_unselected_lane_dir(name: str, selected_lane_dirs: set[str]) -> bool:
    return (
        LANE_DIR_PATTERN.fullmatch(name) is not None and name not in selected_lane_dirs
    )


def is_samplesheet(path: Path) -> bool:
    return path.name.lower().startswith("samplesheet")


def iter_files_to_archive(run_dir: Path, lane_numbers: set[int]):
    """Yield files under run_dir that belong in the archive."""
    selected_lane_dirs = {f"L{lane:03d}" for lane in lane_numbers}
    skipped = []

    for entry in sorted(run_dir.iterdir()):
        if entry.name not in INCLUDED_TOP_LEVEL_ENTRIES:
            skipped.append(entry.name)
            continue

        if entry.is_symlink() or entry.is_file():
            if not is_samplesheet(entry):
                yield entry
            continue

        for root, dirs, files in os.walk(entry):
            dirs[:] = sorted(
                d for d in dirs if not is_unselected_lane_dir(d, selected_lane_dirs)
            )
            for filename in sorted(files):
                src_file = Path(root) / filename
                if not is_samplesheet(src_file):
                    yield src_file

    if skipped:
        print(
            "Skipped top-level entries not in the allowlist: " + ", ".join(skipped),
            file=sys.stderr,
        )


def build_run_archive(run_dir: Path, lane_numbers: set[int], output_tar: Path) -> None:
    with tarfile.open(output_tar, "w:gz") as tar_handle:
        for src_file in iter_files_to_archive(run_dir, lane_numbers):
            arcname = Path(run_dir.name) / src_file.relative_to(run_dir)
            tar_handle.add(src_file, arcname=str(arcname), recursive=False)


def write_md5sum(archive_path: Path) -> Path:
    digest = hashlib.md5()
    with archive_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)

    md5_path = archive_path.with_name(archive_path.name + ".md5")
    md5_path.write_text(
        f"{digest.hexdigest()}  {archive_path.name}\n", encoding="utf-8"
    )
    return md5_path


def main() -> None:
    args = parse_args()
    run_dir = Path(os.path.abspath(args.run))
    if not run_dir.exists() or not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    lane_numbers = set(args.include_lanes)
    invalid_lanes = sorted(lane for lane in lane_numbers if lane < 1 or lane > 8)
    if invalid_lanes:
        raise ValueError(
            f"Lane numbers must be positive integers between 1 and 8, not: {invalid_lanes}"
        )

    check_lane_samplesheets_match_project(run_dir, args.project, lane_numbers)

    archive_name = (
        f"{run_dir.name}_{args.project}_"
        + "_".join(str(lane) for lane in sorted(lane_numbers))
        + ".tar.gz"
    )
    archive_path = Path.cwd() / archive_name

    build_run_archive(run_dir, lane_numbers, archive_path)
    md5_path = write_md5sum(archive_path)
    print(f"Created archive: {archive_path}")
    print(f"Created MD5 file: {md5_path}")


if __name__ == "__main__":
    main()
