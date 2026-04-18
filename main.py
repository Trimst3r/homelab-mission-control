#!/usr/bin/env python3
"""
Homelab Agent Dashboard — mission control for Tim's AI team.
FastAPI backend + embedded HTML frontend with live graphs.
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Literal
import sqlite3
import time
import os
import socket
import urllib.request
import urllib.error
import json
import ssl
from datetime import datetime
from collections import deque

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_PATH = "/opt/dashboard/tasks.db"
PVE_HOST = "192.168.0.10"
PVE_TOKEN = os.environ.get("PVE_TOKEN", "PVEAPIToken=root@pam!dashboard=YOUR_TOKEN_HERE")
QB_HOST = "192.168.0.26"
QB_PORT = 8080

# Rolling history buffers (last 60 points = 10 min at 10s intervals)
dl_history = deque(maxlen=60)
ul_history = deque(maxlen=60)
cpu_history = deque(maxlen=60)
ram_history = deque(maxlen=60)
time_history = deque(maxlen=60)

AGENTS = [
    {"id": "elijah", "name": "Elijah", "role": "CEO", "description": "Triage, delegation, Telegram comms. The one you talk to.", "emoji": "🧠", "color": "#6366f1"},
    {"id": "ben",    "name": "Ben",    "role": "Icarus Server", "description": "Game server status, restarts, player counts, updates, Discord bot.", "emoji": "🎮", "color": "#10b981"},
    {"id": "pete",   "name": "Pete",   "role": "Arr Stack", "description": "Sonarr, Radarr, Prowlarr, qBittorrent, Jellyfin, Jellyseerr.", "emoji": "🎬", "color": "#f59e0b"},
    {"id": "brian",  "name": "Brian",  "role": "Infrastructure", "description": "Proxmox, LXC containers, network, storage, backups.", "emoji": "🔧", "color": "#ef4444"},
]

SERVICES = [
    {"name": "Homepage",    "url": "http://192.168.0.26:3000"},
    {"name": "Jellyfin",    "url": "http://192.168.0.26:8096"},
    {"name": "qBittorrent", "url": "http://192.168.0.26:8080"},
    {"name": "Jellyseerr",  "url": "http://192.168.0.26:5055"},
    {"name": "Prowlarr",    "url": "http://192.168.0.26:9696"},
    {"name": "Sonarr",      "url": "http://192.168.0.26:8989"},
    {"name": "Radarr",      "url": "http://192.168.0.26:7878"},
]


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'todo',
                agent TEXT DEFAULT 'elijah',
                created_at INTEGER DEFAULT (strftime('%s','now')),
                updated_at INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        if count == 0:
            seed_tasks = [
                ("Fix Godless S01", "Find a seeded torrent and force-grab via Sonarr", "todo", "pete"),
                ("Add more Prowlarr indexers", "Improve indexer coverage for better search results", "todo", "pete"),
                ("Set up Wake on LAN", "Tim needs to enable WoL in BIOS and Windows first", "todo", "brian"),
                ("ZFS RAID storage upgrade", "Research and advise on HDDs + RAM for Proxmox", "todo", "brian"),
                ("Set up OBS for streaming", "Switch from Meld to OBS. Configure NVENC on 4070, scenes, alerts.", "todo", "elijah"),
                ("Twitch chatbot", "Wire up bits/cheers responses, commands, shoutouts. Need OAuth token.", "todo", "elijah"),
                ("Monitor Icarus 4am restart", "Verify nightly restart completes cleanly", "in_progress", "ben"),
                ("Deploy agent dashboard", "Build and host mission control web app on CT 111", "done", "elijah"),
                ("Fix HK S01 download", "Season 1 was not monitored + wrong quality profile", "done", "pete"),
                ("Deploy Discord Icarus bot", "HomeServerBot with status, tips, Steam news", "done", "ben"),
                ("Set up Telegram monitoring", "Alert on service downtime via monitor.py cron", "done", "elijah"),
                ("Add LimeTorrents + TPB indexers", "Expanded Prowlarr with 2 working new indexers", "done", "pete"),
                ("Enable Jellyfin GPU transcoding", "GTX 1070 NVENC — devices passed to container", "done", "brian"),
            ]
            conn.executemany("INSERT INTO tasks (title, description, status, agent) VALUES (?,?,?,?)", seed_tasks)
        conn.commit()


# ── Data fetchers ─────────────────────────────────────────────────────────────

def check_service(url, timeout=4):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dashboard/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except:
        return False


def check_icarus(host="192.168.0.28", port=27015, timeout=4):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        req = b'\xff\xff\xff\xffTSource Engine Query\x00'
        sock.sendto(req, (host, port))
        data, _ = sock.recvfrom(4096)
        if len(data) >= 9 and data[4] == 0x41:
            req = b'\xff\xff\xff\xffTSource Engine Query\x00' + data[5:9]
            sock.sendto(req, (host, port))
            data, _ = sock.recvfrom(4096)
        sock.close()
        if len(data) > 5:
            # Parse player count
            try:
                offset = 5
                def read_str(d, o):
                    end = d.index(b'\x00', o)
                    return d[o:end].decode('utf-8', errors='replace'), end + 1
                _, offset = read_str(data, offset)
                _, offset = read_str(data, offset)
                _, offset = read_str(data, offset)
                _, offset = read_str(data, offset)
                offset += 2
                players = data[offset]
                max_players = data[offset + 1]
                return {"online": True, "players": players, "max_players": max_players}
            except:
                return {"online": True, "players": 0, "max_players": 0}
    except:
        pass
    return {"online": False, "players": 0, "max_players": 0}


def get_proxmox_stats():
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(
            f"https://{PVE_HOST}:8006/api2/json/nodes/proxmox/status",
            headers={"Authorization": PVE_TOKEN}
        )
        with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
            d = json.loads(r.read().decode())["data"]
            cpu_pct = round(d.get("cpu", 0) * 100, 1)
            mem = d.get("memory", {})
            ram_pct = round(mem.get("used", 0) / mem.get("total", 1) * 100, 1)
            ram_used_gb = round(mem.get("used", 0) / 1024**3, 1)
            ram_total_gb = round(mem.get("total", 0) / 1024**3, 1)
            disk = d.get("rootfs", {})
            disk_pct = round(disk.get("used", 0) / disk.get("total", 1) * 100, 1)
            disk_used_gb = round(disk.get("used", 0) / 1024**3, 0)
            disk_total_gb = round(disk.get("total", 0) / 1024**3, 0)
            return {
                "cpu": cpu_pct,
                "ram_pct": ram_pct,
                "ram_used": ram_used_gb,
                "ram_total": ram_total_gb,
                "disk_pct": disk_pct,
                "disk_used": int(disk_used_gb),
                "disk_total": int(disk_total_gb),
            }
    except Exception as e:
        return {"error": str(e)}


def get_qbit_transfer():
    try:
        # Login
        login_req = urllib.request.Request(
            f"http://{QB_HOST}:{QB_PORT}/api/v2/auth/login",
            data=os.environ.get("QB_AUTH", "username=admin&password=changeme").encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        with opener.open(login_req, timeout=5) as r:
            pass
        info_req = urllib.request.Request(f"http://{QB_HOST}:{QB_PORT}/api/v2/transfer/info")
        with opener.open(info_req, timeout=5) as r:
            d = json.loads(r.read().decode())
            return {
                "dl_speed": round(d.get("dl_info_speed", 0) / 1024, 1),
                "ul_speed": round(d.get("up_info_speed", 0) / 1024, 1),
                "dl_total": round(d.get("dl_info_data", 0) / 1024**3, 2),
                "ul_total": round(d.get("up_info_data", 0) / 1024**3, 2),
                "connection_status": d.get("connection_status", "unknown"),
            }
    except Exception as e:
        return {"error": str(e), "dl_speed": 0, "ul_speed": 0}


# ── Models ────────────────────────────────────────────────────────────────────

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    status: Optional[Literal["todo", "in_progress", "done"]] = "todo"
    agent: Optional[str] = "elijah"


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[Literal["todo", "in_progress", "done"]] = None
    agent: Optional[str] = None


# ── API routes ────────────────────────────────────────────────────────────────

@app.get("/api/agents")
def get_agents():
    return AGENTS


@app.get("/api/tasks")
def get_tasks():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]


@app.post("/api/tasks")
def create_task(task: TaskCreate):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (title, description, status, agent) VALUES (?,?,?,?)",
            (task.title, task.description, task.status, task.agent)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, task: TaskUpdate):
    with get_db() as conn:
        existing = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Task not found")
        fields = {}
        if task.title is not None: fields["title"] = task.title
        if task.description is not None: fields["description"] = task.description
        if task.status is not None: fields["status"] = task.status
        if task.agent is not None: fields["agent"] = task.agent
        if fields:
            fields["updated_at"] = int(time.time())
            set_clause = ", ".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE tasks SET {set_clause} WHERE id=?", (*fields.values(), task_id))
            conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row)


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        conn.commit()
    return {"ok": True}


@app.get("/api/services")
def get_services():
    results = []
    icarus = check_icarus()
    results.append({"name": "Icarus Server", "url": "udp://192.168.0.28:27015", "up": icarus["online"],
                     "detail": f"{icarus['players']}/{icarus['max_players']} players" if icarus["online"] else "Offline"})
    for svc in SERVICES:
        up = check_service(svc["url"])
        results.append({"name": svc["name"], "url": svc["url"], "up": up, "detail": ""})
    return results


@app.get("/api/stats/proxmox")
def proxmox_stats():
    stats = get_proxmox_stats()
    ts = datetime.now().strftime("%H:%M:%S")
    if "error" not in stats:
        cpu_history.append(stats["cpu"])
        ram_history.append(stats["ram_pct"])
        time_history.append(ts)
    return {**stats, "history": {"cpu": list(cpu_history), "ram": list(ram_history), "timestamps": list(time_history)}}


@app.get("/api/stats/qbit")
def qbit_stats():
    stats = get_qbit_transfer()
    ts = datetime.now().strftime("%H:%M:%S")
    if "error" not in stats:
        dl_history.append(stats["dl_speed"])
        ul_history.append(stats["ul_speed"])
    return {**stats, "history": {"dl": list(dl_history), "ul": list(ul_history), "timestamps": list(time_history)}}


@app.get("/api/stats/icarus")
def icarus_stats():
    return check_icarus()


# ── Frontend ──────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mission Control — Tim's Homelab</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0f0f13;
    --surface: #1a1a24;
    --surface2: #22222f;
    --border: #2e2e40;
    --text: #e2e2f0;
    --muted: #7070a0;
    --accent: #6366f1;
    --green: #10b981;
    --amber: #f59e0b;
    --red: #ef4444;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; min-height: 100vh; }

  header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 16px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky; top: 0; z-index: 100;
  }
  header h1 { font-size: 1.2rem; font-weight: 700; }
  header h1 span { color: var(--accent); }
  #clock { color: var(--muted); font-size: 0.85rem; font-variant-numeric: tabular-nums; }

  main { max-width: 1400px; margin: 0 auto; padding: 24px 20px; }

  .section-heading {
    font-size: 0.68rem; font-weight: 700; letter-spacing: 2px;
    text-transform: uppercase; color: var(--muted); margin-bottom: 12px;
    margin-top: 28px;
  }
  .section-heading:first-child { margin-top: 0; }

  /* ── Agent cards ── */
  .agents-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }
  .agent-card {
    background: var(--surface); border: 1px solid var(--border);
    border-top: 3px solid var(--agent-color); border-radius: 10px; padding: 16px 18px;
  }
  .agent-header { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
  .agent-emoji { font-size: 1.5rem; }
  .agent-name { font-weight: 700; font-size: 1rem; }
  .agent-role { font-size: 0.72rem; color: var(--agent-color); font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
  .agent-desc { font-size: 0.8rem; color: var(--muted); line-height: 1.5; margin-top: 4px; }
  .agent-task-count { margin-top: 8px; font-size: 0.75rem; color: var(--muted); }
  .agent-task-count span { color: var(--text); font-weight: 600; }

  /* ── Stats row ── */
  .stats-row { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
  .stat-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 18px 20px;
  }
  .stat-card h3 { font-size: 0.78rem; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; }
  .stat-bars { display: flex; flex-direction: column; gap: 8px; margin-bottom: 14px; }
  .stat-bar-row { display: flex; align-items: center; gap: 10px; font-size: 0.8rem; }
  .stat-bar-label { width: 55px; color: var(--muted); flex-shrink: 0; }
  .stat-bar-track { flex: 1; background: var(--surface2); border-radius: 4px; height: 6px; overflow: hidden; }
  .stat-bar-fill { height: 100%; border-radius: 4px; transition: width 0.8s ease; }
  .stat-bar-fill.cpu { background: var(--accent); }
  .stat-bar-fill.ram { background: var(--green); }
  .stat-bar-fill.disk { background: var(--amber); }
  .stat-bar-value { width: 50px; text-align: right; font-variant-numeric: tabular-nums; font-size: 0.78rem; }
  canvas { max-height: 120px; }

  /* ── Icarus widget ── */
  .icarus-widget {
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 18px 20px;
    display: flex; flex-direction: column;
  }
  .icarus-status { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
  .icarus-dot { width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }
  .icarus-dot.online { background: var(--green); box-shadow: 0 0 8px #10b98166; }
  .icarus-dot.offline { background: var(--red); }
  .icarus-label { font-weight: 700; }
  .icarus-players { font-size: 2rem; font-weight: 700; color: var(--green); margin: 8px 0 2px; }
  .icarus-sub { font-size: 0.78rem; color: var(--muted); }

  /* ── Services ── */
  .services-strip { display: flex; flex-wrap: wrap; gap: 10px; }
  .service-pill { display: flex; align-items: center; gap: 7px; background: var(--surface); border: 1px solid var(--border); border-radius: 20px; padding: 5px 13px; font-size: 0.8rem; }
  .dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
  .dot.up { background: var(--green); box-shadow: 0 0 5px #10b98155; }
  .dot.down { background: var(--red); box-shadow: 0 0 5px #ef444455; }
  .dot.checking { background: var(--amber); }
  .service-detail { color: var(--muted); font-size: 0.72rem; margin-left: 2px; }

  /* ── Board ── */
  .board { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
  @media (max-width: 860px) { .board { grid-template-columns: 1fr; } }
  .column { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
  .column-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .column-title { font-weight: 700; font-size: 0.88rem; display: flex; align-items: center; gap: 8px; }
  .col-badge { font-size: 0.7rem; padding: 2px 7px; border-radius: 10px; font-weight: 600; }
  .col-todo .col-badge { background: #374151; color: #9ca3af; }
  .col-in_progress .col-badge { background: #1e3a5f; color: #60a5fa; }
  .col-done .col-badge { background: #064e3b; color: #34d399; }
  .task-card { background: var(--surface2); border: 1px solid var(--border); border-radius: 8px; padding: 11px 13px; margin-bottom: 9px; cursor: pointer; transition: border-color 0.15s; }
  .task-card:hover { border-color: var(--accent); }
  .task-title { font-size: 0.86rem; font-weight: 600; margin-bottom: 3px; }
  .task-desc { font-size: 0.76rem; color: var(--muted); line-height: 1.4; }
  .task-footer { display: flex; align-items: center; justify-content: space-between; margin-top: 7px; }
  .task-agent-badge { font-size: 0.68rem; padding: 2px 7px; border-radius: 10px; font-weight: 600; border: 1px solid; }
  .task-delete { background: none; border: none; color: var(--muted); cursor: pointer; font-size: 0.78rem; padding: 2px 4px; border-radius: 4px; transition: color 0.15s; }
  .task-delete:hover { color: var(--red); }

  /* ── Add task ── */
  .add-task-form { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 18px; }
  .form-row { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
  .form-row:last-child { margin-bottom: 0; }
  input, select, textarea { background: var(--surface2); border: 1px solid var(--border); border-radius: 6px; color: var(--text); padding: 7px 11px; font-size: 0.83rem; outline: none; transition: border-color 0.15s; }
  input:focus, select:focus, textarea:focus { border-color: var(--accent); }
  input[name=title] { flex: 1; min-width: 180px; }
  textarea { flex: 1; min-width: 180px; resize: vertical; min-height: 50px; }
  select { min-width: 130px; }
  button.primary { background: var(--accent); color: white; border: none; border-radius: 6px; padding: 7px 18px; font-size: 0.83rem; font-weight: 600; cursor: pointer; transition: opacity 0.15s; white-space: nowrap; }
  button.primary:hover { opacity: 0.85; }
</style>
</head>
<body>

<header>
  <h1>🏠 Tim's <span>Mission Control</span></h1>
  <div id="clock">—</div>
</header>

<main>

  <div class="section-heading">The Team</div>
  <div class="agents-grid" id="agents-grid"></div>

  <div class="section-heading">Infrastructure</div>
  <div class="stats-row">
    <div class="stat-card" id="proxmox-card">
      <h3>🔧 Brian — Proxmox Host</h3>
      <div class="stat-bars">
        <div class="stat-bar-row">
          <span class="stat-bar-label">CPU</span>
          <div class="stat-bar-track"><div class="stat-bar-fill cpu" id="bar-cpu" style="width:0%"></div></div>
          <span class="stat-bar-value" id="val-cpu">—</span>
        </div>
        <div class="stat-bar-row">
          <span class="stat-bar-label">RAM</span>
          <div class="stat-bar-track"><div class="stat-bar-fill ram" id="bar-ram" style="width:0%"></div></div>
          <span class="stat-bar-value" id="val-ram">—</span>
        </div>
        <div class="stat-bar-row">
          <span class="stat-bar-label">Disk</span>
          <div class="stat-bar-track"><div class="stat-bar-fill disk" id="bar-disk" style="width:0%"></div></div>
          <span class="stat-bar-value" id="val-disk">—</span>
        </div>
      </div>
      <canvas id="chart-pve"></canvas>
    </div>

    <div class="stat-card">
      <h3>🎬 Pete — Downloads</h3>
      <div class="stat-bars">
        <div class="stat-bar-row">
          <span class="stat-bar-label">Download</span>
          <div class="stat-bar-track" style="background:transparent"></div>
          <span class="stat-bar-value" style="width:auto" id="val-dl">— KB/s</span>
        </div>
        <div class="stat-bar-row">
          <span class="stat-bar-label">Upload</span>
          <div class="stat-bar-track" style="background:transparent"></div>
          <span class="stat-bar-value" style="width:auto" id="val-ul">— KB/s</span>
        </div>
      </div>
      <canvas id="chart-qbit"></canvas>
    </div>

    <div class="icarus-widget">
      <h3 style="font-size:.78rem;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:12px">🎮 Ben — Icarus Server</h3>
      <div class="icarus-status">
        <div class="icarus-dot offline" id="icarus-dot"></div>
        <span class="icarus-label" id="icarus-status-text">Checking…</span>
      </div>
      <div class="icarus-players" id="icarus-players">—</div>
      <div class="icarus-sub">players online</div>
    </div>
  </div>

  <div class="section-heading">Service Health</div>
  <div class="services-strip" id="services-strip">
    <div class="service-pill"><div class="dot checking"></div> Checking…</div>
  </div>

  <div class="section-heading">Task Board</div>
  <div class="board">
    <div class="column col-todo">
      <div class="column-header"><div class="column-title">📋 Todo <span class="col-badge" id="badge-todo">0</span></div></div>
      <div id="tasks-todo"></div>
    </div>
    <div class="column col-in_progress">
      <div class="column-header"><div class="column-title">⚡ In Progress <span class="col-badge" id="badge-in_progress">0</span></div></div>
      <div id="tasks-in_progress"></div>
    </div>
    <div class="column col-done">
      <div class="column-header"><div class="column-title">✅ Done <span class="col-badge" id="badge-done">0</span></div></div>
      <div id="tasks-done"></div>
    </div>
  </div>

  <div class="section-heading">Add Task</div>
  <div class="add-task-form">
    <div class="form-row">
      <input name="title" placeholder="Task title…" id="new-title">
      <select id="new-agent">
        <option value="elijah">Elijah (CEO)</option>
        <option value="ben">Ben (Icarus)</option>
        <option value="pete">Pete (Arr Stack)</option>
        <option value="brian">Brian (Infrastructure)</option>
      </select>
      <select id="new-status">
        <option value="todo">Todo</option>
        <option value="in_progress">In Progress</option>
        <option value="done">Done</option>
      </select>
      <button class="primary" onclick="addTask()">Add Task</button>
    </div>
    <div class="form-row">
      <textarea id="new-desc" placeholder="Description (optional)…"></textarea>
    </div>
  </div>

</main>

<script>
const AGENTS = {};
const AGENT_COLORS = { elijah: '#6366f1', ben: '#10b981', pete: '#f59e0b', brian: '#ef4444' };

// Clock
setInterval(() => {
  document.getElementById('clock').textContent = new Date().toLocaleString('en-GB', {
    weekday:'short', day:'numeric', month:'short', hour:'2-digit', minute:'2-digit', second:'2-digit'
  });
}, 1000);

// Chart defaults
Chart.defaults.color = '#7070a0';
Chart.defaults.borderColor = '#2e2e40';

function makeLineChart(canvasId, label, color, color2, label2) {
  const ctx = document.getElementById(canvasId).getContext('2d');
  const datasets = [{
    label, data: [], borderColor: color, backgroundColor: color + '22',
    borderWidth: 2, pointRadius: 0, fill: true, tension: 0.4
  }];
  if (label2) datasets.push({
    label: label2, data: [], borderColor: color2, backgroundColor: color2 + '11',
    borderWidth: 1.5, pointRadius: 0, fill: true, tension: 0.4
  });
  return new Chart(ctx, {
    type: 'line',
    data: { labels: [], datasets },
    options: {
      responsive: true, maintainAspectRatio: true,
      animation: false,
      plugins: { legend: { display: !!label2, labels: { boxWidth: 10, font: { size: 10 } } } },
      scales: {
        x: { display: false },
        y: { min: 0, grid: { color: '#2e2e4055' }, ticks: { font: { size: 10 }, maxTicksLimit: 4 } }
      }
    }
  });
}

const pveChart = makeLineChart('chart-pve', 'CPU %', '#6366f1', '#10b981', 'RAM %');
const qbitChart = makeLineChart('chart-qbit', 'DL KB/s', '#f59e0b', '#6366f1', 'UL KB/s');

function updateChart(chart, labels, ...datasets) {
  chart.data.labels = labels;
  datasets.forEach((data, i) => { chart.data.datasets[i].data = data; });
  chart.update('none');
}

// Agents
async function loadAgents() {
  const data = await fetch('/api/agents').then(r => r.json());
  data.forEach(a => AGENTS[a.id] = a);
  document.getElementById('agents-grid').innerHTML = data.map(a => `
    <div class="agent-card" style="--agent-color:${a.color}">
      <div class="agent-header">
        <div class="agent-emoji">${a.emoji}</div>
        <div><div class="agent-name">${a.name}</div><div class="agent-role">${a.role}</div></div>
      </div>
      <div class="agent-desc">${a.description}</div>
      <div class="agent-task-count" id="agent-count-${a.id}">Active tasks: <span>—</span></div>
    </div>`).join('');
}

// Tasks
let allTasks = [];
async function loadTasks() {
  allTasks = await fetch('/api/tasks').then(r => r.json());
  const cols = { todo: [], in_progress: [], done: [] };
  allTasks.forEach(t => (cols[t.status] || cols.todo).push(t));
  Object.keys(AGENTS).forEach(id => {
    const el = document.getElementById('agent-count-' + id);
    if (el) el.innerHTML = `Active tasks: <span>${allTasks.filter(t=>t.agent===id&&t.status!=='done').length}</span>`;
  });
  ['todo','in_progress','done'].forEach(s => {
    document.getElementById('badge-'+s).textContent = cols[s].length;
    document.getElementById('tasks-'+s).innerHTML = cols[s].map(taskCard).join('');
  });
}

function taskCard(t) {
  const color = AGENT_COLORS[t.agent] || '#6366f1';
  const agentName = AGENTS[t.agent]?.name || t.agent;
  return `<div class="task-card" onclick="cycleStatus(${t.id},'${t.status}')">
    <div class="task-title">${esc(t.title)}</div>
    ${t.description ? `<div class="task-desc">${esc(t.description)}</div>` : ''}
    <div class="task-footer">
      <span class="task-agent-badge" style="color:${color};border-color:${color}22;background:${color}11">${agentName}</span>
      <button class="task-delete" onclick="event.stopPropagation();del(${t.id})">✕</button>
    </div>
  </div>`;
}

async function cycleStatus(id, cur) {
  const next = {todo:'in_progress', in_progress:'done', done:'todo'};
  await fetch('/api/tasks/'+id, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({status:next[cur]})});
  loadTasks();
}
async function del(id) {
  if (!confirm('Delete this task?')) return;
  await fetch('/api/tasks/'+id, {method:'DELETE'});
  loadTasks();
}
async function addTask() {
  const title = document.getElementById('new-title').value.trim();
  if (!title) return;
  await fetch('/api/tasks', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
    title, description: document.getElementById('new-desc').value.trim(),
    agent: document.getElementById('new-agent').value,
    status: document.getElementById('new-status').value,
  })});
  document.getElementById('new-title').value = '';
  document.getElementById('new-desc').value = '';
  loadTasks();
}

// Services
async function loadServices() {
  document.getElementById('services-strip').innerHTML = '<div class="service-pill"><div class="dot checking"></div> Checking…</div>';
  const data = await fetch('/api/services').then(r => r.json());
  document.getElementById('services-strip').innerHTML = data.map(s => `
    <div class="service-pill">
      <div class="dot ${s.up?'up':'down'}"></div>
      ${s.name}
      ${s.detail ? `<span class="service-detail">${s.detail}</span>` : ''}
    </div>`).join('');
}

// Proxmox stats
async function loadPveStats() {
  const d = await fetch('/api/stats/proxmox').then(r => r.json());
  if (d.error) return;
  document.getElementById('bar-cpu').style.width = d.cpu + '%';
  document.getElementById('val-cpu').textContent = d.cpu + '%';
  document.getElementById('bar-ram').style.width = d.ram_pct + '%';
  document.getElementById('val-ram').textContent = d.ram_pct + '%';
  document.getElementById('bar-disk').style.width = d.disk_pct + '%';
  document.getElementById('val-disk').textContent = d.disk_pct + '%';
  if (d.history) updateChart(pveChart, d.history.timestamps, d.history.cpu, d.history.ram);
}

// qBit stats
async function loadQbitStats() {
  const d = await fetch('/api/stats/qbit').then(r => r.json());
  document.getElementById('val-dl').textContent = (d.dl_speed || 0) + ' KB/s';
  document.getElementById('val-ul').textContent = (d.ul_speed || 0) + ' KB/s';
  if (d.history) updateChart(qbitChart, d.history.timestamps, d.history.dl, d.history.ul);
}

// Icarus
async function loadIcarus() {
  const d = await fetch('/api/stats/icarus').then(r => r.json());
  const dot = document.getElementById('icarus-dot');
  dot.className = 'icarus-dot ' + (d.online ? 'online' : 'offline');
  document.getElementById('icarus-status-text').textContent = d.online ? 'Online' : 'Offline';
  document.getElementById('icarus-players').textContent = d.online ? d.players : '—';
  document.getElementById('icarus-players').style.color = d.online ? 'var(--green)' : 'var(--red)';
}

function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// Init
loadAgents().then(loadTasks);
loadServices();
loadPveStats();
loadQbitStats();
loadIcarus();

setInterval(loadTasks, 30000);
setInterval(loadServices, 60000);
setInterval(loadPveStats, 10000);
setInterval(loadQbitStats, 10000);
setInterval(loadIcarus, 15000);
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


init_db()
