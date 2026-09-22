
#!/usr/bin/env python3
"""
To-Do List
==========
Plan your daily targets, tick them off, and see how every day of the month went.

  * Add, edit, delete and check off tasks for any day
  * Star a task to mark it as important
  * Calendar colours each day: green / yellow / orange / red
  * A progress bar for the selected day is always visible at the bottom
  * Works on a phone: open the "Phone" address printed below in your phone's browser

How to run
----------
    pip install flask
    python todo_app.py

Your tasks are saved in a file called todo.db next to this script.
"""

import calendar
import os
import re
import socket
import sqlite3
from contextlib import closing
from datetime import datetime

from flask import Flask, Response, g, jsonify, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "todo.db")

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with closing(sqlite3.connect(DB_PATH)) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                date       TEXT    NOT NULL,              -- YYYY-MM-DD
                title      TEXT    NOT NULL,
                important  INTEGER NOT NULL DEFAULT 0,
                done       INTEGER NOT NULL DEFAULT 0,
                created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_date ON tasks(date)")
        db.commit()


# ---------------------------------------------------------------------------
# Progress and colour rules  (change the numbers here to change the rules)
# ---------------------------------------------------------------------------
def day_status(total, pct, important_all_done):
    """
    Colour of a day.

      green   75% or more done
      yellow  50-74% done AND every important task done
      orange  40-49% done AND every important task done
              (or 50-74% done but an important task was missed)
      red     under 40% done
              (or 40-49% done but an important task was missed)

    A day with no tasks has no colour ("none").
    """
    if total == 0:
        return "none"
    if pct >= 75:
        return "green"
    if pct >= 50:
        return "yellow" if important_all_done else "orange"
    if pct >= 40:
        return "orange" if important_all_done else "red"
    return "red"


def make_progress(total, done, imp_total, imp_done):
    pct = int(done * 100 / total + 0.5) if total else 0  # round half up
    return {
        "total": total,
        "done": done,
        "pct": pct,
        "imp_total": imp_total,
        "imp_done": imp_done,
        "status": day_status(total, pct, imp_done == imp_total),
    }


def progress_for_date(db, date):
    row = db.execute(
        """
        SELECT COUNT(*)                                            AS total,
               COALESCE(SUM(done), 0)                              AS done,
               COALESCE(SUM(important), 0)                         AS imp_total,
               COALESCE(SUM(CASE WHEN important = 1 AND done = 1
                                 THEN 1 ELSE 0 END), 0)            AS imp_done
        FROM tasks WHERE date = ?
        """,
        (date,),
    ).fetchone()
    return make_progress(row["total"], row["done"], row["imp_total"], row["imp_done"])


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def valid_date(value):
    if not isinstance(value, str) or not DATE_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def clean_title(value):
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    if not value or len(value) > 200:
        return None
    return value


def task_json(row):
    return {
        "id": row["id"],
        "date": row["date"],
        "title": row["title"],
        "important": bool(row["important"]),
        "done": bool(row["done"]),
    }


def error(message, code=400):
    return jsonify(error=message), code


@app.after_request
def no_cache_for_api(resp):
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return Response(PAGE, mimetype="text/html")


