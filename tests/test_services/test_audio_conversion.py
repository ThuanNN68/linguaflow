"""Focused tests for bounded browser-audio preprocessing."""

from __future__ import annotations

import asyncio

import pytest
from imageio_ffmpeg import get_ffmpeg_exe

from src.services.voice.audio_conversion import (
    AudioConversionError,
    AudioConversionTimeoutError,
    FFmpegAudioConverter,
)


def _converter(*, runner=None, max_input=20, max_output=20):
    return FFmpegAudioConverter(
        timeout_seconds=30,
        max_input_bytes=max_input,
        max_output_bytes=max_output,
        executable="ffmpeg",
        runner=runner,
    )


def test_ffmpeg_concurrency_is_safe_across_separate_event_loops(monkeypatch):
    converter = _converter()
    monkeypatch.setattr(converter, "_run_ffmpeg_sync", lambda audio: audio)

    assert asyncio.run(converter._run_ffmpeg(b"first-loop")) == b"first-loop"
    assert asyncio.run(converter._run_ffmpeg(b"second-loop")) == b"second-loop"


def test_ffmpeg_command_is_fixed_shell_free_and_uses_only_pipes():
    command = _converter().command()

    assert command[0] == "ffmpeg"
    assert command.count("pipe:0") == 1
    assert command.count("pipe:1") == 1
    assert ("-ac", "1") == command[command.index("-ac") : command.index("-ac") + 2]
    assert ("-ar", "16000") == command[command.index("-ar") : command.index("-ar") + 2]
    assert ("-c:a", "flac") == command[command.index("-c:a") : command.index("-c:a") + 2]
    assert "-map_metadata" in command
    assert "-vn" in command
    assert all("voice.webm" not in argument for argument in command)


@pytest.mark.asyncio
async def test_converter_returns_temporary_flac_without_mutating_input():
    original = b"browser-native-audio"
    seen: list[bytes] = []

    async def runner(audio_bytes: bytes) -> bytes:
        seen.append(audio_bytes)
        return b"converted-flac"

    result = await _converter(runner=runner).convert(original)

    assert original == b"browser-native-audio"
    assert seen == [original]
    assert result.data == b"converted-flac"
    assert result.filename == "voice-transcription.flac"
    assert result.content_type == "audio/flac"


@pytest.mark.asyncio
@pytest.mark.parametrize("audio_bytes", [b"", b"input-is-too-large"])
async def test_converter_rejects_empty_or_oversized_input_without_running(
    audio_bytes,
):
    called = False

    async def runner(_audio_bytes: bytes) -> bytes:
        nonlocal called
        called = True
        return b"flac"

    with pytest.raises(AudioConversionError, match="preprocessing failed"):
        await _converter(runner=runner, max_input=5).convert(audio_bytes)
    assert called is False


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [b"", b"converted-output-too-large"])
async def test_converter_rejects_empty_or_oversized_output(output):
    async def runner(_audio_bytes: bytes) -> bytes:
        return output

    with pytest.raises(AudioConversionError, match="preprocessing failed"):
        await _converter(runner=runner, max_output=5).convert(b"input")


@pytest.mark.asyncio
async def test_converter_timeout_is_controlled_without_raw_details():
    async def runner(_audio_bytes: bytes) -> bytes:
        raise TimeoutError("secret path and ffmpeg output")

    with pytest.raises(AudioConversionTimeoutError, match="preprocessing timed out") as exc_info:
        await _converter(runner=runner).convert(b"input")
    assert "secret path" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_converter_failure_is_controlled_without_raw_details():
    async def runner(_audio_bytes: bytes) -> bytes:
        raise RuntimeError("secret path and ffmpeg output")

    with pytest.raises(AudioConversionError, match="preprocessing failed") as exc_info:
        await _converter(runner=runner).convert(b"input")
    assert "secret path" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_real_ffmpeg_conversion_does_not_require_async_subprocess_support(monkeypatch):
    executable = get_ffmpeg_exe()
    source = await asyncio.create_subprocess_exec(
        executable,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=0.1",
        "-c:a",
        "libopus",
        "-f",
        "webm",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    webm_bytes, _ = await source.communicate()
    assert source.returncode == 0

    async def unsupported_async_subprocess(*_args, **_kwargs):
        raise NotImplementedError("selector event loop does not support subprocesses")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unsupported_async_subprocess)

    result = await FFmpegAudioConverter(
        timeout_seconds=10,
        max_input_bytes=1024 * 1024,
        max_output_bytes=1024 * 1024,
    ).convert(webm_bytes)

    assert result.data.startswith(b"fLaC")
    assert result.content_type == "audio/flac"
