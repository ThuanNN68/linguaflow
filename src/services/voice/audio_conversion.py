"""Bounded STT-only conversion of browser-native audio.

The durable attachment remains untouched. FFmpeg receives bytes through stdin
and returns a temporary Gemini-supported FLAC representation through stdout;
no user-controlled path or converter diagnostics cross the service boundary.
"""

from __future__ import annotations

import asyncio
import subprocess
import threading
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from imageio_ffmpeg import get_ffmpeg_exe

_READ_CHUNK_BYTES = 64 * 1024
_FFMPEG_CONCURRENCY_BY_LOOP: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _ffmpeg_concurrency() -> asyncio.Semaphore:
    """Return a semaphore owned by the currently running event loop."""
    loop = asyncio.get_running_loop()
    semaphore = _FFMPEG_CONCURRENCY_BY_LOOP.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(2)
        _FFMPEG_CONCURRENCY_BY_LOOP[loop] = semaphore
    return semaphore


class AudioConversionError(RuntimeError):
    """Browser audio could not be converted safely for transcription."""


class AudioConversionTimeoutError(AudioConversionError):
    """Audio conversion exceeded its configured deadline."""


@dataclass(frozen=True, slots=True)
class PreparedAudio:
    """Temporary provider input; never the application's canonical audio."""

    data: bytes
    filename: str
    content_type: str


ConversionRunner = Callable[[bytes], Awaitable[bytes]]


class FFmpegAudioConverter:
    """Convert one bounded in-memory input to mono 16 kHz FLAC."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_input_bytes: int,
        max_output_bytes: int,
        executable: str | None = None,
        runner: ConversionRunner | None = None,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 300:
            raise ValueError("Audio conversion timeout must be between 1 and 300 seconds")
        if max_input_bytes <= 0 or max_output_bytes <= 0:
            raise ValueError("Audio conversion byte limits must be positive")
        resolved_executable = executable if executable is not None else get_ffmpeg_exe()
        if not resolved_executable.strip():
            raise ValueError("Audio converter executable is not configured")
        self._timeout_seconds = timeout_seconds
        self._max_input_bytes = max_input_bytes
        self._max_output_bytes = max_output_bytes
        self._executable = resolved_executable.strip()
        self._runner = runner

    def command(self) -> tuple[str, ...]:
        """Return the fixed, shell-free conversion command for audit/tests."""
        return (
            self._executable,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-map_metadata",
            "-1",
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "flac",
            "-compression_level",
            "5",
            "-f",
            "flac",
            "pipe:1",
        )

    async def convert(self, audio_bytes: bytes) -> PreparedAudio:
        if not audio_bytes:
            raise AudioConversionError("Audio preprocessing failed")
        if len(audio_bytes) > self._max_input_bytes:
            raise AudioConversionError("Audio preprocessing failed")
        try:
            output = (
                await self._runner(audio_bytes) if self._runner is not None else await self._run_ffmpeg(audio_bytes)
            )
        except AudioConversionError:
            raise
        except TimeoutError:
            raise AudioConversionTimeoutError("Audio preprocessing timed out") from None
        except Exception:  # noqa: BLE001 - converter details and paths stay private
            raise AudioConversionError("Audio preprocessing failed") from None
        if not output or len(output) > self._max_output_bytes:
            raise AudioConversionError("Audio preprocessing failed")
        return PreparedAudio(
            data=output,
            filename="voice-transcription.flac",
            content_type="audio/flac",
        )

    async def _run_ffmpeg(self, audio_bytes: bytes) -> bytes:
        async with _ffmpeg_concurrency():
            return await asyncio.to_thread(self._run_ffmpeg_sync, audio_bytes)

    def _run_ffmpeg_sync(self, audio_bytes: bytes) -> bytes:
        """Run FFmpeg outside the event loop while retaining hard byte limits.

        Uvicorn's Windows selector loop does not implement async subprocesses.
        A worker thread keeps the command shell-free and works identically on
        Windows and Linux, while the watchdog and chunked reader retain the
        timeout/output bounds of the previous async implementation.
        """
        process: subprocess.Popen[bytes] | None = None
        writer: threading.Thread | None = None
        writer_failed = threading.Event()
        timed_out = threading.Event()

        try:
            process = subprocess.Popen(  # noqa: S603 - fixed audited command
                self.command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            if process.stdin is None or process.stdout is None:
                raise AudioConversionError("Audio preprocessing failed")

            def feed() -> None:
                try:
                    process.stdin.write(audio_bytes)
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    writer_failed.set()

            writer = threading.Thread(target=feed, name="voice-ffmpeg-input", daemon=True)
            writer.start()

            def expire() -> None:
                timed_out.set()
                if process is not None and process.poll() is None:
                    process.kill()

            timer = threading.Timer(self._timeout_seconds, expire)
            timer.daemon = True
            timer.start()
            try:
                output = self._read_limited_sync(process.stdout, process)
                return_code = process.wait()
            finally:
                timer.cancel()

            if timed_out.is_set():
                raise AudioConversionTimeoutError("Audio preprocessing timed out")
            if writer_failed.is_set() or return_code != 0:
                raise AudioConversionError("Audio preprocessing failed")
            return output
        except AudioConversionError:
            self._stop_process_sync(process)
            raise
        except Exception:  # noqa: BLE001 - FFmpeg details stay private
            self._stop_process_sync(process)
            raise AudioConversionError("Audio preprocessing failed") from None
        finally:
            if writer is not None:
                writer.join(timeout=1)

    def _read_limited_sync(
        self,
        stream,
        process: subprocess.Popen[bytes],
    ) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = self._max_output_bytes - total
            chunk = stream.read(min(_READ_CHUNK_BYTES, remaining + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > self._max_output_bytes:
                self._stop_process_sync(process)
                raise AudioConversionError("Audio preprocessing failed")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _stop_process_sync(process: subprocess.Popen[bytes] | None) -> None:
        if process is None or process.poll() is not None:
            return
        process.kill()
        process.wait()
