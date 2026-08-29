"""Command line interface for mp3tomp4."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXTENSIONS: tuple[str, ...] = (".mp3",)
KNOWN_AUDIO_EXTENSIONS: tuple[str, ...] = (
    ".mp3",
    ".m4a",
    ".wav",
    ".flac",
    ".ogg",
    ".opus",
    ".aac",
    ".wma",
)
STILLIMAGE_CODECS = frozenset({"libx264", "libx265"})


class ConversionError(RuntimeError):
    """Raised when ffmpeg fails to convert a file."""


@dataclass(frozen=True)
class Settings:
    """Everything that controls how a single file is encoded."""

    width: int = 1920
    height: int = 1080
    fps: int = 30
    background: str = "black"
    video_codec: str = "libx264"
    preset: str = "medium"
    crf: int = 23
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    faststart: bool = True

    def __post_init__(self) -> None:
        if self.width % 2 or self.height % 2:
            raise ValueError("width and height must both be even numbers")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("width and height must be positive")
        if self.fps <= 0:
            raise ValueError("fps must be positive")


# --------------------------------------------------------------------------
# Pure helpers (no ffmpeg required — these are what the test suite covers)
# --------------------------------------------------------------------------


def parse_resolution(value: str) -> tuple[int, int]:
    """Parse a ``WIDTHxHEIGHT`` string into a tuple of ints."""
    separator = "x" if "x" in value.lower() else ":"
    parts = value.lower().split(separator)
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"invalid resolution: {value!r} (expected e.g. 1920x1080)")
    try:
        width, height = (int(part) for part in parts)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid resolution: {value!r} (expected e.g. 1920x1080)"
        ) from None
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("resolution values must be positive")
    if width % 2 or height % 2:
        raise argparse.ArgumentTypeError("resolution values must be even (H.264 requirement)")
    return width, height


def normalise_extensions(values: Iterable[str]) -> tuple[str, ...]:
    """Normalise user supplied extensions to lowercase, dot-prefixed form."""
    cleaned = []
    for value in values:
        for item in value.split(","):
            item = item.strip().lower()
            if not item:
                continue
            if not item.startswith("."):
                item = "." + item
            if item not in cleaned:
                cleaned.append(item)
    return tuple(cleaned)


def find_inputs(root: Path, extensions: Sequence[str], recursive: bool = False) -> list[Path]:
    """Return sorted audio files under ``root`` matching ``extensions``."""
    wanted = {ext.lower() for ext in extensions}
    walker = root.rglob("*") if recursive else root.glob("*")
    found = [
        path
        for path in walker
        if path.is_file() and path.suffix.lower() in wanted and not path.name.startswith(".")
    ]
    return sorted(found, key=lambda p: str(p).lower())


def output_path_for(source: Path, input_root: Path, output_root: Path | None) -> Path:
    """Map an input file to its ``.mp4`` destination, preserving folder structure."""
    if output_root is None:
        return source.with_suffix(".mp4")
    try:
        relative = source.relative_to(input_root)
    except ValueError:
        relative = Path(source.name)
    return (output_root / relative).with_suffix(".mp4")


def find_collisions(pairs: Sequence[tuple[Path, Path]]) -> list[Path]:
    """Return destinations that more than one source file would write to."""
    seen: dict = {}
    clashing: list[Path] = []
    for _, destination in pairs:
        key = str(destination).lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            clashing.append(destination)
    return clashing


def build_filter(settings: Settings) -> str:
    """Build the scale/pad filter chain used when a cover image is supplied."""
    return (
        f"scale={settings.width}:{settings.height}:force_original_aspect_ratio=decrease,"
        f"pad={settings.width}:{settings.height}:(ow-iw)/2:(oh-ih)/2:color={settings.background},"
        "setsar=1,format=yuv420p"
    )


def build_command(
    source: Path,
    destination: Path,
    settings: Settings,
    cover: Path | None = None,
) -> list[str]:
    """Build the full ffmpeg argument list for one conversion."""
    command: list[str] = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]

    if cover is None:
        command += [
            "-f",
            "lavfi",
            "-i",
            f"color=c={settings.background}:s={settings.width}x{settings.height}:r={settings.fps}",
        ]
    else:
        command += ["-loop", "1", "-framerate", str(settings.fps), "-i", str(cover)]

    command += ["-i", str(source)]

    if cover is not None:
        command += ["-vf", build_filter(settings)]

    command += [
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        settings.video_codec,
        "-preset",
        settings.preset,
        "-crf",
        str(settings.crf),
    ]

    if settings.video_codec in STILLIMAGE_CODECS:
        command += ["-tune", "stillimage"]

    command += [
        "-c:a",
        settings.audio_codec,
        "-b:a",
        settings.audio_bitrate,
        "-pix_fmt",
        "yuv420p",
        "-shortest",
    ]

    if settings.faststart:
        command += ["-movflags", "+faststart"]

    command.append(str(destination))
    return command


def format_command(command: Sequence[str]) -> str:
    """Render a command list as a copy-pasteable shell string."""
    return " ".join(shlex.quote(part) for part in command)


# --------------------------------------------------------------------------
# ffmpeg interaction
# --------------------------------------------------------------------------


def require_ffmpeg() -> None:
    """Raise a friendly error if ffmpeg/ffprobe are not on PATH."""
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise ConversionError(
            f"{' and '.join(missing)} not found on PATH. "
            "Install ffmpeg (https://ffmpeg.org/download.html) and try again."
        )


def has_embedded_cover(source: Path) -> bool:
    """Return True if the audio file carries attached cover art."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v",
        "-show_entries",
        "stream_disposition=attached_pic",
        "-of",
        "json",
        str(source),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        streams = json.loads(result.stdout or "{}").get("streams", [])
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return False
    return any(stream.get("disposition", {}).get("attached_pic") == 1 for stream in streams)


