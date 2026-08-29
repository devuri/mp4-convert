# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-08-28

First release.

### Added

- `mp3tomp4` command that converts every matching audio file in a folder to MP4,
  keeping the original base filename.
- Automatic use of embedded album art as the video background, with a solid
  colour fallback (`--cover auto|none|PATH`).
- Skip-existing behaviour by default, with `--overwrite` to force re-encoding.
- Atomic writes: each file is encoded to a `.part.mp4` and renamed on success.
- `--recursive` discovery with folder structure mirrored into `--output-dir`.
- Detection of inputs that would collide on the same output name.
- Encoding controls: `--size`, `--fps`, `--background`, `--video-codec`,
  `--preset`, `--crf`, `--audio-codec`, `--audio-bitrate`, `--no-faststart`.
- `--jobs` for parallel conversions, `--dry-run` to preview ffmpeg commands, and
  `--quiet` for scripted use.
- Distinct exit codes for failure, bad input, and missing ffmpeg.

[Unreleased]: https://github.com/OWNER/mp3-to-mp4/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/OWNER/mp3-to-mp4/releases/tag/v1.0.0
