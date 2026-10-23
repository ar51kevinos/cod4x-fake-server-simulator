#!/usr/bin/env python3
"""
CoD4x Fake Server Simulator
===========================
Local testing tool for CoD4x master servers.

Simulates multiple CoD4x game servers that appear in a local master list.
Useful for testing master server behaviour, rate-limits, bans, cloning, etc.

WARNING: For local / private master testing only.
Do NOT use against the public CoD4x master.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import signal
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "config.json"
EXAMPLE_CONFIG = BASE_DIR / "config.example.json"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ServerTemplate:
    hostname: str = "Unknown"
    mapname: str = "mp_crash"
    gametype: str = "sd"
    clients: int = 0
    maxclients: int = 24
    humans: int = 0
    password: bool = False


@dataclass
class Stats:
    heartbeats_sent: int = 0
    getinfo_received: int = 0
    getstatus_received: int = 0
    connect_attempts: int = 0
    servers_active: int = 0
    start_time: float = field(default_factory=time.time)

    def uptime(self) -> str:
        seconds = int(time.time() - self.start_time)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

stats = Stats()
stats_lock = threading.Lock()
log_lock = threading.Lock()
running = True
log_file_handle = None


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_path: str):
    global log_file_handle
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    log_file_handle = open(path, "a", encoding="utf-8")
    log(f"=== Simulator started at {datetime.now().isoformat()} ===")


def log(msg: str, also_print: bool = True):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    with log_lock:
        if log_file_handle:
            log_file_handle.write(line + "\n")
            log_file_handle.flush()
    if also_print:
        print(line)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: Path) -> dict:
    if not path.exists():
        if EXAMPLE_CONFIG.exists():
            print(f"[!] {path.name} not found. Copying from config.example.json ...")
            path.write_text(EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            print(f"[!] No config found. Create {path.name} first.")
            sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_proxies(filename: str) -> List[Tuple[str, int]]:
    proxies = []
    path = BASE_DIR / filename
    if not path.is_file():
        log(f"Proxy file '{filename}' not found.")
        return proxies

    with open(path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                log(f"Proxy line {line_num} ignored (bad format): {line}")
                continue
            try:
                ip, port_str = line.rsplit(":", 1)
                proxies.append((ip.strip(), int(port_str)))
            except ValueError:
                log(f"Proxy line {line_num} ignored (bad port): {line}")

    return proxies


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def load_cache(cache_file: str, max_age_minutes: int) -> List[ServerTemplate]:
    path = BASE_DIR / cache_file
    if not path.is_file():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - data.get("timestamp", 0)
        if age > max_age_minutes * 60:
            log(f"Cache expired ({int(age/60)} min old).")
            return []

        templates = [ServerTemplate(**item) for item in data.get("servers", [])]
        log(f"Loaded {len(templates)} server(s) from cache ({int(age/60)} min old).")
        return templates
    except Exception as e:
        log(f"Failed to read cache: {e}")
        return []


def save_cache(cache_file: str, templates: List[ServerTemplate]):
    path = BASE_DIR / cache_file
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "timestamp": time.time(),
        "servers": [asdict(t) for t in templates],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Saved {len(templates)} server(s) to cache.")


# ---------------------------------------------------------------------------
# Master / real server queries
# ---------------------------------------------------------------------------

def query_master_for_servers(host: str, port: int, timeout: float = 4.0) -> List[str]:
    servers = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)

    queries = [
        b"\xff\xff\xff\xffgetservers 6 full empty",
        b"\xff\xff\xff\xffgetservers 6",
        b"\xff\xff\xff\xffgetserversExt 6 full empty",
    ]

    try:
        for q in queries:
            try:
                sock.sendto(q, (host, port))
                data, _ = sock.recvfrom(65535)
                if data.startswith(b"\xff\xff\xff\xff"):
                    data = data[4:]

                if b"\\" in data:
                    parts = data.split(b"\\")
                    for i in range(1, len(parts) - 1, 2):
                        try:
                            if len(parts[i]) == 4 and len(parts[i + 1]) >= 2:
                                ip = ".".join(str(b) for b in parts[i])
                                p = struct.unpack(">H", parts[i + 1][:2])[0]
                                servers.append(f"{ip}:{p}")
                        except Exception:
                            continue

                text = data.decode("latin-1", errors="ignore")
                found = re.findall(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)", text)
                servers.extend(found)
            except Exception:
                continue
    finally:
        sock.close()

    return list(set(servers))


def query_server_info(ip: str, port: int, timeout: float = 2.0) -> Optional[ServerTemplate]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        challenge = str(random.randint(10000, 999999))
        sock.sendto(f"\xff\xff\xff\xffgetinfo {challenge}".encode(), (ip, port))
        data, _ = sock.recvfrom(4096)

        if not data.startswith(b"\xff\xff\xff\xff"):
            return None

        text = data[4:].decode("latin-1", errors="ignore")
        if not text.startswith("infoResponse"):
            return None

        info: Dict[str, str] = {}
        parts = text.split("\\")
        for i in range(1, len(parts) - 1, 2):
            info[parts[i]] = parts[i + 1] if i + 1 < len(parts) else ""

        return ServerTemplate(
            hostname=info.get("hostname", "Unknown"),
            mapname=info.get("mapname", "mp_crash"),
            gametype=info.get("gametype", "sd"),
            clients=int(info.get("clients", 0) or 0),
            maxclients=int(info.get("sv_maxclients", 24) or 24),
            humans=int(info.get("g_humanplayers", 0) or 0),
            password=info.get("pswrd", "0") == "1",
        )
    except Exception:
        return None
    finally:
        sock.close()


def collect_real_servers(cfg: dict) -> List[ServerTemplate]:
    cache_cfg = cfg.get("cache", {})
    if cache_cfg.get("enabled", True):
        cached = load_cache(
            cache_cfg.get("file", "data/real_servers_cache.json"),
            cache_cfg.get("max_age_minutes", 30),
        )
        if cached:
            return cached

    master = cfg["master"]
    log("Querying local master for real servers...")
    addresses = query_master_for_servers(master["host"], master["port"])

    if not addresses:
        log("No servers found on the master.")
        return []

    log(f"{len(addresses)} address(es) found. Fetching details...")
    real: List[ServerTemplate] = []
    for addr in addresses[:40]:
        try:
            ip, port_str = addr.split(":")
            info = query_server_info(ip, int(port_str))
            if info and info.hostname:
                real.append(info)
                log(f"  + {info.hostname} | {info.mapname} | {info.clients}/{info.maxclients}")
        except Exception:
            continue

    log(f"{len(real)} real server(s) collected.")
    if real and cache_cfg.get("enabled", True):
        save_cache(cache_cfg.get("file", "data/real_servers_cache.json"), real)
    return real


# ---------------------------------------------------------------------------
# Fake server
# ---------------------------------------------------------------------------

class FakeServer:
    def __init__(
        self,
        index: int,
        port: int,
        cfg: dict,
        template: Optional[ServerTemplate] = None,
        proxy: Optional[Tuple[str, int]] = None,
    ):
        self.index = index
        self.port = port
        self.cfg = cfg
        self.proxy = proxy
        self.running = True
        self.message_id = 1
        self.connect_attempts = 0

        servers_cfg = cfg["servers"]
        custom = cfg.get("custom", {})
        force_map = servers_cfg.get("force_map")
        force_gt = servers_cfg.get("force_gametype")

        if servers_cfg.get("info_mode") == "clone" and template:
            self.name = template.hostname
            self.map = force_map or template.mapname
            self.gametype = force_gt or template.gametype
            self.max_clients = template.maxclients
            self.players = template.clients
            self.humans = template.humans
            self.password = template.password
        else:
            names = custom.get("names") or [f"^1Test Server #{index+1}"]
            self.name = names[index % len(names)]
            self.map = force_map or custom.get("map") or random.choice(
                ["mp_crash", "mp_strike", "mp_crossfire", "mp_backlot", "mp_vacant"]
            )
            self.gametype = force_gt or custom.get("gametype") or random.choice(
                ["sd", "dm", "war", "dom"]
            )
            self.max_clients = custom.get("max_clients", 24)
            pmin = custom.get("players_min", 0)
            pmax = min(custom.get("players_max", 16), self.max_clients)
            self.players = random.randint(pmin, pmax)
            self.humans = max(0, self.players - random.randint(0, 2))
            self.password = custom.get("password", False)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind(("0.0.0.0", self.port))
        except OSError as e:
            log(f"Port {self.port} already in use → {e}")
            self.running = False
            return

        proxy_str = f"{self.proxy[0]}:{self.proxy[1]}" if self.proxy else "-"
        log(f"Server #{index+1:02d} started | port {self.port} | proxy {proxy_str}")
        log(f"         {self.name} | {self.map}/{self.gametype} | {self.players}/{self.max_clients}")

        with stats_lock:
            stats.servers_active += 1

    # ----- packet builders -------------------------------------------------

    def _info_string(self, **kwargs) -> str:
        return "\\".join(f"{k}\\{v}" for k, v in kwargs.items())

    def build_info_response(self, challenge: str) -> bytes:
        info = self._info_string(
            challenge=challenge,
            protocol="6",
            hostname=self.name,
            mapname=self.map,
            clients=str(self.players),
            g_humanplayers=str(self.humans),
            sv_maxclients=str(self.max_clients),
            gametype=self.gametype,
            pure="1",
            build="1234",
            shortversion="x21",
            pswrd="1" if self.password else "0",
            mod="0",
            hw="1",
        )
        return b"\xff\xff\xff\xff" + f"infoResponse\n{info}".encode("latin-1", errors="replace")

    def build_status_response(self, challenge: str) -> bytes:
        info = self._info_string(
            challenge=challenge,
            protocol="6",
            hostname=self.name,
            mapname=self.map,
            clients=str(self.players),
            g_humanplayers=str(self.humans),
            sv_maxclients=str(self.max_clients),
            gametype=self.gametype,
            pure="1",
            shortversion="x21",
            pswrd="1" if self.password else "0",
        )
        players = [
            f"{random.randint(0, 45)} {random.randint(12, 130)} \"Player{random.randint(1, 999)}\""
            for _ in range(self.players)
        ]
        body = f"statusResponse\n{info}\n" + "\n".join(players) + "\n"
        return b"\xff\xff\xff\xff" + body.encode("latin-1", errors="replace")

    def build_heartbeat(self) -> bytes:
        data = b"\xff\xff\xff\xffheartbeat COD4x\x00"
        data += struct.pack("<H", self.port)
        data += struct.pack("<I", self.message_id)
        token = self.cfg.get("auth_token") or ""
        if token:
            data += struct.pack("<I", 2)
            data += token.encode("ascii")[:8].ljust(8, b"\x00")
            data += b"\xff" * 64 + b"\x00"
        return data

    def reject_connect(self, addr):
        msg = self.cfg.get("connection", {}).get(
            "reject_message", "This is a test server (fake). Connection refused."
        )
        for prefix in ("error", "print", "disconnect"):
            try:
                packet = f"\xff\xff\xff\xff{prefix}\n{msg}".encode("latin-1", errors="replace")
                self.sock.sendto(packet, addr)
            except Exception:
                pass

    # ----- packet handler --------------------------------------------------

    def handle_packet(self, data: bytes, addr):
        if not data.startswith(b"\xff\xff\xff\xff"):
            return

        try:
            text = data[4:].decode("latin-1", errors="ignore").strip()
        except Exception:
            return

        if text.startswith("getinfo"):
            ch = text.split()[1] if len(text.split()) > 1 else ""
            self.sock.sendto(self.build_info_response(ch), addr)
            with stats_lock:
                stats.getinfo_received += 1
            return

        if text.startswith("getstatus"):
            ch = text.split()[1] if len(text.split()) > 1 else ""
            self.sock.sendto(self.build_status_response(ch), addr)
            with stats_lock:
                stats.getstatus_received += 1
            return

        if text.startswith("connect") or text.startswith("getchallenge"):
            self.connect_attempts += 1
            with stats_lock:
                stats.connect_attempts += 1

            if self.cfg.get("connection", {}).get("log_attempts", True):
                proxy_str = f"{self.proxy[0]}:{self.proxy[1]}" if self.proxy else "-"
                log(
                    f"CONNECT attempt #{self.connect_attempts} on server #{self.index+1:02d} "
                    f"(port {self.port}) from {addr[0]}:{addr[1]} | proxy={proxy_str} | {self.name}"
                )

            self.reject_connect(addr)
            return

    # ----- threads ---------------------------------------------------------

    def listener(self):
        while self.running and running:
            try:
                self.sock.settimeout(1.0)
                data, addr = self.sock.recvfrom(4096)
                self.handle_packet(data, addr)
            except socket.timeout:
                continue
            except Exception:
                break

    def heartbeat_loop(self):
        hb = self.cfg.get("heartbeat", {})
        interval = hb.get("interval", 25)
        if hb.get("aggressive"):
            interval = 8
        jitter = hb.get("jitter", 6)
        master = (self.cfg["master"]["host"], self.cfg["master"]["port"])

        while self.running and running:
            try:
                self.sock.sendto(self.build_heartbeat(), master)
                self.message_id += 1
                with stats_lock:
                    stats.heartbeats_sent += 1
            except Exception:
                pass
            delay = interval + random.uniform(-jitter, jitter)
            time.sleep(max(3.0, delay))

    def player_updater(self):
        players_cfg = self.cfg.get("players", {})
        if not players_cfg.get("dynamic", True):
            return
        interval = players_cfg.get("change_interval", 40)

        while self.running and running:
            time.sleep(interval + random.randint(0, 20))
            change = random.randint(-3, 4)
            self.players = max(0, min(self.max_clients, self.players + change))
            self.humans = max(0, self.players - random.randint(0, 2))

    def start(self):
        if not self.running:
            return
        threading.Thread(target=self.listener, daemon=True).start()
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        threading.Thread(target=self.player_updater, daemon=True).start()

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except Exception:
            pass
        with stats_lock:
            stats.servers_active = max(0, stats.servers_active - 1)


# ---------------------------------------------------------------------------
# Stats printer
# ---------------------------------------------------------------------------

def stats_loop(interval: int):
    while running:
        time.sleep(interval)
        with stats_lock:
            log(
                f"STATS | uptime {stats.uptime()} | "
                f"servers {stats.servers_active} | "
                f"heartbeats {stats.heartbeats_sent} | "
                f"getinfo {stats.getinfo_received} | "
                f"getstatus {stats.getstatus_received} | "
                f"connects {stats.connect_attempts}"
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="CoD4x Fake Server Simulator (local master testing only)"
    )
    parser.add_argument(
        "-c", "--config",
        default=str(DEFAULT_CONFIG),
        help="Path to config.json (default: ./config.json)",
    )
    parser.add_argument(
        "-n", "--servers",
        type=int,
        help="Override number of fake servers",
    )
    parser.add_argument(
        "--mode",
        choices=["clone", "custom"],
        help="Override info mode (clone or custom)",
    )
    parser.add_argument(
        "--proxies",
        action="store_true",
        help="Force enable proxy.txt",
    )
    parser.add_argument(
        "--no-proxies",
        action="store_true",
        help="Force disable proxies",
    )
    parser.add_argument(
        "--no-dynamic-players",
        action="store_true",
        help="Disable dynamic player count",
    )
    parser.add_argument(
        "--aggressive",
        action="store_true",
        help="Faster heartbeats",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Reduce console output (still logs to file)",
    )
    return parser.parse_args()


def main():
    global running, log_file_handle

    args = parse_args()
    cfg = load_config(Path(args.config))

    # CLI overrides
    if args.servers is not None:
        cfg["servers"]["count"] = args.servers
    if args.mode:
        cfg["servers"]["info_mode"] = args.mode
    if args.proxies:
        cfg.setdefault("proxy", {})["enabled"] = True
    if args.no_proxies:
        cfg.setdefault("proxy", {})["enabled"] = False
    if args.no_dynamic_players:
        cfg.setdefault("players", {})["dynamic"] = False
    if args.aggressive:
        cfg.setdefault("heartbeat", {})["aggressive"] = True
        cfg["heartbeat"]["interval"] = 8

    # Logging
    log_path = cfg.get("logging", {}).get("file", "logs/simulator.log")
    setup_logging(log_path)

    print("=" * 70)
    print("  CoD4x Fake Server Simulator")
    print("  Local master testing only – do not use on public masters")
    print("=" * 70)
    print(f"  Master     : {cfg['master']['host']}:{cfg['master']['port']}")
    print(f"  Servers    : {cfg['servers']['count']}")
    print(f"  Info mode  : {cfg['servers']['info_mode']}")
    print(f"  Proxies    : {'enabled' if cfg.get('proxy', {}).get('enabled') else 'disabled'}")
    print(f"  Log file   : {log_path}")
    print("=" * 70)
    print()

    # Proxies
    proxies: List[Tuple[str, int]] = []
    if cfg.get("proxy", {}).get("enabled"):
        proxies = load_proxies(cfg["proxy"].get("file", "proxy.txt"))
        if proxies:
            log(f"Loaded {len(proxies)} proxy/proxies.")
        else:
            log("No valid proxies loaded.")

    # Real servers (for clone mode)
    templates: List[ServerTemplate] = []
    if cfg["servers"].get("info_mode") == "clone":
        templates = collect_real_servers(cfg)
        if not templates:
            log("No real servers available – falling back to custom names.")

    # Start fake servers
    servers: List[FakeServer] = []
    count = cfg["servers"]["count"]
    base_port = cfg["servers"]["base_port"]

    for i in range(count):
        if not running:
            break
        port = base_port + i
        template = templates[i % len(templates)] if templates else None
        proxy = proxies[i % len(proxies)] if proxies else None
        srv = FakeServer(i, port, cfg, template, proxy)
        if srv.running:
            srv.start()
            servers.append(srv)
        time.sleep(0.03)

    log(f"{len(servers)} fake server(s) running.")

    # Stats thread
    stats_interval = cfg.get("logging", {}).get("console_stats_interval", 60)
    if stats_interval > 0:
        threading.Thread(target=stats_loop, args=(stats_interval,), daemon=True).start()

    # Graceful shutdown
    def shutdown(sig=None, frame=None):
        global running
        if not running:
            return
        running = False
        print("\n[*] Shutting down...")
        for s in servers:
            s.stop()
        log("Simulator stopped.")
        if log_file_handle:
            log_file_handle.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("[*] Running. Press Ctrl+C to stop.\n")
    try:
        while running:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