@app.get("/manifest.json")
def manifest():
    return jsonify(
        name="To-Do List",
        short_name="To-Do",
        start_url="/",
        display="standalone",
        background_color="#F6F7FB",
        theme_color="#2B44D6",
        icons=[{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
    )


@app.get("/icon.svg")
def icon():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
        '<rect width="512" height="512" rx="112" fill="#2B44D6"/>'
        '<path d="M136 264l88 88 152-176" fill="none" stroke="#fff" '
        'stroke-width="44" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )
    return Response(svg, mimetype="image/svg+xml")


@app.get("/api/day")
def api_day():
    date = request.args.get("date", "")
    if not valid_date(date):
        return error("Invalid date.")
    db = get_db()
    rows = db.execute("SELECT * FROM tasks WHERE date = ? ORDER BY id", (date,)).fetchall()
    return jsonify(tasks=[task_json(r) for r in rows], progress=progress_for_date(db, date))


@app.post("/api/tasks")
def api_add():
    data = request.get_json(silent=True) or {}
    date = data.get("date")
    title = clean_title(data.get("title"))
    if not valid_date(date):
        return error("Invalid date.")
    if title is None:
        return error("Write a task first (up to 200 characters).")
    db = get_db()
    cur = db.execute(
        "INSERT INTO tasks (date, title, important) VALUES (?, ?, ?)",
        (date, title, 1 if data.get("important") else 0),
    )
    db.commit()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(task=task_json(row), progress=progress_for_date(db, date)), 201


@app.patch("/api/tasks/<int:task_id>")
def api_update(task_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return error("Task not found.", 404)

    title = row["title"]
    if "title" in data:
        title = clean_title(data["title"])
        if title is None:
            return error("A task can't be empty (up to 200 characters).")
    important = int(bool(data["important"])) if "important" in data else row["important"]
    done = int(bool(data["done"])) if "done" in data else row["done"]

    db.execute(
        "UPDATE tasks SET title = ?, important = ?, done = ? WHERE id = ?",
        (title, important, done, task_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return jsonify(task=task_json(row), progress=progress_for_date(db, row["date"]))


@app.delete("/api/tasks/<int:task_id>")
def api_delete(task_id):
    db = get_db()
    row = db.execute("SELECT date FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return error("Task not found.", 404)
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()
    return jsonify(progress=progress_for_date(db, row["date"]))


@app.get("/api/month")
def api_month():
    try:
        year = int(request.args["year"])
        month = int(request.args["month"])
        if not (1 <= month <= 12 and 1970 <= year <= 2200):
            raise ValueError
    except (KeyError, ValueError):
        return error("Invalid month.")

    last_day = calendar.monthrange(year, month)[1]
    start = f"{year:04d}-{month:02d}-01"
    end = f"{year:04d}-{month:02d}-{last_day:02d}"

    rows = get_db().execute(
        """
        SELECT date,
               COUNT(*)                                            AS total,
               COALESCE(SUM(done), 0)                              AS done,
               COALESCE(SUM(important), 0)                         AS imp_total,
               COALESCE(SUM(CASE WHEN important = 1 AND done = 1
                                 THEN 1 ELSE 0 END), 0)            AS imp_done
        FROM tasks
        WHERE date BETWEEN ? AND ?
        GROUP BY date
        """,
        (start, end),
    ).fetchall()

    days = {
        r["date"]: make_progress(r["total"], r["done"], r["imp_total"], r["imp_done"])
        for r in rows
    }
    return jsonify(year=year, month=month, days=days)


# ---------------------------------------------------------------------------
# The app screen (HTML + CSS + JavaScript, served as one page)
# ---------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#2B44D6">
<meta name="color-scheme" content="light dark">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="To-Do List">
<title>To-Do List</title>
<link rel="manifest" href="/manifest.json">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<style>
:root{
  --paper:#F6F7FB; --surface:#FFFFFF; --well:#EBEEF6;
  --ink:#1B2340; --muted:#68708F; --rule:#DCE1EE;
  --pen:#2B44D6; --pen-soft:#E6EAFC; --on-pen:#FFFFFF;
  --green:#3DBE6A; --yellow:#F4C430; --orange:#F08A2E; --red:#EF5A5F; --none:#C4CBDD;
  --on-status:#111936;
  --serif:"Iowan Old Style","Palatino Linotype","Book Antiqua",Georgia,"Noto Serif",serif;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --dock-h:172px;
}
@media (prefers-color-scheme:dark){
  :root{
    --paper:#0D1122; --surface:#151A30; --well:#1F2542;
    --ink:#E9ECF8; --muted:#9AA3C2; --rule:#2A3150;
    --pen:#8C9CFF; --pen-soft:#232B55; --on-pen:#0D1122; --none:#3A4266;
  }
}

*,*::before,*::after{box-sizing:border-box}
[hidden]{display:none!important}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);font-size:16px;line-height:1.45;-webkit-tap-highlight-color:transparent}
h1,h2,h3,p,ul{margin:0}
ul{padding:0;list-style:none}
button{font:inherit;color:inherit;cursor:pointer}
svg{display:block}
:focus-visible{outline:2px solid var(--pen);outline-offset:2px}

.app{max-width:520px;margin:0 auto;padding:calc(env(safe-area-inset-top,0px) + 18px) 20px calc(var(--dock-h) + 28px)}

/* ---------- Brand + date ---------- */
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:17px}
.logo{display:grid;place-items:center;width:28px;height:28px;border-radius:9px;background:var(--pen);color:var(--on-pen)}
.logo svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:3;stroke-linecap:round;stroke-linejoin:round}

.datebar{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin:26px 0 22px}
.date-block{min-width:0}
.datepill{position:relative;display:block;cursor:pointer}
.date-main{display:block;font:700 36px/1.05 var(--serif);letter-spacing:-.01em}
.date-sub{display:block;margin-top:6px;font:400 18px/1.3 var(--serif);color:var(--muted)}
.datepill input{position:absolute;inset:0;width:100%;height:100%;opacity:0;border:0;cursor:pointer}
.date-tags{margin-top:10px;min-height:30px;display:flex;align-items:center}
.chip{display:inline-block;padding:3px 11px;border-radius:999px;background:var(--pen-soft);color:var(--pen);font:600 13px/1.5 var(--sans)}
.chip.green{background:var(--green);color:var(--on-status)}
.chip.yellow{background:var(--yellow);color:var(--on-status)}
.chip.orange{background:var(--orange);color:var(--on-status)}
.chip.red{background:var(--red);color:var(--on-status)}
.btn-text{border:0;background:transparent;color:var(--pen);font-weight:600;font-size:14px;padding:6px 10px;border-radius:10px}
.date-tags .btn-text{padding-left:0}
.nav-pair{display:flex;gap:8px;padding-top:4px}

/* ---------- Buttons ---------- */
.icon-btn{width:36px;height:36px;border:0;border-radius:10px;background:transparent;color:var(--muted);display:grid;place-items:center;padding:0;transition:background-color .2s,color .2s}
.icon-btn svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.icon-btn.round{width:42px;height:42px;border-radius:50%;background:var(--well);color:var(--ink)}
.icon-btn.star.on{color:var(--pen)}
.icon-btn.star.on svg{fill:currentColor}
.icon-btn.save{color:var(--pen)}
@media (hover:hover){
  .icon-btn:hover{background:var(--well);color:var(--ink)}
  .icon-btn.star.on:hover{color:var(--pen)}
  .icon-btn.danger:hover{color:var(--red)}
}
.btn{display:inline-flex;align-items:center;gap:6px;border:0;border-radius:999px;padding:9px 16px 9px 13px;font-weight:600;font-size:15px;background:var(--pen);color:var(--on-pen);transition:transform .12s}
.btn svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:2.4;stroke-linecap:round;stroke-linejoin:round}
.btn:active{transform:scale(.96)}
.btn-outline{margin-top:16px;border:2px solid var(--pen);background:transparent;color:var(--pen);border-radius:999px;padding:8px 18px;font-weight:600;font-size:15px}

/* ---------- Add task ---------- */
.add{display:flex;align-items:center;gap:6px;border-bottom:2px solid var(--ink);padding:2px 0 4px}
.add:focus-within{border-bottom-color:var(--pen)}
.add input{flex:1;min-width:0;border:0;background:transparent;color:var(--ink);font:500 17px var(--sans);padding:12px 0;outline:none}
.add input::placeholder{color:var(--muted);font-weight:400}
.hint{margin-top:10px;font-size:13.5px;color:var(--muted)}

/* ---------- Task list ---------- */
.list-head{display:flex;align-items:baseline;justify-content:space-between;margin:28px 0 8px}
.list-head h2{font:700 22px/1.2 var(--serif)}
.count{font-size:14px;color:var(--muted);font-variant-numeric:tabular-nums}
.tasks{border-top:1px solid var(--rule)}
.task{display:flex;align-items:flex-start;gap:12px;padding:12px 0;border-bottom:1px solid var(--rule);transition:opacity .25s,transform .25s}
.task.enter{animation:enter .32s ease-out}
.task.removing{opacity:0;transform:translateX(28px)}
@keyframes enter{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:none}}

