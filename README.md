# CoD4x Fake Server Simulator

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#)

Local testing tool that simulates multiple **CoD4x** game servers.

Useful for testing your own master server implementation (registration, heartbeats, getinfo/getstatus, rate-limits, bans, etc.).

> **Important**  
> This tool is intended **only** for local / private master servers that **you** control.  
> Do **not** point it at the public CoD4x master.

---

## Features

- Simulate **N** fake CoD4x servers at once
- **Clone** real server info (name, map, gametype, players, password…) from your local master
- Or use fully **custom** names / maps / player counts
- Optional **proxy list** (`proxy.txt`) associated with each fake server
- Responds correctly to `getinfo` and `getstatus`
- Sends periodic **heartbeats**
- Detects and **rejects** real join attempts (with logging)
- External **JSON config**
- **CLI arguments**
- **File logging** + live stats
- **Cache** of cloned servers (avoids re-querying the master every launch)
- Zero external dependencies (Python standard library only)

---

## Requirements

- Python **3.8+**
- A local CoD4x master server (e.g. `127.0.0.1:20810`)

No `pip install` needed.

---

## Quick start

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/cod4x-fake-server-simulator.git
cd cod4x-fake-server-simulator

# 2. Create your config
cp config.example.json config.json

# 3. (Optional) Add proxies
cp proxy.txt.example proxy.txt
# edit proxy.txt if needed

# 4. Edit config.json to match your local master
#    especially master.host / master.port

# 5. Run
python simulator.py
```

---

## Configuration

All settings live in `config.json` (see `config.example.json`).

### Main options

| Key | Description | Default |
|-----|-------------|---------|
| `master.host` | Local master IP | `127.0.0.1` |
| `master.port` | Local master port | `20810` |
| `servers.count` | Number of fake servers | `10` |
| `servers.base_port` | First UDP port used | `28960` |
| `servers.info_mode` | `"clone"` or `"custom"` | `"clone"` |
| `servers.force_map` | Force same map for all | `null` |
| `servers.force_gametype` | Force same gametype | `null` |
| `proxy.enabled` | Use `proxy.txt` | `false` |
| `connection.reject_message` | Message sent on join attempt | *(see example)* |
| `heartbeat.aggressive` | Faster heartbeats | `false` |
| `players.dynamic` | Players count changes over time | `true` |
| `cache.enabled` | Cache cloned servers | `true` |

### `info_mode`

- **`clone`** – Queries your local master, fetches real servers, and copies **all** their info (name, map, gametype, players, max clients, password…).
- **`custom`** – Uses the lists/values under the `custom` section of the config.

You can still force a map or gametype with `force_map` / `force_gametype` even in clone mode.

### Proxies

Create `proxy.txt` (one `ip:port` per line):

```text
127.0.0.1:8080
192.168.1.10:1080
# comments are allowed
```

Enable it in config:

```json
"proxy": {
  "enabled": true,
  "file": "proxy.txt"
}
```

Each fake server is assigned a proxy in round-robin order (used for logging / association). This does **not** perform real UDP game traffic proxying.

---

## Command-line usage

```bash
# Use a specific config file
python simulator.py -c myconfig.json

# Override number of servers
python simulator.py -n 20

# Force clone mode
python simulator.py --mode clone

# Force custom mode + 5 servers
python simulator.py --mode custom -n 5

# Enable proxies
python simulator.py --proxies

# Disable dynamic player count
python simulator.py --no-dynamic-players

# Faster heartbeats
python simulator.py --aggressive
```

```text
options:
  -h, --help            show this help message and exit
  -c, --config PATH     Path to config.json
  -n, --servers N       Number of fake servers
  --mode {clone,custom}
  --proxies             Force enable proxy.txt
  --no-proxies          Force disable proxies
  --no-dynamic-players
  --aggressive          Faster heartbeats
  --silent              Reduce console output
```

---

## What the simulator does

1. (Optional) Asks the local master for the current server list (`getservers`)
2. Queries those servers with `getinfo` and caches the result
3. Starts N UDP listeners (ports `base_port`, `base_port+1`, …)
4. Periodically sends heartbeats to the master
5. Answers `getinfo` / `getstatus` so the servers appear in the list
6. When someone tries to join → logs the attempt and sends a reject message

---

## Logs & stats

- File log: `logs/simulator.log` (configurable)
- Live stats every 60 seconds (configurable):

```text
STATS | uptime 00:05:12 | servers 10 | heartbeats 24 | getinfo 8 | getstatus 3 | connects 1
```

Connect attempts look like:

```text
[2026-10-23 14:32:11] CONNECT attempt #1 on server #03 (port 28962) from 192.168.1.42:54321 | proxy=127.0.0.1:8080 | ^1[EU] Sniper Only
```

---

## Project structure

```text
cod4x-fake-server-simulator/
├── simulator.py              # Main script
├── config.example.json       # Example configuration
├── proxy.txt.example         # Example proxy list
├── requirements.txt          # (empty – stdlib only)
├── .gitignore
├── README.md
├── LICENSE
├── logs/                     # Created at runtime
└── data/                     # Cache of cloned servers
```

---

## Disclaimer

This project is a **local testing utility**.

- It is not a DoS tool.
- It is not intended for the public CoD4x master.
- Using it against infrastructure you do not own may be against the terms of service of that infrastructure and possibly illegal.

Use it only against master servers that you operate yourself.

---

## License

MIT – see [LICENSE](LICENSE).
