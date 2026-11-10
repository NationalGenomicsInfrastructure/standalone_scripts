#!/usr/bin/env python3
"""Create a tarball for a single project/lane subset of a run directory."""

import argparse
import csv
import hashlib
import os
import shutil
import tarfile
import tempfile
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a tarball of a run directory containing only selected lanes "
            "for one project, with a filtered SampleSheet.csv. Include only BCL files "
            "for the selected lanes and exclude Demultiplexing directories by default."
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
        type=lambda s: [str(item) for item in s.split(",")],
        required=True,
        help="Comma-separated list of lane numbers to include, e.g. 1,2,4",
    )
    return parser.parse_args()


def write_project_samplesheet(
    run_dir: Path, project_id: str, lane_numbers: set[int], output_path: Path
) -> None:
    source_path = run_dir / "SampleSheet.csv"
    if not source_path.exists():
        raise FileNotFoundError(f"SampleSheet.csv not found in {run_dir}")

    lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        raise ValueError(f"SampleSheet.csv is empty: {source_path}")

    data_index = None
    for idx, line in enumerate(lines):
        if line.strip() == "[Data]":
            data_index = idx
            break

    if data_index is None:
        raise ValueError(f"[Data] section not found in {source_path}")

    preamble = lines[: data_index + 1]
    data_lines = lines[data_index + 1 :]
    if not data_lines:
        raise ValueError(f"No data rows found in {source_path}")

    header_line = data_lines[0].strip()
    if not header_line:
        raise ValueError(f"Header row is missing in {source_path}")

    header = next(csv.reader([header_line]))
    if "Lane" not in header or "Sample_Name" not in header:
        raise ValueError(
            "SampleSheet.csv does not contain the required 'Lane' and 'Sample_Name' columns."
        )

    lane_index = header.index("Lane")
    sample_name_index = header.index("Sample_Name")

    filtered_rows = []
    for line in data_lines[1:]:
        if not line.strip():
            continue
        row = next(csv.reader([line]))
        if len(row) <= max(lane_index, sample_name_index):
            continue

        try:
            lane_value = int(row[lane_index].strip())
        except ValueError:
            continue

        sample_name = row[sample_name_index].strip()
        if lane_value in lane_numbers and sample_name.startswith(f"{project_id}_"):
            filtered_rows.append(row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as out_handle:
        for line in preamble:
            out_handle.write(line + "\n")
        out_handle.write(header_line + "\n")
        for row in filtered_rows:
            out_handle.write(",".join(row) + "\n")


def should_include_file(
    src_path: Path, rel_path: Path, selected_lanes: set[str]
) -> bool:
    if src_path.name.lower().startswith("samplesheet"):
        return False

    parts = rel_path.parts
    for part in parts:
        if part.startswith("Demultiplexing"):
            return False
        if part.startswith("L") and part not in selected_lanes:
            return False
    return True


def build_run_archive(
    run_dir: Path, filtered_samplesheet: Path, lane_numbers: list[str], output_tar: Path
) -> None:
    selected_lanes = lane_numbers

    with tempfile.TemporaryDirectory(prefix="runfolder_subset_") as temp_dir_name:
        stage_root = Path(temp_dir_name) / run_dir.name
        stage_root.mkdir(parents=True, exist_ok=True)

        for root, dirs, files in os.walk(run_dir):
            src_root = Path(root)
            rel_root = src_root.relative_to(run_dir)

            dirs[:] = [
                d
                for d in dirs
                if not d.startswith("Demultiplexing")
                and not (d.startswith("L") and d not in selected_lanes)
            ]

            for filename in files:
                src_file = src_root / filename
                rel_file = src_file.relative_to(run_dir)

                if src_file.name.lower().startswith("samplesheet"):
                    if src_file.resolve() == filtered_samplesheet.resolve():
                        continue
                    continue

                if not should_include_file(src_file, rel_file, selected_lanes):
                    continue

                dest_file = stage_root / rel_file
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)

        filtered_copy = stage_root / "SampleSheet.csv"
        shutil.copy2(filtered_samplesheet, filtered_copy)

        with tarfile.open(output_tar, "w:gz") as tar_handle:
            tar_handle.add(stage_root, arcname=run_dir.name)


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
    run_dir = Path.cwd() / args.run
    if not run_dir.exists() or not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    lane_numbers = set(args.include_lanes)
    invalid_lanes = sorted(
        value
        for value in lane_numbers
        if int(value) < 1 or int(value) > 8
    )
    if invalid_lanes:
        raise ValueError(
            f"Lane numbers must be positive integers between 1 and 8, not: {invalid_lanes}"
        )

    project_samplesheet = (
        Path.cwd() / f"{args.project}_SampleSheet.csv"
    )
    try:
        write_project_samplesheet(
            run_dir, args.project, lane_numbers, project_samplesheet
        )

        archive_name = (
            f"{args.run}_{args.project}_"
            + "_".join(str(lane) for lane in sorted(args.include_lanes))
            + ".tar.gz"
        )
        archive_path = Path.cwd() / archive_name

        build_run_archive(
            run_dir, project_samplesheet, sorted(args.include_lanes), archive_path
        )
        md5_path = write_md5sum(archive_path)
        print(f"Created archive: {archive_path}")
        print(f"Created MD5 file: {md5_path}")
    finally:
        if project_samplesheet.exists():
            project_samplesheet.unlink()


if __name__ == "__main__":
    main()