.check{flex:none;width:28px;height:28px;border-radius:9px;border:2px solid var(--muted);background:transparent;padding:0;display:grid;place-items:center;transition:background-color .25s,border-color .25s,transform .15s}
.check svg{width:18px;height:18px;fill:none;stroke:var(--on-pen);stroke-width:3;stroke-linecap:round;stroke-linejoin:round}
.check svg path{stroke-dasharray:24;stroke-dashoffset:24;transition:stroke-dashoffset .3s ease .12s}
.check:active{transform:scale(.9)}
.task.done .check{background:var(--pen);border-color:var(--pen)}
.task.done .check svg path{stroke-dashoffset:0}
.task.just-done .check{animation:pop .4s ease}
@keyframes pop{0%{transform:scale(1)}40%{transform:scale(1.2)}100%{transform:scale(1)}}

.task-body{flex:1;min-width:0;padding-top:1px}
/* The strike-through: a pen line that is drawn across the text when the task is done. */
.task-text{
  font-size:17px;line-height:26px;font-weight:500;overflow-wrap:anywhere;
  background:linear-gradient(var(--pen),var(--pen)) no-repeat 0 46% / 0% 2.5px;
  transition:background-size .55s cubic-bezier(.65,0,.35,1),color .4s ease;
}
.task.done .task-text{background-size:100% 2.5px;color:var(--muted)}
.actions{display:flex;flex:none;margin:-4px -6px 0 0}
.task.editing{align-items:center}
.edit-input{flex:1;min-width:0;border:0;border-bottom:2px solid var(--pen);background:transparent;color:var(--ink);font:500 17px var(--sans);padding:4px 0;outline:none}

.empty{text-align:center;padding:38px 12px 8px;color:var(--muted)}
.empty svg{width:72px;height:72px;margin:0 auto 10px}
.empty-title{font:700 19px/1.3 var(--serif);color:var(--ink);margin-bottom:4px}

/* ---------- Calendar ---------- */
.cal-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:26px 0 16px}
.cal-title{font:700 27px/1.15 var(--serif);letter-spacing:-.01em}
.cal-nav{display:flex;align-items:center;gap:6px}
.weekdays,.days{display:grid;grid-template-columns:repeat(7,1fr);gap:6px}
.weekdays span{text-align:center;font-size:12px;font-weight:600;color:var(--muted);padding-bottom:4px}
.day{aspect-ratio:1/1;border:2px solid transparent;border-radius:12px;background:var(--well);color:var(--ink);font:600 16px var(--sans);display:grid;place-items:center;padding:0;transition:transform .15s}
.day.blank{visibility:hidden}
.day.s-none{color:var(--muted)}
.day.s-planned{background:transparent;border:2px dashed var(--none)}
.day.s-green{background:var(--green);color:var(--on-status)}
.day.s-yellow{background:var(--yellow);color:var(--on-status)}
.day.s-orange{background:var(--orange);color:var(--on-status)}
.day.s-red{background:var(--red);color:var(--on-status)}
.day.today{box-shadow:0 0 0 2px var(--paper),0 0 0 4px var(--pen)}
.day.selected{border:2px solid var(--ink);transform:scale(1.08)}

.detail{margin-top:24px;padding-top:18px;border-top:1px solid var(--rule)}
.detail h3{font:700 21px/1.25 var(--serif)}
.detail-status{margin:6px 0 10px;color:var(--muted);font-size:15px}
.mini{border-top:1px solid var(--rule)}
.mini li{display:flex;align-items:flex-start;gap:10px;padding:9px 0;border-bottom:1px solid var(--rule);font-size:16px;line-height:24px}
.mini .mark{flex:none;width:22px;height:22px;margin-top:1px;border-radius:7px;border:2px solid var(--muted);display:grid;place-items:center}
.mini .mark svg{width:14px;height:14px;fill:none;stroke:var(--on-pen);stroke-width:3.2;stroke-linecap:round;stroke-linejoin:round;opacity:0}
.mini li.done .mark{background:var(--pen);border-color:var(--pen)}
.mini li.done .mark svg{opacity:1}
.mini .txt{flex:1;min-width:0;overflow-wrap:anywhere}
.mini li.done .txt{color:var(--muted);text-decoration:line-through;text-decoration-color:var(--pen);text-decoration-thickness:2px}
.mini .imp{flex:none;margin-top:3px;color:var(--pen)}
.mini .imp svg{width:17px;height:17px;fill:currentColor;stroke:currentColor;stroke-width:2;stroke-linejoin:round}
.mini-empty{padding:12px 0;color:var(--muted);border-top:1px solid var(--rule)}

