# mp3tomp4

Batch-convert audio files into MP4 videos with a static background — for the many
platforms that will happily host an hour of talking, as long as you wrap it in a
video container first.

[![CI](https://github.com/OWNER/mp3-to-mp4/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/mp3-to-mp4/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

```console
$ mp3tomp4
[1/3] episode_42.mp3 -> episode_42.mp4
[2/3] My Track.mp3 -> My Track.mp4
[3/3] song1.mp3 -> song1.mp4
Done. 3 converted, 0 failed.
```

## Features

- Converts every audio file in a folder in one command, keeping the base filename.
- Uses embedded album art as the video background automatically, falling back to a
  solid colour when a file has none.
- Skips files that have already been converted, so an interrupted batch resumes
  cheaply instead of re-encoding everything.
- Writes to a temporary file and renames on success — you never end up with a
  half-written `.mp4` that looks finished.
- Mirrors subfolder structure into an output directory with `--recursive`.
- Parallel encoding with `-j`, plus `--dry-run` to see the exact ffmpeg commands first.
- Pure standard library. The only dependency is ffmpeg itself.

## Requirements

- Python 3.9 or newer
- [ffmpeg](https://ffmpeg.org/download.html) and `ffprobe` on your `PATH`

| Platform | Install ffmpeg |
| --- | --- |
| macOS | `brew install ffmpeg` |
| Debian / Ubuntu | `sudo apt install ffmpeg` |
| Fedora | `sudo dnf install ffmpeg` |
| Windows | `winget install Gyan.FFmpeg` |

Check it worked with `ffmpeg -version`.

## Install

```bash
pipx install git+https://github.com/OWNER/mp3-to-mp4.git
```

Or with pip, in a virtual environment:

```bash
pip install git+https://github.com/OWNER/mp3-to-mp4.git
```

To work on the code:

```bash
git clone https://github.com/OWNER/mp3-to-mp4.git
cd mp3-to-mp4
pip install -e ".[dev]"
```

## Quick start

Convert every `.mp3` in the current folder:

```bash
mp3tomp4
```

```text
song1.mp3       ->  song1.mp4
My Track.mp3    ->  My Track.mp4
episode_42.mp3  ->  episode_42.mp4
```

If you would rather not install anything, the package runs as a module from a
clone: `python -m mp3tomp4`.

## Usage

```bash
mp3tomp4 [INPUT] [OPTIONS]
```

`INPUT` is a file or a folder, and defaults to the current directory.

### Common recipes

```bash
# See what would happen, without encoding anything
mp3tomp4 --dry-run

# Walk a whole tree and mirror it into a separate output folder
mp3tomp4 ~/podcast -r -o ~/uploads

# One cover image for every track
mp3tomp4 --cover artwork.png

# Plain background, ignore any embedded album art
mp3tomp4 --cover none --background 0x101010

# 720p, small files, four at a time
mp3tomp4 -s 1280x720 --crf 28 --preset veryfast -j 4

# Other formats too
mp3tomp4 -e mp3,m4a,flac

# Re-encode everything, including files already converted
mp3tomp4 --overwrite
```

### Options

| Option | Default | Description |
| --- | --- | --- |
| `-o`, `--output-dir DIR` | alongside source | Write MP4s to `DIR` instead of next to the input |
| `-r`, `--recursive` | off | Also convert files in subfolders |
| `-e`, `--ext EXT` | `.mp3` | Input extensions; repeatable or comma separated |
| `-s`, `--size WxH` | `1920x1080` | Video resolution (both values must be even) |
| `--fps N` | `30` | Frame rate |
| `--background COLOR` | `black` | Colour name or hex such as `0x101010` |
| `--cover MODE` | `auto` | `auto`, `none`, or a path to an image file |
| `--video-codec NAME` | `libx264` | Video codec |
| `--preset NAME` | `medium` | x264/x265 speed-vs-size preset |
| `--crf N` | `23` | Quality; lower means better and bigger |
| `--audio-codec NAME` | `aac` | Audio codec |
| `--audio-bitrate RATE` | `192k` | Audio bitrate |
| `--no-faststart` | off | Skip moving the MP4 index to the front of the file |
| `--skip-existing` | on | Leave existing MP4s alone |
| `-f`, `--overwrite` | off | Re-encode even when the MP4 exists |
| `-j`, `--jobs N` | `1` | Run N conversions in parallel |
| `-n`, `--dry-run` | off | Print ffmpeg commands without running them |
| `-q`, `--quiet` | off | Only report problems |

Exit codes: `0` success, `1` one or more files failed, `2` bad input, `3` ffmpeg missing.

## How it works

Each conversion is a single ffmpeg call. With no cover art, the video track comes
from a generated colour source:

```bash
ffmpeg -y -f lavfi -i color=c=black:s=1920x1080:r=30 -i input.mp3 \
  -c:v libx264 -preset medium -crf 23 -tune stillimage \
  -c:a aac -b:a 192k -pix_fmt yuv420p -shortest -movflags +faststart output.mp4
```

With cover art, a single image is looped instead, then scaled and letterboxed to
fit the target resolution without distortion. `-shortest` ends the video when the
audio ends, and `-tune stillimage` tells x264 to expect a picture that never
changes, which keeps these files remarkably small — a static 1080p hour is
typically only a few megabytes larger than the source audio.

Run `mp3tomp4 --dry-run` to see the exact command for your own files.

## FAQ

**Why is my output only a few MB for an hour of audio?**
That is expected. The video track is one unchanging frame, so H.264 spends almost
nothing on it; nearly all the size is the audio.

**Two files want the same output name.** If `song.mp3` and `song.wav` are both in
scope, they would both produce `song.mp4`. The tool refuses to start rather than
silently clobbering one. Rename a file or use `--output-dir`.

**Can I use a video or animated background?** Not currently — the design assumes a
still image, which is what makes the output so small. Open an issue if you need it.

**Does `-j` make it faster?** Usually yes for many short files. For a handful of
long ones, ffmpeg already uses multiple cores per encode, so gains are smaller.

## Development

```bash
pip install -e ".[dev]"
pytest          # the ffmpeg integration test skips automatically if ffmpeg is absent
ruff check .
```

Contributions are welcome — please open an issue before large changes, and keep
`pytest` and `ruff check` green.

## Releasing

Maintainers: bump the version in `pyproject.toml` and `src/mp3tomp4/__init__.py`,
update `CHANGELOG.md`, then tag:

```bash
git tag -a v1.0.0 -m "v1.0.0"
git push origin v1.0.0
```

The release workflow builds the sdist and wheel and attaches them to a GitHub
Release automatically.

## License

MIT — see [LICENSE](LICENSE).
