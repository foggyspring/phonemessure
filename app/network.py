"""LAN IP discovery."""
from __future__ import annotations

import socket
from typing import List


def primary_lan_ip() -> str:
    """Best-effort guess of the host's LAN IP.

    Opens a UDP socket toward a public address; the kernel picks the outbound
    interface without actually sending a packet, so this works offline too.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def all_lan_ips() -> List[str]:
    """All non-loopback IPv4 addresses bound on this host."""
    ips: List[str] = []
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    primary = primary_lan_ip()
    if primary not in ips and primary != "127.0.0.1":
        ips.insert(0, primary)
    return ips or ["127.0.0.1"]