.summary{margin-top:34px;display:flex;align-items:baseline;justify-content:space-between;gap:10px}
.summary h3{font:700 21px/1.2 var(--serif)}
.summary span{font-size:14px;color:var(--muted)}
.legend{margin-top:10px;border-top:1px solid var(--rule)}
.legend li{display:flex;align-items:center;gap:14px;padding:12px 0;border-bottom:1px solid var(--rule)}
.swatch{flex:none;width:26px;height:26px;border-radius:8px}
.legend b{display:block;font-size:16px}
.legend small{display:block;color:var(--muted);font-size:13.5px;line-height:1.35}
.legend em{margin-left:auto;padding-left:8px;font-style:normal;font-size:14px;color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums}

/* ---------- Bottom dock: daily progress + tabs ---------- */
.dock{position:fixed;left:0;right:0;bottom:0;z-index:10;max-width:520px;margin:0 auto;background:var(--surface);border-radius:24px 24px 0 0;padding:14px 20px calc(env(safe-area-inset-bottom,0px) + 8px);box-shadow:0 -10px 30px rgba(13,17,34,.10)}
.p-top{display:flex;align-items:center;justify-content:space-between;gap:10px}
.p-label{display:flex;align-items:center;gap:8px;font-weight:600;font-size:14px;color:var(--muted)}
.p-pct{font:700 30px/1 var(--serif);font-variant-numeric:tabular-nums}
.bar{position:relative;height:14px;border-radius:999px;background:var(--well);overflow:hidden;margin:10px 0 8px}
.fill{height:100%;width:0;border-radius:999px;background:var(--none);transition:width .7s cubic-bezier(.22,.9,.3,1),background-color .4s}
.fill.green{background:var(--green)}
.fill.yellow{background:var(--yellow)}
.fill.orange{background:var(--orange)}
.fill.red{background:var(--red)}
.tick{position:absolute;top:0;bottom:0;width:2px;background:var(--surface);transform:translateX(-1px)}
.bar.celebrate{animation:celebrate .8s ease}
@keyframes celebrate{0%{transform:scaleY(1)}30%{transform:scaleY(1.7)}100%{transform:scaleY(1)}}
.p-sub{display:flex;justify-content:space-between;gap:10px;font-size:13px;color:var(--muted)}
.tabs{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px}
.tab{display:flex;align-items:center;justify-content:center;gap:8px;padding:10px 8px;border:0;border-radius:14px;background:transparent;color:var(--muted);font-weight:600;font-size:15px}
.tab svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.tab.active{background:var(--pen-soft);color:var(--pen)}

.toast{position:fixed;left:50%;bottom:calc(var(--dock-h) + 14px);transform:translate(-50%,10px);max-width:calc(100% - 40px);background:var(--ink);color:var(--paper);padding:10px 16px;border-radius:12px;font-size:14px;opacity:0;pointer-events:none;transition:opacity .25s,transform .25s;z-index:30}
.toast.show{opacity:1;transform:translate(-50%,0)}

