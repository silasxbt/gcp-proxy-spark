"""Download only audited release artifacts; never execute a remote script."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import tempfile


def fetch(tool, directory):
    lock = json.loads(Path(__file__).with_name("versions.json").read_text())[tool]
    system = platform.system().lower()
    arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine())
    if system not in ("linux", "darwin") or not arch:
        raise ValueError("Unsupported platform")
    variant = arch + ("-compatible" if tool == "mihomo" and system == "linux" and arch == "amd64" else "")
    name = (f'sing-box-{lock["version"]}-{system}-{variant}.tar.gz' if tool == "singbox"
            else f'mihomo-{system}-{variant}-v{lock["version"]}.gz')
    asset = next((a for a in lock["assets"] if a["name"] == name), None)
    if not asset:
        raise ValueError("No pinned artifact for this platform")
    destination = Path(directory) / ("sing-box" if tool == "singbox" else "mihomo")
    # curl detects truncated Content-Length transfers and can retry them. Hash still
    # decides trust; retries never relax validation or execute partially fetched bytes.
    with tempfile.TemporaryDirectory(prefix="spark-download-") as temporary:
        archive_path = Path(temporary) / "artifact"
        subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location", "--proto", "=https",
                        "--proto-redir", "=https", "--retry", "3", "--retry-all-errors", "--max-time", "120",
                        "--max-filesize", "150000000", "--output", str(archive_path), asset["browser_download_url"]],
                       check=True, capture_output=True, timeout=520)
        data = archive_path.read_bytes()
    if len(data) > 150_000_000 or "sha256:" + hashlib.sha256(data).hexdigest() != asset["digest"]:
        raise ValueError("Release SHA256 verification failed")
    if tool == "singbox":
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members = [m for m in archive.getmembers() if m.isfile() and m.name.endswith("/sing-box")]
            if len(members) != 1:
                raise ValueError("Unexpected archive layout")
            binary = archive.extractfile(members[0]).read()
    else:
        binary = gzip.decompress(data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(binary)
            os.fchmod(stream.fileno(), 0o755)
        os.replace(temp, destination)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return destination
