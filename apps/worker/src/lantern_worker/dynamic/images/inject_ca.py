"""Trust the sandbox CA inside the application image. Runs once, at image build time.

Setting SSL_CERT_FILE and friends is not enough: certifi ships its own bundle, and some SDKs
ship theirs (stripe-python, for example). Append the CA to every CA bundle found on the
Python path and to the system bundle.

    python inject_ca.py /lantern/run-ca.crt
"""

import os
import sys
from pathlib import Path

NAMES = {"cacert.pem", "ca-certificates.crt", "ca-bundle.crt", "cacerts.pem", "cacerts.txt"}
SYSTEM = [Path("/etc/ssl/certs/ca-certificates.crt")]


def bundles() -> list[Path]:
    found = [p for p in SYSTEM if p.exists()]
    roots = {Path(p) for p in sys.path if p and Path(p).is_dir() and "site-packages" in p}
    for root in roots:
        for dirpath, _, files in os.walk(root):
            for name in files:
                if name in NAMES:
                    path = Path(dirpath) / name
                    if path.read_bytes().count(b"BEGIN CERTIFICATE") >= 10:
                        found.append(path)
    return found


def main() -> int:
    ca = Path(sys.argv[1]).read_bytes()
    for path in bundles():
        with path.open("ab") as fh:
            fh.write(b"\n" + ca)
        print(f"lantern: trusted sandbox CA in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