@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01ms!important;transition-duration:.01ms!important}
}
</style>
</head>
<body>
<div class="app">
  <header class="brand"><span class="logo" data-icon="check"></span>To-Do List</header>

  <main>
    <!-- ===================== TASKS ===================== -->
    <section id="view-tasks">
      <div class="datebar">
        <div class="date-block">
          <label class="datepill">
            <span class="date-main" id="dateMain"></span>
            <span class="date-sub" id="dateSub"></span>
            <input type="date" id="datePicker" aria-label="Choose a date">
          </label>
          <div class="date-tags">
            <span class="chip" id="todayChip" hidden>Today</span>
            <button class="btn-text" id="goToday" type="button" hidden>Go to today</button>
          </div>
        </div>
        <div class="nav-pair">
          <button class="icon-btn round" id="prevDay" type="button" aria-label="Previous day" data-icon="left"></button>
          <button class="icon-btn round" id="nextDay" type="button" aria-label="Next day" data-icon="right"></button>
        </div>
      </div>

      <form class="add" id="addForm" autocomplete="off">
        <input id="newTitle" type="text" maxlength="200" placeholder="Add a target" aria-label="New task">
        <button class="icon-btn star" id="newImportant" type="button" aria-pressed="false" aria-label="Mark as important" title="Mark as important" data-icon="star"></button>
        <button class="btn" type="submit"><span data-icon="plus"></span>Add</button>
      </form>
      <p class="hint">Tap the star to mark a task important. Important tasks change your calendar colours.</p>

      <div class="list-head">
        <h2 id="listTitle">Today's targets</h2>
        <span class="count" id="listCount"></span>
      </div>
      <ul class="tasks" id="taskList"></ul>
      <div class="empty" id="empty" hidden>
        <svg viewBox="0 0 72 72" aria-hidden="true"><rect x="14" y="8" width="44" height="56" rx="8" fill="none" stroke="currentColor" stroke-width="2.5"/><path d="M24 24h24M24 34h24M24 44h12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/><path d="m42 47 6 6 12-13" fill="none" stroke="var(--pen)" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <p class="empty-title">Nothing planned for this day</p>
        <p>Type a target above and tap Add.</p>
      </div>
    </section>

    <!-- ===================== CALENDAR ===================== -->
    <section id="view-calendar" hidden>
      <div class="cal-head">
        <h2 class="cal-title" id="calTitle"></h2>
        <div class="cal-nav">
          <button class="btn-text" id="calToday" type="button" hidden>Today</button>
          <button class="icon-btn round" id="prevMonth" type="button" aria-label="Previous month" data-icon="left"></button>
          <button class="icon-btn round" id="nextMonth" type="button" aria-label="Next month" data-icon="right"></button>
        </div>
      </div>
      <div class="weekdays" id="weekdays"></div>
      <div class="days" id="days"></div>

      <div class="detail">
        <h3 id="dTitle"></h3>
        <p class="detail-status" id="dStatus"></p>
        <ul class="mini" id="dList"></ul>
        <button class="btn-outline" id="openDay" type="button">Open this day</button>
      </div>

      <div class="summary">
        <h3>This month</h3>
        <span id="avgText"></span>
      </div>
      <ul class="legend">
        <li><i class="swatch" style="background:var(--green)"></i><div><b>Green</b><small>75% or more of tasks done</small></div><em id="cnt-green"></em></li>
        <li><i class="swatch" style="background:var(--yellow)"></i><div><b>Yellow</b><small>50% to 74% done, all important tasks finished</small></div><em id="cnt-yellow"></em></li>
        <li><i class="swatch" style="background:var(--orange)"></i><div><b>Orange</b><small>40% to 49% done, all important tasks finished</small></div><em id="cnt-orange"></em></li>
        <li><i class="swatch" style="background:var(--red)"></i><div><b>Red</b><small>Under 40% of tasks done</small></div><em id="cnt-red"></em></li>
      </ul>
      <p class="hint">If an important task is missed, the day drops one colour (yellow becomes orange, orange becomes red). Days that haven't arrived yet stay uncoloured.</p>
    </section>
  </main>
</div>

<!-- ===================== BOTTOM: PROGRESS + TABS ===================== -->
<footer class="dock" id="dock">
  <div class="p-top">
    <div class="p-label">Daily progress <span class="chip" id="pChip" hidden></span></div>
    <div class="p-pct" id="pPct">0%</div>
  </div>
  <div class="bar" id="pBar"><div class="fill" id="pFill"></div></div>
  <div class="p-sub"><span id="pLeft">No tasks yet</span><span id="pRight"></span></div>
  <nav class="tabs" aria-label="Sections">
    <button class="tab active" type="button" data-tab="tasks"><span data-icon="list"></span>Tasks</button>
    <button class="tab" type="button" data-tab="calendar"><span data-icon="cal"></span>Calendar</button>
  </nav>
</footer>

<div class="toast" id="toast" role="status" aria-live="polite"></div>

<script>
(() => {
'use strict';

/* ------------ Settings you can change ------------ */
const WEEK_STARTS_ON = 0;              // 0 = Sunday, 1 = Monday
const PROGRESS_TICKS = [40, 50, 75];   // marks on the progress bar (the colour limits)

/* ------------ Helpers ------------ */
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const pad = (n) => String(n).padStart(2, '0');
const fmt = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parse = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
const todayStr = () => fmt(new Date());
const addDays = (s, n) => { const d = parse(s); d.setDate(d.getDate() + n); return fmt(d); };
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') n.className = v;
    else if (k === 'html') n.innerHTML = v;
    else if (v !== false && v != null) n.setAttribute(k, v);
  }
  for (const kid of kids) if (kid != null) n.append(kid);
  return n;
}

const svg = (inner) => `<svg viewBox="0 0 24 24" aria-hidden="true">${inner}</svg>`;
const ICON = {
  star:  svg('<polygon points="12 2.5 15.1 8.8 22 9.8 17 14.7 18.2 21.5 12 18.3 5.8 21.5 7 14.7 2 9.8 8.9 8.8"/>'),
  edit:  svg('<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>'),
  trash: svg('<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'),
  check: svg('<path d="M4 12.5 9.5 18 20 6.5"/>'),
  x:     svg('<path d="M18 6 6 18"/><path d="m6 6 12 12"/>'),
  plus:  svg('<path d="M12 5v14"/><path d="M5 12h14"/>'),
  left:  svg('<path d="m15 18-6-6 6-6"/>'),
  right: svg('<path d="m9 18 6-6-6-6"/>'),
  list:  svg('<path d="m3 17 2 2 4-4"/><path d="m3 7 2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/>'),
  cal:   svg('<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/>'),
};

/* ------------ State ------------ */
const now = new Date();
const state = {
  date: todayStr(),       // the day being viewed
  tasks: [],              // tasks of that day
  progress: null,         // progress of that day (calculated by the server)
  tab: 'tasks',
  newImportant: false,
  cal: { y: now.getFullYear(), m: now.getMonth() },
  monthDays: {},          // progress of every day in the month shown in the calendar
};
let dayToken = 0, monthToken = 0;
const STATUS_NAME = { green: 'Green', yellow: 'Yellow', orange: 'Orange', red: 'Red' };

