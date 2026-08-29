"""Batch-convert audio files into MP4 videos with a static background."""

__version__ = "1.0.0"

from .cli import Settings, build_command, main, output_path_for  # noqa: E402

__all__ = ["Settings", "build_command", "main", "output_path_for", "__version__"]
