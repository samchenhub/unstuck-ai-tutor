import functools
import shutil
import subprocess
from pathlib import Path


def _bundled_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # no wheel for this platform, or the binary is missing
        return None


@functools.cache
def ffmpeg_exe() -> str:
    """The ffmpeg to run: the one bundled with the app (imageio-ffmpeg, so nobody has to install it, and the same
    build with Opus/MP3/HTTPS everywhere), else one on the PATH."""
    return _bundled_ffmpeg() or shutil.which("ffmpeg") or "ffmpeg"


def _header_args(video: Path | str, headers: dict[str, str] | None) -> list[str]:
    """ffmpeg input options for HTTP headers; they must come before `-i` and only apply to http(s) inputs
    (ffmpeg rejects them for a local file)."""
    if not headers or not str(video).startswith(("http://", "https://")):
        return []
    args: list[str] = []
    if "User-Agent" in headers:
        args += ["-user_agent", headers["User-Agent"]]
    rest = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if k != "User-Agent")
    if rest:
        args += ["-headers", rest]
    return args


def extract_frame(video: Path | str, t: float, out_path: Path, headers: dict[str, str] | None = None) -> Path:
    """Grab the frame at t seconds as a JPEG using ffmpeg; `video` may be a local file or an http(s) URL."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", *_header_args(video, headers)]
    cmd += ["-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(out_path)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"ffmpeg failed at {t:.1f}s: {stderr}") from exc
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg produced no frame at {t:.1f}s")
    return out_path