/* ------------ Talking to the server ------------ */
async function api(url, { method = 'GET', body } = {}) {
  const init = { method, headers: {} };
  if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
  const res = await fetch(url, init);
  if (!res.ok) {
    let msg = 'Something went wrong.';
    try { msg = (await res.json()).error || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

let toastTimer;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 3200);
}
const fail = (e) => toast(e instanceof TypeError ? "Can't reach the app. Check that it is still running." : (e.message || 'Something went wrong.'));

/* ------------ Day colour (planned days stay uncoloured) ------------ */
function effStatus(dateStr, p) {
  if (!p || p.total === 0) return 'none';
  if (dateStr > todayStr() && p.done === 0) return 'planned';
  return p.status;
}

/* ------------ Bottom progress bar ------------ */
let shownPct = 0, tweenId = 0;
function tweenPct(to) {
  const label = $('#pPct');
  const instant = document.hidden || matchMedia('(prefers-reduced-motion: reduce)').matches;
  const id = ++tweenId, from = shownPct, start = performance.now();
  if (instant) { shownPct = to; label.textContent = to + '%'; return; }
  const step = (t) => {
    if (id !== tweenId) return;
    const k = Math.min(1, (t - start) / 550);
    shownPct = Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3)));
    label.textContent = shownPct + '%';
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function setProgress(p, celebrate = false) {
  state.progress = p;
  state.monthDays[state.date] = p;                 // keep the calendar in step
  const st = effStatus(state.date, p);

  const fill = $('#pFill');
  fill.className = 'fill ' + (STATUS_NAME[st] ? st : '');
  fill.style.width = p.pct + '%';
  tweenPct(p.pct);

  const chip = $('#pChip');
  chip.hidden = !STATUS_NAME[st] && st !== 'planned';
  chip.className = 'chip ' + (STATUS_NAME[st] ? st : '');
  chip.textContent = STATUS_NAME[st] ? STATUS_NAME[st] + ' day' : 'Planned';

  $('#pLeft').textContent = p.total === 0 ? 'No tasks yet'
    : p.done === p.total ? 'All tasks done'
    : `${p.done} of ${p.total} tasks done`;
  $('#pRight').textContent = p.imp_total ? `${p.imp_done} of ${p.imp_total} important` : '';

  if (celebrate && p.total > 0 && p.done === p.total) {
    const bar = $('#pBar');
    bar.classList.remove('celebrate'); void bar.offsetWidth; bar.classList.add('celebrate');
  }
  if (state.tab === 'calendar') { renderCalendar(); renderDetail(); }
}

/* ------------ Date bar ------------ */
function renderDateBar() {
  const d = parse(state.date), t = todayStr();
  $('#dateMain').textContent = d.toLocaleDateString(undefined, { weekday: 'long' });
  $('#dateSub').textContent = d.toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' });
  $('#todayChip').hidden = state.date !== t;
  $('#goToday').hidden = state.date === t;
  $('#datePicker').value = state.date;
  $('#listTitle').textContent = state.date === t ? "Today's targets" : 'Targets for this day';
}

/* ------------ Tasks ------------ */
function updateEmpty() { $('#empty').hidden = state.tasks.length > 0; }
function updateCount() {
  const n = state.tasks.length, done = state.tasks.filter((t) => t.done).length;
  $('#listCount').textContent = n ? `${done} of ${n} done` : '';
}

function renderTasks() {
  $('#taskList').replaceChildren(...state.tasks.map((t) => taskEl(t)));
  updateEmpty();
  updateCount();
}

function taskEl(t, animate = false) {
  const li = el('li', { class: 'task' + (t.done ? ' done' : '') + (animate ? ' enter' : ''), 'data-id': t.id });

  const check = el('button', { class: 'check', type: 'button', 'aria-label': 'Mark task as done', 'aria-pressed': String(t.done), html: ICON.check });
  check.addEventListener('click', () => toggleDone(t.id, li));

  const body = el('div', { class: 'task-body' }, el('span', { class: 'task-text' }, t.title));

  const star = el('button', { class: 'icon-btn star' + (t.important ? ' on' : ''), type: 'button', 'aria-label': 'Important', 'aria-pressed': String(t.important), title: 'Important', html: ICON.star });
  star.addEventListener('click', () => toggleImportant(t.id, star));

  const edit = el('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Edit task', title: 'Edit', html: ICON.edit });
  edit.addEventListener('click', () => startEdit(t, li));

  const del = el('button', { class: 'icon-btn danger', type: 'button', 'aria-label': 'Delete task', title: 'Delete', html: ICON.trash });
  del.addEventListener('click', () => removeTask(t.id, li));

  li.append(check, body, el('div', { class: 'actions' }, star, edit, del));
  return li;
}

function applyDone(t, li, value) {
  t.done = value;
  li.classList.toggle('done', value);                     // this triggers the strike-through animation
  li.querySelector('.check').setAttribute('aria-pressed', String(value));
  if (value) { li.classList.add('just-done'); setTimeout(() => li.classList.remove('just-done'), 450); }
  updateCount();
}

async function toggleDone(id, li) {
  const t = state.tasks.find((x) => x.id === id);
  if (!t) return;
  const next = !t.done;
  applyDone(t, li, next);
  try {
    const r = await api(`/api/tasks/${id}`, { method: 'PATCH', body: { done: next } });
    setProgress(r.progress, next);
  } catch (e) { applyDone(t, li, !next); fail(e); }
}

