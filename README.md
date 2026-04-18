# Homelab Mission Control

A self-hosted dashboard for monitoring and managing a Proxmox homelab. Built with FastAPI and vanilla JS — no heavy frameworks, runs as a single Python file.

![Python](https://img.shields.io/badge/python-3.10+-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green) ![License](https://img.shields.io/badge/license-MIT-blue)

## Features

- **Live Proxmox stats** — CPU, RAM, disk usage with rolling 10-minute charts
- **Game server status** — Icarus dedicated server player count via Steam A2S query (UDP)
- **Download stats** — qBittorrent real-time download/upload speed graphs
- **Service health** — HTTP checks for Jellyfin, Jellyseerr, Sonarr, Radarr, qBittorrent, Homepage
- **Kanban task board** — Todo / In Progress / Done with per-agent assignment, persistent via SQLite
- **Dark UI** — Fully embedded HTML/CSS/JS frontend, no build step required

## Screenshots

> Dashboard shows live infrastructure stats, game server player count, service health pills, and a task board — all in one page.

## Quick Start

### Requirements
- Python 3.10+
- Proxmox VE with API token (read-only is fine)
- qBittorrent with Web UI enabled

### Install

```bash
pip install fastapi uvicorn pydantic
```

### Configure

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

### Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Visit `http://your-server:8000`

### Run as a systemd service

```ini
[Unit]
Description=Homelab Mission Control Dashboard
After=network.target

[Service]
WorkingDirectory=/opt/dashboard
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `PVE_HOST` | Proxmox host IP | `192.168.0.10` |
| `PVE_TOKEN` | Proxmox API token | `PVEAPIToken=root@pam!dashboard=abc123` |
| `QB_HOST` | qBittorrent host IP | `192.168.0.26` |
| `QB_PORT` | qBittorrent Web UI port | `8080` |
| `QB_AUTH` | qBittorrent credentials | `username=admin&password=yourpassword` |
| `ICARUS_HOST` | Icarus server IP | `192.168.0.28` |

## Creating a Proxmox API Token

1. In Proxmox web UI: Datacenter → Permissions → API Tokens
2. Create token for `root@pam` (or any user with read access)
3. Uncheck "Privilege Separation" if you want full read access
4. Copy the token string into your `.env`

## Tech Stack

- **Backend:** Python, FastAPI, SQLite
- **Frontend:** Vanilla JS, Chart.js, embedded in Python (no separate build)
- **Monitoring:** UDP Steam A2S queries, HTTP checks, Proxmox REST API, qBittorrent API

## Customising the Task Board

The board has 4 built-in "agents" (assignees) — edit the `AGENTS` list in `main.py` to match your setup. Tasks persist in a local SQLite database.

## Licence

MIT
