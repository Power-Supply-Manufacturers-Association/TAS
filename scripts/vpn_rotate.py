#!/usr/bin/env python3
"""ProtonVPN WireGuard egress rotation for scrape campaigns (reusable).

WSL2 routes through the Windows host, so a WireGuard tunnel brought up INSIDE WSL
changes the egress IP of everything in WSL -- including a headless browser. That is
what beats Akamai-style IP-reputation blocks (proven on the Phoenix Contact campaign,
and needed again for TDK: product.tdk.com starts 403ing every request after a few
hundred graph calls from one IP).

Prerequisites already set up on this box:
  - configs in ~/proton/clean/p0..p6.conf, DNS/IPv6 stripped
  - NOPASSWD sudo for /usr/bin/wg-quick and /usr/bin/wg

Deliberately NOT automatic: a caller must ask for a rotation. Rotating on the first
403 would hide a bug in the request itself, so callers should first try a fresh
browser context (new connections, no cookies) and only rotate if the block persists.

  from vpn_rotate import Rotator
  rot = Rotator()
  print(rot.egress_ip())     # current public IP, or None if it cannot be read
  rot.rotate()               # down the current tunnel, up the next config
  rot.clear()                # back to the bare connection when the campaign ends
"""
import subprocess
import time
from pathlib import Path

CONF_DIR = Path.home() / "proton" / "clean"


class Rotator:
    def __init__(self, conf_dir=CONF_DIR):
        self.confs = sorted(Path(conf_dir).glob("*.conf"))
        if not self.confs:
            raise FileNotFoundError(f"no WireGuard configs in {conf_dir}")
        self.idx = -1

    # ---- state ----
    @staticmethod
    def _sudo(*args, timeout=60):
        return subprocess.run(["sudo", "-n", *args], capture_output=True,
                              text=True, timeout=timeout)

    def up_interfaces(self):
        r = self._sudo("/usr/bin/wg", "show", "interfaces")
        return r.stdout.split() if r.returncode == 0 else []

    def egress_ip(self, timeout=12):
        for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
            try:
                r = subprocess.run(["curl", "-s", "--max-time", str(timeout), url],
                                   capture_output=True, text=True, timeout=timeout + 5)
                ip = r.stdout.strip()
                if ip and len(ip) <= 45:
                    return ip
            except subprocess.SubprocessError:
                continue
        return None

    # ---- actions ----
    def clear(self):
        """Tear down every tunnel we may have brought up (back to the bare link)."""
        for iface in self.up_interfaces():
            self._sudo("/usr/bin/wg-quick", "down", iface, timeout=90)

    def rotate(self, skip=0):
        """Bring up the next config. Returns (name, egress_ip)."""
        self.clear()
        self.idx = (self.idx + 1 + skip) % len(self.confs)
        conf = self.confs[self.idx]
        r = self._sudo("/usr/bin/wg-quick", "up", str(conf), timeout=120)
        if r.returncode != 0:
            # a config that will not come up is a real problem, not something to
            # paper over by silently continuing on the previous IP
            raise RuntimeError(f"wg-quick up {conf.name} failed: "
                               f"{(r.stderr or r.stdout).strip()[:300]}")
        time.sleep(2)
        return conf.stem, self.egress_ip()


if __name__ == "__main__":
    import sys
    rot = Rotator()
    if len(sys.argv) > 1 and sys.argv[1] == "clear":
        rot.clear()
        print("cleared ->", rot.egress_ip())
    else:
        print("configs:", [c.stem for c in rot.confs])
        print("before:", rot.egress_ip(), "| up:", rot.up_interfaces())
        print("rotated to:", rot.rotate())