async function toggleImportant(id, btn) {
  const t = state.tasks.find((x) => x.id === id);
  if (!t) return;
  const next = !t.important;
  const paint = (v) => { t.important = v; btn.classList.toggle('on', v); btn.setAttribute('aria-pressed', String(v)); };
  paint(next);
  try {
    const r = await api(`/api/tasks/${id}`, { method: 'PATCH', body: { important: next } });
    setProgress(r.progress);
  } catch (e) { paint(!next); fail(e); }
}

function startEdit(t, li) {
  const input = el('input', { class: 'edit-input', type: 'text', maxlength: '200', value: t.title, 'aria-label': 'Edit task' });
  const save = el('button', { class: 'icon-btn save', type: 'button', 'aria-label': 'Save changes', title: 'Save', html: ICON.check });
  const cancel = el('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Cancel', title: 'Cancel', html: ICON.x });
  const row = el('li', { class: 'task editing', 'data-id': t.id }, input, save, cancel);
  li.replaceWith(row);
  input.focus();
  input.select();

  let busy = false;
  const finish = async (commit) => {
    if (busy) return;
    if (commit) {
      const title = input.value.trim();
      if (!title) { input.focus(); return; }
      if (title !== t.title) {
        busy = true;
        try {
          const r = await api(`/api/tasks/${t.id}`, { method: 'PATCH', body: { title } });
          t.title = r.task.title;
        } catch (e) { busy = false; fail(e); return; }
      }
    }
    row.replaceWith(taskEl(t));
    renderDetail();
  };
  save.addEventListener('click', () => finish(true));
  cancel.addEventListener('click', () => finish(false));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    if (e.key === 'Escape') finish(false);
  });
}

async function removeTask(id, li) {
  if (!confirm('Delete this task?')) return;
  try {
    const r = await api(`/api/tasks/${id}`, { method: 'DELETE' });
    li.classList.add('removing');
    setTimeout(() => li.remove(), 250);
    state.tasks = state.tasks.filter((x) => x.id !== id);
    updateEmpty();
    updateCount();
    setProgress(r.progress);
  } catch (e) { fail(e); }
}

function setNewImportant(v) {
  state.newImportant = v;
  const b = $('#newImportant');
  b.classList.toggle('on', v);
  b.setAttribute('aria-pressed', String(v));
}

async function addTask(ev) {
  ev.preventDefault();
  const input = $('#newTitle');
  const title = input.value.trim();
  if (!title) { input.focus(); return; }
  try {
    const r = await api('/api/tasks', { method: 'POST', body: { date: state.date, title, important: state.newImportant } });
    state.tasks.push(r.task);
    $('#taskList').append(taskEl(r.task, true));
    input.value = '';
    setNewImportant(false);
    updateEmpty();
    updateCount();
    setProgress(r.progress);
    input.focus();
  } catch (e) { fail(e); }
}

/* ------------ Loading a day ------------ */
async function loadDay() {
  const token = ++dayToken;
  renderDateBar();
  try {
    const r = await api(`/api/day?date=${state.date}`);
    if (token !== dayToken) return;
    state.tasks = r.tasks;
    renderTasks();
    setProgress(r.progress);
    renderDetail();
  } catch (e) { fail(e); }
}

/* ------------ Calendar ------------ */
async function loadMonth() {
  const { y, m } = state.cal, token = ++monthToken;
  try {
    const r = await api(`/api/month?year=${y}&month=${m + 1}`);
    if (token !== monthToken) return;
    state.monthDays = r.days;
  } catch (e) { fail(e); }
  renderCalendar();
}

function shiftMonth(n) {
  let { y, m } = state.cal;
  m += n;
  if (m < 0) { m = 11; y--; }
  if (m > 11) { m = 0; y++; }
  state.cal = { y, m };
  loadMonth();
}

function dayLabel(ds, p, st) {
  const base = parse(ds).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' });
  if (st === 'none') return base + ', no tasks';
  if (st === 'planned') return `${base}, planned`;
  return `${base}, ${STATUS_NAME[st]}, ${p.pct} percent done`;
}

function renderCalendar() {
  const { y, m } = state.cal, t = todayStr();
  $('#calTitle').textContent = new Date(y, m, 1).toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
  const cur = new Date();
  $('#calToday').hidden = (y === cur.getFullYear() && m === cur.getMonth());   // only needed when you are on another month

  const wd = $('#weekdays');
  wd.replaceChildren();
  for (let i = 0; i < 7; i++) {
    const d = new Date(2023, 0, 1 + ((i + WEEK_STARTS_ON) % 7));   // 1 Jan 2023 was a Sunday
    wd.append(el('span', {}, d.toLocaleDateString(undefined, { weekday: 'short' })));
  }

  const lead = (new Date(y, m, 1).getDay() - WEEK_STARTS_ON + 7) % 7;
  const total = new Date(y, m + 1, 0).getDate();
  const grid = $('#days');
  grid.replaceChildren();
  for (let i = 0; i < lead; i++) grid.append(el('span', { class: 'day blank', 'aria-hidden': 'true' }));
  for (let day = 1; day <= total; day++) {
    const ds = `${y}-${pad(m + 1)}-${pad(day)}`;
    const p = state.monthDays[ds];
    const st = effStatus(ds, p);
    const cls = ['day', 's-' + st];
    if (ds === t) cls.push('today');
    if (ds === state.date) cls.push('selected');
    const b = el('button', { class: cls.join(' '), type: 'button', 'data-date': ds, 'aria-label': dayLabel(ds, p, st), 'aria-pressed': String(ds === state.date) }, String(day));
    b.addEventListener('click', () => selectDate(ds));
    grid.append(b);
  }
  renderLegend();
}