def extract_cover(source: Path, destination: Path) -> Path | None:
    """Extract embedded cover art to ``destination``; return None if there is none."""
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-an",
        "-frames:v",
        "1",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not destination.exists() or destination.stat().st_size == 0:
        return None
    return destination


def resolve_cover(source: Path, mode: str, temp_dir: Path) -> Path | None:
    """Work out which image (if any) should back this file's video track."""
    if mode == "none":
        return None
    if mode == "auto":
        if not has_embedded_cover(source):
            return None
        target = temp_dir / f"{abs(hash(str(source)))}.png"
        return extract_cover(source, target)
    return Path(mode)


def convert_file(
    source: Path,
    destination: Path,
    settings: Settings,
    cover_mode: str = "auto",
    dry_run: bool = False,
) -> list[str]:
    """Convert one audio file. Returns the ffmpeg command that was (or would be) run."""
    if dry_run:
        # Don't extract anything for a preview; show a readable stand-in instead
        # of a temp path that will not exist by the time the user reads it.
        if cover_mode == "auto":
            preview = Path("<embedded cover art>") if has_embedded_cover(source) else None
        elif cover_mode == "none":
            preview = None
        else:
            preview = Path(cover_mode)
        return build_command(source, destination, settings, preview)

    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="mp3tomp4-") as tmp:
        temp_dir = Path(tmp)
        cover = resolve_cover(source, cover_mode, temp_dir)
        if cover is not None and not cover.exists():
            raise ConversionError(f"cover image not found: {cover}")

        # Encode to a sibling temp file so an interrupted run never leaves a
        # half-written .mp4 that looks like a finished one.
        partial = destination.with_name(destination.name + ".part.mp4")
        command = build_command(source, partial, settings, cover)

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            partial.unlink(missing_ok=True)
            detail = (result.stderr or "").strip().splitlines()
            message = detail[-1] if detail else f"ffmpeg exited with code {result.returncode}"
            raise ConversionError(message)

        partial.replace(destination)

    return command


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    from . import __version__

    parser = argparse.ArgumentParser(
        prog="mp3tomp4",
        description="Turn audio files into MP4 videos with a static background, "
        "so they can be uploaded anywhere that only accepts video.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  mp3tomp4                          convert every .mp3 in this folder\n"
            "  mp3tomp4 ~/podcast -o ~/out -r    walk a tree, mirror it into ~/out\n"
            "  mp3tomp4 --cover art.jpg          use one image for every video\n"
            "  mp3tomp4 --skip-existing -j 4     resume a batch, four at a time\n"
        ),
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=".",
        type=Path,
        help="file or folder to convert (default: current folder)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="write MP4s here instead of next to the source files",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="also convert files in subfolders",
    )
    parser.add_argument(
        "-e",
        "--ext",
        action="append",
        default=None,
        metavar="EXT",
        help=f"input extensions to pick up (default: {' '.join(DEFAULT_EXTENSIONS)}); "
        "repeatable or comma separated",
    )
    parser.add_argument(
        "-s",
        "--size",
        type=parse_resolution,
        default=(1920, 1080),
        metavar="WxH",
        help="video resolution (default: 1920x1080)",
    )
    parser.add_argument("--fps", type=int, default=30, help="frame rate (default: 30)")
    parser.add_argument(
        "--background",
        default="black",
        metavar="COLOR",
        help="background colour name or hex, e.g. black or 0x101010 (default: black)",
    )
    parser.add_argument(
        "--cover",
        default="auto",
        metavar="MODE",
        help="'auto' to use embedded album art when present, 'none' for a plain "
        "background, or a path to an image file (default: auto)",
    )
    parser.add_argument("--video-codec", default="libx264", help="video codec (default: libx264)")
    parser.add_argument("--preset", default="medium", help="x264/x265 preset (default: medium)")
    parser.add_argument(
        "--crf", type=int, default=23, help="quality, lower is better (default: 23)"
    )
    parser.add_argument("--audio-codec", default="aac", help="audio codec (default: aac)")
    parser.add_argument("--audio-bitrate", default="192k", help="audio bitrate (default: 192k)")
    parser.add_argument(
        "--no-faststart",
        action="store_true",
        help="skip moving the MP4 index to the front of the file",
    )

    overwrite = parser.add_mutually_exclusive_group()
    overwrite.add_argument(
        "--skip-existing",
        action="store_true",
        help="leave existing MP4s alone (default)",
    )
    overwrite.add_argument(
        "-f",
        "--overwrite",
        action="store_true",
        help="re-encode even if the MP4 already exists",
    )

    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=1,
        metavar="N",
        help="run N conversions in parallel (default: 1)",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="print the ffmpeg commands without running them",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="only report problems")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    def log(message: str) -> None:
        if not args.quiet:
            print(message, flush=True)

    if args.jobs < 1:
        parser.error("--jobs must be at least 1")

    if args.cover not in ("auto", "none") and not Path(args.cover).is_file():
        print(f"error: cover image not found: {args.cover}", file=sys.stderr)
        return 2

    extensions = normalise_extensions(args.ext) if args.ext else DEFAULT_EXTENSIONS

    source_root: Path = args.input
    if source_root.is_file():
        sources = [source_root]
        input_root = source_root.parent
    elif source_root.is_dir():
        input_root = source_root
        sources = find_inputs(source_root, extensions, args.recursive)
    else:
        print(f"error: no such file or folder: {source_root}", file=sys.stderr)
        return 2

    if not sources:
        log(f"No files matching {', '.join(extensions)} in {source_root}")
        return 0

    pairs = [(src, output_path_for(src, input_root, args.output_dir)) for src in sources]

    collisions = find_collisions(pairs)
    if collisions:
        names = ", ".join(str(path.name) for path in collisions[:5])
        print(
            f"error: several inputs would produce the same output ({names}). "
            "Use --output-dir or rename the files.",
            file=sys.stderr,
        )
        return 2

    if not args.overwrite:
        pending = []
        for src, dst in pairs:
            if dst.exists():
                log(f"skip     {src.name} (output already exists)")
            else:
                pending.append((src, dst))
        pairs = pending

    if not pairs:
        log("Nothing to do.")
        return 0

    if not args.dry_run:
        try:
            require_ffmpeg()
        except ConversionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3

    settings = Settings(
        width=args.size[0],
        height=args.size[1],
        fps=args.fps,
        background=args.background,
        video_codec=args.video_codec,
        preset=args.preset,
        crf=args.crf,
        audio_codec=args.audio_codec,
        audio_bitrate=args.audio_bitrate,
        faststart=not args.no_faststart,
    )

    total = len(pairs)
    width = len(str(total))
    failures: list[tuple[Path, str]] = []
    completed = 0

    def worker(index_and_pair: tuple[int, tuple[Path, Path]]) -> tuple[Path, str] | None:
        index, (src, dst) = index_and_pair
        try:
            command = convert_file(src, dst, settings, args.cover, args.dry_run)
        except ConversionError as exc:
            return (src, str(exc))
        except OSError as exc:
            return (src, str(exc))
        if args.dry_run:
            print(format_command(command), flush=True)
        else:
            log(f"[{index:>{width}}/{total}] {src.name} -> {dst.name}")
        return None

    work = list(enumerate(pairs, start=1))
    if args.jobs == 1 or args.dry_run:
        results = [worker(item) for item in work]
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            results = list(pool.map(worker, work))

    for result in results:
        if result is None:
            completed += 1
        else:
            failures.append(result)

    for src, message in failures:
        print(f"error: {src.name}: {message}", file=sys.stderr)

    if args.dry_run:
        return 0

    log(f"Done. {completed} converted, {len(failures)} failed.")
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
