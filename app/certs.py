"""Self-signed certificate generation.

Browsers (especially iOS Safari) require HTTPS for `getUserMedia`. We mint a
cert that lists every LAN IP and `localhost`, and re-mint when the set of IPs
changes so phones don't get TLS errors after the laptop hops networks.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import json
from pathlib import Path
from typing import Iterable, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _san_list(ips: Iterable[str]) -> list[x509.GeneralName]:
    names: list[x509.GeneralName] = [x509.DNSName("localhost")]
    for ip in ips:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            continue
    names.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
    return names


def _fingerprint(ips: Iterable[str]) -> str:
    return json.dumps(sorted(set(ips)))


def ensure_cert(cert_dir: Path, ips: Iterable[str]) -> Tuple[Path, Path]:
    """Return (cert_path, key_path), generating or refreshing as needed."""
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / "server.crt"
    key_path = cert_dir / "server.key"
    fp_path = cert_dir / "san.json"

    want = _fingerprint(ips)
    if cert_path.exists() and key_path.exists() and fp_path.exists():
        if fp_path.read_text(encoding="utf-8") == want:
            return cert_path, key_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "phonemessure local"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "phonemessure"),
    ])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(_san_list(ips)), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    fp_path.write_text(want, encoding="utf-8")
    return cert_path, key_path
