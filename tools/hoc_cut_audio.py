#!/usr/bin/env python3
"""Cut House of Cards learning clips from the reviewed TSV timelines.

The repository and source tree are read-only inputs.  The output tree is
written atomically and can be processed one episode or one season at a time.
Speech is cut at the exact reviewed boundaries; a short *silence-only* pad is
added around each clip for shadowing.  No source audio is included outside the
requested interval, and the status intentionally never claims an audition.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence


STATUS = "Timeline-aligned;audio-not-confirmed"
DEFAULT_PADDING = 0.4
DURATION_TOLERANCE = 0.10
TIME_RE = re.compile(r"^(?:(\d+):)?(\d{2}):(\d{2})(?:[.,](\d{1,6}))?$")
EPISODE_RE = re.compile(r"^S(\d{2})E(\d{2})$", re.IGNORECASE)
SEASON_RE = re.compile(r"^Season(\d{2})$", re.IGNORECASE)
FILENAME_RE = re.compile(r"^[^/\\\x00\r\n]+\.mp3$", re.IGNORECASE)

LISTENING_COLUMNS = {
    "order",
    "type",
    "canonical_id",
    "start",
    "end",
    "filename",
    "slug",
    "scene_id",
    "policy",
    "status",
}
EXTENDED_COLUMNS = {
    "id",
    "start",
    "end",
    "duration_s",
    "speaker_scene",
    "structure",
    "why",
    "transfer_theme",
    "filename",
    "source_status",
    "daily_candidate",
    "reusable_chunks",
}


class CutterError(RuntimeError):
    """A user-actionable input, validation, or output conflict."""


def parse_seconds(value: str, *, label: str) -> float:
    text = value.strip()
    match = TIME_RE.fullmatch(text)
    if not match:
        raise CutterError(f"{label}: invalid timestamp {value!r}; expected HH:MM:SS.mmm")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    fraction = (match.group(4) or "").ljust(6, "0")
    if minutes >= 60 or seconds >= 60:
        raise CutterError(f"{label}: invalid timestamp {value!r}")
    return hours * 3600 + minutes * 60 + seconds + (int(fraction) / 1_000_000 if fraction else 0.0)


def format_seconds(seconds: float) -> str:
    # Three decimals are enough because the reviewed TSVs are millisecond
    # aligned and this keeps ffmpeg command lines deterministic.
    return f"{max(0.0, seconds):.3f}"


def clean_cell(value: object) -> str:
    return str(value if value is not None else "").strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_duration(path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as exc:
        raise CutterError("ffprobe is required but was not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise CutterError(f"ffprobe failed for {path}: {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CutterError(f"ffprobe timed out for {path}") from exc
    text = result.stdout.strip()
    try:
        duration = float(text)
    except ValueError as exc:
        raise CutterError(f"ffprobe returned an invalid duration for {path}: {text!r}") from exc
    if not duration > 0:
        raise CutterError(f"source has no positive duration: {path}")
    return duration


def run_ffprobe_self_check() -> None:
    try:
        subprocess.run(["ffprobe", "-version"], check=True, capture_output=True, timeout=30)
    except FileNotFoundError as exc:
        raise CutterError("ffprobe is required but was not found on PATH") from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise CutterError("ffprobe is present but could not be invoked") from exc


def read_tsv(path: Path, required: set[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise CutterError(f"missing required TSV: {path}")
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise CutterError(f"cannot read {path}: {exc}") from exc
    with handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise CutterError(f"{path}: missing TSV header")
        headers = [clean_cell(name) for name in reader.fieldnames]
        if len(headers) != len(set(headers)):
            raise CutterError(f"{path}: duplicate TSV header names")
        missing = sorted(required - set(headers))
        if missing:
            raise CutterError(f"{path}: missing required columns: {', '.join(missing)}")
        rows: list[dict[str, str]] = []
        for line_number, raw in enumerate(reader, start=2):
            if raw.get(None) is not None:
                raise CutterError(f"{path}:{line_number}: too many TSV columns")
            if not any(clean_cell(value) for value in raw.values()):
                continue
            row = {key: clean_cell(value) for key, value in raw.items() if key is not None}
            rows.append(row)
    if not rows:
        raise CutterError(f"{path}: no data rows")
    return rows


def episode_token(value: str) -> str:
    match = EPISODE_RE.fullmatch(value.strip())
    if not match:
        raise CutterError(f"invalid episode token {value!r}; expected S01E01")
    return f"S{int(match.group(1)):02d}E{int(match.group(2)):02d}"


def season_token(value: str) -> str:
    text = value.strip()
    if text.lower().startswith("season"):
        text = text[6:]
    if not re.fullmatch(r"\d{1,2}", text):
        raise CutterError(f"invalid season {value!r}; expected 01 or Season01")
    number = int(text)
    if number < 1 or number > 99:
        raise CutterError(f"invalid season {value!r}")
    return f"Season{number:02d}"


def episode_from_dir(path: Path) -> str | None:
    match = EPISODE_RE.fullmatch(path.name)
    return episode_token(path.name) if match else None


def discover_episode_dirs(repo: Path, requested_episode: str | None, requested_season: str | None) -> list[Path]:
    if not repo.is_dir():
        raise CutterError(f"repository directory does not exist: {repo}")
    candidates: list[Path] = []
    for season_dir in sorted(repo.glob("Season??")):
        if not season_dir.is_dir() or not SEASON_RE.fullmatch(season_dir.name):
            continue
        for ep_dir in sorted(season_dir.glob("S??E??")):
            if not ep_dir.is_dir() or episode_from_dir(ep_dir) is None:
                continue
            candidates.append(ep_dir)
    if requested_episode:
        candidates = [path for path in candidates if episode_from_dir(path) == requested_episode]
    elif requested_season:
        candidates = [path for path in candidates if path.parent.name.lower() == requested_season.lower()]
    if not candidates:
        selector = requested_episode or requested_season or "all episodes"
        raise CutterError(f"no episode directories found for {selector}")
    return candidates


def locate_source(source_root: Path, token: str) -> Path:
    if not source_root.is_dir():
        raise CutterError(f"source directory does not exist: {source_root}")
    matches = [
        path
        for path in sorted(source_root.rglob("*.mp3"))
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", path.name, re.IGNORECASE)
    ]
    if len(matches) != 1:
        rendered = ", ".join(str(path) for path in matches[:5]) or "none"
        raise CutterError(f"expected exactly one source MP3 for {token}, found {len(matches)}: {rendered}")
    return matches[0]


@dataclass
class Cut:
    episode: str
    kind: str
    item_id: str
    clip_type: str
    start: float
    end: float
    requested_duration: float
    filename: str
    output_path: Path = field(compare=False)
    flat_path: Path = field(compare=False)
    daily: bool = False
    source_path: Path = field(default=Path(), compare=False)
    source_duration: float = field(default=0.0, compare=False)
    actual_duration: float | None = field(default=None, compare=False)
    digest: str | None = field(default=None, compare=False)
    action: str = field(default="planned", compare=False)

    @property
    def speech_duration(self) -> float:
        return self.end - self.start

    @property
    def padded_duration(self) -> float:
        return self.speech_duration + 2 * DEFAULT_PADDING


@dataclass
class EpisodePlan:
    episode: str
    season: str
    episode_dir: Path
    source_path: Path
    source_duration: float
    cuts: list[Cut]


MANIFEST_FIELDS = [
    "episode",
    "season",
    "kind",
    "item_id",
    "type",
    "source",
    "source_duration_s",
    "requested_start_s",
    "requested_end_s",
    "requested_duration_s",
    "speech_duration_s",
    "padding_start_s",
    "padding_end_s",
    "actual_duration_s",
    "filename",
    "path",
    "flat_path",
    "sha256",
    "status",
    "action",
]


def validate_filename(filename: str, *, path: Path) -> None:
    if not FILENAME_RE.fullmatch(filename):
        raise CutterError(f"{path}: invalid or unsafe MP3 filename {filename!r}")


def validate_cut_row(
    row: dict[str, str],
    *,
    table_path: Path,
    line_number: int,
    source_duration: float,
    ids: set[tuple[str, str]],
    filenames: set[str],
    kind: str,
) -> Cut:
    if kind == "listening":
        item_id = row["canonical_id"]
        clip_type = row["type"]
        if not row["order"].isdigit() or int(row["order"]) < 1:
            raise CutterError(f"{table_path}:{line_number}: order must be a positive integer")
        if row["policy"] not in {"daily", "on-demand"}:
            raise CutterError(f"{table_path}:{line_number}: unsupported policy {row['policy']!r}")
        daily = row["policy"] == "daily"
    else:
        item_id = row["id"]
        clip_type = "extended"
        if row["daily_candidate"].lower() not in {"true", "false"}:
            raise CutterError(f"{table_path}:{line_number}: daily_candidate must be true or false")
        daily = row["daily_candidate"].lower() == "true"
    if not item_id:
        raise CutterError(f"{table_path}:{line_number}: empty item id")
    id_key = (item_id, clip_type) if kind == "listening" else (item_id, clip_type)
    if id_key in ids:
        raise CutterError(f"{table_path}:{line_number}: duplicate {kind} id/type {item_id!r}/{clip_type!r}")
    ids.add(id_key)
    filename = row["filename"]
    validate_filename(filename, path=table_path)
    if filename in filenames:
        raise CutterError(f"{table_path}:{line_number}: duplicate filename {filename!r}")
    filenames.add(filename)
    start = parse_seconds(row["start"], label=f"{table_path}:{line_number} start")
    end = parse_seconds(row["end"], label=f"{table_path}:{line_number} end")
    if end <= start:
        raise CutterError(f"{table_path}:{line_number}: end must be after start")
    if start < 0 or end > source_duration + 0.05:
        raise CutterError(
            f"{table_path}:{line_number}: interval {start:.3f}–{end:.3f}s exceeds source duration {source_duration:.3f}s"
        )
    requested = end - start
    if kind == "extended":
        try:
            declared = float(row["duration_s"])
        except ValueError as exc:
            raise CutterError(f"{table_path}:{line_number}: invalid duration_s {row['duration_s']!r}") from exc
        if declared <= 0 or abs(declared - requested) > 0.011:
            raise CutterError(
                f"{table_path}:{line_number}: duration_s={declared:.3f} does not match interval {requested:.3f}s"
            )
    return Cut(
        episode="",
        kind=kind,
        item_id=item_id,
        clip_type=clip_type,
        start=start,
        end=end,
        requested_duration=requested,
        filename=filename,
        output_path=Path(),
        flat_path=Path(),
        daily=daily,
    )


def build_plan(ep_dir: Path, source_root: Path, out_root: Path) -> EpisodePlan:
    episode = episode_token(ep_dir.name)
    season = season_token(ep_dir.parent.name)
    source = locate_source(source_root, episode)
    source_duration = probe_duration(source)
    listening_path = ep_dir / "listening.tsv"
    extended_path = ep_dir / "extended.tsv"
    listening_rows = read_tsv(listening_path, LISTENING_COLUMNS)
    extended_rows = read_tsv(extended_path, EXTENDED_COLUMNS)

    files: set[str] = set()
    cuts: list[Cut] = []
    for table_kind, table_path, rows in (
        ("listening", listening_path, listening_rows),
        ("extended", extended_path, extended_rows),
    ):
        ids: set[tuple[str, str]] = set()
        for line_number, row in enumerate(rows, start=2):
            cut = validate_cut_row(
                row,
                table_path=table_path,
                line_number=line_number,
                source_duration=source_duration,
                ids=ids,
                filenames=files,
                kind=table_kind,
            )
            cut.episode = episode
            cut.source_path = source
            cut.source_duration = source_duration
            cut.output_path = out_root / season / episode / ("audio" if table_kind == "listening" else "extended") / cut.filename
            # Existing filenames already carry the episode token.  Prefix only
            # non-conforming inputs so a season flat package remains unique.
            flat_name = cut.filename if cut.filename.upper().startswith(episode) else f"{episode}-{cut.filename}"
            cut.flat_path = out_root / season / "flat" / f"{season}-精选" / flat_name
            cuts.append(cut)
    return EpisodePlan(episode, season, ep_dir, source, source_duration, cuts)


def load_manifest(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or set(MANIFEST_FIELDS) - set(reader.fieldnames):
                raise CutterError(f"existing manifest has unexpected or missing columns: {path}")
            result: dict[str, dict[str, str]] = {}
            for row in reader:
                key = row.get("path", "")
                if key:
                    result[key] = {field: clean_cell(row.get(field, "")) for field in MANIFEST_FIELDS}
            return result
    except OSError as exc:
        raise CutterError(f"cannot read existing manifest {path}: {exc}") from exc


def relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def expected_from_manifest(row: dict[str, str] | None, cut: Cut) -> bool:
    if not row:
        return True
    comparisons = {
        "episode": cut.episode,
        "kind": cut.kind,
        "item_id": cut.item_id,
        "filename": cut.filename,
        "requested_start_s": format_seconds(cut.start),
        "requested_end_s": format_seconds(cut.end),
    }
    for field, expected in comparisons.items():
        actual = row.get(field, "")
        try:
            if field in {"requested_start_s", "requested_end_s"}:
                if abs(float(actual) - float(expected)) > 0.002:
                    return False
            elif actual != expected:
                return False
        except ValueError:
            return False
    return True


def check_existing(path: Path, cut: Cut, manifest_row: dict[str, str] | None) -> tuple[bool, float | None, str | None]:
    if not path.exists():
        return False, None, None
    if not path.is_file():
        raise CutterError(f"output path exists but is not a file: {path}")
    actual = probe_duration(path)
    expected = cut.padded_duration
    if abs(actual - expected) > DURATION_TOLERANCE:
        raise CutterError(
            f"refusing to overwrite mismatched output {path}: actual {actual:.3f}s, expected {expected:.3f}s"
        )
    digest = sha256(path)
    if manifest_row and not expected_from_manifest(manifest_row, cut):
        raise CutterError(f"existing manifest entry does not match current TSV for {path}; refusing to overwrite")
    if manifest_row and manifest_row.get("sha256") and manifest_row["sha256"] != digest:
        raise CutterError(f"existing output hash differs from manifest for {path}; refusing to overwrite")
    return True, actual, digest


def ffmpeg_cut(cut: Cut, *, dry_run: bool) -> tuple[float, str, str]:
    """Create one padded MP3 and return (actual duration, sha256, action)."""
    if dry_run:
        return cut.padded_duration, "", "dry-run"
    cut.output_path.parent.mkdir(parents=True, exist_ok=True)
    destination = cut.output_path
    if destination.exists():
        raise CutterError(f"output appeared while processing; refusing to overwrite: {destination}")
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.stem}.", suffix=".part.mp3", dir=destination.parent)
    os.close(fd)
    temporary = Path(temp_name)
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        format_seconds(cut.start),
        "-i",
        str(cut.source_path),
        "-t",
        format_seconds(cut.speech_duration),
        "-map",
        "0:a:0",
        "-af",
        f"adelay={int(round(DEFAULT_PADDING * 1000))}:all=1,apad=pad_dur={format_seconds(DEFAULT_PADDING)}",
        "-t",
        format_seconds(cut.padded_duration),
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        str(temporary),
    ]
    try:
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=300)
        except FileNotFoundError as exc:
            raise CutterError("ffmpeg is required but was not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise CutterError(f"ffmpeg timed out while cutting {cut.filename}") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise CutterError(f"ffmpeg failed while cutting {cut.filename}: {detail}") from exc
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise CutterError(f"ffmpeg produced no output for {cut.filename}")
        actual = probe_duration(temporary)
        if abs(actual - cut.padded_duration) > DURATION_TOLERANCE:
            raise CutterError(
                f"encoded duration mismatch for {cut.filename}: actual {actual:.3f}s, expected {cut.padded_duration:.3f}s"
            )
        digest = sha256(temporary)
        if destination.exists():
            raise CutterError(f"output appeared before atomic install; refusing to overwrite: {destination}")
        os.replace(temporary, destination)
        return actual, digest, "created"
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def ensure_flat_alias(cut: Cut, digest: str, *, dry_run: bool) -> str:
    target = cut.flat_path
    source = cut.output_path
    if dry_run:
        return "dry-run"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file():
            raise CutterError(f"flat output path exists but is not a file: {target}")
        existing_digest = sha256(target)
        if existing_digest != digest:
            raise CutterError(f"flat output hash differs; refusing to overwrite: {target}")
        return "skipped-existing"
    try:
        os.link(source, target)
        return "hardlink"
    except OSError:
        # A caller may put --out across filesystems.  Copying the already
        # verified encoded file is safe and keeps the flat package usable.
        shutil.copy2(source, target)
        if sha256(target) != digest:
            raise CutterError(f"flat copy hash mismatch: {target}")
        return "copied"


def ensure_interval_alias(
    cut: Cut,
    canonical_path: Path,
    actual_duration: float,
    digest: str,
    *,
    dry_run: bool,
) -> tuple[float, str, str]:
    """Reuse an already encoded identical interval without a second encode."""
    if dry_run:
        return cut.padded_duration, "", "dry-run"
    destination = cut.output_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise CutterError(f"output appeared while making interval alias: {destination}")
    try:
        os.link(canonical_path, destination)
        action = "hardlink-interval"
    except OSError:
        shutil.copy2(canonical_path, destination)
        action = "copy-interval"
    if sha256(destination) != digest:
        raise CutterError(f"interval alias hash mismatch: {destination}")
    return actual_duration, digest, action


def row_for_cut(cut: Cut, *, repo_root: Path, action: str) -> dict[str, str]:
    actual = cut.actual_duration if cut.actual_duration is not None else cut.padded_duration
    digest = cut.digest or ""
    return {
        "episode": cut.episode,
        "season": cut.episode[:3].replace("S", "Season"),
        "kind": cut.kind,
        "item_id": cut.item_id,
        "type": cut.clip_type,
        "source": str(cut.source_path),
        "source_duration_s": format_seconds(cut.source_duration),
        "requested_start_s": format_seconds(cut.start),
        "requested_end_s": format_seconds(cut.end),
        "requested_duration_s": format_seconds(cut.requested_duration),
        "speech_duration_s": format_seconds(cut.speech_duration),
        "padding_start_s": format_seconds(DEFAULT_PADDING),
        "padding_end_s": format_seconds(DEFAULT_PADDING),
        "actual_duration_s": format_seconds(actual),
        "filename": cut.filename,
        "path": relative(cut.output_path, repo_root),
        "flat_path": relative(cut.flat_path, repo_root) if cut.daily else "",
        "sha256": digest,
        "status": STATUS,
        "action": action,
    }


def write_manifest(path: Path, rows: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temporary = Path(temp_name)
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
            writer.writeheader()
            for key in sorted(rows):
                writer.writerow({field: rows[key].get(field, "") for field in MANIFEST_FIELDS})
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def validate_flat_collisions(plans: Sequence[EpisodePlan]) -> None:
    owners: dict[Path, Cut] = {}
    for plan in plans:
        for cut in plan.cuts:
            if not cut.daily:
                continue
            old = owners.get(cut.flat_path)
            if old is not None and (old.episode, old.kind, old.item_id) != (cut.episode, cut.kind, cut.item_id):
                raise CutterError(f"flat filename collision: {cut.flat_path}")
            owners[cut.flat_path] = cut


def process_plan(plan: EpisodePlan, *, out_root: Path, manifest: dict[str, dict[str, str]], dry_run: bool) -> tuple[int, int, int]:
    cuts_created = 0
    cuts_skipped = 0
    flat_count = 0
    interval_cache: dict[tuple[str, float, float, float], tuple[Path, float, str]] = {}
    for cut in plan.cuts:
        manifest_key = relative(cut.output_path, out_root)
        previous = manifest.get(manifest_key)
        exists, actual, digest = check_existing(cut.output_path, cut, previous)
        cache_key = (
            str(cut.source_path),
            round(cut.start, 3),
            round(cut.end, 3),
            round(DEFAULT_PADDING, 3),
        )
        if exists:
            cut.actual_duration = actual
            cut.digest = digest
            cut.action = "skipped-existing"
            cuts_skipped += 1
        else:
            cached = interval_cache.get(cache_key)
            if cached is not None:
                canonical_path, cached_actual, cached_digest = cached
                actual, digest, action = ensure_interval_alias(
                    cut,
                    canonical_path,
                    cached_actual,
                    cached_digest,
                    dry_run=dry_run,
                )
            else:
                actual, digest, action = ffmpeg_cut(cut, dry_run=dry_run)
            cut.actual_duration = actual
            cut.digest = digest
            cut.action = action
            if action != "dry-run":
                cuts_created += 1
        if cut.digest:
            interval_cache.setdefault(cache_key, (cut.output_path, cut.actual_duration or cut.padded_duration, cut.digest))
        if cut.digest and cut.daily:
            flat_action = ensure_flat_alias(cut, cut.digest, dry_run=dry_run)
            if flat_action != "dry-run":
                flat_count += 1
        manifest[manifest_key] = row_for_cut(cut, repo_root=out_root, action=cut.action)
    return cuts_created, cuts_skipped, flat_count


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    global DEFAULT_PADDING
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="house-of-cards-audio repository root")
    parser.add_argument("--source-root", required=True, type=Path, help="root containing the season MP3 folders")
    parser.add_argument("--out", required=True, type=Path, help="output root for generated audio packages")
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--episode", type=episode_token, help="process one episode, e.g. S01E01")
    selector.add_argument("--season", type=season_token, help="process one season, e.g. 01 or Season01")
    parser.add_argument("--padding", type=float, default=DEFAULT_PADDING, help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", help="validate and plan without writing files")
    args = parser.parse_args(argv)
    if args.padding < 0 or args.padding > 0.45:
        parser.error("--padding must be between 0 and 0.45 seconds")
    # The cutter's public default is intentionally 0.4 s.  Keep the module
    # constant in sync with the CLI when a test/operator chooses another pad.
    DEFAULT_PADDING = args.padding
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        run_ffprobe_self_check()
        repo = args.repo.expanduser().resolve()
        source_root = args.source_root.expanduser().resolve()
        out_root = args.out.expanduser().resolve()
        episode_dirs = discover_episode_dirs(repo, args.episode, args.season)
        plans = [build_plan(ep_dir, source_root, out_root) for ep_dir in episode_dirs]
        validate_flat_collisions(plans)
        seasons = sorted({plan.season for plan in plans})
        manifests = {season: load_manifest(out_root / season / "manifest.csv") for season in seasons}

        total_cuts = total_created = total_skipped = total_flat = 0
        for plan in plans:
            manifest = manifests[plan.season]
            if args.dry_run:
                # Dry-run still inspects existing files, but it never creates
                # an output directory, manifest, temp file, or alias.
                created, skipped, flat = process_plan(plan, out_root=out_root, manifest=manifest, dry_run=True)
            else:
                created, skipped, flat = process_plan(plan, out_root=out_root, manifest=manifest, dry_run=False)
                write_manifest(out_root / plan.season / "manifest.csv", manifest)
            total_cuts += len(plan.cuts)
            total_created += created
            total_skipped += skipped
            total_flat += flat
            daily = sum(1 for cut in plan.cuts if cut.daily)
            print(
                f"{plan.episode}: source={plan.source_path.name} duration={plan.source_duration:.3f}s "
                f"cuts={len(plan.cuts)} daily-flat={daily} created={created} skipped={skipped}"
            )
        suffix = " (no files written)" if args.dry_run else ""
        print(
            f"validated {len(plans)} episode(s), {total_cuts} cuts, {total_flat} flat aliases; "
            f"created={total_created}, skipped={total_skipped}{suffix}"
        )
        return 0
    except CutterError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
