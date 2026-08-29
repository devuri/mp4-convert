from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import pytest

from mp3tomp4.cli import (
    Settings,
    build_command,
    find_collisions,
    find_inputs,
    main,
    normalise_extensions,
    output_path_for,
    parse_resolution,
)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


# --------------------------------------------------------------------- naming


def test_output_keeps_base_filename():
    src = Path("My Track.mp3")
    assert output_path_for(src, Path("."), None) == Path("My Track.mp4")


def test_output_dir_mirrors_folder_structure():
    src = Path("audio/season1/episode_42.mp3")
    result = output_path_for(src, Path("audio"), Path("out"))
    assert result == Path("out/season1/episode_42.mp4")


def test_output_dir_flattens_files_outside_the_root():
    src = Path("/elsewhere/song.mp3")
    assert output_path_for(src, Path("audio"), Path("out")) == Path("out/song.mp4")


def test_dotted_filenames_only_lose_the_final_suffix():
    src = Path("Live at 12.30 - part.2.mp3")
    assert output_path_for(src, Path("."), None).name == "Live at 12.30 - part.2.mp4"


# ------------------------------------------------------------------ discovery


def test_find_inputs_is_sorted_and_filtered(tmp_path):
    for name in ("b.mp3", "a.mp3", "c.wav", "notes.txt", ".hidden.mp3"):
        (tmp_path / name).touch()
    found = find_inputs(tmp_path, (".mp3",))
    assert [p.name for p in found] == ["a.mp3", "b.mp3"]


def test_find_inputs_matches_extensions_case_insensitively(tmp_path):
    (tmp_path / "SHOUT.MP3").touch()
    assert len(find_inputs(tmp_path, (".mp3",))) == 1


def test_find_inputs_recursive(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "top.mp3").touch()
    (tmp_path / "nested" / "deep.mp3").touch()
    assert len(find_inputs(tmp_path, (".mp3",), recursive=False)) == 1
    assert len(find_inputs(tmp_path, (".mp3",), recursive=True)) == 2


def test_collisions_are_detected():
    pairs = [
        (Path("song.mp3"), Path("song.mp4")),
        (Path("song.wav"), Path("song.mp4")),
        (Path("other.mp3"), Path("other.mp4")),
    ]
    assert find_collisions(pairs) == [Path("song.mp4")]


# --------------------------------------------------------------------- inputs


@pytest.mark.parametrize(
    "value,expected",
    [("1920x1080", (1920, 1080)), ("1280X720", (1280, 720)), ("640:480", (640, 480))],
)
def test_parse_resolution(value, expected):
    assert parse_resolution(value) == expected


@pytest.mark.parametrize("value", ["1920", "axb", "1921x1080", "-2x2", "1920x1080x30"])
def test_parse_resolution_rejects_bad_values(value):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_resolution(value)


def test_normalise_extensions():
    assert normalise_extensions(["mp3", ".WAV,flac", " "]) == (".mp3", ".wav", ".flac")


def test_settings_reject_odd_dimensions():
    with pytest.raises(ValueError):
        Settings(width=1921)


# -------------------------------------------------------------- command build


def test_command_uses_lavfi_colour_source_without_cover():
    command = build_command(Path("in.mp3"), Path("out.mp4"), Settings())
    assert "lavfi" in command
    assert "color=c=black:s=1920x1080:r=30" in command
    assert command[-1] == "out.mp4"
    assert "-vf" not in command


def test_command_loops_a_cover_image_when_given():
    command = build_command(Path("in.mp3"), Path("out.mp4"), Settings(), Path("art.jpg"))
    assert "-loop" in command
    assert "art.jpg" in command
    assert "lavfi" not in command
    filters = command[command.index("-vf") + 1]
    assert "force_original_aspect_ratio=decrease" in filters
    assert "setsar=1" in filters


def test_command_respects_settings():
    settings = Settings(width=1280, height=720, fps=24, audio_bitrate="320k", crf=18)
    command = build_command(Path("in.mp3"), Path("out.mp4"), settings)
    assert "color=c=black:s=1280x720:r=24" in command
    assert command[command.index("-b:a") + 1] == "320k"
    assert command[command.index("-crf") + 1] == "18"


def test_stillimage_tune_only_for_x264_family():
    assert "-tune" in build_command(Path("a.mp3"), Path("a.mp4"), Settings())
    other = build_command(Path("a.mp3"), Path("a.mp4"), Settings(video_codec="libvpx-vp9"))
    assert "-tune" not in other


def test_faststart_can_be_disabled():
    assert "+faststart" in build_command(Path("a.mp3"), Path("a.mp4"), Settings())
    assert "+faststart" not in build_command(
        Path("a.mp3"), Path("a.mp4"), Settings(faststart=False)
    )


# ------------------------------------------------------------------ behaviour


def test_dry_run_prints_commands_and_writes_nothing(tmp_path, capsys):
    (tmp_path / "song.mp3").touch()
    assert main([str(tmp_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "ffmpeg" in out
    assert not (tmp_path / "song.mp4").exists()


def test_missing_input_returns_error_code(tmp_path):
    assert main([str(tmp_path / "nope"), "--dry-run"]) == 2


def test_empty_folder_is_not_an_error(tmp_path, capsys):
    assert main([str(tmp_path)]) == 0
    assert "No files" in capsys.readouterr().out


def test_colliding_inputs_abort(tmp_path, capsys):
    (tmp_path / "song.mp3").touch()
    (tmp_path / "song.wav").touch()
    assert main([str(tmp_path), "-e", "mp3,wav", "--dry-run"]) == 2
    assert "same output" in capsys.readouterr().err


def test_missing_cover_image_fails_before_encoding(tmp_path, capsys):
    (tmp_path / "song.mp3").touch()
    assert main([str(tmp_path), "--cover", str(tmp_path / "nope.jpg")]) == 2
    assert "cover image not found" in capsys.readouterr().err


def test_existing_outputs_are_skipped_by_default(tmp_path, capsys):
    (tmp_path / "song.mp3").touch()
    (tmp_path / "song.mp4").write_text("already here")
    assert main([str(tmp_path)]) == 0
    assert "skip" in capsys.readouterr().out
    assert (tmp_path / "song.mp4").read_text() == "already here"


# ---------------------------------------------------------------- integration


@needs_ffmpeg
def test_real_conversion_produces_a_playable_video(tmp_path):
    source = tmp_path / "tone.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            str(source),
        ],
        check=True,
    )

    assert main([str(tmp_path), "-s", "320x240", "--preset", "ultrafast", "-q"]) == 0

    output = tmp_path / "tone.mp4"
    assert output.exists() and output.stat().st_size > 0
    assert not list(tmp_path.glob("*.part.mp4"))

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height",
            "-of",
            "csv=p=0",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "video" in probe.stdout
    assert "audio" in probe.stdout
    assert "320" in probe.stdout
