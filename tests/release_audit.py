"""Review the staged/tracked allowlist and common secret patterns before publishing."""
import ipaddress
from pathlib import Path
import re
import subprocess

ROOT_FILES = {"README.md", "LICENSE", "NOTICE.md", "AGENTS.md", "SECURITY.md", "pyproject.toml", ".gitignore"}
ROOT_DIRS = {"spark", "tests", "docs", "skills", ".github"}
PATTERNS = [r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", r"gh[pousr]_[A-Za-z0-9]{30,}",
            r"AKIA[0-9A-Z]{16}", r"AIza[0-9A-Za-z_-]{35}", r'"type"\s*:\s*"service_account"',
            r"https?://[^\s]+/sub/[A-Za-z0-9_-]{32,}"]


def main():
    files = subprocess.check_output(["git", "ls-files", "-z"], text=True).split("\0")
    errors = []
    for filename in filter(None, files):
        p = Path(filename)
        if filename not in ROOT_FILES and p.parts[0] not in ROOT_DIRS:
            errors.append(f"Non-allowlisted path: {filename}")
        if p.is_symlink() or p.suffix in (".pem", ".key", ".pyc") or "__pycache__" in p.parts:
            errors.append(f"Private/binary/symlink path: {filename}")
            continue
        text = p.read_text()
        for pattern in PATTERNS:
            if re.search(pattern, text):
                errors.append(f"Potential secret: {filename} (content suppressed)")
        for match in re.findall(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", text):
            try:
                ip = ipaddress.ip_address(match)
            except ValueError:
                continue
            if ip.is_global:
                errors.append(f"Unexpected public IP in {filename}")
    for required in ROOT_FILES:
        if required not in files:
            errors.append(f"Missing required release file: {required}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"PASS release allowlist and secret-pattern scan: {len(files)-1} files (not a substitute for manual review)")


if __name__ == "__main__":
    main()