function renderLegend() {
  const prefix = `${state.cal.y}-${pad(state.cal.m + 1)}-`, t = todayStr();
  const counts = { green: 0, yellow: 0, orange: 0, red: 0 };
  let sum = 0, n = 0;
  for (const [d, p] of Object.entries(state.monthDays)) {
    if (!d.startsWith(prefix) || p.total === 0 || d > t) continue;
    counts[p.status]++; sum += p.pct; n++;
  }
  for (const k of Object.keys(counts)) $('#cnt-' + k).textContent = plural(counts[k], 'day', 'days');
  $('#avgText').textContent = n ? `Average ${Math.round(sum / n)}% done` : 'No tracked days yet';
}

function renderDetail() {
  const p = state.progress;
  if (!p) return;
  $('#dTitle').textContent = parse(state.date).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  const st = effStatus(state.date, p);
  let msg;
  if (st === 'none') msg = state.date > todayStr() ? 'Nothing planned yet.' : 'No tasks on this day.';
  else if (st === 'planned') msg = `Planned: ${plural(p.total, 'task', 'tasks')}.`;
  else {
    msg = `${STATUS_NAME[st]} day. ${p.done} of ${plural(p.total, 'task', 'tasks')} done (${p.pct}%).`;
    const missed = p.imp_total - p.imp_done;
    if (missed > 0) msg += ` ${plural(missed, 'important task is', 'important tasks are')} not done.`;
  }
  $('#dStatus').textContent = msg;

  const ul = $('#dList');
  ul.replaceChildren(...state.tasks.map((t) => {
    const li = el('li', { class: t.done ? 'done' : '' },
      el('span', { class: 'mark', html: ICON.check }),
      el('span', { class: 'txt' }, t.title));
    if (t.important) li.append(el('span', { class: 'imp', title: 'Important', html: ICON.star }));
    return li;
  }));
  ul.hidden = state.tasks.length === 0;
}

async function selectDate(ds) {
  state.date = ds;
  $$('.day').forEach((b) => {
    const on = b.dataset.date === ds;
    b.classList.toggle('selected', on);
    b.setAttribute('aria-pressed', String(on));
  });
  await loadDay();
}

/* ------------ Tabs ------------ */
function showTab(tab) {
  state.tab = tab;
  $('#view-tasks').hidden = tab !== 'tasks';
  $('#view-calendar').hidden = tab !== 'calendar';
  $$('.tab').forEach((b) => {
    const on = b.dataset.tab === tab;
    b.classList.toggle('active', on);
    b.setAttribute('aria-current', on ? 'page' : 'false');
  });
  if (tab === 'calendar') {
    const d = parse(state.date);
    state.cal = { y: d.getFullYear(), m: d.getMonth() };
    renderCalendar();
    renderDetail();
    loadMonth();
  }
  window.scrollTo(0, 0);
}

/* ------------ Start-up ------------ */
function goToDate(ds) { state.date = ds; if (state.tab === 'calendar') { const d = parse(ds); state.cal = { y: d.getFullYear(), m: d.getMonth() }; loadMonth(); } loadDay(); }

function init() {
  $$('[data-icon]').forEach((n) => { n.innerHTML = ICON[n.dataset.icon]; });

  const bar = $('#pBar');
  PROGRESS_TICKS.forEach((v) => bar.append(el('i', { class: 'tick', style: `left:${v}%`, title: v + '%' })));

  $('#addForm').addEventListener('submit', addTask);
  $('#newImportant').addEventListener('click', () => setNewImportant(!state.newImportant));
  $('#prevDay').addEventListener('click', () => goToDate(addDays(state.date, -1)));
  $('#nextDay').addEventListener('click', () => goToDate(addDays(state.date, 1)));
  $('#goToday').addEventListener('click', () => goToDate(todayStr()));
  const picker = $('#datePicker');
  picker.addEventListener('change', () => { if (picker.value) goToDate(picker.value); });
  picker.addEventListener('click', () => { try { picker.showPicker(); } catch (_) {} });

  $('#prevMonth').addEventListener('click', () => shiftMonth(-1));
  $('#nextMonth').addEventListener('click', () => shiftMonth(1));
  $('#calToday').addEventListener('click', () => goToDate(todayStr()));
  $('#openDay').addEventListener('click', () => showTab('tasks'));
  $$('.tab').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));

  const dock = $('#dock');
  const syncDock = () => document.documentElement.style.setProperty('--dock-h', dock.offsetHeight + 'px');
  if ('ResizeObserver' in window) new ResizeObserver(syncDock).observe(dock);
  syncDock();

  // Come back to the app (e.g. on your phone the next morning): refresh what is on screen.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden || $('.task.editing')) return;
    loadDay();
    if (state.tab === 'calendar') loadMonth();
  });

  loadDay();
}

init();
})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Start-up
# ---------------------------------------------------------------------------
init_db()


def local_ip():
    """The address of this computer on your Wi-Fi (used to open the app on a phone)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # nothing is sent; this only picks the active network
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print()
    print("  To-Do List is running")
    print(f"  This computer : http://localhost:{port}")
    print(f"  Phone         : http://{local_ip()}:{port}   (phone must be on the same Wi-Fi)")
    print("  Stop the app  : press Ctrl+C")
    print()
    app.run(host="0.0.0.0", port=port, debug=False)
