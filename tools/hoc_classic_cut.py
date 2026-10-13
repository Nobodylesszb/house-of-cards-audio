#!/usr/bin/env python3
"""Create an MP3-only classic edition from a manually reviewed selection TSV.

The selection is metadata, not proof of subjective audio review.  Every clip
is one continuous source interval.  The script never separates voices or
joins different scenes, and never overwrites an existing MP3 silently.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import tempfile
from pathlib import Path

import hoc_cut_audio as base


FIELDS = ["order", "episode", "source_kind", "source_id", "start", "end", "title_slug", "reason", "confidence", "caveat"]
OUT_FIELDS = FIELDS + ["source_mp3", "output_mp3", "actual_duration_s", "sha256", "processing", "status"]
PROCESSING = "highpass=80Hz;afftdn=nr=6:nf=-50:tn=1;silence-padding=0.4s-each-side"


def cut_clean(cut: base.Cut, *, dry_run: bool) -> tuple[float, str]:
    """Apply only light broadband cleanup; never claim source separation."""
    if dry_run:
        return cut.padded_duration, ""
    destination = cut.output_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise base.CutterError(f"refusing to overwrite {destination}")
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.stem}.", suffix=".part.mp3", dir=destination.parent)
    os.close(fd)
    temporary = Path(temp_name)
    # afftdn buffers roughly 0.3 seconds.  Feed it extra *silence*, then trim
    # back to the intended duration, so no source speech or final phoneme is
    # sacrificed to the filter's latency.
    filter_chain = (
        "highpass=f=80,"
        f"adelay={int(round(base.DEFAULT_PADDING * 1000))}:all=1,"
        f"apad=pad_dur={base.format_seconds(base.DEFAULT_PADDING + 0.35)},"
        "afftdn=nr=6:nf=-50:tn=1,"
        f"atrim=duration={base.format_seconds(cut.padded_duration)},asetpts=N/SR/TB"
    )
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", base.format_seconds(cut.start), "-i", str(cut.source_path),
        "-t", base.format_seconds(cut.speech_duration), "-map", "0:a:0",
        "-af", filter_chain, "-t", base.format_seconds(cut.padded_duration),
        "-c:a", "libmp3lame", "-b:a", "160k", str(temporary),
    ]
    try:
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=300)
        except FileNotFoundError as exc:
            raise base.CutterError("ffmpeg is required") from exc
        except subprocess.CalledProcessError as exc:
            raise base.CutterError(f"ffmpeg failed for {cut.filename}: {exc.stderr.strip()}") from exc
        except subprocess.TimeoutExpired as exc:
            raise base.CutterError(f"ffmpeg timed out for {cut.filename}") from exc
        actual = base.probe_duration(temporary)
        if abs(actual - cut.padded_duration) > base.DURATION_TOLERANCE:
            raise base.CutterError(f"encoded duration mismatch for {cut.filename}: {actual:.3f}s")
        digest = base.sha256(temporary)
        if destination.exists():
            raise base.CutterError(f"output appeared before install: {destination}")
        os.replace(temporary, destination)
        return actual, digest
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--out", type=Path, help="output root; defaults to repository root")
    parser.add_argument("--edition-name", default="经典独白与对话-纯享版-按季顺序", help="single output folder name")
    parser.add_argument("--manifest-name", default="classic-pure-manifest.tsv", help="manifest filename in output root")
    parser.add_argument("--min-count", type=int, default=40)
    parser.add_argument("--max-count", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    repo = args.repo.resolve()
    out_root = args.out.resolve() if args.out else repo
    source_root = args.source_root.resolve()
    selection_path = args.selection.resolve()
    rows = base.read_tsv(selection_path, set(FIELDS))
    if args.edition_name in {"", ".", ".."} or "/" in args.edition_name or "\\" in args.edition_name:
        raise base.CutterError("edition-name must be a single directory name")
    if args.manifest_name in {"", ".", ".."} or "/" in args.manifest_name or "\\" in args.manifest_name or not args.manifest_name.endswith(".tsv"):
        raise base.CutterError("manifest-name must be a TSV filename in the output root")
    if args.min_count < 1 or args.max_count < args.min_count:
        raise base.CutterError("invalid min-count/max-count")
    pure_dir = out_root / args.edition_name
    manifest_path = out_root / args.manifest_name
    seen_orders: set[int] = set()
    seen_names: set[str] = set()
    validated = []
    for row in rows:
        try:
            order = int(row["order"])
        except ValueError as exc:
            raise base.CutterError(f"invalid selection order: {row['order']!r}") from exc
        if order < 1 or order in seen_orders:
            raise base.CutterError(f"duplicate or nonpositive selection order: {order}")
        seen_orders.add(order)
        episode = base.episode_token(row["episode"])
        kind = row["source_kind"]
        if kind not in {"listening", "extended"}:
            raise base.CutterError(f"{episode}: source_kind must be listening or extended")
        slug = row["title_slug"]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            raise base.CutterError(f"{episode}: invalid title_slug {slug!r}")
        ep_dir = repo / f"Season{episode[1:3]}" / episode
        table = ep_dir / f"{kind}.tsv"
        if not table.exists():
            raise base.CutterError(f"missing source table: {table}")
        source_rows = base.read_tsv(table, base.LISTENING_COLUMNS if kind == "listening" else base.EXTENDED_COLUMNS)
        id_field = "canonical_id" if kind == "listening" else "id"
        matches = [r for r in source_rows if r[id_field] == row["source_id"] and r["start"] == row["start"] and r["end"] == row["end"]]
        if len(matches) != 1:
            raise base.CutterError(f"{episode}: expected one {kind} row for {row['source_id']}, got {len(matches)}")
        original = matches[0]
        start = base.parse_seconds(row["start"], label=f"{episode} start")
        end = base.parse_seconds(row["end"], label=f"{episode} end")
        source_start = base.parse_seconds(original["start"], label=f"{episode} table start")
        source_end = base.parse_seconds(original["end"], label=f"{episode} table end")
        if abs(start - source_start) > 0.002 or abs(end - source_end) > 0.002:
            raise base.CutterError(f"{episode}: selected range differs from reviewed {kind} source range")
        if end <= start:
            raise base.CutterError(f"{episode}: nonpositive clip duration")
        source_mp3 = base.locate_source(source_root, episode)
        source_duration = base.probe_duration(source_mp3)
        if end > source_duration + 0.05:
            raise base.CutterError(f"{episode}: clip exceeds source duration")
        filename = f"{order:03d}-HOC-{episode}-{slug}.mp3"
        if filename in seen_names:
            raise base.CutterError(f"duplicate output filename: {filename}")
        seen_names.add(filename)
        cut = base.Cut(
            episode=episode,
            kind=kind,
            item_id=row["source_id"],
            clip_type="classic-pure",
            start=start,
            end=end,
            requested_duration=end - start,
            filename=filename,
            output_path=pure_dir / filename,
            flat_path=Path(),
            source_path=source_mp3,
            source_duration=source_duration,
        )
        validated.append((order, row, cut))
    validated.sort(key=lambda item: item[0])
    if [item[0] for item in validated] != list(range(1, len(validated) + 1)):
        raise base.CutterError("selection orders must be contiguous from 1")
    if [item[2].episode for item in validated] != sorted(item[2].episode for item in validated):
        raise base.CutterError("selection rows must be in season/episode order")
    if not args.min_count <= len(validated) <= args.max_count:
        raise base.CutterError(f"edition expects {args.min_count}–{args.max_count} selections, got {len(validated)}")
    output = []
    for _, row, cut in validated:
        exists, duration, digest = base.check_existing(cut.output_path, cut, None)
        if not exists:
            duration, digest = cut_clean(cut, dry_run=args.dry_run)
        output.append({
            **{field: row[field] for field in FIELDS},
            "source_mp3": str(cut.source_path),
            "output_mp3": str(cut.output_path.relative_to(out_root)),
            "actual_duration_s": base.format_seconds(duration),
            "sha256": digest,
            "processing": PROCESSING,
            "status": base.STATUS,
        })
        print(f"{cut.filename}: {'existing' if exists else 'planned' if args.dry_run else 'created'}")
    if not args.dry_run:
        with manifest_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUT_FIELDS, delimiter="\t")
            writer.writeheader()
            writer.writerows(output)
    print(f"validated={len(validated)} mp3_only_dir={pure_dir} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except base.CutterError as exc:
        raise SystemExit(f"ERROR: {exc}")
