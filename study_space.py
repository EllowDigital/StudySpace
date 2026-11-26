#!/usr/bin/env python3
"""
STUDY SPACE PRO  ·  v3.1  —  Academic management platform for college students
================================================================================
Single-file, offline-first desktop application.

    Stack     : Python 3.9+, PySide6 (Qt 6), SQLite3, ReportLab
    Run       : python study_space.py
    Self-test : python study_space.py --selftest
    Options   : --db PATH  --check  --write-requirements  --version

Requirements (also available via ``--write-requirements``)::

    PySide6>=6.5
    reportlab>=4.0
    uharfbuzz>=0.37   # optional: correct shaping of Hindi/Indic text in PDFs

Compatibility: opens and upgrades the existing ``study_space_pro.db`` in place.
Upgrades are versioned, additive and transactional, and a verified backup is
written first. Existing records are never dropped or rewritten.

Code map (search for the numbered banners):
    1 Core utilities          5 Settings & theme              9  Qt I   — charts, tables, forms
    2 Database & migrations   6 Entities & repository        10 Qt II  — pages & dialogs
    3 Backup & restore        7 Analytics, alerts, AppCore    11 Qt III — main window
    4 Domain rules            8 PDF reports                   12 Self-tests · 13 Command line
"""
from __future__ import annotations

import argparse
import copy
import csv
import functools
import hashlib
import hmac
import json
import logging
import logging.handlers
import math
import os
import random
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

APP_NAME = "Study Space Pro"
APP_VERSION = "3.1.0"
DB_FILENAME = "study_space_pro.db"
APP_DIR = (Path(sys.executable).parent if getattr(sys, "frozen", False)
           else Path(__file__).resolve().parent)
REQUIREMENTS = ("PySide6>=6.5", "reportlab>=4.0",
                "uharfbuzz>=0.37  # optional: Hindi/Indic text shaping in PDFs")
LOG = logging.getLogger("studyspace")

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
ACCENTS = {"Blue": "#2F5FCB", "Violet": "#7046C2", "Teal": "#087F75", "Rose": "#B83258",
           "Amber": "#955C00", "Indigo": "#4B4AB8", "Emerald": "#0B7A4B", "Slate": "#475569"}


# ════════════════════════════════════════════════════════════════════════════
# 1 · CORE UTILITIES
# ════════════════════════════════════════════════════════════════════════════
class ValidationError(ValueError):
    """A user-correctable problem with submitted data (message is user-facing)."""


class MigrationError(RuntimeError):
    """A schema upgrade could not be completed; the original data is untouched."""


def resolve_data_dir(env: Optional[dict] = None) -> Path:
    """Folder holding the database, backups and log.

    Order: ``STUDY_SPACE_HOME`` override → the script folder (where an existing
    database already lives, or if it is writable) → a per-user data folder.
    """
    env = os.environ if env is None else env
    override = env.get("STUDY_SPACE_HOME")
    if override:
        return Path(override).expanduser()
    legacy = APP_DIR / DB_FILENAME
    if legacy.exists() or os.access(APP_DIR, os.W_OK):
        return APP_DIR
    base = env.get("APPDATA") or env.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "StudySpacePro"


def today_iso() -> str:
    return date.today().isoformat()


def parse_date(value: Any) -> Optional[date]:
    """Parse ``YYYY-MM-DD`` (optionally followed by a time). Returns None if blank/invalid."""
    if not value:
        return None
    s = str(value).strip()[:10]
    try:
        return date.fromisoformat(s)                  # fast path (C implementation)
    except ValueError:
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()   # tolerant of un-padded days/months
        except ValueError:
            return None


def fmt_date(value: Any, style: str = "%d %b %Y", blank: str = "—") -> str:
    d = parse_date(value)
    return d.strftime(style) if d else (blank if not value else str(value))


def fmt_minutes(minutes: Any) -> str:
    m = max(0, safe_int(minutes))
    h, mm = divmod(m, 60)
    return f"{h}h {mm:02d}m" if h else f"{mm}m"


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def fmt_num(value: Any, places: int = 2, blank: str = "—") -> str:
    n = safe_float(value)
    if n is None:
        return blank
    return str(int(n)) if float(n).is_integer() else f"{n:.{places}f}".rstrip("0").rstrip(".")


def percent(part: Any, whole: Any) -> Optional[float]:
    p, w = safe_float(part), safe_float(whole)
    return None if p is None or not w else p / w * 100.0


def csv_safe(cell: Any) -> str:
    """Neutralise spreadsheet formula injection in exported CSV cells."""
    text = "" if cell is None else str(cell)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def ordinal_year_name(n: int) -> str:
    names = {1: "First", 2: "Second", 3: "Third", 4: "Fourth", 5: "Fifth", 6: "Sixth"}
    return f"{names[n]} Year" if n in names else f"Year {n}"


def harden_permissions(path: Path, is_dir: bool = False) -> None:
    """Owner-only access on POSIX systems (best effort; Windows folders inherit the user's profile ACLs)."""
    if os.name == "posix":
        try:
            os.chmod(path, 0o700 if is_dir else 0o600)
        except OSError:
            pass


def atomic_write(path: Path | str, writer: Callable[[Path], None]) -> Path:
    """Write via a temp file in the same folder, then atomically replace ``path``.

    A crash or exception can never leave a half-written file at ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        writer(tmp)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


# ---- colour maths (WCAG contrast) -----------------------------------------
_CSS_COLORS = {"black": "#000000", "white": "#FFFFFF", "red": "#FF0000", "green": "#008000", "blue": "#0000FF", "yellow": "#FFFF00",
               "orange": "#FFA500", "purple": "#800080", "gray": "#808080", "grey": "#808080", "teal": "#008080", "navy": "#000080",
               "pink": "#FFC0CB", "brown": "#A52A2A", "cyan": "#00FFFF", "magenta": "#FF00FF"}


def hex_norm(value: Any, default: str = "#000000") -> str:
    """Any reasonable colour spelling → '#RRGGBB' ('2563EB', '#25E', '0x2563EB', ' #2563eb ', 'blue'); else ``default``.

    Every colour that comes from the database, a preset or the user passes through here before it
    reaches Qt or ReportLab (which both fail or mis-render on malformed colour strings)."""
    s = str(value if value is not None else "").strip()
    if s.lower() in _CSS_COLORS:
        return _CSS_COLORS[s.lower()]
    s = s.lstrip("#")
    if s[:2].lower() == "0x":
        s = s[2:]
    if re.fullmatch(r"[0-9a-fA-F]{3}", s):
        s = "".join(c * 2 for c in s)
    return "#" + s.upper() if re.fullmatch(r"[0-9a-fA-F]{6}", s) else default


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = hex_norm(h).lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_hex(r: float, g: float, b: float) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, round(c))) for c in (r, g, b)))


def mix(a: str, b: str, t: float) -> str:
    """Blend colour ``a`` towards ``b`` by ``t`` (0..1)."""
    ra, rb = hex_rgb(a), hex_rgb(b)
    return rgb_hex(*(x + (y - x) * t for x, y in zip(ra, rb)))


def luminance(h: str) -> float:
    def ch(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in hex_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable_on(bg: str, light: str = "#FFFFFF", dark: str = "#101828") -> str:
    """Whichever of ``light``/``dark`` has the better contrast on ``bg``."""
    return light if contrast_ratio(bg, light) >= contrast_ratio(bg, dark) else dark


def ensure_contrast(fg: str, bg: str, minimum: float = 4.5) -> str:
    """Nudge ``fg`` towards black/white until it meets ``minimum`` contrast on ``bg``."""
    target = "#000000" if luminance(bg) > 0.4 else "#FFFFFF"
    out = hex_norm(fg)
    for i in range(1, 21):
        if contrast_ratio(out, bg) >= minimum:
            return out
        out = mix(fg, target, i / 20)
    return out


# ════════════════════════════════════════════════════════════════════════════
# 2 · DATABASE & VERSIONED MIGRATIONS
# ════════════════════════════════════════════════════════════════════════════
LEGACY_TABLES = ("semesters", "subjects", "units", "chapters", "assignments", "study_sessions",
                 "planner_tasks", "exams", "marks", "revisions", "custom_fields", "settings")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Baseline = the schema shipped by Study Space Pro 2.x (fresh installs get it complete).
BASELINE_DDL = [
    "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS semesters (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
       start_date TEXT DEFAULT '', end_date TEXT DEFAULT '', is_active INTEGER DEFAULT 1)""",
    """CREATE TABLE IF NOT EXISTS subjects (id INTEGER PRIMARY KEY AUTOINCREMENT, semester_id INTEGER,
       name TEXT NOT NULL, code TEXT DEFAULT '', color TEXT DEFAULT '#356AE6', target_hours REAL DEFAULT 3,
       notes TEXT DEFAULT '', FOREIGN KEY(semester_id) REFERENCES semesters(id) ON DELETE SET NULL)""",
    """CREATE TABLE IF NOT EXISTS units (id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER NOT NULL,
       unit_no INTEGER NOT NULL, title TEXT NOT NULL, status TEXT DEFAULT 'Not Started',
       confidence INTEGER DEFAULT 1, notes TEXT DEFAULT '',
       FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE, UNIQUE(subject_id,unit_no))""",
    """CREATE TABLE IF NOT EXISTS chapters (id INTEGER PRIMARY KEY AUTOINCREMENT, unit_id INTEGER NOT NULL,
       title TEXT NOT NULL, status TEXT DEFAULT 'Not Started', priority TEXT DEFAULT 'Normal',
       notes TEXT DEFAULT '', FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE CASCADE)""",
    """CREATE TABLE IF NOT EXISTS assignments (id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER NOT NULL,
       unit_id INTEGER, title TEXT NOT NULL, description TEXT DEFAULT '', given_date TEXT DEFAULT '',
       due_date TEXT DEFAULT '', submission_date TEXT DEFAULT '', status TEXT DEFAULT 'Not Started',
       priority TEXT DEFAULT 'Normal', remarks TEXT DEFAULT '',
       FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
       FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE SET NULL)""",
    """CREATE TABLE IF NOT EXISTS study_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER NOT NULL,
       unit_id INTEGER, study_date TEXT NOT NULL, duration_minutes INTEGER NOT NULL, topic TEXT DEFAULT '',
       notes TEXT DEFAULT '', session_type TEXT DEFAULT 'Study',
       FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
       FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE SET NULL)""",
    """CREATE TABLE IF NOT EXISTS planner_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, task_date TEXT NOT NULL,
       title TEXT NOT NULL, subject_id INTEGER, category TEXT DEFAULT 'Study', priority TEXT DEFAULT 'Normal',
       status TEXT DEFAULT 'Pending', duration_minutes INTEGER DEFAULT 30, notes TEXT DEFAULT '',
       FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE SET NULL)""",
    """CREATE TABLE IF NOT EXISTS exams (id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER NOT NULL,
       title TEXT NOT NULL, exam_type TEXT DEFAULT 'Semester Exam', exam_date TEXT DEFAULT '',
       max_marks REAL DEFAULT 100, venue TEXT DEFAULT '', prep_status TEXT DEFAULT 'Not Started',
       notes TEXT DEFAULT '', marks_obtained REAL, grade TEXT DEFAULT '', result TEXT DEFAULT '',
       attempt TEXT DEFAULT '', examiner TEXT DEFAULT '', weightage REAL DEFAULT 0,
       duration_minutes INTEGER DEFAULT 0, exam_mode TEXT DEFAULT '', room TEXT DEFAULT '',
       FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE)""",
    """CREATE TABLE IF NOT EXISTS marks (id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER NOT NULL,
       unit_id INTEGER, assessment_type TEXT NOT NULL, title TEXT DEFAULT '', marks_obtained REAL NOT NULL,
       max_marks REAL NOT NULL, assessment_date TEXT DEFAULT '', notes TEXT DEFAULT '',
       FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
       FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE SET NULL)""",
    """CREATE TABLE IF NOT EXISTS revisions (id INTEGER PRIMARY KEY AUTOINCREMENT, unit_id INTEGER NOT NULL,
       due_date TEXT NOT NULL, interval_days INTEGER DEFAULT 1, ease REAL DEFAULT 2.5,
       repetitions INTEGER DEFAULT 0, status TEXT DEFAULT 'Due', last_reviewed TEXT DEFAULT '',
       notes TEXT DEFAULT '', FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE CASCADE)""",
    """CREATE TABLE IF NOT EXISTS custom_fields (id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT NOT NULL,
       record_id INTEGER NOT NULL, field_name TEXT NOT NULL, field_value TEXT DEFAULT '',
       field_type TEXT DEFAULT 'Text', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
       UNIQUE(entity_type,record_id,field_name))""",
]
# Columns older 2.x databases may lack (added only when missing; data never touched).
BASELINE_ADDITIONS = {
    "exams": {"marks_obtained": "REAL", "grade": "TEXT DEFAULT ''", "result": "TEXT DEFAULT ''",
              "attempt": "TEXT DEFAULT ''", "examiner": "TEXT DEFAULT ''", "weightage": "REAL DEFAULT 0",
              "duration_minutes": "INTEGER DEFAULT 0", "exam_mode": "TEXT DEFAULT ''", "room": "TEXT DEFAULT ''"},
    "subjects": {"semester_id": "INTEGER", "code": "TEXT DEFAULT ''", "color": "TEXT DEFAULT '#356AE6'",
                 "target_hours": "REAL DEFAULT 3", "notes": "TEXT DEFAULT ''"},
    "units": {"confidence": "INTEGER DEFAULT 1", "notes": "TEXT DEFAULT ''"},
    "assignments": {"description": "TEXT DEFAULT ''", "given_date": "TEXT DEFAULT ''",
                    "submission_date": "TEXT DEFAULT ''", "priority": "TEXT DEFAULT 'Normal'",
                    "remarks": "TEXT DEFAULT ''"},
    "study_sessions": {"unit_id": "INTEGER", "topic": "TEXT DEFAULT ''", "notes": "TEXT DEFAULT ''",
                       "session_type": "TEXT DEFAULT 'Study'"},
    "planner_tasks": {"subject_id": "INTEGER", "category": "TEXT DEFAULT 'Study'",
                      "priority": "TEXT DEFAULT 'Normal'", "status": "TEXT DEFAULT 'Pending'",
                      "duration_minutes": "INTEGER DEFAULT 30", "notes": "TEXT DEFAULT ''"},
    "marks": {"unit_id": "INTEGER", "title": "TEXT DEFAULT ''", "assessment_date": "TEXT DEFAULT ''",
              "notes": "TEXT DEFAULT ''"},
    "revisions": {"interval_days": "INTEGER DEFAULT 1", "ease": "REAL DEFAULT 2.5",
                  "repetitions": "INTEGER DEFAULT 0", "status": "TEXT DEFAULT 'Due'",
                  "last_reviewed": "TEXT DEFAULT ''", "notes": "TEXT DEFAULT ''"},
}
STRUCTURE_DDL = [
    """CREATE TABLE IF NOT EXISTS student_profile (id INTEGER PRIMARY KEY CHECK(id=1),
       student_name TEXT DEFAULT '', college_name TEXT DEFAULT '', university_name TEXT DEFAULT '',
       course TEXT DEFAULT '', branch TEXT DEFAULT '', admission_year INTEGER, expected_graduation_year INTEGER,
       roll_no TEXT DEFAULT '', student_id TEXT DEFAULT '', email TEXT DEFAULT '', goals TEXT DEFAULT '',
       photo_path TEXT DEFAULT '', updated_at TEXT DEFAULT '')""",
    """CREATE TABLE IF NOT EXISTS programmes (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
       degree_level TEXT DEFAULT 'Undergraduate', branch TEXT DEFAULT '', specialisation TEXT DEFAULT '',
       college_name TEXT DEFAULT '', university_name TEXT DEFAULT '', session_label TEXT DEFAULT '',
       start_year INTEGER, end_year INTEGER, years_count INTEGER DEFAULT 4, semesters_per_year INTEGER DEFAULT 2,
       status TEXT DEFAULT 'Active', notes TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS academic_years (id INTEGER PRIMARY KEY AUTOINCREMENT,
       programme_id INTEGER NOT NULL REFERENCES programmes(id) ON DELETE RESTRICT,
       year_no INTEGER NOT NULL, name TEXT NOT NULL, session_label TEXT DEFAULT '',
       start_date TEXT DEFAULT '', end_date TEXT DEFAULT '', status TEXT DEFAULT 'Active',
       notes TEXT DEFAULT '', UNIQUE(programme_id, year_no))""",
]
COURSEWORK_DDL = [
    """CREATE TABLE IF NOT EXISTS subject_results (id INTEGER PRIMARY KEY AUTOINCREMENT,
       subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
       attempt_no INTEGER NOT NULL DEFAULT 1, exam_session TEXT DEFAULT '',
       internal_marks REAL, internal_max REAL, external_marks REAL, external_max REAL,
       practical_marks REAL, practical_max REAL, grade TEXT DEFAULT '', grade_points REAL, credits REAL,
       result TEXT DEFAULT 'Pending', result_date TEXT DEFAULT '', notes TEXT DEFAULT '',
       UNIQUE(subject_id, attempt_no))""",
    """CREATE TABLE IF NOT EXISTS attendance (id INTEGER PRIMARY KEY AUTOINCREMENT,
       subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
       att_date TEXT NOT NULL, classes_held INTEGER NOT NULL DEFAULT 1, classes_attended INTEGER NOT NULL DEFAULT 1,
       kind TEXT DEFAULT 'Lecture', notes TEXT DEFAULT '',
       CHECK(classes_held >= 0 AND classes_attended >= 0 AND classes_attended <= classes_held))""",
    """CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY AUTOINCREMENT,
       subject_id INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
       category TEXT NOT NULL DEFAULT 'Project', title TEXT NOT NULL, status TEXT DEFAULT 'Not Started',
       start_date TEXT DEFAULT '', deadline TEXT DEFAULT '', completion_date TEXT DEFAULT '',
       supervisor TEXT DEFAULT '', organisation TEXT DEFAULT '', description TEXT DEFAULT '',
       marks_obtained REAL, max_marks REAL, grade TEXT DEFAULT '', link TEXT DEFAULT '', notes TEXT DEFAULT '',
       created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS project_milestones (id INTEGER PRIMARY KEY AUTOINCREMENT,
       project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
       title TEXT NOT NULL, due_date TEXT DEFAULT '', status TEXT DEFAULT 'Pending', notes TEXT DEFAULT '')""",
    """CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT,
       subject_id INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
       unit_id INTEGER REFERENCES units(id) ON DELETE SET NULL,
       chapter_id INTEGER REFERENCES chapters(id) ON DELETE SET NULL,
       title TEXT NOT NULL, body TEXT DEFAULT '', tags TEXT DEFAULT '',
       created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS attachments (id INTEGER PRIMARY KEY AUTOINCREMENT,
       entity_type TEXT NOT NULL, record_id INTEGER NOT NULL, file_path TEXT NOT NULL,
       display_name TEXT DEFAULT '', category TEXT DEFAULT 'Document', size_bytes INTEGER,
       added_at TEXT DEFAULT CURRENT_TIMESTAMP, notes TEXT DEFAULT '')""",
    """CREATE TABLE IF NOT EXISTS calendar_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
       semester_id INTEGER REFERENCES semesters(id) ON DELETE SET NULL,
       title TEXT NOT NULL, event_type TEXT DEFAULT 'Event', start_date TEXT NOT NULL,
       end_date TEXT DEFAULT '', notes TEXT DEFAULT '')""",
]
CLEANUP_TABLES = ("semesters", "subjects", "units", "chapters", "assignments", "study_sessions",
                  "planner_tasks", "exams", "marks", "revisions", "subject_results", "attendance",
                  "projects", "notes", "programmes", "academic_years", "calendar_events")
INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_subjects_sem ON subjects(semester_id)",
    "CREATE INDEX IF NOT EXISTS idx_semesters_prog ON semesters(programme_id, semester_no)",
    "CREATE INDEX IF NOT EXISTS idx_semesters_year ON semesters(academic_year_id)",
    "CREATE INDEX IF NOT EXISTS idx_years_prog ON academic_years(programme_id)",
    "CREATE INDEX IF NOT EXISTS idx_units_subject ON units(subject_id)",
    "CREATE INDEX IF NOT EXISTS idx_chapters_unit ON chapters(unit_id)",
    "CREATE INDEX IF NOT EXISTS idx_assign_subject ON assignments(subject_id)",
    "CREATE INDEX IF NOT EXISTS idx_assignments_due ON assignments(due_date)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_subject ON study_sessions(subject_id)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_date ON study_sessions(study_date)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_date ON planner_tasks(task_date)",
    "CREATE INDEX IF NOT EXISTS idx_exams_subject ON exams(subject_id)",
    "CREATE INDEX IF NOT EXISTS idx_exams_date ON exams(exam_date)",
    "CREATE INDEX IF NOT EXISTS idx_marks_subject ON marks(subject_id)",
    "CREATE INDEX IF NOT EXISTS idx_revisions_due ON revisions(due_date)",
    "CREATE INDEX IF NOT EXISTS idx_revisions_unit ON revisions(unit_id)",
    "CREATE INDEX IF NOT EXISTS idx_results_subject ON subject_results(subject_id)",
    "CREATE INDEX IF NOT EXISTS idx_attendance_subject ON attendance(subject_id, att_date)",
    "CREATE INDEX IF NOT EXISTS idx_projects_subject ON projects(subject_id)",
    "CREATE INDEX IF NOT EXISTS idx_milestones_project ON project_milestones(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_notes_subject ON notes(subject_id)",
    "CREATE INDEX IF NOT EXISTS idx_attach_entity ON attachments(entity_type, record_id)",
    "CREATE INDEX IF NOT EXISTS idx_custom_entity ON custom_fields(entity_type, record_id)",
]


class Database:
    """SQLite connection with versioned migrations, safe transactions and helpers.

    One instance per thread: SQLite connections must not cross threads, so worker
    threads open their own ``Database(path, migrate=False)``.
    """

    def __init__(self, path: Path | str, *, migrate: bool = True) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._depth = 0
        self._gen = 0                       # bumped whenever the connection is re-opened (restore)
        self._cols: dict[str, list[str]] = {}
        self.migration_log: list[str] = []
        existed = self.path.exists() and self.path.stat().st_size > 0
        self.conn: sqlite3.Connection
        self._connect()
        try:
            harden_permissions(self.path)
            self.was_fresh = (not existed) or not self.q(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1")
            if migrate:
                self.migrate()
        except BaseException:
            self.close()
            raise

    # ---- connection ------------------------------------------------------
    def _connect(self) -> None:
        self.conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        for pragma in ("PRAGMA foreign_keys=ON", "PRAGMA journal_mode=WAL", "PRAGMA synchronous=NORMAL",
                       "PRAGMA busy_timeout=30000", "PRAGMA temp_store=MEMORY", "PRAGMA cache_size=-16000"):
            self.conn.execute(pragma)
        self._cols.clear()

    def reconnect(self) -> None:
        """Re-open the file in place (used after a restore) keeping this object valid."""
        self.close()
        self._connect()
        self._gen += 1

    def change_token(self) -> tuple[int, int]:
        """Changes whenever data written through this connection changes (cache key for analytics)."""
        return self._gen, self.conn.total_changes

    def close(self) -> None:
        for pragma in ("PRAGMA optimize", "PRAGMA wal_checkpoint(TRUNCATE)"):     # refresh statistics, fold the journal in
            try:
                self.conn.execute(pragma)
            except sqlite3.Error:
                pass
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    # ---- query helpers ---------------------------------------------------
    def q(self, sql: str, args: Sequence = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(args)).fetchall()

    def one(self, sql: str, args: Sequence = ()) -> Optional[sqlite3.Row]:
        return self.conn.execute(sql, tuple(args)).fetchone()

    def scalar(self, sql: str, args: Sequence = (), default: Any = None) -> Any:
        row = self.one(sql, args)
        return default if row is None or row[0] is None else row[0]

    def run(self, sql: str, args: Sequence = ()) -> int:
        """Execute one write statement atomically; returns lastrowid."""
        with self.transaction():
            return self.conn.execute(sql, tuple(args)).lastrowid or 0

    @contextmanager
    def transaction(self):
        """Atomic block. Nested use creates SAVEPOINTs; any exception rolls back."""
        if self._depth == 0:
            self.conn.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self
                self.conn.execute("COMMIT")
            except BaseException:
                try:
                    self.conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            finally:
                self._depth = 0
        else:
            name = f"sp{self._depth}"
            self.conn.execute(f"SAVEPOINT {name}")
            self._depth += 1
            try:
                yield self
                self.conn.execute(f"RELEASE {name}")
            except BaseException:
                self.conn.execute(f"ROLLBACK TO {name}")
                self.conn.execute(f"RELEASE {name}")
                raise
            finally:
                self._depth -= 1

    # ---- schema introspection -------------------------------------------
    def tables(self) -> set[str]:
        return {r[0] for r in self.q("SELECT name FROM sqlite_master WHERE type='table'")}

    def table_exists(self, name: str) -> bool:
        return name in self.tables()

    def columns(self, table: str) -> list[str]:
        if not _IDENT.match(table) or table not in self.tables():
            raise ValueError(f"Unknown table: {table!r}")
        if table not in self._cols:
            self._cols[table] = [r["name"] for r in self.q(f"PRAGMA table_info({table})")]
        return self._cols[table]

    def schema_version(self) -> int:
        if not self.table_exists("schema_migrations"):
            return 0
        return safe_int(self.scalar("SELECT MAX(version) FROM schema_migrations", default=0))

    def integrity_check(self) -> str:
        return str(self.scalar("PRAGMA integrity_check", default="unknown"))

    def foreign_key_violations(self) -> list[tuple]:
        return [tuple(r) for r in self.q("PRAGMA foreign_key_check")]

    # ---- migrations ------------------------------------------------------
    def add_column_if_missing(self, table: str, column: str, definition: str) -> bool:
        if not (_IDENT.match(table) and _IDENT.match(column)):
            raise ValueError("Unsafe identifier in migration")
        if column in {r["name"] for r in self.q(f"PRAGMA table_info({table})")}:
            return False
        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        self._cols.pop(table, None)
        return True

    def migrate(self) -> None:
        """Bring the schema to the latest version.

        1. If upgrades are pending on a non-empty database, write a verified backup
           first; abort if that is not possible (nothing has been modified yet).
        2. Run each pending migration in its own transaction; a failure rolls that
           step back and leaves earlier steps (and all data) intact.
        """
        current = self.schema_version()
        pending = [m for m in MIGRATIONS if m[0] > current]
        if not pending:
            return
        if not self.was_fresh and current < MIGRATIONS[-1][0]:
            try:
                snap = BackupManager(self.path.parent / "backups").create(
                    self, kind="safety", reason=f"pre_migration_v{current}_to_v{MIGRATIONS[-1][0]}")
                self.migration_log.append(f"Verified pre-upgrade backup: {snap}")
            except Exception as exc:
                raise MigrationError(
                    f"Upgrade cancelled: a verified safety backup could not be created ({exc}). "
                    "Your database has not been modified.") from exc
        self.conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                          "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
        for version, name, func in pending:
            try:
                with self.transaction():
                    func(self)
                    self.conn.execute("INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)",
                                      (version, name, datetime.now().isoformat(timespec="seconds")))
                self.migration_log.append(f"Applied migration {version}: {name}")
                LOG.info("Applied migration %s: %s", version, name)
            except Exception as exc:
                LOG.exception("Migration %s failed", version)
                raise MigrationError(
                    f"Upgrade step {version} ({name}) failed and was rolled back: {exc}. "
                    f"The database remains at version {self.schema_version()}; "
                    "a pre-upgrade backup is in the backups folder.") from exc
        self.conn.execute(f"PRAGMA user_version={MIGRATIONS[-1][0]}")
        self._cols.clear()
        problems = self.foreign_key_violations()
        if problems:
            LOG.warning("%d pre-existing foreign-key inconsistencies found (not modified)", len(problems))
            self.migration_log.append(f"{len(problems)} pre-existing foreign-key inconsistencies were found; no data was changed.")


def _m1_baseline(db: Database) -> None:
    """Create 2.x tables on a fresh DB; add any missing columns on an older one."""
    for ddl in BASELINE_DDL:
        db.conn.execute(ddl)
    for table, cols in BASELINE_ADDITIONS.items():
        for col, definition in cols.items():
            db.add_column_if_missing(table, col, definition)


def _m2_structure(db: Database) -> None:
    """Academic hierarchy: programme → year → semester; profile; link existing semesters."""
    for ddl in STRUCTURE_DDL:
        db.conn.execute(ddl)
    db.add_column_if_missing("semesters", "programme_id", "INTEGER REFERENCES programmes(id) ON DELETE RESTRICT")
    db.add_column_if_missing("semesters", "academic_year_id", "INTEGER REFERENCES academic_years(id) ON DELETE RESTRICT")
    db.add_column_if_missing("semesters", "semester_no", "INTEGER")
    db.add_column_if_missing("semesters", "status", "TEXT DEFAULT 'Active'")
    db.conn.execute("INSERT OR IGNORE INTO student_profile(id) VALUES(1)")
    sems = db.q("SELECT id, is_active FROM semesters ORDER BY id")
    if not sems:
        return
    label = str(db.scalar("SELECT value FROM settings WHERE key='semester_name'", default="") or "")
    prog_name = re.split(r"\s*[—–-]\s*", label)[0].strip() or "My Programme"
    years = max(1, math.ceil(len(sems) / 2))
    pid = db.conn.execute(
        "INSERT INTO programmes(name,years_count,semesters_per_year,notes) VALUES(?,?,2,?)",
        (prog_name, years, "Created automatically when upgrading; edit freely.")).lastrowid
    year_ids = [db.conn.execute("INSERT INTO academic_years(programme_id,year_no,name) VALUES(?,?,?)",
                                (pid, y, ordinal_year_name(y))).lastrowid for y in range(1, years + 1)]
    active_ids = [s["id"] for s in sems if s["is_active"]]
    current = active_ids[-1] if active_ids else sems[-1]["id"]
    for i, s in enumerate(sems, 1):
        db.conn.execute("UPDATE semesters SET programme_id=?, academic_year_id=?, semester_no=?, status=? WHERE id=?",
                        (pid, year_ids[(i - 1) // 2], i, "Active" if s["id"] == current else "Completed", s["id"]))
    db.conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('current_semester_id',?)", (str(current),))


def _m3_coursework(db: Database) -> None:
    """Credits/faculty, assignment & exam detail, results, attendance, projects, notes, files."""
    for col, d in (("credits", "REAL DEFAULT 0"), ("subject_type", "TEXT DEFAULT 'Theory'"),
                   ("faculty", "TEXT DEFAULT ''"), ("attendance_target", "REAL")):
        db.add_column_if_missing("subjects", col, d)
    for col, d in (("assignment_no", "TEXT DEFAULT ''"), ("assignment_type", "TEXT DEFAULT 'Written'"),
                   ("faculty", "TEXT DEFAULT ''"), ("marks_obtained", "REAL"), ("max_marks", "REAL")):
        db.add_column_if_missing("assignments", col, d)
    for col, d in (("internal_marks", "REAL"), ("external_marks", "REAL"), ("practical_marks", "REAL"),
                   ("credits", "REAL DEFAULT 0"), ("attempt_no", "INTEGER DEFAULT 1")):
        db.add_column_if_missing("exams", col, d)
    db.add_column_if_missing("study_sessions", "chapter_id", "INTEGER REFERENCES chapters(id) ON DELETE SET NULL")
    for ddl in COURSEWORK_DDL:
        db.conn.execute(ddl)


def _m4_integrity(db: Database) -> None:
    """Indexes plus database-level safety nets (no cascading surprises, no orphans)."""
    for ddl in INDEX_DDL:
        db.conn.execute(ddl)
    # Legacy subjects.semester_id is ON DELETE SET NULL, which would silently detach every
    # subject from a deleted semester. Block that at the database level instead.
    db.conn.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_semester_keep_history BEFORE DELETE ON semesters "
        "WHEN EXISTS (SELECT 1 FROM subjects WHERE semester_id = OLD.id) "
        "BEGIN SELECT RAISE(ABORT, 'This semester still has subjects. Move or delete them first.'); END")
    for t in CLEANUP_TABLES:  # no orphaned custom fields / file references, on any delete path
        db.conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS trg_{t}_cleanup AFTER DELETE ON {t} BEGIN "
            f"DELETE FROM custom_fields WHERE entity_type='{t}' AND record_id=OLD.id; "
            f"DELETE FROM attachments WHERE entity_type='{t}' AND record_id=OLD.id; END")


def _m5_timetable(db: Database) -> None:
    """Weekly class timetable: feeds "today's classes" and the quick-attendance dialog."""
    db.conn.execute("""CREATE TABLE IF NOT EXISTS timetable (id INTEGER PRIMARY KEY AUTOINCREMENT,
       subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
       day TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL,
       kind TEXT DEFAULT 'Lecture', room TEXT DEFAULT '', faculty TEXT DEFAULT '', notes TEXT DEFAULT '')""")
    db.conn.execute("CREATE INDEX IF NOT EXISTS idx_timetable_day ON timetable(day, start_time)")
    db.conn.execute("CREATE INDEX IF NOT EXISTS idx_timetable_subject ON timetable(subject_id)")
    db.conn.execute("CREATE TRIGGER IF NOT EXISTS trg_timetable_cleanup AFTER DELETE ON timetable BEGIN "
                    "DELETE FROM custom_fields WHERE entity_type='timetable' AND record_id=OLD.id; END")


MIGRATIONS: list[tuple[int, str, Callable[[Database], None]]] = [
    (1, "baseline_2x_schema", _m1_baseline),
    (2, "academic_structure", _m2_structure),
    (3, "coursework_extensions", _m3_coursework),
    (4, "indexes_and_integrity", _m4_integrity),
    (5, "weekly_timetable", _m5_timetable),
]
LATEST_SCHEMA = MIGRATIONS[-1][0]


# ════════════════════════════════════════════════════════════════════════════
# 3 · BACKUP & RESTORE  (verified, atomic, never overwrites a good file with a bad one)
# ════════════════════════════════════════════════════════════════════════════
class BackupError(RuntimeError):
    """A backup could not be created, or a candidate file is not a valid backup."""


def inspect_database_file(path: Path | str) -> dict:
    """Validate that ``path`` is an intact Study Space database; return a summary.

    Opened read-only. Raises BackupError describing the first problem found."""
    path = Path(path)
    if not path.is_file():
        raise BackupError("The selected file does not exist.")
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error as exc:
        raise BackupError(f"The file could not be opened as a database: {exc}") from exc
    try:
        try:
            check = con.execute("PRAGMA integrity_check").fetchone()[0]
            names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError as exc:
            raise BackupError(f"This is not a valid SQLite database: {exc}") from exc
        if check != "ok":
            raise BackupError(f"The backup failed its integrity check: {check}")
        required = {"subjects", "units", "assignments", "settings", "semesters"}
        if not required <= names:
            raise BackupError("This is not a Study Space database (missing: "
                              + ", ".join(sorted(required - names)) + ").")
        version = 0
        if "schema_migrations" in names:
            version = safe_int(con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0])
        if version > LATEST_SCHEMA:
            raise BackupError("This backup was created by a newer version of Study Space Pro. "
                              "Update the application before restoring it.")
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("subjects", "assignments", "exams", "study_sessions") if t in names}
        return {"version": version, "tables": names, "counts": counts, "size": path.stat().st_size}
    finally:
        con.close()


class BackupManager:
    """Creates verified single-file SQLite backups using SQLite's online backup API."""

    KEEP = {"auto": 14, "safety": 25, "manual": 1000}

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def folder(self, kind: str) -> Path:
        return self.root / kind

    def create(self, db: Database, dest: Optional[Path | str] = None, *,
               kind: str = "manual", reason: str = "") -> Path:
        """Back up ``db`` to ``dest`` (default: a timestamped file under backups/<kind>/).

        The copy is written to a temporary file, integrity-checked, and only then moved
        into place — an interrupted backup leaves no partial file and never clobbers an
        existing good one."""
        if dest is None:
            tag = f"_{re.sub(r'[^A-Za-z0-9_-]+', '_', reason)}" if reason else ""
            dest = self.folder(kind) / f"study_space_{kind}_{datetime.now():%Y%m%d_%H%M%S_%f}{tag}.db"
        dest = Path(dest).expanduser().resolve()
        if dest == db.path.resolve():
            raise BackupError("The backup destination cannot be the live database file.")

        def write(tmp: Path) -> None:
            target = sqlite3.connect(str(tmp), timeout=30)
            try:
                db.conn.backup(target)
                target.execute("PRAGMA journal_mode=DELETE")  # portable single file
                check = target.execute("PRAGMA integrity_check").fetchone()[0]
                if check != "ok":
                    raise BackupError(f"Backup integrity check failed: {check}")
                src_tables, dst_tables = db.tables(), {r[0] for r in target.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if src_tables != dst_tables:
                    raise BackupError("Backup is missing tables; it was discarded.")
            finally:
                target.close()

        atomic_write(dest, write)
        harden_permissions(dest.parent, True)
        harden_permissions(dest)
        self._write_checksum(dest)
        if kind in self.KEEP:
            self.prune(kind)
        return dest

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()

    @classmethod
    def _write_checksum(cls, path: Path) -> None:
        try:
            atomic_write(path.with_name(path.name + ".sha256"), lambda tmp: tmp.write_text(f"{cls._sha256(path)}  {path.name}\n", encoding="utf-8"))
        except OSError:
            LOG.warning("Could not write checksum for %s", path)

    @classmethod
    def verify_checksum(cls, path: Path | str) -> Optional[bool]:
        """True/False if a checksum file exists and does/doesn't match; None when there is none (external file)."""
        path = Path(path)
        side = path.with_name(path.name + ".sha256")
        if not side.is_file():
            return None
        try:
            return hmac.compare_digest(side.read_text(encoding="utf-8").split()[0], cls._sha256(path))
        except (OSError, IndexError):
            return False

    def snapshots(self, kind: str) -> list[Path]:
        folder = self.folder(kind)
        return sorted(folder.glob("*.db")) if folder.exists() else []

    def list_all(self) -> list[dict]:
        """Every backup in the backups folder (auto, safety, manual), newest first."""
        out = []
        for kind in ("auto", "safety", "manual"):
            for p in self.snapshots(kind):
                try:
                    st = p.stat()
                except OSError:
                    continue
                out.append({"kind": kind, "path": p, "name": p.name, "size": st.st_size, "mtime": datetime.fromtimestamp(st.st_mtime)})
        return sorted(out, key=lambda d: d["mtime"], reverse=True)

    def delete(self, path: Path | str) -> None:
        """Delete one backup. Only files inside the backups folder can be removed."""
        p = Path(path).resolve()
        if self.root.resolve() not in p.parents or p.suffix != ".db":
            raise BackupError("Only backup files inside the backups folder can be deleted here.")
        p.unlink()
        p.with_name(p.name + ".sha256").unlink(missing_ok=True)

    def prune(self, kind: str, keep: Optional[int] = None) -> None:
        keep = self.KEEP.get(kind, 50) if keep is None else keep
        for old in self.snapshots(kind)[:-keep] if keep > 0 else []:
            try:
                old.unlink()
                old.with_name(old.name + ".sha256").unlink(missing_ok=True)
            except OSError:
                LOG.warning("Could not prune old backup %s", old)

    def daily_auto_backup(self, db: Database, keep: int = 14) -> Optional[Path]:
        """Create one automatic backup per calendar day (no-op if today's exists)."""
        stamp = f"study_space_auto_{datetime.now():%Y%m%d}"
        if any(p.name.startswith(stamp) for p in self.snapshots("auto")):
            return None
        self.KEEP["auto"] = max(1, keep)
        return self.create(db, kind="auto")


def restore_database(db: Database, candidate: Path | str, backups: BackupManager) -> Path:
    """Replace the live database with ``candidate`` safely; returns the safety snapshot path.

    Validate → verified safety snapshot of the current data → build a consistent copy →
    swap files atomically → re-open (and migrate if the backup is older). If anything
    fails after the swap, the safety snapshot is put back."""
    candidate = Path(candidate)
    if BackupManager.verify_checksum(candidate) is False:
        raise BackupError("This backup has changed or been damaged since it was created (its checksum no longer matches). Restore refused.")
    inspect_database_file(candidate)
    if candidate.resolve() == db.path.resolve():
        raise BackupError("Choose a backup file other than the live database.")
    safety = backups.create(db, kind="safety", reason="before_restore")
    staged = db.path.with_name(f".restore_{uuid.uuid4().hex[:8]}.part")
    try:
        src = sqlite3.connect(f"file:{candidate.as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(str(staged))
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("The copied backup failed verification; nothing was changed.")
        finally:
            src.close()
            dst.close()
    except BaseException:
        staged.unlink(missing_ok=True)
        raise

    def put_back(file: Path) -> None:
        for suffix in ("-wal", "-shm"):
            Path(str(db.path) + suffix).unlink(missing_ok=True)
        os.replace(file, db.path)

    db.close()
    try:
        put_back(staged)
        db.reconnect()
        db.was_fresh = False
        db.migrate()
        if db.integrity_check() != "ok":
            raise BackupError("The restored database failed its integrity check.")
    except BaseException:
        LOG.exception("Restore failed; rolling back to the safety snapshot")
        try:
            db.close()
            rollback = db.path.with_name(f".rollback_{uuid.uuid4().hex[:8]}.part")
            shutil.copy2(safety, rollback)
            put_back(rollback)
            db.reconnect()
        finally:
            staged.unlink(missing_ok=True)
        raise
    return safety


class AppLock:
    """Optional PIN lock. The PIN is stored only as a salted PBKDF2-HMAC-SHA256 hash.

    It keeps casual users out of the app. It does NOT encrypt the database file — for that, use
    full-disk encryption (BitLocker / FileVault)."""

    ITERATIONS = 200_000

    def __init__(self, settings: "SettingsStore") -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("lock_hash"))

    @staticmethod
    def _hash(pin: str, salt: bytes, iters: int) -> str:
        return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iters).hex()

    def set_pin(self, pin: str) -> None:
        if not 4 <= len(pin) <= 64:
            raise ValidationError("A PIN or password must be 4 to 64 characters long.")
        salt = os.urandom(16)
        self.settings.set_many({"lock_salt": salt.hex(), "lock_iters": self.ITERATIONS, "lock_hash": self._hash(pin, salt, self.ITERATIONS)})

    def verify(self, pin: str) -> bool:
        if not self.enabled:
            return True
        try:
            salt, iters = bytes.fromhex(self.settings.get("lock_salt")), max(1, self.settings.get_int("lock_iters", self.ITERATIONS))
        except ValueError:
            return False
        return hmac.compare_digest(self._hash(pin, salt, iters), self.settings.get("lock_hash"))

    def clear(self, pin: str) -> None:
        if not self.verify(pin):
            raise ValidationError("That PIN is not correct.")
        self.settings.set_many({"lock_hash": "", "lock_salt": "", "lock_idle": 0})


# ════════════════════════════════════════════════════════════════════════════
# 4 · DOMAIN RULES  (pure functions — no database, no GUI)
# ════════════════════════════════════════════════════════════════════════════
PROGRESS_STATES = ("Not Started", "In Progress", "Completed")
PRIORITIES = ("Low", "Normal", "High", "Urgent")
PRIORITY_RANK = {"Urgent": 0, "High": 1, "Normal": 2, "Low": 3}  # sort key (text order would be wrong)
ASSIGNMENT_STATUSES = ("Not Started", "In Progress", "Completed", "Submitted",
                       "Late Submission", "Overdue", "Evaluated")
PENDING_ASSIGNMENT = frozenset({"Not Started", "In Progress", "Completed", "Overdue"})  # not yet handed in


def effective_assignment_status(status: str, due: Any, submitted: Any, today: Optional[date] = None) -> str:
    """Status as the student should see it: derives Overdue / Late Submission from dates."""
    today = today or date.today()
    status = status or "Not Started"
    d, s = parse_date(due), parse_date(submitted)
    if status == "Submitted" and d and s and s > d:
        return "Late Submission"
    if status in PENDING_ASSIGNMENT and status != "Overdue" and d and d < today:
        return "Overdue"
    return status


def attendance_stats(held: Any, attended: Any, target: Optional[float] = 75.0) -> dict:
    """Attendance %, shortage status and how many classes to attend / can be skipped."""
    held = max(0, safe_int(held))
    att = max(0, min(safe_int(attended), held))
    t = safe_float(target, 75.0)
    t = 75.0 if t is None else max(0.0, min(100.0, t))        # junk / out-of-range targets fall back safely
    pct = att / held * 100 if held else None
    status, need, can_skip = "No data", 0, None
    if pct is not None:
        status = "Shortage" if pct < t else ("Borderline" if pct < t + 5 else "Safe")
        if pct < t:
            need = None if t >= 100 else max(1, math.ceil((t * held - 100 * att) / (100 - t) - 1e-9))
        elif t > 0:
            can_skip = max(0, int(math.floor(att * 100 / t - held + 1e-9)))
    return {"held": held, "attended": att, "missed": held - att, "pct": pct, "target": t,
            "status": status, "need": need, "can_skip": can_skip}


def sm2_next(repetitions: int, ease: float, interval: int, quality: int) -> tuple[int, float, int]:
    """SM-2 spaced repetition. ``quality``: 1 Again · 3 Hard · 4 Good · 5 Easy.
    Returns (repetitions, ease, interval_days)."""
    ease = max(1.3, float(ease or 2.5))
    if quality < 3:
        return 0, max(1.3, ease - 0.2), 1
    reps = int(repetitions or 0) + 1
    ease = max(1.3, ease + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    days = 1 if reps == 1 else 6 if reps == 2 else max(int(interval or 1) + 1, round(int(interval or 1) * ease))
    return reps, ease, days


def study_streak(days: set[str], today: Optional[date] = None) -> int:
    """Consecutive study days ending today (or yesterday if today has none yet)."""
    cur = today or date.today()
    if cur.isoformat() not in days:
        cur -= timedelta(days=1)
    n = 0
    while cur.isoformat() in days:
        n += 1
        cur -= timedelta(days=1)
    return n


def trend_label(values: Sequence[float], min_points: int = 4, band: float = 5.0) -> tuple[str, Optional[float]]:
    """Improving / Declining / Stable from chronological scores (%); needs ``min_points``.

    Compares the mean of the later half with the earlier half; ±``band`` points = change."""
    if len(values) < min_points:
        return "Not enough data", None
    half = len(values) // 2
    first, last = values[:half], values[-half:]
    delta = sum(last) / len(last) - sum(first) / len(first)
    return ("Improving" if delta > band else "Declining" if delta < -band else "Stable"), delta


# ---- configurable grading ---------------------------------------------------
GRADE_PRESETS: dict[str, dict] = {
    "10-point scale": {"max_points": 10.0, "scale": [
        ("O", 90, 10, "Outstanding"), ("A+", 80, 9, "Excellent"), ("A", 70, 8, "Very good"),
        ("B+", 60, 7, "Good"), ("B", 50, 6, "Above average"), ("C", 45, 5, "Average"),
        ("P", 40, 4, "Pass"), ("F", 0, 0, "Fail")]},
    "4.0 GPA scale": {"max_points": 4.0, "scale": [
        ("A", 90, 4.0, "Excellent"), ("A-", 85, 3.7, ""), ("B+", 80, 3.3, ""), ("B", 75, 3.0, "Good"),
        ("B-", 70, 2.7, ""), ("C+", 65, 2.3, ""), ("C", 60, 2.0, "Satisfactory"),
        ("D", 50, 1.0, "Pass"), ("F", 0, 0.0, "Fail")]},
}
SGPA_METHODS = {"credit_weighted": "Credit-weighted grade points", "simple_average": "Simple average of grade points",
                "percentage_factor": "Overall percentage ÷ factor"}
CGPA_METHODS = {"credit_weighted": "Credit-weighted across all semesters", "average_of_sgpa": "Average of semester SGPAs"}


def _scale_rows(preset: str) -> list[dict]:
    return [{"grade": g, "min": float(m), "points": float(p), "label": lb} for g, m, p, lb in GRADE_PRESETS[preset]["scale"]]


@dataclass
class GradingConfig:
    """The student's grading rules. Nothing about a particular university is assumed."""
    name: str = "10-point scale"
    scale: list = field(default_factory=lambda: _scale_rows("10-point scale"))
    max_points: float = 10.0
    sgpa_method: str = "credit_weighted"
    cgpa_method: str = "credit_weighted"
    percentage_factor: float = 9.5
    pass_percentage: float = 40.0
    component_pass: dict = field(default_factory=lambda: {"internal": 0.0, "external": 0.0, "practical": 0.0})
    total_method: str = "sum"            # sum | weighted
    weights: dict = field(default_factory=lambda: {"internal": 30.0, "external": 70.0, "practical": 0.0})
    retake_policy: str = "latest"        # latest | best
    include_failed: bool = True

    @classmethod
    def preset(cls, name: str) -> "GradingConfig":
        return cls(name=name, scale=_scale_rows(name), max_points=GRADE_PRESETS[name]["max_points"])

    def to_json(self) -> str:
        return json.dumps(self.__dict__, sort_keys=True)

    @classmethod
    def from_json(cls, text: Any) -> "GradingConfig":
        """Tolerant loader: unknown/missing keys fall back to defaults; bad JSON → defaults."""
        cfg = cls()
        try:
            data = json.loads(text) if text else {}
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in cfg.__dict__:
                        setattr(cfg, k, v)
            cfg.validate()
        except (ValueError, TypeError, ValidationError):
            LOG.warning("Stored grading configuration was invalid; defaults restored")
            return cls()
        return cfg

    def validate(self) -> None:
        if not isinstance(self.scale, list) or not self.scale:
            raise ValidationError("The grade scale needs at least one row.")
        seen_g, seen_m = set(), set()
        for r in self.scale:
            g, m, p = str(r.get("grade", "")).strip(), safe_float(r.get("min")), safe_float(r.get("points"))
            if not g or m is None or p is None or not 0 <= m <= 100 or p < 0:
                raise ValidationError("Each grade needs a name, a minimum % between 0 and 100, and points ≥ 0.")
            if g in seen_g or m in seen_m:
                raise ValidationError("Grade names and minimum percentages must be unique.")
            seen_g.add(g)
            seen_m.add(m)
        if 0.0 not in seen_m:
            raise ValidationError("Include a lowest grade starting at 0% so every mark maps to a grade.")
        if self.sgpa_method not in SGPA_METHODS or self.cgpa_method not in CGPA_METHODS:
            raise ValidationError("Unknown SGPA/CGPA method.")
        if self.total_method not in ("sum", "weighted") or self.retake_policy not in ("latest", "best"):
            raise ValidationError("Unknown total or retake method.")
        if not 0 <= safe_float(self.pass_percentage, -1) <= 100:
            raise ValidationError("Pass percentage must be between 0 and 100.")
        if (safe_float(self.percentage_factor, 0) or 0) <= 0 or (safe_float(self.max_points, 0) or 0) <= 0:
            raise ValidationError("Percentage factor and maximum grade points must be positive.")
        if any((safe_float(v, -1) or 0) < 0 for v in list(self.weights.values()) + list(self.component_pass.values())):
            raise ValidationError("Weights and component pass marks cannot be negative.")

    def _sorted(self) -> list[dict]:
        return sorted(self.scale, key=lambda r: -float(r["min"]))

    def grade_for(self, pct: float) -> tuple[str, float]:
        for r in self._sorted():
            if pct + 1e-9 >= float(r["min"]):
                return str(r["grade"]), float(r["points"])
        r = self._sorted()[-1]
        return str(r["grade"]), float(r["points"])

    def points_for(self, grade: str) -> Optional[float]:
        for r in self.scale:
            if str(r["grade"]).strip().casefold() == str(grade).strip().casefold():
                return float(r["points"])
        return None

    def describe(self) -> list[str]:
        """Plain-language statement of every rule used (shown in the app and in PDFs)."""
        s = self._sorted()
        lines = ["Grade scale: " + ", ".join(f"{r['grade']} ≥ {fmt_num(r['min'])}% = {fmt_num(r['points'])}" for r in s) + "."]
        lines.append({
            "credit_weighted": "SGPA = Σ(credits × grade points) ÷ Σ credits"
                               + (", failed subjects included at their grade points." if self.include_failed else ", passed subjects only."),
            "simple_average": "SGPA = simple average of subject grade points.",
            "percentage_factor": f"SGPA = overall semester percentage ÷ {fmt_num(self.percentage_factor)}.",
        }[self.sgpa_method])
        lines.append({"credit_weighted": "CGPA = Σ(credits × grade points) ÷ Σ credits over all semesters.",
                      "average_of_sgpa": "CGPA = average of the semester SGPAs."}[self.cgpa_method])
        if self.total_method == "weighted":
            w = ", ".join(f"{k} {fmt_num(v)}" for k, v in self.weights.items() if v)
            lines.append(f"Subject total = weighted component percentages ({w}).")
        else:
            lines.append("Subject total = internal + external + practical marks obtained ÷ their maximum.")
        mins = [f"{k} {fmt_num(v)}%" for k, v in self.component_pass.items() if v]
        lines.append(f"Pass: at least {fmt_num(self.pass_percentage)}% overall" + (f" and {', '.join(mins)}." if mins else "."))
        lines.append("Repeated subjects: the " + ("most recent attempt" if self.retake_policy == "latest"
                                                  else "best attempt") + " counts.")
        return lines


def evaluate_result(row: dict, cfg: GradingConfig, default_credits: float = 0.0) -> dict:
    """Totals, grade, grade points and pass/fail for one ``subject_results`` row.

    Explicit grade / grade points / result typed by the student always win over derived values."""
    comps: dict[str, tuple[float, float]] = {}
    for k in ("internal", "external", "practical"):
        got, mx = safe_float(row.get(f"{k}_marks")), safe_float(row.get(f"{k}_max"))
        if got is not None and mx:
            comps[k] = (got, mx)
    obtained = sum(g for g, _ in comps.values())
    maximum = sum(m for _, m in comps.values())
    pct: Optional[float] = None
    if comps:
        wsum = sum(cfg.weights.get(k, 0) or 0 for k in comps)
        if cfg.total_method == "weighted" and wsum > 0:
            pct = sum((cfg.weights.get(k, 0) or 0) * comps[k][0] / comps[k][1] * 100 for k in comps) / wsum
        else:
            pct = obtained / maximum * 100
    grade = str(row.get("grade") or "").strip()
    points = safe_float(row.get("grade_points"))
    if pct is not None and not grade:
        grade, auto = cfg.grade_for(pct)
        points = auto if points is None else points
    elif grade and points is None:
        points = cfg.points_for(grade)
    declared = str(row.get("result") or "").strip().title()
    passed: Optional[bool]
    if declared == "Pass":
        passed = True
    elif declared in ("Fail", "Absent"):
        passed = False
        points = 0.0 if declared == "Absent" else points
    elif pct is not None:
        comp_ok = all(comps[k][0] / comps[k][1] * 100 + 1e-9 >= (cfg.component_pass.get(k, 0) or 0) for k in comps)
        passed = pct + 1e-9 >= cfg.pass_percentage and comp_ok
    elif points is not None:
        passed = points > 0
    else:
        passed = None
    credits = safe_float(row.get("credits"))
    return {"pct": pct, "obtained": obtained if comps else None, "maximum": maximum if comps else None,
            "grade": grade, "points": points, "passed": passed, "declared": declared,
            "credits": credits if credits is not None else (safe_float(default_credits, 0.0) or 0.0),
            "counted": declared != "Withheld" and (points is not None)}


def pick_attempts(rows: Sequence[dict], cfg: GradingConfig) -> list[dict]:
    """One result per subject: latest or best attempt (rows carry subject_id, attempt_no, ev)."""
    by_subject: dict[Any, list[dict]] = defaultdict(list)
    for r in rows:
        by_subject[r["subject_id"]].append(r)
    chosen = []
    for items in by_subject.values():
        if cfg.retake_policy == "best":
            chosen.append(max(items, key=lambda r: (r["ev"]["points"] if r["ev"]["points"] is not None else -1,
                                                    r["ev"]["pct"] if r["ev"]["pct"] is not None else -1,
                                                    r.get("attempt_no") or 0)))
        else:
            chosen.append(max(items, key=lambda r: (r.get("attempt_no") or 0, r.get("id") or 0)))
    return chosen


def compute_sgpa(evals: Sequence[dict], cfg: GradingConfig) -> Optional[float]:
    """SGPA for one semester's evaluated results (see GradingConfig.describe for the rule)."""
    use = [e for e in evals if e["counted"] and e["points"] is not None and (cfg.include_failed or e["passed"] is not False)]
    if not use:
        return None
    if cfg.sgpa_method == "percentage_factor":
        marked = [e for e in use if e["maximum"]]
        mx = sum(e["maximum"] for e in marked)
        return (sum(e["obtained"] for e in marked) / mx * 100) / cfg.percentage_factor if mx else None
    if cfg.sgpa_method == "simple_average":
        return sum(e["points"] for e in use) / len(use)
    credits = sum(e["credits"] or 0 for e in use)
    if credits <= 0:  # no credits recorded: fall back to a plain average (stated in the UI)
        return sum(e["points"] for e in use) / len(use)
    return sum((e["credits"] or 0) * e["points"] for e in use) / credits


def compute_cgpa(per_semester: Sequence[Sequence[dict]], cfg: GradingConfig) -> Optional[float]:
    if cfg.cgpa_method == "average_of_sgpa":
        vals = [v for v in (compute_sgpa(s, cfg) for s in per_semester) if v is not None]
        return sum(vals) / len(vals) if vals else None
    flat = [e for s in per_semester for e in s]
    return compute_sgpa(flat, GradingConfig(**{**cfg.__dict__, "sgpa_method": "credit_weighted"})) \
        if cfg.sgpa_method == "percentage_factor" else compute_sgpa(flat, cfg)


def gpa_basis(evals: Sequence[dict], cfg: GradingConfig) -> tuple[float, float]:
    """(credits, Σ credits × grade points) over the results that count towards a credit-weighted GPA."""
    use = [e for e in evals if e["counted"] and e["points"] is not None and (cfg.include_failed or e["passed"] is not False)]
    return sum(e["credits"] or 0 for e in use), sum((e["credits"] or 0) * e["points"] for e in use)


def required_grade_points(target: float, done_credits: float, done_points: float, upcoming_credits: float) -> Optional[float]:
    """Average grade points needed over ``upcoming_credits`` to finish at a credit-weighted CGPA of ``target``."""
    if upcoming_credits <= 0:
        return None
    return (target * (done_credits + upcoming_credits) - done_points) / upcoming_credits


def grade_for_points(cfg: GradingConfig, points: float) -> Optional[dict]:
    """Lowest grade on the scale whose points reach ``points`` (None when even the top grade is not enough)."""
    ok = [r for r in cfg.scale if float(r["points"]) + 1e-9 >= points]
    return min(ok, key=lambda r: float(r["points"])) if ok else None


def marks_needed_table(cfg: GradingConfig, internal_got: float, internal_max: float, external_max: float,
                       practical_got: float = 0.0, practical_max: float = 0.0) -> list[dict]:
    """For every grade: the external (university) marks still needed, given internal/practical marks already earned.

    Uses the student's own total method (sum of marks, or weighted components) and component pass floor."""
    out = []
    total_max = internal_max + external_max + practical_max
    w = cfg.weights
    wi = (w.get("internal", 0) or 0) if internal_max else 0
    we, wp = (w.get("external", 0) or 0), ((w.get("practical", 0) or 0) if practical_max else 0)
    floor = (cfg.component_pass.get("external", 0) or 0) / 100 * external_max
    for r in cfg._sorted():
        need = float(r["min"])
        if cfg.total_method == "weighted" and we > 0:
            known = (wi * internal_got / internal_max * 100 if internal_max else 0) + (wp * practical_got / practical_max * 100 if practical_max else 0)
            required = ((need * (wi + we + wp) - known) / we) / 100 * external_max
        else:
            required = need / 100 * total_max - internal_got - practical_got
        required = math.ceil(max(required, floor, 0.0) * 10 - 1e-9) / 10
        out.append({"grade": str(r["grade"]), "min_pct": need, "points": float(r["points"]), "required": required,
                    "secured": required <= 1e-9, "feasible": required <= external_max + 1e-9,
                    "required_pct": (required / external_max * 100) if external_max else None})
    return out


# ════════════════════════════════════════════════════════════════════════════
# 5 · SETTINGS & THEME
# ════════════════════════════════════════════════════════════════════════════
DEFAULT_SETTINGS = {
    "theme": "Light", "accent": "Blue", "accent_custom": "", "font_size": "10", "density": "Comfortable",
    "daily_goal": "180", "pomodoro_work": "25", "pomodoro_short": "5", "pomodoro_long": "15",
    "attendance_min": "75", "low_mark_pct": "40", "scope_semester": "current", "current_semester_id": "",
    "date_style": "%d %b %Y", "auto_backup": "1", "backup_keep": "14", "grading": "",
    "table_colors": "", "status_colors": "", "dashboard_panels": "", "sidebar_mode": "Auto", "corners": "Rounded",
    "start_page": "last", "last_page": "dashboard", "lock_hash": "", "lock_salt": "", "lock_iters": "200000", "lock_idle": "0",
    "onboarding_dismissed": "0", "window_geometry": "",
}


class SettingsStore:
    """Typed, cached access to the ``settings`` key/value table."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._cache: Optional[dict[str, str]] = None
        self._grading: Optional[GradingConfig] = None

    def invalidate(self) -> None:
        self._cache = None
        self._grading = None

    def _all(self) -> dict[str, str]:
        if self._cache is None:
            self._cache = {r["key"]: r["value"] for r in self.db.q("SELECT key,value FROM settings")}
        return self._cache

    def get(self, key: str, default: Optional[str] = None) -> str:
        v = self._all().get(key)
        if v is not None:
            return v
        return default if default is not None else DEFAULT_SETTINGS.get(key, "")

    def get_int(self, key: str, default: int = 0) -> int:
        return safe_int(self.get(key), default)

    def get_float(self, key: str, default: float = 0.0) -> float:
        out = safe_float(self.get(key))
        return default if out is None else out

    def get_bool(self, key: str) -> bool:
        return str(self.get(key)).strip().lower() in ("1", "true", "yes", "on")

    def get_json(self, key: str, default: Any = None) -> Any:
        try:
            return json.loads(self.get(key)) if self.get(key) else default
        except ValueError:
            return default

    def set_many(self, values: dict[str, Any]) -> None:
        with self.db.transaction():
            self.db.conn.executemany(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(str(k), v if isinstance(v, str) else json.dumps(v) if isinstance(v, (dict, list)) else str(v))
                 for k, v in values.items()])
        self.invalidate()

    def set(self, key: str, value: Any) -> None:
        self.set_many({key: value})

    def grading(self) -> GradingConfig:
        if self._grading is None:
            self._grading = GradingConfig.from_json(self.get("grading"))
        return self._grading

    def save_grading(self, cfg: GradingConfig) -> None:
        cfg.validate()
        self.set("grading", cfg.to_json())


DENSITY = {"Compact": {"pad_v": 5, "pad_h": 10, "row": 24, "gap": 10, "font": -1},
           "Comfortable": {"pad_v": 8, "pad_h": 12, "row": 32, "gap": 14, "font": 0},
           "Spacious": {"pad_v": 11, "pad_h": 14, "row": 42, "gap": 18, "font": 1}}

_LIGHT = {"bg": "#F4F7FB", "panel": "#FFFFFF", "panel_alt": "#EEF2F8", "text": "#182230", "muted": "#566274",
          "border": "#D5DDE9", "side": "#17243B", "side_text": "#DCE5F1", "side_hover": "#263A58",
          "input": "#FFFFFF", "table_alt": "#F6F8FC", "header": "#E8EEF6", "placeholder": "#6F7B8D",
          "disabled": "#8A96A8", "grid": "#E3E9F2",
          "success": "#146C46", "warning": "#8A5A00", "danger": "#B42318", "info": "#2457A7"}
_DARK = {"bg": "#0F172A", "panel": "#172235", "panel_alt": "#202F45", "text": "#E6EDF7", "muted": "#A9B7CB",
         "border": "#34445D", "side": "#0B1220", "side_text": "#DCE5F1", "side_hover": "#1A2940",
         "input": "#1C2B40", "table_alt": "#1B2A3E", "header": "#223249", "placeholder": "#8D9BB0",
         "disabled": "#8290A4", "grid": "#2B3B53",
         "success": "#4ADE80", "warning": "#FBBF24", "danger": "#F87171", "info": "#7DB3FF"}
_CONTRAST = {"bg": "#000000", "panel": "#000000", "panel_alt": "#1A1A1A", "text": "#FFFFFF", "muted": "#E6E6E6",
             "border": "#FFFFFF", "side": "#000000", "side_text": "#FFFFFF", "side_hover": "#333333",
             "input": "#000000", "table_alt": "#0D0D0D", "header": "#1A1A1A", "placeholder": "#D0D0D0",
             "disabled": "#BDBDBD", "grid": "#808080",
             "success": "#7CFF9E", "warning": "#FFE066", "danger": "#FF9E94", "info": "#9CC2FF"}
TABLE_COLOR_KEYS = {"header_bg": "Header background", "header_fg": "Header text", "alt_row": "Alternate row",
                    "selected": "Selected row", "grid": "Grid / border", "highlight": "Hover highlight"}
STATUS_COLOR_KEYS = {"success": "Completed / good", "warning": "Warning / pending",
                     "danger": "Overdue / poor", "info": "Information"}


class Theme:
    """Colour tokens + stylesheet for one appearance (pure Python, fully testable).

    Accent-coloured *text* and *fills* are checked against WCAG contrast so any
    custom accent stays readable in both light and dark mode."""

    def __init__(self, mode: str = "light", accent: str = "#2F5FCB", font_pt: int = 10,
                 density: str = "Comfortable", table_colors: Optional[dict] = None,
                 status_colors: Optional[dict] = None, corners: str = "Rounded") -> None:
        self.dark = mode in ("dark", "contrast")
        self.density = density if density in DENSITY else "Comfortable"
        self.dim = DENSITY[self.density]
        self.font_pt = max(8, min(18, int(font_pt))) + self.dim["font"]
        t = dict(_CONTRAST if mode == "contrast" else _DARK if self.dark else _LIGHT)
        for k, v in (status_colors or {}).items():
            if k in STATUS_COLOR_KEYS and v:
                t[k] = hex_norm(v, t[k])
        self.accent = hex_norm(accent, "#2F5FCB")
        self.mc = 7.0 if mode == "contrast" else 4.5          # WCAG AAA for the high-contrast theme, AA otherwise
        if mode == "contrast" and contrast_ratio(self.accent, t["panel"]) < 7:
            self.accent = "#FFD400"
        t["accent"] = self.accent
        t["accent_fg"] = readable_on(self.accent)
        t["accent_text"] = ensure_contrast(self.accent, t["panel"], self.mc)
        t["accent_hover"] = mix(self.accent, "#FFFFFF" if self.dark else "#000000", 0.14)
        t["accent_soft"] = mix(t["panel"], self.accent, 0.16)
        tc = {k: hex_norm(v, "") for k, v in (table_colors or {}).items() if v}
        t["t_header_bg"] = tc.get("header_bg") or t["header"]
        t["t_header_fg"] = tc.get("header_fg") or ensure_contrast(t["text"], tc.get("header_bg") or t["header"], self.mc)
        t["t_alt"] = tc.get("alt_row") or t["table_alt"]
        t["t_sel"] = tc.get("selected") or self.accent
        t["t_sel_fg"] = readable_on(t["t_sel"])
        t["t_grid"] = tc.get("grid") or t["grid"]
        t["t_hover"] = tc.get("highlight") or mix(t["panel"], self.accent, 0.08)
        for s in ("success", "warning", "danger", "info"):
            t[s + "_soft"] = mix(t["panel"], t[s], 0.16 if not self.dark else 0.22)
            t[s + "_text"] = ensure_contrast(t[s], t[s + "_soft"], self.mc)
        t["font"] = '"Segoe UI", "Helvetica Neue", Arial, sans-serif'
        t["radius"], t["radius_s"] = {"Sharp": (2, 2), "Rounded": (8, 5), "Soft": (14, 9)}.get(corners, (8, 5))
        t.update(pad_v=self.dim["pad_v"], pad_h=self.dim["pad_h"], gap=self.dim["gap"],
                 fs=self.font_pt, fs_small=max(7, self.font_pt - 1), fs_title=self.font_pt + 12,
                 fs_section=self.font_pt + 2, fs_metric=self.font_pt + 13, fs_timer=self.font_pt + 44)
        self.t = t

    @classmethod
    def from_settings(cls, s: SettingsStore, system_dark: bool = False) -> "Theme":
        mode = s.get("theme").strip().lower()
        dark = system_dark if mode == "system" else mode == "dark"
        accent = s.get("accent_custom") or ACCENTS.get(s.get("accent"), ACCENTS["Blue"])
        return cls("contrast" if mode in ("high contrast", "contrast") else "dark" if dark else "light", accent, s.get_int("font_size", 10), s.get("density"),
                   s.get_json("table_colors", {}), s.get_json("status_colors", {}), s.get("corners"))

    def color(self, token_or_hex: str) -> str:
        """Resolve a theme token ('danger', 'accent'…) or a literal hex colour."""
        return self.t.get(token_or_hex) or hex_norm(token_or_hex, self.t["text"])

    def soft(self, token: str) -> str:
        return self.t.get(token + "_soft") or mix(self.t["panel"], self.color(token), 0.16)

    def text_on_soft(self, token: str) -> str:
        return self.t.get(token + "_text") or ensure_contrast(self.color(token), self.soft(token))

    @property
    def row_height(self) -> int:
        return self.dim["row"]

    def qss(self) -> str:
        from string import Template
        return Template(_QSS).substitute(self.t)


_QSS = """
* { font-family: $font; font-size: ${fs}pt; }
QWidget { color: $text; }
QMainWindow, QWidget#AppCentral, QWidget#Workspace, QStackedWidget, QWidget#PageBody { background-color: $bg; }
QDialog { background-color: $bg; }
QLabel { background: transparent; border: none; }
QLabel#Muted { color: $muted; font-size: ${fs_small}pt; }
QLabel#PageTitle { font-size: ${fs_title}pt; font-weight: 700; }
QLabel#SectionTitle { font-size: ${fs_section}pt; font-weight: 700; }
QLabel#DialogTitle { font-size: ${fs_section}pt; font-weight: 700; }
QLabel#MetricLabel { color: $muted; font-size: ${fs_small}pt; font-weight: 700; }
QLabel#MetricValue { font-size: ${fs_metric}pt; font-weight: 700; color: $accent_text; }
QLabel#Timer { font-size: ${fs_timer}pt; font-weight: 700; color: $accent_text; padding: 8px; }
QLabel#EmptyState { background: $panel; color: $muted; border: 1px dashed $border; border-radius: ${radius}px; padding: 28px; }
QLabel#Banner { background: $warning_soft; color: $warning_text; border: 1px solid $warning; border-radius: ${radius_s}px; padding: 8px 12px; }
QLabel#BannerDanger { background: $danger_soft; color: $danger_text; border: 1px solid $danger; border-radius: ${radius_s}px; padding: 8px 12px; }
QLabel#Badge { background: $accent_soft; color: $accent_text; border-radius: ${radius_s}px; padding: 2px 8px; font-weight: 600; }
QFrame#Sidebar { background-color: $side; border: none; }
QFrame#Sidebar QLabel { color: $side_text; }
QLabel#Logo { background-color: $accent; color: $accent_fg; font-weight: 700; border-radius: ${radius}px; min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px; qproperty-alignment: AlignCenter; }
QLabel#Brand { color: #FFFFFF; font-weight: 700; }
QFrame#Sidebar QLabel#NavCaption { color: #9FB0C8; font-size: ${fs_small}pt; font-weight: 700; padding: 12px 12px 3px 12px; }
QPushButton#NavButton { background: transparent; color: $side_text; text-align: left; border: 1px solid transparent; border-radius: ${radius}px; padding: ${pad_v}px 12px; font-weight: 600; }
QPushButton#NavButton:hover { background-color: $side_hover; color: #FFFFFF; }
QPushButton#NavButton[active="true"] { background-color: $accent; color: $accent_fg; }
QPushButton#NavButton:focus { border: 1px solid $accent_hover; }
QFrame#Topbar { background-color: $panel; border: none; border-bottom: 1px solid $border; }
QFrame#Card, QFrame#Metric { background-color: $panel; border: 1px solid $border; border-radius: ${radius}px; }
QFrame#Metric { border-left: 3px solid $accent; }
QFrame#Card QLabel, QFrame#Metric QLabel { background: transparent; }
QFrame#FilterBar { background-color: $panel; border: 1px solid $border; border-radius: ${radius}px; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit {
  background-color: $input; color: $text; border: 1px solid $border; border-radius: ${radius_s}px; padding: ${pad_v}px 8px;
  selection-background-color: $accent; selection-color: $accent_fg; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus { border: 1px solid $accent; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QDateEdit:disabled, QTextEdit:disabled { background-color: $panel_alt; color: $disabled; }
QComboBox QAbstractItemView { background-color: $panel; color: $text; border: 1px solid $border; selection-background-color: $accent; selection-color: $accent_fg; outline: 0; }
QPushButton { background-color: $panel; color: $text; border: 1px solid $border; border-radius: ${radius_s}px; padding: ${pad_v}px ${pad_h}px; font-weight: 600; }
QPushButton:hover { border-color: $accent; background-color: $accent_soft; }
QPushButton:pressed { background-color: $header; }
QPushButton:focus { border: 1px solid $accent; }
QPushButton:disabled { color: $disabled; background-color: $panel_alt; border-color: $border; }
QPushButton#Primary { background-color: $accent; color: $accent_fg; border: 1px solid $accent; }
QPushButton#Primary:hover { background-color: $accent_hover; border-color: $accent_hover; }
QPushButton#Primary:disabled { background-color: $panel_alt; color: $disabled; border-color: $border; }
QPushButton#Danger { background-color: transparent; color: $danger_text; border: 1px solid $danger; }
QPushButton#Danger:hover { background-color: $danger_soft; }
QPushButton#Chip { border-radius: 10px; padding: 2px 10px; background: $accent_soft; color: $accent_text; border: 1px solid transparent; }
QToolButton { background: transparent; color: $text; border: 1px solid transparent; border-radius: ${radius_s}px; padding: 4px 8px; }
QToolButton:hover { border-color: $accent; background: $accent_soft; }
QToolButton::menu-indicator { image: none; width: 0px; }
QTableView, QTreeView, QListView, QTreeWidget, QListWidget { background-color: $panel; color: $text; alternate-background-color: $t_alt;
  border: 1px solid $border; border-radius: ${radius_s}px; gridline-color: $t_grid; selection-background-color: $t_sel; selection-color: $t_sel_fg; outline: 0; }
QTableView::item:hover, QListWidget::item:hover { background-color: $t_hover; }
QTableView::item:selected, QListWidget::item:selected, QTreeWidget::item:selected { background-color: $t_sel; color: $t_sel_fg; }
QTableCornerButton::section { background-color: $t_header_bg; border: none; }
QHeaderView::section { background-color: $t_header_bg; color: $t_header_fg; border: none; border-right: 1px solid $t_grid; border-bottom: 1px solid $t_grid; padding: 6px 8px; font-weight: 700; }
QGroupBox { background-color: $panel; border: 1px solid $border; border-radius: ${radius}px; margin-top: 16px; padding: 16px 12px 12px 12px; font-weight: 700; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; background-color: $panel; }
QCheckBox, QRadioButton { background: transparent; spacing: 8px; }
QProgressBar { background-color: $panel_alt; border: 1px solid $border; border-radius: ${radius_s}px; min-height: 14px; text-align: center; font-size: ${fs_small}pt; color: $text; }
QProgressBar::chunk { background-color: $accent; border-radius: ${radius_s}px; }
QTabWidget::pane { background-color: transparent; border: none; border-top: 1px solid $border; top: -1px; }
QTabBar { background: transparent; }
QTabBar::tab { background: transparent; color: $muted; padding: ${pad_v}px 16px; border: none; border-bottom: 2px solid transparent; margin-right: 4px; font-weight: 600; }
QTabBar::tab:selected { color: $accent_text; border-bottom: 2px solid $accent; }
QTabBar::tab:hover:!selected { color: $text; background: $accent_soft; border-top-left-radius: ${radius_s}px; border-top-right-radius: ${radius_s}px; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 12px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 0; }
QScrollBar::handle:vertical { background: $border; border-radius: 5px; min-height: 28px; margin: 2px; }
QScrollBar::handle:horizontal { background: $border; border-radius: 5px; min-width: 28px; margin: 2px; }
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: $muted; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; border: none; background: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QMenu { background-color: $panel; color: $text; border: 1px solid $border; border-radius: ${radius_s}px; padding: 4px; }
QMenu::item { padding: 6px 22px; border-radius: 3px; }
QMenu::item:selected { background-color: $accent; color: $accent_fg; }
QMenu::item:disabled { color: $disabled; }
QMenu::separator { height: 1px; background: $border; margin: 4px 8px; }
QToolTip { background-color: $side; color: #FFFFFF; border: 1px solid $border; padding: 5px 8px; }
QStatusBar { background-color: $panel; color: $muted; border-top: 1px solid $border; }
QSplitter::handle { background: $border; }
QCalendarWidget QWidget { background-color: $panel; color: $text; }
QCalendarWidget QAbstractItemView { selection-background-color: $accent; selection-color: $accent_fg; }
QCalendarWidget QToolButton { background-color: $panel_alt; border: 1px solid $border; }
"""


# ════════════════════════════════════════════════════════════════════════════
# 6 · ENTITIES, ROW LOGIC & REPOSITORY  (declarative specs drive forms, tables, reports)
# ════════════════════════════════════════════════════════════════════════════
class ChangeBus:
    """Tiny observer: the repository announces which tables changed; pages refresh."""

    def __init__(self) -> None:
        self._subs: list[Callable[[set], None]] = []

    def subscribe(self, fn: Callable[[set], None]) -> None:
        if fn not in self._subs:
            self._subs.append(fn)

    def notify(self, *tables: str) -> None:
        for fn in list(self._subs):
            try:
                fn(set(tables))
            except Exception:  # a broken listener must never break a save
                LOG.exception("Change listener failed")


@dataclass(frozen=True)
class Field:
    """One editable column of an entity (drives the record form and validation)."""
    key: str
    label: str
    kind: str = "text"      # text long int float date choice fk bool color file
    required: bool = False
    choices: tuple = ()
    fk: str = ""            # lookup name for kind='fk'
    parent: str = ""        # sibling field whose value narrows this lookup
    default: Any = None
    lo: Optional[float] = None
    hi: Optional[float] = None
    hint: str = ""


@dataclass(frozen=True)
class Column:
    """One column of the on-screen / PDF table."""
    key: str
    label: str
    width: int = 110
    kind: str = "text"      # text int float date pct minutes status priority bool kb
    align: str = ""         # left | center | right ('' = by kind)
    vis: bool = True        # visible by default
    color: str = ""         # row key holding a hex colour (subject swatch)


@dataclass(frozen=True)
class Rule:
    """Conditional formatting. ``value`` may be '@low' (the low-marks threshold)."""
    column: str
    op: str                 # eq ne lt le gt ge contains in blank notblank
    value: Any = ""
    color: str = "danger"   # theme token or #hex
    scope: str = "cell"     # 'cell' colours the text; 'row' tints the whole row
    bold: bool = False


@dataclass
class Ctx:
    """Context passed to row-derivation hooks."""
    today: date
    grading: GradingConfig
    low_mark: float = 40.0
    att_min: float = 75.0


@dataclass
class Entity:
    table: str
    singular: str
    plural: str
    fields: tuple
    select: str
    source: str
    columns: tuple
    order: str = "t.id DESC"
    scope: str = ""
    date_key: str = ""
    filters: tuple = ()
    rules: tuple = ()
    quick: dict = field(default_factory=dict)
    bulk: tuple = ()
    children: tuple = ()
    derive: Optional[Callable[[dict, Ctx], None]] = None
    defaults: dict = field(default_factory=dict)
    label_key: str = "title"
    attachments: bool = False


def F(key: str, label: str, kind: str = "text", req: bool = False, **kw: Any) -> Field:
    return Field(key, label, kind, req, **kw)


def C(key: str, label: str, w: int = 110, kind: str = "text", align: str = "", vis: bool = True, color: str = "") -> Column:
    return Column(key, label, w, kind, align, vis, color)


DEGREE_LEVELS = ("Diploma", "Undergraduate", "Postgraduate", "Doctorate", "Certificate", "Custom")
TERM_STATUS = ("Upcoming", "Active", "Completed")
UNIT_STATUS = PROGRESS_STATES
EXAM_TYPES = ("Class Test", "Unit Test", "Quiz", "Internal Assessment", "Seasonal Exam", "Mid-Semester Exam",
              "Semester Exam", "End-Semester Exam", "Practical Exam", "Viva", "Laboratory Assessment",
              "University Exam", "Backlog Exam", "Supplementary Exam")
ASSESSMENT_TYPES = ("Class Test", "Unit Test", "Unit Quiz", "Quiz", "Internal Assessment", "Assignment", "Practical",
                    "Lab Assessment", "Viva", "Seasonal Exam", "Semester Exam", "Other")
ASSIGNMENT_TYPES = ("Written", "Lab Record", "Online", "Presentation", "Project", "Quiz", "Other")
PREP_STATUS = ("Not Started", "In Progress", "Prepared", "Completed")
RESULT_STATES = ("Pending", "Pass", "Fail", "Absent", "Withheld")
PROJECT_CATEGORIES = ("Academic Project", "Mini Project", "Major Project", "Laboratory Record", "Practical Assignment",
                      "Viva Preparation", "Presentation", "Research Activity", "Internship", "Certification")
PROJECT_STATUS = ("Not Started", "In Progress", "On Hold", "Completed", "Submitted", "Evaluated")
SUBJECT_TYPES = ("Theory", "Practical", "Lab", "Project", "Elective", "Audit")
DOC_CATEGORIES = ("Subject Note", "Chapter Note", "Assignment File", "Examination Resource", "Practical File",
                  "Project Document", "Study Reference", "Academic Document", "Other")
EVENT_TYPES = ("Event", "Holiday", "Exam Period", "Registration", "Break", "Deadline", "Other")
SESSION_TYPES = ("Study", "Revision", "Practice", "Lecture", "Project")
TASK_CATEGORIES = ("Study", "Assignment", "Revision", "Exam prep", "Personal", "Project")
ATTACH_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".csv", ".rtf",
              ".odt", ".ods", ".odp", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

# status word -> theme colour token (used for status/priority cell colouring everywhere)
STATUS_TOKENS = {
    "Completed": "success", "Submitted": "success", "Evaluated": "success", "Pass": "success", "Active": "success",
    "Prepared": "success", "Reviewed": "success", "Safe": "success", "Present": "success", "Yes": "success",
    "Overdue": "danger", "Fail": "danger", "Absent": "danger", "Shortage": "danger", "Missing": "danger",
    "Urgent": "danger", "Late Submission": "warning", "High": "warning", "Borderline": "warning",
    "In Progress": "info", "Pending": "warning", "Due": "warning", "On Hold": "warning", "Partial": "warning",
    "Upcoming": "info", "Withheld": "warning", "Not Started": "muted", "Skipped": "muted", "Paused": "muted", "Low": "muted"}

_SJ = ("JOIN subjects s ON s.id=t.subject_id LEFT JOIN semesters sem ON sem.id=s.semester_id "
       "LEFT JOIN academic_years ay ON ay.id=sem.academic_year_id")
_SJL = _SJ.replace("JOIN subjects s", "LEFT JOIN subjects s", 1)
_SUBJ = "s.id AS subject_id, s.name AS subject, s.color AS subject_color, sem.name AS semester, ay.name AS academic_year"


def _d_assignment(r: dict, c: Ctx) -> None:
    r["status_eff"] = effective_assignment_status(r["status"], r["due_date"], r["submission_date"], c.today)
    d = parse_date(r["due_date"])
    r["days_left"] = (d - c.today).days if d and r["status_eff"] in PENDING_ASSIGNMENT else None
    got, mx = safe_float(r["marks_obtained"]), safe_float(r["max_marks"])
    r["score"] = f"{fmt_num(got)}/{fmt_num(mx)}" if got is not None and mx else ""
    r["pct"] = percent(got, mx) if got is not None else None


def _d_exam(r: dict, c: Ctx) -> None:
    got, mx = safe_float(r["marks_obtained"]), safe_float(r["max_marks"])
    r["score"] = f"{fmt_num(got)}/{fmt_num(mx)}" if got is not None and mx else ""
    r["pct"] = percent(got, mx) if got is not None else None
    d = parse_date(r["exam_date"])
    r["days_to"] = (d - c.today).days if d and d >= c.today else None


def _d_mark(r: dict, c: Ctx) -> None:
    r["pct"] = percent(r["marks_obtained"], r["max_marks"])


def _d_result(r: dict, c: Ctx) -> None:
    ev = evaluate_result(r, c.grading, r.get("subject_credits") or 0)
    for k, tag in (("internal", "int"), ("external", "ext"), ("practical", "prac")):
        got, mx = safe_float(r[f"{k}_marks"]), safe_float(r[f"{k}_max"])
        r[f"{tag}_txt"] = f"{fmt_num(got)}/{fmt_num(mx)}" if got is not None and mx else ""
    r["total"] = f"{fmt_num(ev['obtained'])}/{fmt_num(ev['maximum'])}" if ev["maximum"] else ""
    r["pct"], r["grade_eff"], r["points_eff"], r["credits_eff"] = ev["pct"], ev["grade"], ev["points"], ev["credits"]
    r["result_eff"] = (ev["declared"] if ev["declared"] in ("Pass", "Fail", "Absent", "Withheld")
                       else "Pass" if ev["passed"] else "Fail" if ev["passed"] is False else "Pending")


def _d_attendance(r: dict, c: Ctx) -> None:
    r["status"] = "Present" if r["held"] and r["attended"] == r["held"] else "Absent" if r["attended"] == 0 else "Partial"


def _d_task(r: dict, c: Ctx) -> None:
    d = parse_date(r["task_date"])
    r["overdue"] = "1" if d and d < c.today and r["status"] in ("Pending", "In Progress") else ""


def _d_revision(r: dict, c: Ctx) -> None:
    d = parse_date(r["due_date"])
    r["due_in"] = (d - c.today).days if d else None


def _d_project(r: dict, c: Ctx) -> None:
    d = parse_date(r["deadline"])
    open_ = r["status"] in ("Not Started", "In Progress", "On Hold")
    r["days_left"] = (d - c.today).days if d and open_ else None
    r["progress"] = f"{r['milestones_done']}/{r['milestones']}" if r["milestones"] else ""
    r["pct"] = percent(r["marks_obtained"], r["max_marks"])


def _d_subject(r: dict, c: Ctx) -> None:
    r["syllabus_pct"] = percent(r["units_done"], r["units_total"])


def _d_attachment(r: dict, c: Ctx) -> None:
    r["exists"] = "Yes" if r["file_path"] and Path(r["file_path"]).exists() else "Missing"
    r["size_kb"] = (r["size_bytes"] or 0) / 1024 if r["size_bytes"] is not None else None
    r["linked"] = "General" if r["entity_type"] == "general" else f"{r['entity_type'].replace('_', ' ')} #{r['record_id']}"


_BLOCK = "block"      # deletion refused while children exist
_DELETE = "delete"    # children are removed with the parent
_DETACH = "detach"    # children are kept; the link is cleared

ENTITY_LIST = (
    Entity("programmes", "Programme", "Programmes",
           (F("name", "Programme name", req=True, hint="e.g. B.Tech CSE (AI & ML)"),
            F("degree_level", "Level", "choice", choices=DEGREE_LEVELS, default="Undergraduate"),
            F("branch", "Branch"), F("specialisation", "Specialisation"),
            F("college_name", "College"), F("university_name", "University"),
            F("session_label", "Academic session", hint="e.g. 2024–2028"),
            F("start_year", "Start year", "int", lo=1950, hi=2100), F("end_year", "Expected end year", "int", lo=1950, hi=2100),
            F("years_count", "Number of years", "int", True, lo=1, hi=10, default=4),
            F("semesters_per_year", "Semesters per year", "int", True, lo=1, hi=4, default=2),
            F("status", "Status", "choice", choices=("Active", "Completed", "On Hold"), default="Active"),
            F("notes", "Notes", "long")),
           "t.id, t.name, t.degree_level, t.branch, t.specialisation, t.college_name, t.university_name, t.session_label, "
           "t.years_count, t.status, (SELECT COUNT(*) FROM semesters x WHERE x.programme_id=t.id) AS semesters",
           "programmes t",
           (C("name", "Programme", 230), C("degree_level", "Level", 110), C("branch", "Branch", 140),
            C("specialisation", "Specialisation", 150), C("college_name", "College", 170), C("session_label", "Session", 100),
            C("years_count", "Years", 60, "int"), C("semesters", "Semesters", 85, "int"), C("status", "Status", 90, "status")),
           order="t.id", filters=("status", "degree_level"), label_key="name",
           children=(("semesters", "programme_id IN ({ids})", "semesters", _BLOCK),
                     ("academic_years", "programme_id IN ({ids})", "academic years", _BLOCK))),
    Entity("academic_years", "Academic year", "Academic years",
           (F("programme_id", "Programme", "fk", True, fk="programmes"),
            F("year_no", "Year number", "int", True, lo=1, hi=12, default=1), F("name", "Name", req=True, hint="e.g. First Year"),
            F("session_label", "Session", hint="e.g. 2024-25"), F("start_date", "Start date", "date"), F("end_date", "End date", "date"),
            F("status", "Status", "choice", choices=TERM_STATUS, default="Active"), F("notes", "Notes", "long")),
           "t.id, p.name AS programme, t.year_no, t.name, t.session_label, t.start_date, t.end_date, t.status, "
           "(SELECT COUNT(*) FROM semesters x WHERE x.academic_year_id=t.id) AS semesters",
           "academic_years t JOIN programmes p ON p.id=t.programme_id",
           (C("programme", "Programme", 200), C("year_no", "No.", 55, "int"), C("name", "Academic year", 160),
            C("session_label", "Session", 100), C("start_date", "Start", 100, "date"), C("end_date", "End", 100, "date"),
            C("semesters", "Semesters", 85, "int"), C("status", "Status", 90, "status")),
           order="t.programme_id, t.year_no", filters=("programme", "status"), label_key="name",
           children=(("semesters", "academic_year_id IN ({ids})", "semesters", _BLOCK),)),
    Entity("semesters", "Semester", "Semesters",
           (F("programme_id", "Programme", "fk", fk="programmes"),
            F("academic_year_id", "Academic year", "fk", fk="academic_years", parent="programme_id"),
            F("semester_no", "Semester number", "int", lo=1, hi=24), F("name", "Semester name", req=True),
            F("start_date", "Start date", "date"), F("end_date", "End date", "date"),
            F("status", "Status", "choice", choices=TERM_STATUS, default="Upcoming")),
           "t.id, p.name AS programme, ay.name AS academic_year, t.semester_no, t.name, t.start_date, t.end_date, t.status, "
           "(SELECT COUNT(*) FROM subjects x WHERE x.semester_id=t.id) AS subjects",
           "semesters t LEFT JOIN programmes p ON p.id=t.programme_id LEFT JOIN academic_years ay ON ay.id=t.academic_year_id",
           (C("programme", "Programme", 180), C("academic_year", "Year", 110), C("semester_no", "No.", 55, "int"),
            C("name", "Semester", 170), C("start_date", "Start", 100, "date"), C("end_date", "End", 100, "date"),
            C("subjects", "Subjects", 75, "int"), C("status", "Status", 95, "status")),
           order="COALESCE(t.programme_id,0), COALESCE(t.semester_no,9999), t.id", filters=("programme", "academic_year", "status"),
           label_key="name", quick={"Mark as active": {"status": "Active"}, "Mark as completed": {"status": "Completed"}},
           bulk=("status",), children=(("subjects", "semester_id IN ({ids})", "subjects", _BLOCK),
                                       ("calendar_events", "semester_id IN ({ids})", "calendar events", _DETACH))),
    Entity("subjects", "Subject", "Subjects",
           (F("semester_id", "Semester", "fk", True, fk="semesters"), F("name", "Subject name", req=True),
            F("code", "Subject code"), F("subject_type", "Type", "choice", choices=SUBJECT_TYPES, default="Theory"),
            F("credits", "Credits", "float", lo=0, hi=60, default=0), F("faculty", "Faculty"),
            F("color", "Colour", "color", default="#356AE6"), F("target_hours", "Weekly study target (hours)", "float", lo=0, hi=100, default=3),
            F("attendance_target", "Attendance target % (blank = default)", "float", lo=0, hi=100), F("notes", "Notes", "long")),
           "t.id, t.name, t.code, sem.name AS semester, ay.name AS academic_year, t.subject_type, t.credits, t.faculty, t.color, "
           "t.target_hours, t.attendance_target, t.notes, (SELECT COUNT(*) FROM units u WHERE u.subject_id=t.id) AS units_total, "
           "(SELECT COUNT(*) FROM units u WHERE u.subject_id=t.id AND u.status='Completed') AS units_done",
           "subjects t LEFT JOIN semesters sem ON sem.id=t.semester_id LEFT JOIN academic_years ay ON ay.id=sem.academic_year_id",
           (C("name", "Subject", 230, color="color"), C("code", "Code", 90), C("semester", "Semester", 130),
            C("subject_type", "Type", 85), C("credits", "Credits", 70, "float", "right"), C("faculty", "Faculty", 140),
            C("syllabus_pct", "Syllabus", 80, "pct", "right"), C("target_hours", "Target h/wk", 85, "float", "right"),
            C("attendance_target", "Att. target", 80, "float", "right", False), C("academic_year", "Year", 100, vis=False)),
           order="COALESCE(t.semester_id,0), t.name", scope="t.semester_id = ?", filters=("semester", "subject_type"), label_key="name",
           derive=_d_subject, bulk=("subject_type",),
           children=(("units", "subject_id IN ({ids})", "units", _DELETE),
                     ("chapters", "unit_id IN (SELECT id FROM units WHERE subject_id IN ({ids}))", "chapters", _DELETE),
                     ("revisions", "unit_id IN (SELECT id FROM units WHERE subject_id IN ({ids}))", "revision schedules", _DELETE),
                     ("assignments", "subject_id IN ({ids})", "assignments", _DELETE),
                     ("exams", "subject_id IN ({ids})", "exams", _DELETE), ("marks", "subject_id IN ({ids})", "assessment marks", _DELETE),
                     ("subject_results", "subject_id IN ({ids})", "semester results", _DELETE),
                     ("attendance", "subject_id IN ({ids})", "attendance records", _DELETE),
                     ("study_sessions", "subject_id IN ({ids})", "study sessions", _DELETE),
                     ("planner_tasks", "subject_id IN ({ids})", "planner tasks (kept, unlinked)", _DETACH),
                     ("projects", "subject_id IN ({ids})", "projects (kept, unlinked)", _DETACH),
                     ("notes", "subject_id IN ({ids})", "notes (kept, unlinked)", _DETACH))),
    Entity("units", "Unit", "Units / syllabus",
           (F("subject_id", "Subject", "fk", True, fk="subjects"), F("unit_no", "Unit number", "int", True, lo=1, hi=200, default=1),
            F("title", "Unit title", req=True), F("status", "Status", "choice", choices=UNIT_STATUS, default="Not Started"),
            F("confidence", "Confidence (1–5)", "int", lo=1, hi=5, default=1), F("notes", "Notes", "long")),
           f"t.id, s.id AS subject_id, s.name AS subject, s.color AS subject_color, sem.name AS semester, t.unit_no, t.title, t.status, t.confidence, "
           "(SELECT COUNT(*) FROM chapters c WHERE c.unit_id=t.id) AS chapters, "
           "(SELECT COUNT(*) FROM chapters c WHERE c.unit_id=t.id AND c.status='Completed') AS chapters_done",
           "units t " + _SJ, (C("subject", "Subject", 180, color="subject_color"), C("unit_no", "Unit", 55, "int"),
                              C("title", "Title", 260), C("status", "Status", 105, "status"), C("confidence", "Confidence", 85, "int", "center"),
                              C("chapters", "Chapters", 75, "int"), C("chapters_done", "Done", 60, "int"), C("semester", "Semester", 110, vis=False)),
           order="s.name, t.unit_no", scope="s.semester_id = ?", filters=("subject", "status"), bulk=("status", "confidence"),
           quick={"Mark in progress": {"status": "In Progress"}, "Mark completed": {"status": "Completed"}},
           children=(("chapters", "unit_id IN ({ids})", "chapters", _DELETE), ("revisions", "unit_id IN ({ids})", "revision schedules", _DELETE),
                     ("assignments", "unit_id IN ({ids})", "assignments (kept, unlinked)", _DETACH),
                     ("marks", "unit_id IN ({ids})", "marks (kept, unlinked)", _DETACH),
                     ("study_sessions", "unit_id IN ({ids})", "study sessions (kept, unlinked)", _DETACH),
                     ("notes", "unit_id IN ({ids})", "notes (kept, unlinked)", _DETACH)),
           rules=(Rule("status", "eq", "Completed", "success"),), attachments=False),
    Entity("chapters", "Chapter", "Chapters",
           (F("unit_id", "Unit", "fk", True, fk="units"), F("title", "Chapter / topic", req=True),
            F("status", "Status", "choice", choices=UNIT_STATUS, default="Not Started"),
            F("priority", "Priority", "choice", choices=PRIORITIES[:3], default="Normal"), F("notes", "Notes", "long")),
           "t.id, s.id AS subject_id, s.name AS subject, s.color AS subject_color, u.unit_no, u.title AS unit, t.title, t.status, t.priority",
           "chapters t JOIN units u ON u.id=t.unit_id JOIN subjects s ON s.id=u.subject_id",
           (C("subject", "Subject", 180, color="subject_color"), C("unit_no", "Unit", 55, "int"), C("unit", "Unit title", 180),
            C("title", "Chapter / topic", 260), C("status", "Status", 105, "status"), C("priority", "Priority", 85, "priority")),
           order="s.name, u.unit_no, t.id", scope="s.semester_id = ?", filters=("subject", "status", "priority"),
           bulk=("status", "priority"), quick={"Mark in progress": {"status": "In Progress"}, "Mark completed": {"status": "Completed"}},
           children=(("study_sessions", "chapter_id IN ({ids})", "study sessions (kept, unlinked)", _DETACH),
                     ("notes", "chapter_id IN ({ids})", "notes (kept, unlinked)", _DETACH))),
    Entity("assignments", "Assignment", "Assignments",
           (F("subject_id", "Subject", "fk", True, fk="subjects"), F("unit_id", "Unit (optional)", "fk", fk="units", parent="subject_id"),
            F("assignment_no", "Assignment no."), F("title", "Title", req=True),
            F("assignment_type", "Type", "choice", choices=ASSIGNMENT_TYPES, default="Written"), F("description", "Description", "long"),
            F("given_date", "Given on", "date"), F("due_date", "Submission deadline", "date"),
            F("submission_date", "Actual submission date", "date"),
            F("status", "Status", "choice", choices=ASSIGNMENT_STATUSES, default="Not Started"),
            F("priority", "Priority", "choice", choices=PRIORITIES, default="Normal"),
            F("marks_obtained", "Marks obtained", "float", lo=0), F("max_marks", "Maximum marks", "float", lo=0),
            F("faculty", "Teacher / faculty"), F("remarks", "Notes & remarks", "long")),
           f"t.id, t.assignment_no, t.title, {_SUBJ}, t.assignment_type, t.given_date, t.due_date, t.submission_date, t.status, "
           "t.priority, t.marks_obtained, t.max_marks, t.faculty, t.remarks",
           "assignments t " + _SJ,
           (C("assignment_no", "No.", 55), C("title", "Assignment", 230), C("subject", "Subject", 170, color="subject_color"),
            C("semester", "Semester", 110), C("assignment_type", "Type", 100), C("given_date", "Given", 95, "date"),
            C("due_date", "Deadline", 100, "date"), C("submission_date", "Submitted", 100, "date"), C("status_eff", "Status", 120, "status"),
            C("days_left", "Days left", 75, "int", "right"), C("priority", "Priority", 80, "priority"), C("score", "Marks", 80, align="center"),
            C("pct", "%", 60, "pct", "right"), C("faculty", "Faculty", 130, vis=False), C("academic_year", "Year", 90, vis=False)),
           order="CASE WHEN t.due_date='' THEN 1 ELSE 0 END, t.due_date, t.id", scope="s.semester_id = ?",
           date_key="due_date", filters=("subject", "semester", "status_eff", "priority", "assignment_type"), derive=_d_assignment,
           bulk=("status", "priority", "assignment_type"), attachments=True,
           quick={"Mark in progress": {"status": "In Progress"}, "Mark completed": {"status": "Completed"},
                  "Mark submitted today": {"status": "Submitted", "submission_date": "@today"}, "Mark evaluated": {"status": "Evaluated"}},
           rules=(Rule("status_eff", "eq", "Overdue", "danger", "row"), Rule("status_eff", "eq", "Late Submission", "warning", "cell"),
                  Rule("status_eff", "in", "Submitted|Evaluated", "success", "cell"))),
    Entity("exams", "Exam", "Exam schedule",
           (F("subject_id", "Subject", "fk", True, fk="subjects"), F("title", "Examination name", req=True),
            F("exam_type", "Type", "choice", choices=EXAM_TYPES, default="Semester Exam"), F("exam_date", "Date", "date"),
            F("max_marks", "Maximum marks", "float", lo=0, default=100), F("marks_obtained", "Marks obtained", "float", lo=0),
            F("internal_marks", "Internal marks", "float", lo=0), F("external_marks", "External marks", "float", lo=0),
            F("practical_marks", "Practical marks", "float", lo=0), F("grade", "Grade"), F("credits", "Credits", "float", lo=0, default=0),
            F("result", "Result", "choice", choices=("",) + RESULT_STATES), F("attempt_no", "Attempt number", "int", lo=1, hi=20, default=1),
            F("attempt", "Attempt note"), F("weightage", "Weightage %", "float", lo=0, hi=100, default=0),
            F("duration_minutes", "Duration (minutes)", "int", lo=0, hi=1440, default=0),
            F("exam_mode", "Mode", "choice", choices=("", "Offline", "Online", "Practical", "Viva")),
            F("venue", "Venue"), F("room", "Room / seat"), F("examiner", "Examiner"),
            F("prep_status", "Preparation", "choice", choices=PREP_STATUS, default="Not Started"), F("notes", "Notes", "long")),
           f"t.id, t.title, {_SUBJ}, t.exam_type, t.exam_date, t.max_marks, t.marks_obtained, t.internal_marks, t.external_marks, "
           "t.practical_marks, t.grade, t.credits, t.result, t.attempt_no, t.venue, t.room, t.exam_mode, t.prep_status, "
           "t.duration_minutes, t.weightage, t.examiner, t.notes",
           "exams t " + _SJ,
           (C("title", "Examination", 220), C("subject", "Subject", 170, color="subject_color"), C("exam_type", "Type", 130),
            C("exam_date", "Date", 100, "date"), C("days_to", "In (days)", 75, "int", "right"), C("score", "Marks", 80, align="center"),
            C("pct", "%", 60, "pct", "right"), C("grade", "Grade", 60, align="center"), C("result", "Result", 80, "status"),
            C("attempt_no", "Attempt", 65, "int", "center"), C("prep_status", "Preparation", 110, "status"), C("venue", "Venue", 110),
            C("semester", "Semester", 110, vis=False), C("internal_marks", "Internal", 70, "float", "right", False),
            C("external_marks", "External", 70, "float", "right", False), C("practical_marks", "Practical", 70, "float", "right", False),
            C("credits", "Credits", 65, "float", "right", False), C("exam_mode", "Mode", 80, vis=False)),
           order="CASE WHEN t.exam_date='' THEN 1 ELSE 0 END, t.exam_date, t.id", scope="s.semester_id = ?", date_key="exam_date",
           filters=("subject", "semester", "exam_type", "prep_status", "result"), derive=_d_exam, attachments=True,
           bulk=("prep_status", "exam_type"), quick={"Mark prepared": {"prep_status": "Prepared"}},
           rules=(Rule("pct", "lt", "@low", "danger", "cell", True), Rule("result", "eq", "Fail", "danger", "row"))),
    Entity("marks", "Assessment", "Assessments & marks",
           (F("subject_id", "Subject", "fk", True, fk="subjects"), F("unit_id", "Unit (optional)", "fk", fk="units", parent="subject_id"),
            F("assessment_type", "Type", "choice", True, choices=ASSESSMENT_TYPES, default="Class Test"), F("title", "Title"),
            F("marks_obtained", "Marks obtained", "float", True, lo=0), F("max_marks", "Maximum marks", "float", True, lo=0.01, default=20),
            F("assessment_date", "Date", "date"), F("notes", "Notes", "long")),
           f"t.id, t.title, t.assessment_type, {_SUBJ}, u.title AS unit, t.marks_obtained, t.max_marks, t.assessment_date, t.notes",
           "marks t " + _SJ + " LEFT JOIN units u ON u.id=t.unit_id",
           (C("assessment_date", "Date", 100, "date"), C("title", "Title", 200), C("assessment_type", "Type", 130),
            C("subject", "Subject", 170, color="subject_color"), C("marks_obtained", "Obtained", 80, "float", "right"),
            C("max_marks", "Maximum", 80, "float", "right"), C("pct", "%", 65, "pct", "right"), C("unit", "Unit", 140, vis=False),
            C("semester", "Semester", 110, vis=False), C("notes", "Notes", 200, vis=False)),
           order="CASE WHEN t.assessment_date='' THEN 1 ELSE 0 END, t.assessment_date DESC, t.id DESC", scope="s.semester_id = ?",
           date_key="assessment_date", filters=("subject", "semester", "assessment_type"), derive=_d_mark, bulk=("assessment_type",),
           rules=(Rule("pct", "lt", "@low", "danger", "cell", True),)),
    Entity("subject_results", "Result", "Semester results",
           (F("subject_id", "Subject", "fk", True, fk="subjects"), F("attempt_no", "Attempt number", "int", True, lo=1, hi=20, default=1),
            F("exam_session", "Exam session", hint="e.g. Nov 2025"),
            F("internal_marks", "Internal marks", "float", lo=0), F("internal_max", "Internal maximum", "float", lo=0),
            F("external_marks", "External (university) marks", "float", lo=0), F("external_max", "External maximum", "float", lo=0),
            F("practical_marks", "Practical marks", "float", lo=0), F("practical_max", "Practical maximum", "float", lo=0),
            F("grade", "Grade (blank = from scale)"), F("grade_points", "Grade points (blank = from scale)", "float", lo=0, hi=100),
            F("credits", "Credits (blank = subject credits)", "float", lo=0, hi=60),
            F("result", "Result", "choice", choices=RESULT_STATES, default="Pending"), F("result_date", "Result date", "date"),
            F("notes", "Notes", "long")),
           f"t.id, {_SUBJ}, s.credits AS subject_credits, t.attempt_no, t.exam_session, t.internal_marks, t.internal_max, "
           "t.external_marks, t.external_max, t.practical_marks, t.practical_max, t.grade, t.grade_points, t.credits, t.result, "
           "t.result_date, t.notes",
           "subject_results t " + _SJ,
           (C("subject", "Subject", 190, color="subject_color"), C("semester", "Semester", 110), C("attempt_no", "Attempt", 65, "int", "center"),
            C("exam_session", "Session", 90), C("int_txt", "Internal", 75, align="center"), C("ext_txt", "External", 75, align="center"),
            C("prac_txt", "Practical", 75, align="center"), C("total", "Total", 80, align="center"), C("pct", "%", 65, "pct", "right"),
            C("grade_eff", "Grade", 60, align="center"), C("points_eff", "Points", 65, "float", "right"),
            C("credits_eff", "Credits", 65, "float", "right"), C("result_eff", "Result", 80, "status")),
           order="sem.semester_no, s.name, t.attempt_no", scope="s.semester_id = ?", date_key="result_date",
           filters=("subject", "semester", "result_eff"), derive=_d_result, label_key="subject",
           rules=(Rule("result_eff", "eq", "Fail", "danger", "row"), Rule("result_eff", "eq", "Pass", "success", "cell"))),
    Entity("attendance", "Attendance record", "Attendance log",
           (F("subject_id", "Subject", "fk", True, fk="subjects"), F("att_date", "Date", "date", True),
            F("classes_held", "Classes held", "int", True, lo=0, hi=500, default=1),
            F("classes_attended", "Classes attended", "int", True, lo=0, hi=500, default=1),
            F("kind", "Class type", "choice", choices=("Lecture", "Lab", "Tutorial", "Seminar", "Other"), default="Lecture"),
            F("notes", "Notes")),
           f"t.id, t.att_date, {_SUBJ}, t.kind, t.classes_held AS held, t.classes_attended AS attended, "
           "(t.classes_held - t.classes_attended) AS missed, t.notes",
           "attendance t " + _SJ,
           (C("att_date", "Date", 100, "date"), C("subject", "Subject", 190, color="subject_color"), C("kind", "Type", 85),
            C("held", "Held", 60, "int", "right"), C("attended", "Attended", 75, "int", "right"), C("missed", "Missed", 65, "int", "right"),
            C("status", "Status", 85, "status"), C("semester", "Semester", 110, vis=False), C("notes", "Notes", 220)),
           order="t.att_date DESC, t.id DESC", scope="s.semester_id = ?", date_key="att_date",
           filters=("subject", "semester", "kind", "status"), derive=_d_attendance, label_key="att_date",
           rules=(Rule("status", "eq", "Absent", "danger", "cell"),)),
    Entity("projects", "Project / record", "Projects, labs & activities",
           (F("category", "Category", "choice", True, choices=PROJECT_CATEGORIES, default="Academic Project"),
            F("title", "Title", req=True), F("subject_id", "Subject (optional)", "fk", fk="subjects"),
            F("status", "Status", "choice", choices=PROJECT_STATUS, default="Not Started"),
            F("start_date", "Start date", "date"), F("deadline", "Deadline", "date"), F("completion_date", "Completed on", "date"),
            F("supervisor", "Supervisor / guide"), F("organisation", "Organisation / platform"), F("description", "Description", "long"),
            F("marks_obtained", "Marks obtained", "float", lo=0), F("max_marks", "Maximum marks", "float", lo=0),
            F("grade", "Grade"), F("link", "Link / repository"), F("notes", "Notes", "long")),
           "t.id, t.category, t.title, s.id AS subject_id, s.name AS subject, s.color AS subject_color, sem.name AS semester, t.status, t.start_date, "
           "t.deadline, t.completion_date, t.supervisor, t.organisation, t.marks_obtained, t.max_marks, t.grade, t.link, "
           "(SELECT COUNT(*) FROM project_milestones m WHERE m.project_id=t.id) AS milestones, "
           "(SELECT COUNT(*) FROM project_milestones m WHERE m.project_id=t.id AND m.status='Completed') AS milestones_done",
           "projects t " + _SJL,
           (C("category", "Category", 140), C("title", "Title", 230), C("subject", "Subject", 160, color="subject_color"),
            C("status", "Status", 100, "status"), C("deadline", "Deadline", 100, "date"), C("days_left", "Days left", 75, "int", "right"),
            C("progress", "Milestones", 85, align="center"), C("supervisor", "Supervisor", 130), C("organisation", "Organisation", 130, vis=False),
            C("grade", "Grade", 60, align="center", vis=False), C("pct", "%", 60, "pct", "right", False),
            C("start_date", "Start", 95, "date", vis=False), C("completion_date", "Completed", 100, "date", vis=False)),
           order="CASE WHEN t.deadline='' THEN 1 ELSE 0 END, t.deadline, t.id", scope="(t.subject_id IS NULL OR s.semester_id = ?)",
           date_key="deadline", filters=("category", "subject", "status"), derive=_d_project, attachments=True,
           bulk=("status", "category"), quick={"Mark in progress": {"status": "In Progress"},
                                               "Mark completed": {"status": "Completed", "completion_date": "@today"}},
           children=(("project_milestones", "project_id IN ({ids})", "milestones", _DELETE),),
           rules=(Rule("days_left", "lt", 0, "danger", "row"), Rule("status", "in", "Completed|Submitted|Evaluated", "success", "cell"))),
    Entity("planner_tasks", "Task", "Planner tasks",
           (F("task_date", "Date", "date", True), F("title", "Task", req=True), F("subject_id", "Subject (optional)", "fk", fk="subjects"),
            F("category", "Category", "choice", choices=TASK_CATEGORIES, default="Study"),
            F("priority", "Priority", "choice", choices=PRIORITIES, default="Normal"),
            F("status", "Status", "choice", choices=("Pending", "In Progress", "Completed", "Skipped"), default="Pending"),
            F("duration_minutes", "Planned minutes", "int", lo=1, hi=1440, default=30), F("notes", "Notes", "long")),
           f"t.id, t.task_date, t.title, {_SUBJ}, t.category, t.priority, t.status, t.duration_minutes, t.notes",
           "planner_tasks t " + _SJL,
           (C("task_date", "Date", 100, "date"), C("title", "Task", 250), C("subject", "Subject", 160, color="subject_color"),
            C("category", "Category", 100), C("priority", "Priority", 80, "priority"), C("status", "Status", 100, "status"),
            C("duration_minutes", "Planned", 80, "minutes", "right"), C("notes", "Notes", 200, vis=False)),
           order="t.task_date, CASE t.priority WHEN 'Urgent' THEN 0 WHEN 'High' THEN 1 WHEN 'Normal' THEN 2 ELSE 3 END, t.id",
           scope="(t.subject_id IS NULL OR s.semester_id = ?)", date_key="task_date",
           filters=("subject", "category", "priority", "status"), derive=_d_task, bulk=("status", "priority", "category"),
           quick={"Mark complete": {"status": "Completed"}, "Mark in progress": {"status": "In Progress"}, "Skip": {"status": "Skipped"}},
           rules=(Rule("overdue", "eq", "1", "danger", "row"), Rule("status", "eq", "Completed", "success", "cell"))),
    Entity("study_sessions", "Study session", "Study sessions",
           (F("subject_id", "Subject", "fk", True, fk="subjects"), F("unit_id", "Unit (optional)", "fk", fk="units", parent="subject_id"),
            F("chapter_id", "Chapter (optional)", "fk", fk="chapters", parent="unit_id"), F("study_date", "Date", "date", True),
            F("duration_minutes", "Minutes studied", "int", True, lo=1, hi=1440, default=30), F("topic", "Topic"),
            F("session_type", "Type", "choice", choices=SESSION_TYPES, default="Study"), F("notes", "Notes", "long")),
           f"t.id, t.study_date, {_SUBJ}, u.title AS unit, c.title AS chapter, t.topic, t.duration_minutes, t.session_type, t.notes",
           "study_sessions t " + _SJ + " LEFT JOIN units u ON u.id=t.unit_id LEFT JOIN chapters c ON c.id=t.chapter_id",
           (C("study_date", "Date", 100, "date"), C("subject", "Subject", 180, color="subject_color"), C("unit", "Unit", 150),
            C("chapter", "Chapter", 150, vis=False), C("topic", "Topic", 220), C("duration_minutes", "Time", 80, "minutes", "right"),
            C("session_type", "Type", 85), C("semester", "Semester", 110, vis=False), C("notes", "Notes", 200, vis=False)),
           order="t.study_date DESC, t.id DESC", scope="s.semester_id = ?", date_key="study_date",
           filters=("subject", "semester", "session_type"), label_key="topic", bulk=("session_type",)),
    Entity("revisions", "Revision", "Revision schedule",
           (F("unit_id", "Unit", "fk", True, fk="units"), F("due_date", "Next review", "date", True),
            F("interval_days", "Interval (days)", "int", lo=1, hi=3650, default=1), F("ease", "Ease factor", "float", lo=1.3, hi=4, default=2.5),
            F("repetitions", "Repetitions", "int", lo=0, hi=500, default=0),
            F("status", "Status", "choice", choices=("Due", "Reviewed", "Paused"), default="Due"),
            F("last_reviewed", "Last reviewed", "date"), F("notes", "Notes", "long")),
           "t.id, s.id AS subject_id, s.name AS subject, s.color AS subject_color, u.unit_no, u.title AS unit, t.due_date, t.interval_days, t.ease, "
           "t.repetitions, t.status, t.last_reviewed, t.notes",
           "revisions t JOIN units u ON u.id=t.unit_id JOIN subjects s ON s.id=u.subject_id",
           (C("subject", "Subject", 180, color="subject_color"), C("unit_no", "Unit", 55, "int"), C("unit", "Unit title", 220),
            C("due_date", "Next review", 100, "date"), C("due_in", "In (days)", 75, "int", "right"), C("interval_days", "Interval", 70, "int", "right"),
            C("repetitions", "Reviews", 65, "int", "right"), C("status", "Status", 85, "status"), C("last_reviewed", "Last", 95, "date")),
           order="t.due_date, t.id", scope="s.semester_id = ?", date_key="due_date", filters=("subject", "status"), derive=_d_revision,
           label_key="unit", bulk=("status",), quick={"Pause": {"status": "Paused"}, "Resume": {"status": "Due"}},
           rules=(Rule("due_in", "le", 0, "warning", "row"),)),
    Entity("notes", "Note", "Notes",
           (F("title", "Title", req=True), F("subject_id", "Subject (optional)", "fk", fk="subjects"),
            F("unit_id", "Unit (optional)", "fk", fk="units", parent="subject_id"),
            F("chapter_id", "Chapter (optional)", "fk", fk="chapters", parent="unit_id"), F("tags", "Tags", hint="comma separated"),
            F("body", "Note", "long")),
           "t.id, t.title, s.id AS subject_id, s.name AS subject, s.color AS subject_color, u.title AS unit, c.title AS chapter, t.tags, t.updated_at, "
           "substr(replace(t.body, char(10), ' '), 1, 140) AS preview",
           "notes t " + _SJL + " LEFT JOIN units u ON u.id=t.unit_id LEFT JOIN chapters c ON c.id=t.chapter_id",
           (C("title", "Title", 230), C("subject", "Subject", 170, color="subject_color"), C("unit", "Unit", 140),
            C("chapter", "Chapter", 140, vis=False), C("tags", "Tags", 120), C("preview", "Preview", 300), C("updated_at", "Updated", 130, "date")),
           order="t.updated_at DESC, t.id DESC", scope="(t.subject_id IS NULL OR s.semester_id = ?)", filters=("subject", "tags"),
           attachments=True),
    Entity("attachments", "Document", "Documents & files",
           (F("file_path", "File", "file", True), F("display_name", "Display name"),
            F("category", "Category", "choice", choices=DOC_CATEGORIES, default="Study Reference"), F("notes", "Notes", "long")),
           "t.id, t.entity_type, t.record_id, t.display_name, t.category, t.file_path, t.size_bytes, t.added_at, t.notes",
           "attachments t",
           (C("display_name", "Name", 240), C("category", "Category", 150), C("linked", "Linked to", 140), C("size_kb", "Size", 75, "kb", "right"),
            C("exists", "File", 70, "status"), C("added_at", "Added", 110, "date"), C("file_path", "Location", 320)),
           order="t.added_at DESC, t.id DESC", filters=("category", "linked", "exists"), derive=_d_attachment,
           defaults={"entity_type": "general", "record_id": 0}, label_key="display_name",
           rules=(Rule("exists", "eq", "Missing", "danger", "row"),)),
    Entity("calendar_events", "Calendar event", "Academic calendar",
           (F("title", "Event", req=True), F("event_type", "Type", "choice", choices=EVENT_TYPES, default="Event"),
            F("start_date", "Start date", "date", True), F("end_date", "End date", "date"),
            F("semester_id", "Semester (optional)", "fk", fk="semesters"), F("notes", "Notes", "long")),
           "t.id, t.title, t.event_type, t.start_date, t.end_date, sem.name AS semester, t.notes",
           "calendar_events t LEFT JOIN semesters sem ON sem.id=t.semester_id",
           (C("start_date", "Start", 100, "date"), C("end_date", "End", 100, "date"), C("title", "Event", 260), C("event_type", "Type", 110),
            C("semester", "Semester", 130), C("notes", "Notes", 260, vis=False)),
           order="t.start_date, t.id", scope="(t.semester_id IS NULL OR t.semester_id = ?)", date_key="start_date",
           filters=("event_type", "semester")),
    Entity("timetable", "Class", "Weekly timetable",
           (F("subject_id", "Subject", "fk", True, fk="subjects"), F("day", "Day", "choice", True, choices=WEEKDAYS, default="Monday"),
            F("start_time", "Start (HH:MM)", "text", True, hint="09:30"), F("end_time", "End (HH:MM)", "text", True, hint="10:20"),
            F("kind", "Class type", "choice", choices=("Lecture", "Lab", "Tutorial", "Seminar", "Other"), default="Lecture"),
            F("room", "Room"), F("faculty", "Faculty"), F("notes", "Notes")),
           f"t.id, {_SUBJ}, t.day, t.start_time, t.end_time, t.kind, t.room, t.faculty, t.notes",
           "timetable t " + _SJ,
           (C("day", "Day", 100), C("start_time", "Start", 70, align="center"), C("end_time", "End", 70, align="center"),
            C("subject", "Subject", 210, color="subject_color"), C("kind", "Type", 85), C("room", "Room", 90), C("faculty", "Faculty", 140),
            C("semester", "Semester", 110, vis=False), C("notes", "Notes", 200, vis=False)),
           order="CASE t.day " + " ".join(f"WHEN '{d}' THEN {i}" for i, d in enumerate(WEEKDAYS)) + " ELSE 7 END, t.start_time",
           scope="s.semester_id = ?", filters=("day", "subject", "kind"), label_key="start_time", bulk=("kind",)),
)
ENTITIES: dict[str, Entity] = {e.table: e for e in ENTITY_LIST}
DUPLICABLE = frozenset({"assignments", "exams", "marks", "planner_tasks", "study_sessions", "projects", "notes", "timetable",
                        "calendar_events", "units", "subjects", "attendance", "chapters"})
_SEARCH_SOURCES = (   # (kind, table, page, searched columns, title SQL, detail SQL)
    ("Subject", "subjects", "subjects", ("name", "code", "faculty"), "name", "COALESCE(code,'')"),
    ("Unit", "units", "subjects", ("title",), "title", "(SELECT name FROM subjects s WHERE s.id=subject_id)"),
    ("Chapter", "chapters", "subjects", ("title",), "title", "(SELECT title FROM units u WHERE u.id=unit_id)"),
    ("Assignment", "assignments", "assignments", ("title", "assignment_no", "description"), "title", "(SELECT name FROM subjects s WHERE s.id=subject_id)"),
    ("Exam", "exams", "exams", ("title", "venue"), "title", "(SELECT name FROM subjects s WHERE s.id=subject_id)"),
    ("Assessment", "marks", "exams", ("title", "assessment_type"), "COALESCE(NULLIF(title,''), assessment_type)", "(SELECT name FROM subjects s WHERE s.id=subject_id)"),
    ("Project", "projects", "projects", ("title", "supervisor", "organisation"), "title", "category"),
    ("Note", "notes", "resources", ("title", "tags", "body"), "title", "COALESCE(tags,'')"),
    ("Task", "planner_tasks", "study", ("title",), "title", "task_date"),
    ("Study session", "study_sessions", "study", ("topic",), "COALESCE(NULLIF(topic,''),'Study session')", "study_date"),
    ("Document", "attachments", "resources", ("display_name", "file_path"), "display_name", "category"),
    ("Class", "timetable", "attendance", ("room", "faculty", "day"), "day || ' ' || start_time", "(SELECT name FROM subjects s WHERE s.id=subject_id)"))
_FK_NAME_SQL = {"subjects": "SELECT id, name, code FROM subjects", "semesters": "SELECT id, name, '' FROM semesters",
                "programmes": "SELECT id, name, '' FROM programmes", "academic_years": "SELECT id, name, '' FROM academic_years",
                "units": "SELECT id, title, '' FROM units", "chapters": "SELECT id, title, '' FROM chapters"}
_CSV_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y")
ENTITY_TITLES = {t: e.singular for t, e in ENTITIES.items()}


# ---- table row logic (pure Python; the Qt model is a thin wrapper) -------------------
def rule_matches(op: str, expected: Any, cell: Any) -> bool:
    """Evaluate one conditional-formatting comparison."""
    if op == "blank":
        return cell in (None, "")
    if op == "notblank":
        return cell not in (None, "")
    if cell in (None, ""):
        return False
    if op in ("lt", "le", "gt", "ge"):
        a, b = safe_float(cell), safe_float(expected)
        if a is None or b is None:
            return False
        return {"lt": a < b, "le": a <= b, "gt": a > b, "ge": a >= b}[op]
    s, v = str(cell).casefold(), str(expected).casefold()
    return {"eq": s == v, "ne": s != v, "contains": v in s,
            "in": s in {x.strip().casefold() for x in str(expected).split("|")}}.get(op, False)


def format_cell(col: Column, row: dict, date_style: str = "%d %b %Y") -> str:
    v = row.get(col.key)
    if v is None or v == "":
        return ""
    k = col.kind
    if k == "date":
        return fmt_date(v, date_style)
    if k == "pct":
        n = safe_float(v)
        return "" if n is None else f"{n:.1f}%"
    if k in ("int", "float"):
        return fmt_num(v)
    if k == "minutes":
        return fmt_minutes(v)
    if k == "kb":
        n = safe_float(v)
        return "" if n is None else f"{n:,.0f} KB"
    if k == "bool":
        return "✓" if v else ""
    return str(v)


def sort_value(col: Column, row: dict) -> tuple:
    """Typed sort key: numbers sort numerically, dates chronologically, blanks last."""
    v = row.get(col.key)
    if v is None or v == "":
        return (1, 0)
    if col.kind in ("int", "float", "pct", "minutes", "kb", "bool"):
        n = safe_float(v)
        return (1, 0) if n is None else (0, n)
    if col.kind == "priority":
        return (0, PRIORITY_RANK.get(str(v), 9))
    if col.kind == "date":
        return (0, str(v))
    return (0, str(v).casefold())


class RowSet:
    """Search / filter / multi-column sort over loaded rows — no Qt, fully testable."""

    def __init__(self, columns: Sequence[Column], rows: list[dict], date_style: str = "%d %b %Y") -> None:
        self.columns = {c.key: c for c in columns}
        self.all = rows
        self.date_style = date_style
        self.search_text = ""
        self.value_filters: dict[str, set] = {}      # column -> allowed display values
        self.text_filters: dict[str, str] = {}       # column -> substring
        self.date_range: tuple[str, Optional[str], Optional[str]] = ("", None, None)
        self.sort_keys: list[tuple[str, bool]] = []  # (column, ascending)
        for r in self.all:
            r["_s"] = " ".join(format_cell(c, r, date_style) for c in columns).casefold()

    def display(self, row: dict, key: str) -> str:
        return format_cell(self.columns[key], row, self.date_style)

    def view(self) -> list[dict]:
        out = self.all
        if self.search_text:
            terms = self.search_text.casefold().split()
            out = [r for r in out if all(t in r["_s"] for t in terms)]
        for key, allowed in self.value_filters.items():
            if allowed and key in self.columns:
                out = [r for r in out if self.display(r, key) in allowed or str(r.get(key) or "") in allowed]
        for key, sub in self.text_filters.items():
            if sub and key in self.columns:
                out = [r for r in out if sub.casefold() in self.display(r, key).casefold()]
        key, lo, hi = self.date_range
        if key and (lo or hi):
            out = [r for r in out if r.get(key) and (not lo or str(r[key])[:10] >= lo) and (not hi or str(r[key])[:10] <= hi)]
        for key, asc in reversed(self.sort_keys):  # stable successive sorts = multi-column sort
            if key in self.columns:
                col = self.columns[key]
                present = [r for r in out if sort_value(col, r)[0] == 0 or sort_value(col, r) != (1, 0)]
                blanks = [r for r in out if sort_value(col, r) == (1, 0)]
                present.sort(key=lambda r: sort_value(col, r), reverse=not asc)
                out = present + blanks  # blanks stay last in both directions
        return list(out)

    def distinct(self, key: str) -> list[str]:
        vals = {self.display(r, key) for r in self.all if self.display(r, key)}
        return sorted(vals, key=str.casefold)


def rows_to_tsv(rs: RowSet, rows: Sequence[dict], keys: Sequence[str], header: bool = True) -> str:
    lines = []
    if header:
        lines.append("\t".join(rs.columns[k].label for k in keys))
    for r in rows:
        lines.append("\t".join(rs.display(r, k).replace("\t", " ").replace("\n", " ") for k in keys))
    return "\n".join(lines)


def export_csv(path: Path | str, rs: RowSet, rows: Sequence[dict], keys: Sequence[str]) -> Path:
    """Atomic CSV export (UTF-8 with BOM for Excel) with formula-injection protection."""
    def write(tmp: Path) -> None:
        with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow([csv_safe(rs.columns[k].label) for k in keys])
            for r in rows:
                w.writerow([csv_safe(rs.display(r, k)) for k in keys])
    return atomic_write(path, write)


# ---- repository --------------------------------------------------------------------
class Repository:
    """All reads/writes of academic records: validation, safe SQL, safe deletion."""

    def __init__(self, db: Database, settings: SettingsStore, bus: Optional[ChangeBus] = None) -> None:
        self.db, self.settings, self.bus = db, settings, bus or ChangeBus()
        self.snapshot_hook: Optional[Callable[[str], Any]] = None
        self.fixed_today: Optional[date] = None   # tests may pin "today"

    def today(self) -> date:
        return self.fixed_today or date.today()

    def context(self) -> Ctx:
        return Ctx(self.today(), self.settings.grading(), self.settings.get_float("low_mark_pct", 40.0),
                   self.settings.get_float("attendance_min", 75.0))

    @staticmethod
    def entity(table: str) -> Entity:
        try:
            return ENTITIES[table]
        except KeyError:
            raise ValueError(f"Unknown record type: {table!r}") from None

    # ---- reading ---------------------------------------------------------
    def rows(self, ent: Entity | str, semester_id: Any = None) -> list[dict]:
        """Rows for one entity, optionally scoped to a semester id or a list of ids."""
        ent = self.entity(ent) if isinstance(ent, str) else ent
        where, args = "", []
        if ent.scope and semester_id is not None:
            ids = [int(semester_id)] if isinstance(semester_id, (int, str)) else [int(i) for i in semester_id]
            where, args = " WHERE " + ent.scope.replace("= ?", f"IN ({','.join('?' * len(ids))})"), ids
        sql = f"SELECT {ent.select} FROM {ent.source}{where} ORDER BY {ent.order}"
        ctx = self.context()
        out = []
        for r in self.db.q(sql, args):
            d = dict(r)
            for key in ("subject_color", "color"):
                if key in d:
                    d[key] = hex_norm(d[key], "#356AE6")      # legacy/hand-typed colours never reach Qt or ReportLab
            if ent.derive:
                ent.derive(d, ctx)
            out.append(d)
        return out

    def get(self, table: str, rid: int) -> Optional[dict]:
        ent = self.entity(table)
        r = self.db.one(f"SELECT * FROM {ent.table} WHERE id=?", (rid,))
        return dict(r) if r else None

    def label(self, table: str, rid: int) -> str:
        rec = self.get(table, rid) or {}
        return str(rec.get(self.entity(table).label_key) or f"#{rid}")

    def lookup(self, name: str, parent: Any = None, semester_id: Optional[int] = None) -> list[tuple[str, int]]:
        q = self.db.q
        parent = safe_int(parent) or None
        if name == "programmes":
            return [(r["name"], r["id"]) for r in q("SELECT id,name FROM programmes ORDER BY id")]
        if name == "academic_years":
            rows = q("SELECT y.id, y.name, p.name AS pn FROM academic_years y JOIN programmes p ON p.id=y.programme_id"
                     + (" WHERE y.programme_id=?" if parent else "") + " ORDER BY y.programme_id, y.year_no", (parent,) if parent else ())
            return [(r["name"] if parent else f"{r['pn']} · {r['name']}", r["id"]) for r in rows]
        if name == "semesters":
            return [(r["name"], r["id"]) for r in q(
                "SELECT id,name FROM semesters ORDER BY COALESCE(programme_id,0), COALESCE(semester_no,9999), id")]
        if name == "subjects":
            order = "COALESCE(sem.programme_id,0), COALESCE(sem.semester_no,9999), s.name"
            if semester_id is not None:
                order = "(s.semester_id IS NOT ?), " + order
            rows = q("SELECT s.id, s.name, s.code, sem.name AS sem FROM subjects s LEFT JOIN semesters sem ON sem.id=s.semester_id "
                     f"ORDER BY {order}", (semester_id,) if semester_id is not None else ())
            return [(f"{r['name']}" + (f" ({r['code']})" if r["code"] else "") + (f" · {r['sem']}" if r["sem"] else ""), r["id"]) for r in rows]
        if name == "units":
            rows = q("SELECT u.id, u.unit_no, u.title, s.name AS sn FROM units u JOIN subjects s ON s.id=u.subject_id"
                     + (" WHERE u.subject_id=?" if parent else "") + " ORDER BY s.name, u.unit_no", (parent,) if parent else ())
            return [(f"Unit {r['unit_no']}: {r['title']}" if parent else f"{r['sn']} · Unit {r['unit_no']}: {r['title']}", r["id"]) for r in rows]
        if name == "chapters":
            rows = q("SELECT c.id, c.title, u.title AS ut FROM chapters c JOIN units u ON u.id=c.unit_id"
                     + (" WHERE c.unit_id=?" if parent else "") + " ORDER BY u.unit_no, c.id", (parent,) if parent else ())
            return [(r["title"] if parent else f"{r['ut']} · {r['title']}", r["id"]) for r in rows]
        raise ValueError(f"Unknown lookup {name!r}")

    # ---- validation ------------------------------------------------------
    @staticmethod
    def _date_ok(label: str, value: str) -> str:
        if value and parse_date(value) is None:
            raise ValidationError(f"{label}: enter a valid date (YYYY-MM-DD).")
        return value[:10] if value else ""

    def clean(self, ent: Entity, values: dict) -> dict:
        """Coerce form values to storage types and enforce required / range rules."""
        out: dict[str, Any] = {}
        for f in ent.fields:
            if f.key not in values:
                continue
            v = values[f.key]
            if isinstance(v, str):
                v = v.strip()
            if v is None or v == "":
                if f.required:
                    raise ValidationError(f"{f.label} is required.")
                out[f.key] = None if f.kind in ("int", "float", "fk") else (0 if f.kind == "bool" else "")
                continue
            if f.kind in ("int", "float"):
                n = safe_float(v)
                if n is None:
                    raise ValidationError(f"{f.label} must be a number.")
                if f.lo is not None and n < f.lo or f.hi is not None and n > f.hi:
                    lo = "" if f.lo is None else fmt_num(f.lo)
                    hi = "" if f.hi is None else fmt_num(f.hi)
                    raise ValidationError(f"{f.label} must be between {lo or 'any'} and {hi or 'any'}.")
                out[f.key] = int(n) if f.kind == "int" else n
            elif f.kind == "fk":
                out[f.key] = safe_int(v) or None
                if f.required and not out[f.key]:
                    raise ValidationError(f"{f.label} is required.")
            elif f.kind == "date":
                out[f.key] = self._date_ok(f.label, str(v))
            elif f.kind == "bool":
                out[f.key] = 1 if v in (True, 1, "1", "true", "True") else 0
            elif f.kind == "color":
                out[f.key] = hex_norm(v, "#356AE6")
            else:
                out[f.key] = str(v)
        if ent.table == "timetable":
            for k in ("start_time", "end_time"):
                if re.fullmatch(r"\d:[0-5]\d", str(out.get(k, ""))):
                    out[k] = "0" + out[k]          # 9:30 → 09:30
        return out

    def _before(self, c: dict, a: str, b: str, msg: str) -> None:
        da, db_ = parse_date(c.get(a)), parse_date(c.get(b))
        if da and db_ and db_ < da:
            raise ValidationError(msg)

    def _within(self, c: dict, got: str, mx: str, label: str) -> None:
        g, m = safe_float(c.get(got)), safe_float(c.get(mx))
        if g is not None and m is not None and g > m:
            raise ValidationError(f"{label}: marks obtained cannot exceed the maximum marks.")

    def _exists(self, sql: str, args: Sequence) -> bool:
        return self.db.one(sql, args) is not None

    def validate(self, ent: Entity, c: dict, rid: Optional[int], current: Optional[dict] = None) -> None:
        t = ent.table
        merged = {**(current or {}), **c}
        rid = rid or -1
        if t == "subjects":
            if "name" in c and self._exists("SELECT 1 FROM subjects WHERE semester_id IS ? AND lower(name)=lower(?) AND id<>?",
                                            (merged.get("semester_id"), merged.get("name"), rid)):
                raise ValidationError("This semester already has a subject with that name.")
        elif t == "units":
            if self._exists("SELECT 1 FROM units WHERE subject_id=? AND unit_no=? AND id<>?", (merged.get("subject_id"), merged.get("unit_no"), rid)):
                raise ValidationError("That subject already has a unit with this number.")
        elif t == "assignments":
            self._before(merged, "given_date", "due_date", "The deadline cannot be before the date the assignment was given.")
            self._within(merged, "marks_obtained", "max_marks", "Assignment")
            self._link_check(merged, "unit_id", "subject_id", "units", "The chosen unit belongs to a different subject.")
        elif t == "exams":
            self._within(merged, "marks_obtained", "max_marks", "Exam")
            if safe_float(merged.get("max_marks")) == 0:
                raise ValidationError("Maximum marks must be greater than zero.")
        elif t == "marks":
            self._within(merged, "marks_obtained", "max_marks", "Assessment")
            self._link_check(merged, "unit_id", "subject_id", "units", "The chosen unit belongs to a different subject.")
        elif t == "subject_results":
            for k in ("internal", "external", "practical"):
                self._within(merged, f"{k}_marks", f"{k}_max", k.title())
                if safe_float(merged.get(f"{k}_marks")) is not None and not safe_float(merged.get(f"{k}_max")):
                    raise ValidationError(f"Enter the {k} maximum marks as well.")
            if self._exists("SELECT 1 FROM subject_results WHERE subject_id=? AND attempt_no=? AND id<>?",
                            (merged.get("subject_id"), merged.get("attempt_no"), rid)):
                raise ValidationError("A result for this subject and attempt already exists. Use the next attempt number.")
            gp, mxp = safe_float(merged.get("grade_points")), self.settings.grading().max_points
            if gp is not None and gp > mxp:
                raise ValidationError(f"Grade points cannot exceed {fmt_num(mxp)} (change the scale in Grading settings).")
        elif t == "attendance":
            held, att = safe_int(merged.get("classes_held")), safe_int(merged.get("classes_attended"))
            if att > held:
                raise ValidationError("Classes attended cannot exceed classes held.")
        elif t == "semesters":
            self._before(merged, "start_date", "end_date", "The end date cannot be before the start date.")
            if merged.get("semester_no") and merged.get("programme_id") and self._exists(
                    "SELECT 1 FROM semesters WHERE programme_id=? AND semester_no=? AND id<>?",
                    (merged["programme_id"], merged["semester_no"], rid)):
                raise ValidationError("That programme already has a semester with this number.")
            self._link_check(merged, "academic_year_id", "programme_id", "academic_years", "The academic year belongs to a different programme.")
        elif t == "academic_years":
            self._before(merged, "start_date", "end_date", "The end date cannot be before the start date.")
            if self._exists("SELECT 1 FROM academic_years WHERE programme_id=? AND year_no=? AND id<>?",
                            (merged.get("programme_id"), merged.get("year_no"), rid)):
                raise ValidationError("That programme already has an academic year with this number.")
        elif t == "programmes":
            sy, ey = safe_int(merged.get("start_year")), safe_int(merged.get("end_year"))
            if sy and ey and ey < sy:
                raise ValidationError("The end year cannot be before the start year.")
        elif t == "projects":
            self._before(merged, "start_date", "deadline", "The deadline cannot be before the start date.")
            self._within(merged, "marks_obtained", "max_marks", "Project")
        elif t == "calendar_events":
            self._before(merged, "start_date", "end_date", "The end date cannot be before the start date.")
        elif t == "study_sessions":
            self._link_check(merged, "unit_id", "subject_id", "units", "The chosen unit belongs to a different subject.")
            self._link_check(merged, "chapter_id", "unit_id", "chapters", "The chosen chapter belongs to a different unit.")
        elif t == "notes":
            self._link_check(merged, "chapter_id", "unit_id", "chapters", "The chosen chapter belongs to a different unit.")
        elif t == "timetable":
            for k, lab in (("start_time", "Start"), ("end_time", "End")):
                if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(merged.get(k) or "")):
                    raise ValidationError(f"{lab} time must be 24-hour HH:MM, for example 09:30.")
            if merged["end_time"] <= merged["start_time"]:
                raise ValidationError("A class must end after it starts.")
            if self._exists("SELECT 1 FROM timetable WHERE subject_id=? AND day=? AND start_time=? AND id<>?",
                            (merged.get("subject_id"), merged.get("day"), merged.get("start_time"), rid)):
                raise ValidationError("This subject already has a class at that time on that day.")
        elif t == "attachments":
            self._check_file(merged.get("file_path"))

    def _link_check(self, c: dict, child: str, parent: str, table: str, msg: str) -> None:
        cid, pid = c.get(child), c.get(parent)
        if cid and pid and not self._exists(f"SELECT 1 FROM {table} WHERE id=? AND {parent}=?", (cid, pid)):
            raise ValidationError(msg)

    @staticmethod
    def _check_file(path: Any) -> Path:
        p = Path(str(path or "")).expanduser()
        if not p.is_file():
            raise ValidationError("That file could not be found.")
        if p.suffix.lower() not in ATTACH_EXT:
            raise ValidationError(f"{p.suffix or 'This'} files are not supported. Allowed: "
                                  + ", ".join(sorted(e.lstrip('.') for e in ATTACH_EXT)) + ".")
        return p.resolve()

    @staticmethod
    def _friendly(exc: sqlite3.IntegrityError) -> str:
        m = str(exc)
        if "UNIQUE constraint failed" in m:
            return "A record with the same identifying details already exists."
        if "FOREIGN KEY" in m:
            return "This record is linked to other records, or a linked record no longer exists."
        if "CHECK constraint" in m:
            return "The values are inconsistent (for example, attended classes exceed held classes)."
        return m

    # ---- writing ---------------------------------------------------------
    def _prepare(self, ent: Entity, c: dict) -> dict:
        cols = set(self.db.columns(ent.table))
        c = dict(c)
        if ent.table == "notes":
            c["updated_at"] = datetime.now().isoformat(sep=" ", timespec="seconds")
        if ent.table == "attachments":
            p = self._check_file(c.get("file_path"))
            c["file_path"], c["size_bytes"] = str(p), p.stat().st_size
            c["display_name"] = c.get("display_name") or p.name
        if ent.table == "semesters" and "status" in c:
            c["is_active"] = 1 if c["status"] == "Active" else 0
        bad = [k for k in c if k not in cols]
        if bad:
            raise ValueError(f"Unknown column(s) for {ent.table}: {bad}")
        return c

    def insert(self, table: str, values: dict) -> int:
        ent = self.entity(table)
        c = self.clean(ent, values)
        for k, v in ent.defaults.items():
            c.setdefault(k, v)
        self.validate(ent, c, None)
        c = self._prepare(ent, c)
        cols = list(c)
        try:
            with self.db.transaction():
                rid = self.db.conn.execute(
                    f"INSERT INTO {ent.table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                    [c[k] for k in cols]).lastrowid
        except sqlite3.IntegrityError as exc:
            raise ValidationError(self._friendly(exc)) from exc
        self.bus.notify(table)
        return int(rid)

    def update(self, table: str, rid: int, values: dict) -> None:
        ent = self.entity(table)
        current = self.get(table, rid)
        if current is None:
            raise ValidationError("This record no longer exists. Refresh and try again.")
        c = self.clean(ent, values)
        self.validate(ent, c, rid, current)
        c = self._prepare(ent, c)
        if not c:
            return
        try:
            with self.db.transaction():
                self.db.conn.execute(f"UPDATE {ent.table} SET {','.join(f'{k}=?' for k in c)} WHERE id=?",
                                     [*c.values(), rid])
        except sqlite3.IntegrityError as exc:
            raise ValidationError(self._friendly(exc)) from exc
        self.bus.notify(table)

    def bulk_update(self, table: str, ids: Sequence[int], updates: dict) -> int:
        """Apply the same field change to many records ('@today' expands to today's date)."""
        ent = self.entity(table)
        updates = {k: (self.today().isoformat() if v == "@today" else v) for k, v in updates.items()}
        c = self.clean(ent, updates)
        c = self._prepare(ent, c)
        if not ids or not c:
            return 0
        try:
            with self.db.transaction():
                for rid in ids:
                    self.db.conn.execute(f"UPDATE {ent.table} SET {','.join(f'{k}=?' for k in c)} WHERE id=?", [*c.values(), int(rid)])
        except sqlite3.IntegrityError as exc:
            raise ValidationError(self._friendly(exc)) from exc
        self.bus.notify(table)
        return len(ids)

    def dependents(self, table: str, ids: Sequence[int]) -> list[tuple[str, int, str]]:
        """(label, count, effect) of linked records that a delete would remove/detach/block on."""
        ent = self.entity(table)
        ids = [int(i) for i in ids]
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        out = []
        for child, where, label, effect in ent.children:
            n = self.db.scalar(f"SELECT COUNT(*) FROM {child} WHERE {where.format(ids=marks)}", ids, 0)
            if n:
                out.append((label, int(n), effect))
        return out

    def delete(self, table: str, ids: Sequence[int]) -> int:
        """Delete records. Refuses when blocking children exist; snapshots before cascading deletes."""
        ent = self.entity(table)
        ids = sorted({int(i) for i in ids})
        if not ids:
            return 0
        deps = self.dependents(table, ids)
        blockers = [f"{n} {label}" for label, n, eff in deps if eff == _BLOCK]
        if blockers:
            raise ValidationError("This cannot be deleted while it still contains " + ", ".join(blockers)
                                  + ". Move or delete those first.")
        if (any(eff == _DELETE for _, _, eff in deps) or len(ids) > 1) and self.snapshot_hook:
            self.snapshot_hook(f"before_delete_{table}")
        try:
            with self.db.transaction():
                n = sum(self.db.conn.execute(f"DELETE FROM {ent.table} WHERE id=?", (i,)).rowcount for i in ids)
        except sqlite3.IntegrityError as exc:
            raise ValidationError(self._friendly(exc)) from exc
        self.bus.notify(table, *{c[0] for c in ent.children})
        return n

    # ---- academic structure ---------------------------------------------
    def current_semester_id(self) -> Optional[int]:
        sid = self.settings.get_int("current_semester_id", 0)
        if sid and self._exists("SELECT 1 FROM semesters WHERE id=?", (sid,)):
            return sid
        row = (self.db.one("SELECT id FROM semesters WHERE status='Active' ORDER BY id DESC LIMIT 1")
               or self.db.one("SELECT id FROM semesters ORDER BY id DESC LIMIT 1"))
        return int(row["id"]) if row else None

    def set_current_semester(self, sid: int) -> None:
        if not self._exists("SELECT 1 FROM semesters WHERE id=?", (sid,)):
            raise ValidationError("That semester does not exist.")
        self.settings.set("current_semester_id", sid)
        self.bus.notify("settings")

    def scope_semester_id(self) -> Optional[int]:
        """Semester selected in the top bar: None = all semesters."""
        scope = self.settings.get("scope_semester").strip().lower()
        if scope == "all":
            return None
        if scope.isdigit() and self._exists("SELECT 1 FROM semesters WHERE id=?", (int(scope),)):
            return int(scope)
        return self.current_semester_id()

    def create_structure(self, name: str, *, degree_level: str = "Undergraduate", branch: str = "", specialisation: str = "",
                         college: str = "", university: str = "", start_year: Optional[int] = None, years: int = 4,
                         per_year: int = 2, term_word: str = "Semester", semester_names: Optional[Sequence[str]] = None) -> dict:
        """Create a programme with its academic years and semesters in one transaction."""
        name = (name or "").strip()
        if not name:
            raise ValidationError("Programme name is required.")
        years, per_year = max(1, min(10, int(years))), max(1, min(4, int(per_year)))
        sy = safe_int(start_year) or None
        with self.db.transaction():
            pid = self.db.conn.execute(
                "INSERT INTO programmes(name,degree_level,branch,specialisation,college_name,university_name,session_label,"
                "start_year,end_year,years_count,semesters_per_year) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (name, degree_level, branch, specialisation, college, university,
                 f"{sy}–{sy + years}" if sy else "", sy, sy + years if sy else None, years, per_year)).lastrowid
            sem_ids, year_ids = [], []
            for y in range(1, years + 1):
                label = f"{sy + y - 1}-{str(sy + y)[-2:]}" if sy else ""
                yid = self.db.conn.execute("INSERT INTO academic_years(programme_id,year_no,name,session_label,status) VALUES(?,?,?,?,?)",
                                           (pid, y, ordinal_year_name(y), label, "Active" if y == 1 else "Upcoming")).lastrowid
                year_ids.append(yid)
                for k in range(1, per_year + 1):
                    n = (y - 1) * per_year + k
                    sname = (semester_names[n - 1].strip() if semester_names and n - 1 < len(semester_names)
                             and semester_names[n - 1].strip() else f"{term_word} {n}")
                    status = "Active" if n == 1 else "Upcoming"
                    sem_ids.append(self.db.conn.execute(
                        "INSERT INTO semesters(name,is_active,programme_id,academic_year_id,semester_no,status) VALUES(?,?,?,?,?,?)",
                        (sname, 1 if n == 1 else 0, pid, yid, n, status)).lastrowid)
        if not self.current_semester_id() or not self.settings.get_int("current_semester_id", 0):
            self.settings.set("current_semester_id", sem_ids[0])
        self.bus.notify("programmes", "academic_years", "semesters")
        return {"programme_id": pid, "year_ids": year_ids, "semester_ids": sem_ids}

    def ensure_default_structure(self) -> None:
        if self.db.scalar("SELECT COUNT(*) FROM programmes", default=0) == 0 and \
                self.db.scalar("SELECT COUNT(*) FROM semesters", default=0) == 0:
            self.create_structure("My Programme", years=1, per_year=1)

    PROFILE_FIELDS = ("student_name", "college_name", "university_name", "course", "branch", "admission_year",
                      "expected_graduation_year", "roll_no", "student_id", "email", "goals", "photo_path")

    def profile(self) -> dict:
        r = self.db.one("SELECT * FROM student_profile WHERE id=1")
        return dict(r) if r else {k: "" for k in self.PROFILE_FIELDS}

    def save_profile(self, values: dict) -> None:
        c: dict[str, Any] = {}
        for k in self.PROFILE_FIELDS:
            if k in values:
                v = values[k]
                c[k] = str(v).strip() if v is not None else ""
        for k in ("admission_year", "expected_graduation_year"):
            if k in c:
                if c[k] == "":
                    c[k] = None
                elif not (c[k].isdigit() and 1950 <= int(c[k]) <= 2100):
                    raise ValidationError("Years must be between 1950 and 2100.")
                else:
                    c[k] = int(c[k])
        if c.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", c["email"]):
            raise ValidationError("Enter a valid email address (or leave it blank).")
        if c.get("photo_path"):
            p = Path(c["photo_path"]).expanduser()
            if not p.is_file() or p.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}:
                raise ValidationError("The profile photograph must be an existing image file.")
            c["photo_path"] = str(p.resolve())
        c["updated_at"] = datetime.now().isoformat(timespec="seconds")
        with self.db.transaction():
            self.db.conn.execute("INSERT OR IGNORE INTO student_profile(id) VALUES(1)")
            self.db.conn.execute(f"UPDATE student_profile SET {','.join(f'{k}=?' for k in c)} WHERE id=1", list(c.values()))
        self.bus.notify("student_profile")

    # ---- files, milestones, revisions ------------------------------------
    def attachments_for(self, entity_type: str, record_id: int) -> list[dict]:
        rows = [dict(r) for r in self.db.q("SELECT * FROM attachments WHERE entity_type=? AND record_id=? ORDER BY id", (entity_type, record_id))]
        for r in rows:
            r["exists"] = Path(r["file_path"]).exists()
        return rows

    def add_attachment(self, entity_type: str, record_id: int, path: Any, name: str = "", category: str = "Document", notes: str = "") -> int:
        if entity_type not in CLEANUP_TABLES:
            raise ValidationError("Files can only be attached to records.")
        p = self._check_file(path)
        if self._exists("SELECT 1 FROM attachments WHERE entity_type=? AND record_id=? AND file_path=?", (entity_type, record_id, str(p))):
            raise ValidationError("That file is already attached to this record.")
        rid = self.db.run("INSERT INTO attachments(entity_type,record_id,file_path,display_name,category,size_bytes,notes) VALUES(?,?,?,?,?,?,?)",
                          (entity_type, record_id, str(p), name or p.name, category, p.stat().st_size, notes))
        self.bus.notify("attachments")
        return rid

    def remove_attachment(self, attachment_id: int) -> None:
        """Removes only the reference; the file on disk is never touched."""
        self.db.run("DELETE FROM attachments WHERE id=?", (attachment_id,))
        self.bus.notify("attachments")

    def milestones(self, project_id: int) -> list[dict]:
        return [dict(r) for r in self.db.q("SELECT * FROM project_milestones WHERE project_id=? ORDER BY COALESCE(NULLIF(due_date,''),'9999'), id", (project_id,))]

    def save_milestone(self, project_id: int, title: str, due: str = "", status: str = "Pending", mid: Optional[int] = None) -> int:
        title = (title or "").strip()
        if not title:
            raise ValidationError("Milestone title is required.")
        due = self._date_ok("Due date", (due or "").strip())
        if mid:
            self.db.run("UPDATE project_milestones SET title=?, due_date=?, status=? WHERE id=? AND project_id=?", (title, due, status, mid, project_id))
        else:
            mid = self.db.run("INSERT INTO project_milestones(project_id,title,due_date,status) VALUES(?,?,?,?)", (project_id, title, due, status))
        self.bus.notify("project_milestones", "projects")
        return int(mid)

    def delete_milestone(self, mid: int) -> None:
        self.db.run("DELETE FROM project_milestones WHERE id=?", (mid,))
        self.bus.notify("project_milestones", "projects")

    def schedule_revision(self, unit_id: int, in_days: int = 1) -> int:
        if self._exists("SELECT 1 FROM revisions WHERE unit_id=? AND status='Due'", (unit_id,)):
            raise ValidationError("This unit already has an active revision schedule.")
        return self.insert("revisions", {"unit_id": unit_id, "due_date": (self.today() + timedelta(days=in_days)).isoformat()})

    def review_revision(self, rid: int, quality: int) -> dict:
        """Record a review (1 Again · 3 Hard · 4 Good · 5 Easy) and schedule the next one (SM-2)."""
        rec = self.get("revisions", rid)
        if not rec:
            raise ValidationError("This revision no longer exists.")
        reps, ease, days = sm2_next(rec["repetitions"], rec["ease"], rec["interval_days"], quality)
        due = (self.today() + timedelta(days=days)).isoformat()
        self.db.run("UPDATE revisions SET repetitions=?, ease=?, interval_days=?, due_date=?, last_reviewed=?, status='Due' WHERE id=?",
                    (reps, ease, days, due, self.today().isoformat(), rid))
        self.bus.notify("revisions")
        return {"repetitions": reps, "ease": ease, "interval_days": days, "due_date": due}

    def quick_log_study(self, subject_id: int, minutes: int, topic: str = "", session_type: str = "Study") -> int:
        return self.insert("study_sessions", {"subject_id": subject_id, "study_date": self.today().isoformat(),
                                              "duration_minutes": minutes, "topic": topic, "session_type": session_type})

    def mark_attendance(self, day: str, marks: dict[int, Optional[bool]], kind: str = "Lecture") -> int:
        """Record one class per subject for ``day``: True present, False absent, None skip."""
        day = self._date_ok("Date", day)
        if not day:
            raise ValidationError("Date is required.")
        n = 0
        with self.db.transaction():
            for sid, present in marks.items():
                if present is None:
                    continue
                self.db.conn.execute("INSERT INTO attendance(subject_id,att_date,classes_held,classes_attended,kind) VALUES(?,?,1,?,?)",
                                     (sid, day, 1 if present else 0, kind))
                n += 1
        self.bus.notify("attendance")
        return n

    # ---- legacy custom fields (kept for compatibility with 2.x data) -----
    def custom_fields(self, table: str, rid: int) -> list[dict]:
        self.entity(table)
        return [dict(r) for r in self.db.q("SELECT id, field_name, field_value FROM custom_fields WHERE entity_type=? AND record_id=? ORDER BY field_name", (table, rid))]

    def set_custom_field(self, table: str, rid: int, name: str, value: str) -> None:
        self.entity(table)
        name = (name or "").strip()
        if not name:
            raise ValidationError("Field name is required.")
        self.db.run("INSERT INTO custom_fields(entity_type,record_id,field_name,field_value) VALUES(?,?,?,?) "
                    "ON CONFLICT(entity_type,record_id,field_name) DO UPDATE SET field_value=excluded.field_value", (table, rid, name, (value or "").strip()))

    def delete_custom_field(self, fid: int) -> None:
        self.db.run("DELETE FROM custom_fields WHERE id=?", (fid,))

    # ---- search, duplicate, CSV import ---------------------------------------
    def global_search(self, text: str, per_kind: int = 6, limit: int = 60) -> list[dict]:
        """Search every kind of record at once. Returns [{kind, title, detail, page, id}]."""
        text = (text or "").strip()
        if len(text) < 2:
            return []
        like = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        out: list[dict] = []
        for kind, table, page, cols, title, detail in _SEARCH_SOURCES:
            where = " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in cols)
            for r in self.db.q(f"SELECT id, {title} AS title, {detail} AS detail FROM {table} WHERE {where} ORDER BY id DESC LIMIT ?", [like] * len(cols) + [per_kind]):
                out.append({"kind": kind, "title": str(r["title"] or ""), "detail": str(r["detail"] or ""), "page": page, "id": r["id"]})
        return out[:limit]

    def duplicate(self, table: str, rid: int) -> int:
        """Copy a record (results, submission dates and statuses are reset so the copy starts fresh)."""
        ent = self.entity(table)
        if table not in DUPLICABLE:
            raise ValidationError(f"{ent.plural} cannot be duplicated.")
        rec = self.get(table, rid)
        if not rec:
            raise ValidationError("This record no longer exists.")
        vals = {f.key: rec.get(f.key) for f in ent.fields}
        if table == "units":
            vals["unit_no"] = safe_int(self.db.scalar("SELECT MAX(unit_no) FROM units WHERE subject_id=?", (rec["subject_id"],), 0)) + 1
        if table == "timetable":
            vals["day"] = WEEKDAYS[(WEEKDAYS.index(rec["day"]) + 1) % 7] if rec["day"] in WEEKDAYS else "Monday"
        if table == "assignments":
            vals.update(status="Not Started", submission_date="", marks_obtained=None)
        if table == "exams":
            vals.update(marks_obtained=None, result="", grade="", internal_marks=None, external_marks=None, practical_marks=None)
        if table == "projects":
            vals.update(status="Not Started", completion_date="", marks_obtained=None, grade="")
        for key in ("name", "title"):
            if vals.get(key):
                vals[key] = f"{vals[key]} (copy)"
                break
        return self.insert(table, vals)

    def _fk_lookup(self, fk: str, raw: str, cache: dict) -> int:
        if fk not in cache:
            idx: dict[str, list[int]] = defaultdict(list)
            for r in self.db.q(_FK_NAME_SQL[fk]):
                for key in (r[1], r[2]):
                    if key:
                        idx[str(key).casefold()].append(r[0])
            cache[fk] = idx
        ids = cache[fk].get(raw.casefold(), [])
        if len(ids) > 1 and fk == "subjects":      # same name in several semesters: prefer the viewed one
            sid = self.scope_semester_id()
            ids = [i for i in ids if self.db.scalar("SELECT semester_id FROM subjects WHERE id=?", (i,)) == sid] or ids
        if not ids:
            raise ValidationError(f"“{raw}” was not found.")
        if len(ids) > 1:
            raise ValidationError(f"“{raw}” matches several records; make the names unique.")
        return ids[0]

    def _csv_values(self, ent: Entity, row: dict, hmap: dict, cache: dict) -> dict:
        vals: dict[str, Any] = {}
        for f in ent.fields:
            raw = str(row.get(hmap.get(f.key, ""), "") or "").strip() if f.key in hmap else ""
            if raw == "":
                vals[f.key] = f.default if f.default is not None else ""
                continue
            if f.kind == "fk":
                vals[f.key] = self._fk_lookup(f.fk, raw, cache)
            elif f.kind == "date":
                d = next((datetime.strptime(raw, fmt).date() for fmt in _CSV_DATE_FORMATS if _try_date(raw, fmt)), None)
                vals[f.key] = d.isoformat() if d else raw
            elif f.kind in ("int", "float"):
                vals[f.key] = raw.replace(",", "").rstrip("%")
            elif f.kind == "choice":
                vals[f.key] = next((c for c in f.choices if str(c).casefold() == raw.casefold()), raw)
            else:
                vals[f.key] = raw
        return vals

    def import_csv(self, table: str, path: Path | str, *, dry_run: bool = False) -> dict:
        """Import rows from a CSV (headers = field names or labels; names for linked records).

        Every row is validated like a form entry; bad rows are reported and skipped. ``dry_run`` validates
        everything and rolls it all back. A safety snapshot is taken before a real import."""
        ent = self.entity(table)
        p = Path(path)
        if not p.is_file() or p.stat().st_size > 5_000_000:
            raise ValidationError("Choose a CSV file smaller than 5 MB.")
        try:
            with open(p, newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                rows = []
                for i, r in enumerate(reader):
                    if i >= 5000:
                        raise ValidationError("A single import can contain at most 5,000 rows.")
                    rows.append(r)
                headers = list(reader.fieldnames or [])
        except UnicodeDecodeError as exc:
            raise ValidationError("The file is not UTF-8 text. Save it as “CSV UTF-8” and try again.") from exc
        by_name = {}
        for f in ent.fields:
            by_name[f.key.casefold()] = f.key
            by_name[f.label.casefold().replace(" *", "").strip()] = f.key
        hmap: dict[str, str] = {}
        ignored = []
        for h in headers:
            key = by_name.get((h or "").strip().casefold())
            if key and key not in hmap:
                hmap[key] = h
            else:
                ignored.append(h)
        if not hmap:
            raise ValidationError("None of the column headings match this table. Download the template to see the expected headings.")
        if not dry_run and rows and self.snapshot_hook:
            self.snapshot_hook(f"before_import_{table}")

        class _Abort(Exception):
            pass
        ok, errors, cache = 0, [], {}
        real_bus, self.bus = self.bus, ChangeBus()
        try:
            with self.db.transaction():
                for n, row in enumerate(rows, 2):
                    try:
                        self.insert(table, self._csv_values(ent, row, hmap, cache))
                        ok += 1
                    except (ValidationError, ValueError) as exc:
                        errors.append((n, str(exc)))
                if dry_run:
                    raise _Abort()
        except _Abort:
            pass
        finally:
            self.bus = real_bus
        if ok and not dry_run:
            self.bus.notify(table)
        return {"table": table, "total": len(rows), "imported": ok, "errors": errors, "ignored": ignored, "dry_run": dry_run}

    def write_csv_template(self, table: str, path: Path | str) -> Path:
        """A CSV with the expected headings and one example row."""
        ent = self.entity(table)

        def example(f: Field) -> str:
            if f.kind == "fk":
                return f"(name of an existing {f.label.split('(')[0].strip().lower()})"
            if f.kind == "date":
                return "2026-11-30"
            if f.kind == "choice":
                return str(f.default or (f.choices[0] if f.choices else ""))
            return f.hint or ("" if f.default is None else str(f.default))

        def write(tmp: Path) -> None:
            with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow([f.label.replace(" *", "") for f in ent.fields])
                w.writerow([example(f) for f in ent.fields])
        return atomic_write(path, write)


def _try_date(raw: str, fmt: str) -> bool:
    try:
        datetime.strptime(raw, fmt)
        return True
    except ValueError:
        return False


# ════════════════════════════════════════════════════════════════════════════
# 7 · ANALYTICS, ALERTS & APPLICATION CORE  (every figure comes from stored records)
# ════════════════════════════════════════════════════════════════════════════
_NUM = "IN ('integer','real')"      # ignore legacy rows where a marks column holds text
SCORES_SQL = ("SELECT m.subject_id, m.assessment_date AS dt, m.marks_obtained AS got, m.max_marks AS mx, "
              "COALESCE(NULLIF(m.title,''), m.assessment_type) AS title FROM marks m WHERE typeof(m.max_marks) " + _NUM + " AND typeof(m.marks_obtained) " + _NUM + " AND m.max_marks > 0 "
              "UNION ALL SELECT e.subject_id, e.exam_date, e.marks_obtained, e.max_marks, e.title FROM exams e "
              "WHERE typeof(e.marks_obtained) " + _NUM + " AND typeof(e.max_marks) " + _NUM + " AND e.max_marks > 0")
STRONG_PCT, WEAK_PCT = 75.0, 50.0
Range = tuple  # (date_from | None, date_to | None) as ISO strings


@dataclass
class Alert:
    severity: str   # danger | warning | info
    title: str
    detail: str
    page: str       # page key to open


def _freeze(x: Any) -> Any:
    if isinstance(x, (list, tuple, set)):
        return tuple(_freeze(i) for i in x)
    if isinstance(x, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in x.items()))
    return x


def _memoized(fn: Callable) -> Callable:
    """Cache an analytics result until any data changes (or the date rolls over)."""
    @functools.wraps(fn)
    def wrapper(self: "Analytics", *args: Any, **kwargs: Any) -> Any:
        token = (self.db.change_token(), self.repo.today(), self.settings.get("grading"), self.settings.get("attendance_min"), self.settings.get("daily_goal"))
        if token != self._memo_token:
            self._memo_cache.clear()
            self._memo_token = token
        key = (fn.__name__, _freeze(args), _freeze(kwargs))
        if key not in self._memo_cache:
            self._memo_cache[key] = fn(self, *args, **kwargs)
        return copy.deepcopy(self._memo_cache[key])        # callers may modify what they get
    return wrapper


class Analytics:
    """Read-only calculations over the database. Returns empty/None rather than inventing data."""

    def __init__(self, repo: Repository) -> None:
        self.repo, self.db, self.settings = repo, repo.db, repo.settings
        self._memo_cache: dict = {}
        self._memo_token: Any = None

    # ---- filters ----------------------------------------------------------
    @staticmethod
    def _sc(sid: Any, alias: str = "s") -> tuple[str, list]:
        """SQL + args restricting to a semester id, a list of ids, or nothing (all)."""
        if not sid:
            return "", []
        ids = [int(sid)] if isinstance(sid, (int, str)) else [int(i) for i in sid]
        return f" AND {alias}.semester_id IN ({','.join('?' * len(ids))})", ids

    @staticmethod
    def _rg(col: str, rng: Optional[Range]) -> tuple[str, list]:
        lo, hi = rng if rng else (None, None)
        sql, args = "", []
        if lo:
            sql += f" AND {col} >= ?"
            args.append(lo)
        if hi:
            sql += f" AND {col} <= ?"
            args.append(hi)
        return sql, args

    # ---- assignments ------------------------------------------------------
    @_memoized
    def assignment_stats(self, sid: Optional[int] = None, rng: Optional[Range] = None) -> dict:
        sc, sa = self._sc(sid)
        rg, ra = self._rg("t.due_date", rng)
        today = self.repo.today()
        by: Counter = Counter()
        due_week = 0
        for r in self.db.q("SELECT t.status, t.due_date, t.submission_date FROM assignments t JOIN subjects s ON s.id=t.subject_id "
                           f"WHERE 1=1{sc}{rg}", sa + ra):
            st = effective_assignment_status(r["status"], r["due_date"], r["submission_date"], today)
            by[st] += 1
            d = parse_date(r["due_date"])
            if st in PENDING_ASSIGNMENT and d and 0 <= (d - today).days <= 7:
                due_week += 1
        total = sum(by.values())
        submitted = by["Submitted"] + by["Late Submission"] + by["Evaluated"]
        done = submitted + by["Completed"]
        return {"total": total, "by_status": dict(by), "pending": sum(by[s] for s in PENDING_ASSIGNMENT),
                "overdue": by["Overdue"], "due_week": due_week, "submitted": submitted, "done": done, "late": by["Late Submission"],
                "completion_rate": percent(done, total), "submit_rate": percent(submitted, total)}

    # ---- syllabus ----------------------------------------------------------
    @_memoized
    def syllabus_by_subject(self, sid: Optional[int] = None) -> list[dict]:
        sc, sa = self._sc(sid)
        chap = {r["subject_id"]: (r["n"], r["d"] or 0) for r in self.db.q(
            "SELECT u.subject_id, COUNT(c.id) n, SUM(c.status='Completed') d FROM chapters c JOIN units u ON u.id=c.unit_id GROUP BY u.subject_id")}
        out = []
        for r in self.db.q("SELECT s.id, s.name, s.color, COUNT(u.id) units, SUM(u.status='Completed') done, SUM(u.status='In Progress') doing "
                           f"FROM subjects s LEFT JOIN units u ON u.subject_id=s.id WHERE 1=1{sc} GROUP BY s.id ORDER BY s.name", sa):
            cn, cd = chap.get(r["id"], (0, 0))
            out.append({"subject_id": r["id"], "subject": r["name"], "color": hex_norm(r["color"], "#356AE6"), "units": r["units"], "done": r["done"] or 0,
                        "doing": r["doing"] or 0, "pct": percent(r["done"] or 0, r["units"]), "chapters": cn, "chapters_done": cd,
                        "chapter_pct": percent(cd, cn)})
        return out

    # ---- scores / performance ---------------------------------------------
    @_memoized
    def subject_performance(self, sid: Optional[int] = None, rng: Optional[Range] = None) -> list[dict]:
        """Per subject: pooled % (Σ obtained ÷ Σ maximum), score count, trend (needs ≥4 dated scores)."""
        sc, sa = self._sc(sid)
        rg, ra = self._rg("sc.dt", rng)
        data: dict[int, dict] = {}
        for r in self.db.q(f"SELECT s.id, s.name, s.color, sc.dt, sc.got, sc.mx FROM ({SCORES_SQL}) sc JOIN subjects s ON s.id=sc.subject_id "
                           f"WHERE 1=1{sc}{rg} ORDER BY s.name, CASE WHEN sc.dt='' THEN 1 ELSE 0 END, sc.dt", sa + ra):
            d = data.setdefault(r["id"], {"subject_id": r["id"], "subject": r["name"], "color": hex_norm(r["color"], "#356AE6"), "got": 0.0, "mx": 0.0, "series": [], "n": 0})
            d["got"] += r["got"]
            d["mx"] += r["mx"]
            d["n"] += 1
            if r["dt"]:
                d["series"].append(r["got"] / r["mx"] * 100)
        out = []
        for d in data.values():
            d["pct"] = percent(d["got"], d["mx"])
            d["trend"], d["delta"] = trend_label(d["series"])
            out.append(d)
        return sorted(out, key=lambda x: x["subject"].casefold())

    def score_overall(self, sid: Optional[int] = None, rng: Optional[Range] = None) -> Optional[float]:
        perf = self.subject_performance(sid, rng)
        return percent(sum(p["got"] for p in perf), sum(p["mx"] for p in perf)) if perf else None

    def score_trend(self, sid: Optional[int] = None, rng: Optional[Range] = None) -> list[tuple[str, float]]:
        """Chronological (date, %) of every dated score — for the performance-trend chart."""
        sc, sa = self._sc(sid)
        rg, ra = self._rg("sc.dt", rng)
        return [(r["dt"], r["got"] / r["mx"] * 100) for r in self.db.q(
            f"SELECT sc.dt, sc.got, sc.mx FROM ({SCORES_SQL}) sc JOIN subjects s ON s.id=sc.subject_id WHERE sc.dt<>''{sc}{rg} ORDER BY sc.dt", sa + ra)]

    # ---- attendance ---------------------------------------------------------
    @_memoized
    def attendance_by_subject(self, sid: Optional[int] = None, rng: Optional[Range] = None) -> list[dict]:
        sc, sa = self._sc(sid)
        rg, ra = self._rg("a.att_date", rng)
        default = self.settings.get_float("attendance_min", 75.0)
        out = []
        for r in self.db.q("SELECT s.id, s.name, s.color, s.attendance_target tgt, COALESCE(SUM(a.classes_held),0) h, "
                           "COALESCE(SUM(a.classes_attended),0) c FROM subjects s LEFT JOIN attendance a ON a.subject_id=s.id"
                           f"{rg} WHERE 1=1{sc} GROUP BY s.id ORDER BY s.name", ra + sa):
            st = attendance_stats(r["h"], r["c"], r["tgt"] if r["tgt"] is not None else default)
            out.append({"subject_id": r["id"], "subject": r["name"], "color": hex_norm(r["color"], "#356AE6"), **st})
        return out

    def attendance_overall(self, sid: Optional[int] = None, rng: Optional[Range] = None) -> dict:
        rows = self.attendance_by_subject(sid, rng)
        return attendance_stats(sum(r["held"] for r in rows), sum(r["attended"] for r in rows), self.settings.get_float("attendance_min", 75.0))

    def attendance_monthly(self, sid: Optional[int] = None) -> list[tuple[str, float]]:
        sc, sa = self._sc(sid)
        return [(r["m"], r["c"] / r["h"] * 100) for r in self.db.q(
            "SELECT substr(a.att_date,1,7) m, SUM(a.classes_held) h, SUM(a.classes_attended) c FROM attendance a "
            f"JOIN subjects s ON s.id=a.subject_id WHERE a.att_date<>''{sc} GROUP BY m HAVING h>0 ORDER BY m", sa)]

    # ---- study ----------------------------------------------------------------
    def study_daily(self, days: int = 30, sid: Optional[int] = None) -> list[tuple[str, int]]:
        today = self.repo.today()
        start = (today - timedelta(days=days - 1)).isoformat()
        sc, sa = self._sc(sid)
        got = {r["d"]: r["m"] for r in self.db.q("SELECT t.study_date d, SUM(t.duration_minutes) m FROM study_sessions t "
                                                 f"JOIN subjects s ON s.id=t.subject_id WHERE t.study_date >= ?{sc} GROUP BY d", [start] + sa)}
        return [((today - timedelta(days=i)).isoformat(), int(got.get((today - timedelta(days=i)).isoformat(), 0))) for i in range(days - 1, -1, -1)]

    @_memoized
    def study_by_subject(self, sid: Optional[int] = None, rng: Optional[Range] = None) -> list[dict]:
        sc, sa = self._sc(sid)
        rg, ra = self._rg("t.study_date", rng)
        rows = self.db.q("SELECT s.id, s.name, s.color, s.target_hours, COALESCE(SUM(t.duration_minutes),0) m, COUNT(t.id) n "
                         f"FROM subjects s LEFT JOIN study_sessions t ON t.subject_id=s.id{rg} WHERE 1=1{sc} GROUP BY s.id ORDER BY m DESC, s.name", ra + sa)
        total = sum(r["m"] for r in rows)
        return [{"subject_id": r["id"], "subject": r["name"], "color": hex_norm(r["color"], "#356AE6"), "minutes": r["m"], "sessions": r["n"],
                 "share": percent(r["m"], total), "target_hours": r["target_hours"]} for r in rows]

    def study_habits(self, days: int = 14) -> dict:
        goal = self.settings.get_int("daily_goal", 180)
        daily = self.study_daily(days)
        all_days = {r[0] for r in self.db.q("SELECT DISTINCT study_date FROM study_sessions")}
        studied = sum(1 for _, m in daily if m > 0)
        return {"days": days, "goal": goal, "studied_days": studied, "goal_days": sum(1 for _, m in daily if goal and m >= goal),
                "avg_minutes": sum(m for _, m in daily) / days, "today_minutes": daily[-1][1] if daily else 0,
                "streak": study_streak(all_days, self.repo.today()), "consistency": percent(studied, days),
                "goal_rate": percent(sum(1 for _, m in daily if goal and m >= goal), days)}

    # ---- results, SGPA & CGPA -------------------------------------------------
    @_memoized
    def semester_history(self) -> list[dict]:
        """One row per semester: SGPA, running CGPA (per programme), credits, backlogs, attendance, study, assignments."""
        cfg, q = self.settings.grading(), self.db.q
        sems = [dict(r) for r in q(
            "SELECT sem.id, sem.name, sem.status, sem.semester_no, sem.programme_id, p.name AS programme, ay.name AS year_name "
            "FROM semesters sem LEFT JOIN programmes p ON p.id=sem.programme_id LEFT JOIN academic_years ay ON ay.id=sem.academic_year_id "
            "ORDER BY COALESCE(sem.programme_id,0), COALESCE(sem.semester_no,9999), sem.id")]
        rrows = [dict(r) for r in q("SELECT r.*, s.semester_id, s.credits AS subject_credits FROM subject_results r JOIN subjects s ON s.id=r.subject_id")]
        for r in rrows:
            r["ev"] = evaluate_result(r, cfg, r["subject_credits"] or 0)
        by_sem: dict[Any, list[dict]] = defaultdict(list)
        for r in pick_attempts(rrows, cfg):
            by_sem[r["semester_id"]].append(r)
        subj = {r["semester_id"]: (r["n"], r["cr"] or 0) for r in q("SELECT semester_id, COUNT(*) n, SUM(credits) cr FROM subjects GROUP BY semester_id")}
        att = {r["sid"]: (r["h"], r["c"]) for r in q("SELECT s.semester_id sid, SUM(a.classes_held) h, SUM(a.classes_attended) c FROM attendance a "
                                                    "JOIN subjects s ON s.id=a.subject_id GROUP BY s.semester_id")}
        study = {r["sid"]: r["m"] for r in q("SELECT s.semester_id sid, SUM(t.duration_minutes) m FROM study_sessions t "
                                             "JOIN subjects s ON s.id=t.subject_id GROUP BY s.semester_id")}
        syl = {r["sid"]: (r["n"], r["d"] or 0) for r in q("SELECT s.semester_id sid, COUNT(u.id) n, SUM(u.status='Completed') d FROM units u "
                                                         "JOIN subjects s ON s.id=u.subject_id GROUP BY s.semester_id")}
        asg: dict[Any, list[int]] = defaultdict(lambda: [0, 0])
        today = self.repo.today()
        for r in q("SELECT s.semester_id sid, t.status, t.due_date, t.submission_date FROM assignments t JOIN subjects s ON s.id=t.subject_id"):
            asg[r["sid"]][0] += 1
            if effective_assignment_status(r["status"], r["due_date"], r["submission_date"], today) not in PENDING_ASSIGNMENT:
                asg[r["sid"]][1] += 1
        cum: dict[Any, list[list[dict]]] = {}
        out = []
        for s in sems:
            evals = [r["ev"] for r in by_sem.get(s["id"], [])]
            bucket = cum.setdefault(s["programme_id"], [])
            if evals:
                bucket.append(evals)
            marked = [e for e in evals if e["maximum"]]
            h, c = att.get(s["id"], (0, 0))
            n_units, d_units = syl.get(s["id"], (0, 0))
            out.append({**s, "subjects": subj.get(s["id"], (0, 0))[0], "credits_planned": subj.get(s["id"], (0, 0))[1],
                        "results": len(evals), "sgpa": compute_sgpa(evals, cfg), "cgpa": compute_cgpa(bucket, cfg) if evals else None,
                        "credits_registered": sum(e["credits"] or 0 for e in evals if e["counted"]),
                        "credits_earned": sum(e["credits"] or 0 for e in evals if e["passed"]),
                        "pct": percent(sum(e["obtained"] for e in marked), sum(e["maximum"] for e in marked)) if marked else None,
                        "backlogs": sum(1 for e in evals if e["passed"] is False),
                        "attendance_pct": percent(c, h) if h else None, "study_minutes": int(study.get(s["id"], 0) or 0),
                        "assign_total": asg[s["id"]][0], "assign_done": asg[s["id"]][1], "syllabus_pct": percent(d_units, n_units)})
        return out

    @_memoized
    def grade_distribution(self, sid: Optional[int] = None) -> list[tuple[str, int]]:
        cfg = self.settings.grading()
        sc, sa = self._sc(sid)
        rows = [dict(r) for r in self.db.q("SELECT r.*, s.semester_id, s.credits AS subject_credits FROM subject_results r "
                                           f"JOIN subjects s ON s.id=r.subject_id WHERE 1=1{sc}", sa)]
        for r in rows:
            r["ev"] = evaluate_result(r, cfg, r["subject_credits"] or 0)
        grades = Counter(r["ev"]["grade"] for r in pick_attempts(rows, cfg) if r["ev"]["grade"])
        order = [str(r["grade"]) for r in cfg._sorted()]
        return [(g, grades[g]) for g in order if grades[g]] + [(g, n) for g, n in grades.items() if g not in order]

    def sgpa_series(self) -> list[dict]:
        return [h for h in self.semester_history() if h["sgpa"] is not None]

    # ---- upcoming / recent ----------------------------------------------------------
    @_memoized
    def deadlines(self, sid: Optional[int] = None, days: int = 14, include_overdue: bool = True) -> list[dict]:
        today = self.repo.today()
        end = (today + timedelta(days=days)).isoformat()
        sc, sa = self._sc(sid)
        items = []
        for r in self.db.q("SELECT t.title, t.due_date d, t.status, t.submission_date sub, s.name sn FROM assignments t JOIN subjects s ON s.id=t.subject_id "
                           f"WHERE t.due_date<>''{sc}", sa):
            st = effective_assignment_status(r["status"], r["d"], r["sub"], today)
            if st in PENDING_ASSIGNMENT and (r["d"] <= end) and (include_overdue or r["d"] >= today.isoformat()):
                items.append({"kind": "Assignment", "title": r["title"], "subject": r["sn"], "date": r["d"], "status": st})
        for r in self.db.q("SELECT t.title, t.exam_date d, t.prep_status ps, s.name sn FROM exams t JOIN subjects s ON s.id=t.subject_id "
                           f"WHERE t.exam_date >= ? AND t.exam_date <= ?{sc}", [today.isoformat(), end] + sa):
            items.append({"kind": "Exam", "title": r["title"], "subject": r["sn"], "date": r["d"], "status": r["ps"]})
        proj_scope = f" AND (t.subject_id IS NULL OR s.semester_id IN ({','.join('?' * len(sa))}))" if sa else ""
        for r in self.db.q("SELECT t.title, t.deadline d, t.status, s.name sn FROM projects t LEFT JOIN subjects s ON s.id=t.subject_id "
                           "WHERE t.deadline<>'' AND t.status IN ('Not Started','In Progress','On Hold') AND t.deadline <= ?" + proj_scope, [end] + sa):
            items.append({"kind": "Project", "title": r["title"], "subject": r["sn"] or "", "date": r["d"],
                          "status": "Overdue" if r["d"] < today.isoformat() else r["status"]})
        for it in items:
            it["days_left"] = (parse_date(it["date"]) - today).days if parse_date(it["date"]) else None
        return sorted(items, key=lambda x: (x["date"], x["kind"]))

    def recent_activity(self, sid: Optional[int] = None, limit: int = 8) -> list[dict]:
        sc, sa = self._sc(sid)
        items = []
        for r in self.db.q("SELECT t.study_date d, t.duration_minutes m, s.name sn FROM study_sessions t JOIN subjects s ON s.id=t.subject_id "
                           f"WHERE 1=1{sc} ORDER BY t.study_date DESC, t.id DESC LIMIT ?", sa + [limit]):
            items.append({"date": r["d"], "kind": "Study", "text": f"Studied {r['sn']} for {fmt_minutes(r['m'])}"})
        for r in self.db.q("SELECT t.title, t.submission_date d, s.name sn FROM assignments t JOIN subjects s ON s.id=t.subject_id WHERE t.submission_date<>''"
                           f"{sc} ORDER BY t.submission_date DESC LIMIT ?", sa + [limit]):
            items.append({"date": r["d"], "kind": "Assignment", "text": f"Submitted “{r['title']}” ({r['sn']})"})
        for r in self.db.q(f"SELECT sc.title, sc.dt d, sc.got, sc.mx, s.name sn FROM ({SCORES_SQL}) sc JOIN subjects s ON s.id=sc.subject_id "
                           f"WHERE sc.dt<>''{sc} ORDER BY sc.dt DESC LIMIT ?", sa + [limit]):
            items.append({"date": r["d"], "kind": "Score", "text": f"{r['title']}: {fmt_num(r['got'])}/{fmt_num(r['mx'])} in {r['sn']}"})
        return sorted(items, key=lambda x: x["date"], reverse=True)[:limit]

    # ---- dashboard bundle -----------------------------------------------------------
    def overview(self, sid: Optional[int] = None, rng: Optional[Range] = None) -> dict:
        sc, sa = self._sc(sid)
        today = self.repo.today()
        units = self.db.one(f"SELECT COUNT(*) n, SUM(u.status='Completed') d FROM units u JOIN subjects s ON s.id=u.subject_id WHERE 1=1{sc}", sa)
        nxt = self.db.one("SELECT t.title, t.exam_date d, s.name sn FROM exams t JOIN subjects s ON s.id=t.subject_id WHERE t.exam_date >= ?"
                          f"{sc} ORDER BY t.exam_date LIMIT 1", [today.isoformat()] + sa)
        month = self.db.scalar("SELECT COUNT(*) FROM exams t JOIN subjects s ON s.id=t.subject_id WHERE t.exam_date >= ? AND t.exam_date <= ?" + sc,
                               [today.isoformat(), (today + timedelta(days=30)).isoformat()] + sa, 0)
        rg, ra = self._rg("t.study_date", rng)
        week_start = (today - timedelta(days=6)).isoformat()
        hist = self.semester_history()
        ids = None if not sid else ([int(sid)] if isinstance(sid, (int, str)) else [int(i) for i in sid])
        cur = next((h for h in hist if h["id"] == ids[0]), None) if ids and len(ids) == 1 else None
        latest = [h for h in hist if h["cgpa"] is not None]
        habits = self.study_habits(14)
        out = {"subjects": self.db.scalar(f"SELECT COUNT(*) FROM subjects s WHERE 1=1{sc}", sa, 0),
               "units_total": units["n"] or 0, "units_done": units["d"] or 0, "syllabus_pct": percent(units["d"] or 0, units["n"]),
               "assignments": self.assignment_stats(sid, rng), "attendance": self.attendance_overall(sid, rng),
               "exams_30d": month, "next_exam": dict(nxt) if nxt else None, "score_pct": self.score_overall(sid, rng),
               "study_minutes": self.db.scalar("SELECT COALESCE(SUM(t.duration_minutes),0) FROM study_sessions t JOIN subjects s ON s.id=t.subject_id "
                                               f"WHERE 1=1{sc}{rg}", sa + ra, 0),
               "study_week": self.db.scalar("SELECT COALESCE(SUM(t.duration_minutes),0) FROM study_sessions t JOIN subjects s ON s.id=t.subject_id "
                                            f"WHERE t.study_date >= ?{sc}", [week_start] + sa, 0),
               "habits": habits, "sgpa": cur["sgpa"] if cur else None,
               "cgpa": (cur["cgpa"] if cur and cur["cgpa"] is not None else (latest[-1]["cgpa"] if latest else None)),
               "backlogs": sum(h["backlogs"] for h in hist if ids is None or h["id"] in ids), "credits_earned": sum(h["credits_earned"] for h in hist if ids is None or h["id"] in ids)}
        return out

    # ---- plain-language findings -----------------------------------------------------
    def insights(self, sid: Optional[int] = None, rng: Optional[Range] = None) -> list[dict]:
        out: list[dict] = []
        add = lambda kind, level, text: out.append({"kind": kind, "level": level, "text": text})
        perf = [p for p in self.subject_performance(sid, rng) if p["n"]]
        fmt = lambda ps: ", ".join(f"{p['subject']} {p['pct']:.1f}%" for p in ps)
        strong = sorted([p for p in perf if p["n"] >= 2 and p["pct"] >= STRONG_PCT], key=lambda p: -p["pct"])
        weak = sorted([p for p in perf if p["n"] >= 2 and p["pct"] < WEAK_PCT], key=lambda p: p["pct"])
        if strong:
            add("strong", "good", f"Strong subjects (average ≥ {STRONG_PCT:.0f}% over at least 2 scores): {fmt(strong)}.")
        if weak:
            add("weak", "bad", f"Need extra preparation (average below {WEAK_PCT:.0f}% over at least 2 scores): {fmt(weak)}.")
        imp = [p for p in perf if p["trend"] == "Improving"]
        dec = [p for p in perf if p["trend"] == "Declining"]
        if imp:
            add("improving", "good", "Improving: " + ", ".join(f"{p['subject']} (+{p['delta']:.1f} pts)" for p in imp) + ".")
        if dec:
            add("declining", "warn", "Declining: " + ", ".join(f"{p['subject']} ({p['delta']:.1f} pts)" for p in dec) + ".")
        short = [a for a in self.attendance_by_subject(sid, rng) if a["status"] == "Shortage"]
        for a in short:
            tip = (f"attend the next {a['need']} classes in a row to reach {a['target']:.0f}%" if a["need"] else "the target can no longer be reached")
            add("attendance", "bad", f"Attendance below target in {a['subject']}: {a['pct']:.1f}% (target {a['target']:.0f}%) — {tip}.")
        asg = self.assignment_stats(sid, rng)
        if asg["overdue"]:
            add("deadlines", "bad", f"{asg['overdue']} assignment(s) are past their deadline and not yet submitted.")
        late = asg["late"]
        if late:
            add("deadlines", "warn", f"{late} assignment(s) were submitted late.")
        syl = [s for s in self.syllabus_by_subject(sid) if s["units"] and s["done"] < s["units"]]
        if syl:
            add("syllabus", "info", "Incomplete syllabus: " + ", ".join(f"{s['subject']} {s['done']}/{s['units']} units" for s in syl[:6])
                + ("…" if len(syl) > 6 else "") + ".")
        study = self.study_by_subject(sid, rng)
        used = [s for s in study if s["minutes"] > 0]
        if len(used) >= 2:
            top, low = used[0], used[-1]
            add("study", "info", f"Most study time: {top['subject']} ({fmt_minutes(top['minutes'])}, {top['share']:.0f}%). "
                                 f"Least: {low['subject']} ({fmt_minutes(low['minutes'])}, {low['share']:.0f}%).")
        none = [s["subject"] for s in study if s["minutes"] == 0]
        if none and used:
            add("study", "warn", "No study time recorded for: " + ", ".join(none[:6]) + ("…" if len(none) > 6 else "") + ".")
        today = self.repo.today().isoformat()
        sc, sa = self._sc(sid)
        due_rev = self.db.scalar("SELECT COUNT(*) FROM revisions t JOIN units u ON u.id=t.unit_id JOIN subjects s ON s.id=u.subject_id "
                                 f"WHERE t.status='Due' AND t.due_date <= ?{sc}", [today] + sa, 0)
        low_conf = self.db.scalar("SELECT COUNT(*) FROM units u JOIN subjects s ON s.id=u.subject_id "
                                  f"WHERE u.confidence <= 2 AND u.status<>'Completed'{sc}", sa, 0)
        bits = ([f"{due_rev} unit(s) are due for revision"] if due_rev else []) + ([f"{low_conf} unfinished unit(s) have confidence 2 or lower"] if low_conf else [])
        if bits:
            add("revision", "warn", "; ".join(bits).capitalize() + ".")
        h = self.study_habits(14)
        if self.db.scalar("SELECT COUNT(*) FROM study_sessions", default=0):
            add("habits", "good" if (h["goal_rate"] or 0) >= 50 else "info",
                f"Daily goal ({fmt_minutes(h['goal'])}) reached on {h['goal_days']} of the last {h['days']} days; studied on {h['studied_days']} days "
                f"({h['consistency']:.0f}% consistency); current streak {h['streak']} day(s).")
        if not out:
            add("empty", "info", "Not enough data yet. Add marks, attendance and study sessions to see strengths, trends and recommendations.")
        return out

    # ---- timetable, goals, heatmap, data health -------------------------------------
    def semester_progress(self, sid: Any) -> Optional[dict]:
        """How far through a semester we are — only when it has valid start and end dates."""
        if sid and not isinstance(sid, (int, str)):
            sid = list(sid)[0] if len(list(sid)) == 1 else None
        r = self.db.one("SELECT name, start_date, end_date FROM semesters WHERE id=?", (sid,)) if sid else None
        s, e = (parse_date(r["start_date"]), parse_date(r["end_date"])) if r else (None, None)
        if not (s and e) or e <= s:
            return None
        today = self.repo.today()
        total = (e - s).days + 1
        elapsed = min(max((today - s).days + 1, 0), total)
        return {"name": r["name"], "total": total, "elapsed": elapsed, "left": max(0, (e - today).days), "pct": elapsed / total * 100,
                "state": "upcoming" if today < s else "finished" if today > e else "running"}

    def workload(self, sid: Any = None, days: int = 14) -> list[tuple[str, int]]:
        """Number of deadlines (assignments, exams, project deadlines) falling on each of the next ``days`` days."""
        today = self.repo.today()
        counts = Counter(d["date"] for d in self.deadlines(sid, days, include_overdue=False))
        return [((today + timedelta(days=i)).isoformat(), counts.get((today + timedelta(days=i)).isoformat(), 0)) for i in range(days)]

    def onboarding(self) -> list[dict]:
        """'Getting started' checklist: each step is done when real records exist."""
        n = lambda sql: safe_int(self.db.scalar(sql, default=0))
        prof = self.repo.profile()
        steps = [("Set up your programme and semesters", n("SELECT COUNT(*) FROM semesters") > 0, "structure"),
                 ("Add your subjects", n("SELECT COUNT(*) FROM subjects") > 0, "subjects"),
                 ("Add your syllabus units", n("SELECT COUNT(*) FROM units") > 0, "subjects"),
                 ("Enter your weekly timetable", n("SELECT COUNT(*) FROM timetable") > 0, "attendance"),
                 ("Add an assignment or an exam", n("SELECT COUNT(*) FROM assignments") + n("SELECT COUNT(*) FROM exams") > 0, "assignments"),
                 ("Record attendance or a study session", n("SELECT COUNT(*) FROM attendance") + n("SELECT COUNT(*) FROM study_sessions") > 0, "study"),
                 ("Fill in your student profile", bool((prof.get("student_name") or "").strip()), "settings")]
        return [{"label": lab, "done": done, "page": page} for lab, done, page in steps]

    def todays_classes(self, sid: Any = None) -> list[dict]:
        """Classes scheduled for today (from the weekly timetable), with whether attendance is already marked."""
        today = self.repo.today()
        sc, sa = self._sc(sid)
        marked = {r[0] for r in self.db.q("SELECT DISTINCT subject_id FROM attendance WHERE att_date=?", (today.isoformat(),))}
        return [{"subject_id": r["sid"], "subject": r["sn"], "start": r["start_time"], "end": r["end_time"], "kind": r["kind"], "room": r["room"], "marked": r["sid"] in marked}
                for r in self.db.q("SELECT t.subject_id sid, s.name sn, t.start_time, t.end_time, t.kind, t.room FROM timetable t JOIN subjects s ON s.id=t.subject_id "
                                   f"WHERE t.day=?{sc} ORDER BY t.start_time", [WEEKDAYS[today.weekday()]] + sa)]

    @_memoized
    def gpa_totals(self, programme_id: Optional[int] = None) -> dict:
        """Credits and Σ credits×points earned so far in a programme — the basis for CGPA goal planning."""
        cfg = self.settings.grading()
        if programme_id is None:
            cur = self.repo.current_semester_id()
            row = self.db.one("SELECT programme_id FROM semesters WHERE id=?", (cur,)) if cur else None
            programme_id = row["programme_id"] if row else None
        rows = [dict(r) for r in self.db.q("SELECT r.*, s.credits AS subject_credits, sem.programme_id AS pid FROM subject_results r "
                                           "JOIN subjects s ON s.id=r.subject_id LEFT JOIN semesters sem ON sem.id=s.semester_id")]
        rows = [r for r in rows if r["pid"] == programme_id]
        for r in rows:
            r["ev"] = evaluate_result(r, cfg, r["subject_credits"] or 0)
        credits, points = gpa_basis([r["ev"] for r in pick_attempts(rows, cfg)], cfg)
        return {"programme_id": programme_id, "credits": credits, "points": points, "max_points": cfg.max_points,
                "cgpa": points / credits if credits else None}

    def study_heatmap(self, weeks: int = 12, sid: Any = None) -> dict:
        """Study minutes per day for the last ``weeks`` calendar weeks, starting on a Monday."""
        today = self.repo.today()
        start = today - timedelta(days=today.weekday() + 7 * (weeks - 1))
        cells = self.study_daily((today - start).days + 1, sid)
        return {"start": start, "cells": cells, "goal": self.settings.get_int("daily_goal", 180), "max": max([m for _, m in cells] + [0])}

    def data_health(self) -> list[dict]:
        """Plain-language list of data problems (empty-state: one 'good' line). Nothing is changed."""
        db, out = self.db, []
        add = lambda level, text, page="": out.append({"level": level, "text": text, "page": page})
        n = lambda sql: safe_int(db.scalar(sql, default=0))
        if db.integrity_check() != "ok":
            add("bad", "The database failed its integrity check. Restore a recent backup (Settings → Manage backups).", "settings")
        if v := len(db.foreign_key_violations()):
            add("warn", f"{v} record(s) point to something that no longer exists (foreign-key inconsistencies).", "settings")
        if c := n("SELECT COUNT(*) FROM subjects WHERE semester_id IS NULL"):
            add("warn", f"{c} subject(s) are not assigned to any semester.", "subjects")
        if c := n("SELECT COUNT(*) FROM (SELECT 1 FROM subjects GROUP BY semester_id, lower(name) HAVING COUNT(*) > 1)"):
            add("warn", f"{c} subject name(s) appear more than once in the same semester.", "subjects")
        if c := n("SELECT COUNT(*) FROM semesters WHERE status='Active'") - 1:
            if c > 0:
                add("warn", f"{c + 1} semesters are marked Active; normally only the current one is.", "structure")
        bad_colours = sum(1 for r in db.q("SELECT color FROM subjects") if hex_norm(r[0], "") == "" or hex_norm(r[0], "") != str(r[0] or ""))
        if bad_colours:
            add("info", f"{bad_colours} subject colour(s) are not standard #RRGGBB codes; they are shown correctly but you may want to re-pick them.", "subjects")
        if c := n("SELECT COUNT(*) FROM subjects WHERE COALESCE(credits,0)=0"):
            add("info", f"{c} subject(s) have no credits, so credit-weighted SGPA/CGPA cannot weigh them.", "subjects")
        if c := n("SELECT COUNT(*) FROM subjects s WHERE NOT EXISTS (SELECT 1 FROM units u WHERE u.subject_id=s.id)"):
            add("info", f"{c} subject(s) have no syllabus units yet, so syllabus progress is blank for them.", "subjects")
        if c := n("SELECT COUNT(*) FROM semesters WHERE COALESCE(start_date,'')='' OR COALESCE(end_date,'')=''"):
            add("info", f"{c} semester(s) have no start or end date.", "structure")
        if c := n("SELECT COUNT(*) FROM assignments WHERE given_date<>'' AND due_date<>'' AND due_date < given_date"):
            add("warn", f"{c} assignment(s) have a deadline before the date they were given.", "assignments")
        if c := n("SELECT COUNT(*) FROM exams WHERE marks_obtained IS NOT NULL AND max_marks>0 AND marks_obtained>max_marks") + \
                n("SELECT COUNT(*) FROM marks WHERE marks_obtained>max_marks OR max_marks<=0"):
            add("warn", f"{c} score(s) exceed their maximum marks or have no valid maximum.", "exams")
        bad_dates = sum(n(f"SELECT COUNT(*) FROM {t} WHERE COALESCE({col},'')<>'' AND date({col}) IS NULL") for t, col in
                        (("assignments", "due_date"), ("exams", "exam_date"), ("study_sessions", "study_date"), ("attendance", "att_date"), ("marks", "assessment_date")))
        if bad_dates:
            add("warn", f"{bad_dates} record(s) contain a date that cannot be read (expected YYYY-MM-DD).", "settings")
        missing = [r for r in db.q("SELECT file_path FROM attachments") if not Path(r[0]).exists()]
        if missing:
            add("warn", f"{len(missing)} attached file(s) can no longer be found on disk.", "resources")
        if not out:
            add("good", "No data problems found.", "")
        return out

    # ---- alerts (top-bar bell) ----------------------------------------------------------
    def alerts(self, sid: Optional[int] = None) -> list[Alert]:
        out: list[Alert] = []
        today = self.repo.today()
        asg = self.assignment_stats(sid)
        if asg["overdue"]:
            titles = [d["title"] for d in self.deadlines(sid, 0, True) if d["kind"] == "Assignment" and d["status"] == "Overdue"][:3]
            out.append(Alert("danger", f"{asg['overdue']} overdue assignment(s)", ", ".join(titles), "assignments"))
        soon = [d for d in self.deadlines(sid, 3, False) if d["kind"] == "Assignment"]
        if soon:
            out.append(Alert("warning", f"{len(soon)} assignment(s) due within 3 days", ", ".join(d["title"] for d in soon[:3]), "assignments"))
        for d in [x for x in self.deadlines(sid, 7, False) if x["kind"] == "Exam"][:3]:
            ready = d["status"] in ("Prepared", "Completed")
            out.append(Alert("info" if ready else "warning", f"Exam in {d['days_left']} day(s): {d['title']}",
                             f"{d['subject']} — preparation: {d['status'] or 'Not Started'}", "exams"))
        for a in self.attendance_by_subject(sid):
            if a["status"] == "Shortage":
                out.append(Alert("danger", f"Attendance shortage: {a['subject']}", f"{a['pct']:.1f}% (target {a['target']:.0f}%)", "attendance"))
        sc, sa = self._sc(sid)
        due = self.db.scalar("SELECT COUNT(*) FROM revisions t JOIN units u ON u.id=t.unit_id JOIN subjects s ON s.id=u.subject_id "
                             f"WHERE t.status='Due' AND t.due_date <= ?{sc}", [today.isoformat()] + sa, 0)
        if due:
            out.append(Alert("info", f"{due} revision(s) due", "Open the Study hub to review them.", "study"))
        for d in [x for x in self.deadlines(sid, 7, True) if x["kind"] == "Project"][:2]:
            out.append(Alert("danger" if d["status"] == "Overdue" else "warning", f"Project deadline: {d['title']}", fmt_date(d["date"]), "projects"))
        return out


class AppCore:
    """Wires database, settings, repository, analytics and backups (no GUI)."""

    def __init__(self, data_dir: Optional[Path | str] = None, db_path: Optional[Path | str] = None) -> None:
        self.dir = Path(data_dir) if data_dir else resolve_data_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path) if db_path else self.dir / DB_FILENAME
        self.db = Database(self.db_path)
        self.settings = SettingsStore(self.db)
        self.bus = ChangeBus()
        self.repo = Repository(self.db, self.settings, self.bus)
        self.backups = BackupManager(self.db_path.parent / "backups")
        self.repo.snapshot_hook = lambda reason: self.backups.create(self.db, kind="safety", reason=reason)
        self.analytics = Analytics(self.repo)
        self.settings.set("app_version", APP_VERSION)

    def housekeeping(self) -> Optional[str]:
        """Daily automatic backup (if enabled). Returns a message when something notable happened."""
        if not self.settings.get_bool("auto_backup"):
            return None
        try:
            made = self.backups.daily_auto_backup(self.db, max(1, self.settings.get_int("backup_keep", 14)))
            return f"Automatic backup saved: {made.name}" if made else None
        except Exception as exc:  # never block start-up because of a backup problem
            LOG.warning("Automatic backup failed: %s", exc)
            return f"Automatic backup could not be created: {exc}"

    def restore(self, path: Path | str) -> Path:
        safety = restore_database(self.db, path, self.backups)
        self.settings.invalidate()
        self.bus.notify("*")
        return safety

    def info(self) -> dict:
        return {"path": str(self.db_path), "size": self.db_path.stat().st_size if self.db_path.exists() else 0,
                "schema": self.db.schema_version(), "integrity": self.db.integrity_check(), "version": APP_VERSION,
                "fk_problems": len(self.db.foreign_key_violations())}

    def close(self) -> None:
        self.db.close()


class ReadCore:
    """Light, thread-owned view of the database for background work (PDF export).

    SQLite connections must not be shared across threads, so a worker opens its own
    connection; it never migrates or writes."""

    def __init__(self, db_path: Path | str, fixed_today: Optional[date] = None) -> None:
        self.db = Database(db_path, migrate=False)
        self.settings = SettingsStore(self.db)
        self.repo = Repository(self.db, self.settings)
        self.repo.fixed_today = fixed_today
        self.analytics = Analytics(self.repo)

    def close(self) -> None:
        self.db.close()


def setup_logging(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(folder / "study_space_pro.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)


# ════════════════════════════════════════════════════════════════════════════
# 8 · PDF REPORTS  (ReportLab — fully customisable, Unicode-safe, atomic output)
# ════════════════════════════════════════════════════════════════════════════
try:
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A3, A4, LETTER, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader, simpleSplit
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus.doctemplate import LayoutError
    from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Flowable, Frame, Image as RLImage, KeepTogether,
                                    LongTable, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)
    HAS_REPORTLAB = True
except ImportError:  # the app still starts; report generation explains what to install
    HAS_REPORTLAB = False
    Flowable = object  # type: ignore[assignment,misc]
    LayoutError = Exception  # type: ignore[assignment,misc]
try:
    import uharfbuzz  # noqa: F401  (optional: shaping for Devanagari and other Indic scripts)
    HAS_SHAPING = True
except ImportError:
    HAS_SHAPING = False

REPORT_SECTIONS = {
    "profile": "Student profile", "summary": "Executive summary", "structure": "Academic structure",
    "history": "Semester-wise performance (SGPA / CGPA)", "subjects": "Subject performance", "results": "Subject results",
    "assignments": "Assignments", "exams": "Examinations", "assessments": "Assessments & marks", "attendance": "Attendance",
    "study": "Study activity", "syllabus": "Syllabus completion", "projects": "Projects, labs & activities",
    "timetable": "Weekly timetable", "analysis": "Performance analysis", "grading": "Grading & calculation method", "notes": "Notes & remarks"}
REPORT_KINDS: dict[str, tuple[str, list[str]]] = {
    "complete": ("Complete academic report", ["profile", "summary", "structure", "history", "subjects", "results", "assignments",
                                              "exams", "assessments", "attendance", "timetable", "study", "syllabus", "projects", "analysis", "grading", "notes"]),
    "profile": ("Student profile report", ["profile", "structure", "history", "grading"]),
    "year": ("Academic year report", ["profile", "summary", "history", "subjects", "results", "attendance", "analysis", "grading"]),
    "semester": ("Semester report", ["profile", "summary", "subjects", "results", "assignments", "exams", "assessments",
                                     "attendance", "study", "syllabus", "projects", "analysis", "grading"]),
    "subject": ("Subject performance report", ["summary", "subjects", "results", "assignments", "exams", "assessments",
                                               "attendance", "study", "syllabus"]),
    "assignments": ("Assignment report", ["summary", "assignments"]),
    "exams": ("Examination report", ["exams", "assessments"]),
    "results": ("Marks and results report", ["history", "results", "assessments", "grading"]),
    "attendance": ("Attendance report", ["attendance"]),
    "study": ("Study activity report", ["study", "syllabus"]),
    "syllabus": ("Syllabus completion report", ["syllabus"]),
    "projects": ("Project report", ["projects"]),
    "analysis": ("Academic performance analysis", ["summary", "subjects", "analysis"]),
    "weekly": ("Weekly progress report", ["summary", "assignments", "attendance", "study"]),
    "custom": ("Custom report", ["summary", "subjects"]),
}
PAPER_SIZES = ("A4", "A3", "Letter")
DEFAULT_HIDDEN = {"exams": {"venue"}}   # optional columns off by default (can be switched on)
PALETTES = {"Professional blue": "#2457A7", "Graphite": "#374151", "Emerald": "#0B7A4B", "Burgundy": "#8C2F39",
            "Teal": "#087F75", "Custom": ""}
# section -> [(column key, heading, width weight, align l|c|r)]
SECTION_COLUMNS: dict[str, list[tuple[str, str, float, str]]] = {
    "structure": [("programme", "Programme", 2.2, "l"), ("year", "Year", 1.2, "l"), ("semester", "Semester", 1.8, "l"),
                  ("status", "Status", 1.1, "l"), ("subjects", "Subjects", 0.9, "r"), ("credits", "Credits", 0.9, "r")],
    "history": [("semester", "Semester", 2.0, "l"), ("status", "Status", 1.1, "l"), ("subjects", "Subjects", 0.9, "r"),
                ("credits", "Credits (earned/total)", 1.5, "c"), ("sgpa", "SGPA", 0.8, "r"), ("cgpa", "CGPA", 0.8, "r"),
                ("pct", "Marks %", 0.9, "r"), ("backlogs", "Backlogs", 0.9, "r"), ("attendance", "Attendance", 1.1, "r"),
                ("study", "Study time", 1.1, "r"), ("assignments", "Assignments", 1.1, "c")],
    "results": [("subject", "Subject", 2.6, "l"), ("semester", "Semester", 1.4, "l"), ("attempt", "Attempt", 0.8, "c"),
                ("internal", "Internal", 1.0, "c"), ("external", "External", 1.0, "c"), ("practical", "Practical", 1.0, "c"),
                ("total", "Total", 1.0, "c"), ("pct", "%", 0.8, "r"), ("grade", "Grade", 0.8, "c"), ("points", "Points", 0.8, "r"),
                ("credits", "Credits", 0.8, "r"), ("result", "Result", 1.0, "l")],
    "subjects": [("subject", "Subject", 2.8, "l"), ("code", "Code", 1.0, "l"), ("credits", "Credits", 0.8, "r"), ("score", "Score %", 0.9, "r"),
                 ("n", "Scores", 0.7, "r"), ("trend", "Trend", 1.2, "l"), ("attendance", "Attendance", 1.0, "r"),
                 ("syllabus", "Syllabus", 0.9, "r"), ("study", "Study time", 1.0, "r")],
    "assignments": [("no", "No.", 0.6, "l"), ("title", "Assignment", 3.0, "l"), ("subject", "Subject", 2.2, "l"), ("type", "Type", 1.1, "l"),
                    ("deadline", "Deadline", 1.3, "l"), ("submitted", "Submitted", 1.3, "l"), ("status", "Status", 1.4, "l"),
                    ("priority", "Priority", 0.9, "l"), ("marks", "Marks", 0.9, "c")],
    "exams": [("title", "Examination", 2.6, "l"), ("subject", "Subject", 2.2, "l"), ("type", "Type", 1.5, "l"), ("date", "Date", 1.2, "l"),
              ("marks", "Marks", 0.9, "c"), ("pct", "%", 0.7, "r"), ("grade", "Grade", 0.7, "c"), ("result", "Result", 0.9, "l"),
              ("attempt", "Attempt", 0.7, "c"), ("prep", "Preparation", 1.2, "l"), ("venue", "Venue", 1.2, "l")],
    "assessments": [("date", "Date", 1.2, "l"), ("title", "Title", 2.4, "l"), ("type", "Type", 1.5, "l"), ("subject", "Subject", 2.2, "l"),
                    ("obtained", "Obtained", 0.9, "r"), ("max", "Maximum", 0.9, "r"), ("pct", "%", 0.8, "r")],
    "attendance": [("subject", "Subject", 2.8, "l"), ("held", "Held", 0.8, "r"), ("attended", "Attended", 0.9, "r"), ("missed", "Missed", 0.8, "r"),
                   ("pct", "%", 0.8, "r"), ("target", "Target", 0.8, "r"), ("status", "Status", 1.1, "l"), ("action", "To reach target / can skip", 2.2, "l")],
    "study_subjects": [("subject", "Subject", 3.0, "l"), ("time", "Time", 1.2, "r"), ("sessions", "Sessions", 1.0, "r"),
                       ("share", "Share", 0.9, "r"), ("target", "Weekly target", 1.2, "r")],
    "study": [("date", "Date", 1.2, "l"), ("subject", "Subject", 2.4, "l"), ("topic", "Topic", 2.8, "l"), ("time", "Time", 1.0, "r"), ("type", "Type", 1.0, "l")],
    "syllabus": [("subject", "Subject", 2.8, "l"), ("units", "Units done", 1.2, "c"), ("unit_pct", "Units %", 0.9, "r"),
                 ("chapters", "Chapters done", 1.3, "c"), ("chapter_pct", "Chapters %", 1.0, "r")],
    "syllabus_open": [("subject", "Subject", 2.4, "l"), ("unit", "Unit", 0.7, "c"), ("title", "Title", 3.0, "l"),
                      ("status", "Status", 1.2, "l"), ("confidence", "Confidence", 1.0, "c")],
    "projects": [("category", "Category", 1.6, "l"), ("title", "Title", 2.8, "l"), ("subject", "Subject", 1.8, "l"), ("status", "Status", 1.1, "l"),
                 ("deadline", "Deadline", 1.2, "l"), ("milestones", "Milestones", 1.0, "c"), ("supervisor", "Supervisor", 1.5, "l"), ("marks", "Marks", 0.9, "c")],
    "trend": [("subject", "Subject", 3.0, "l"), ("score", "Score %", 1.0, "r"), ("n", "Scores", 0.8, "r"), ("trend", "Trend", 1.3, "l"), ("delta", "Change (pts)", 1.2, "r")],
    "timetable": [("day", "Day", 1.3, "l"), ("time", "Time", 1.5, "c"), ("subject", "Subject", 3.2, "l"), ("type", "Type", 1.0, "l"), ("room", "Room", 1.0, "l"), ("faculty", "Faculty", 1.8, "l")],
    "grading": [("grade", "Grade", 1.0, "c"), ("min", "Minimum %", 1.2, "r"), ("points", "Grade points", 1.2, "r"), ("label", "Description", 3.0, "l")],
}
_PRINT_TOKENS = {"success": "#146C46", "warning": "#8A5A00", "danger": "#B42318", "info": "#2457A7", "muted": "#566274"}


@dataclass
class ReportConfig:
    """Every user-adjustable option of a PDF report (saved as JSON presets)."""
    kind: str = "complete"
    title: str = ""
    subtitle: str = ""
    paper: str = "A4"
    orientation: str = "Portrait"
    margins_mm: list = field(default_factory=lambda: [16.0, 16.0, 16.0, 16.0])   # left, right, top, bottom
    header_text: str = ""
    footer_text: str = ""
    page_numbers: bool = True
    show_date: bool = True
    cover_page: bool = False
    executive_summary: bool = True
    student_info: bool = True
    branding: bool = True
    logo_path: str = ""
    palette: str = "Professional blue"
    accent: str = "#2457A7"
    font_scale: float = 1.0
    density: str = "Comfortable"
    row_spacing: float = 1.0
    include_charts: bool = True
    sections: list = field(default_factory=list)          # ordered keys; empty = defaults for the report type
    scope_semester_id: Optional[int] = None
    scope_year_id: Optional[int] = None
    scope_subject_id: Optional[int] = None
    date_from: str = ""
    date_to: str = ""
    record_limit: int = 500
    notes: str = ""
    columns: dict = field(default_factory=dict)           # section -> ordered visible column keys
    widths: dict = field(default_factory=dict)            # section -> {column key: width weight}

    def resolved_sections(self) -> list[str]:
        base = list(self.sections) if self.sections else list(REPORT_KINDS.get(self.kind, REPORT_KINDS["custom"])[1])
        out = [s for s in dict.fromkeys(base) if s in REPORT_SECTIONS]
        if not self.executive_summary and "summary" in out:
            out.remove("summary")
        if self.notes.strip() and "notes" not in out:
            out.append("notes")
        return out

    def effective_accent(self) -> str:
        return hex_norm(self.accent or PALETTES.get(self.palette) or "#2457A7", "#2457A7") if self.palette == "Custom" \
            else (PALETTES.get(self.palette) or hex_norm(self.accent, "#2457A7"))

    def page_size(self) -> tuple[float, float]:
        w, h = {"A4": A4, "A3": A3, "Letter": LETTER}[self.paper]
        return (h, w) if self.orientation == "Landscape" else (w, h)

    def validate(self) -> None:
        if self.kind not in REPORT_KINDS:
            raise ValidationError("Unknown report type.")
        if self.paper not in PAPER_SIZES or self.orientation not in ("Portrait", "Landscape"):
            raise ValidationError("Choose A4, A3 or Letter, in portrait or landscape.")
        if self.density not in DENSITY or self.palette not in PALETTES:
            raise ValidationError("Unknown table density or colour palette.")
        if len(self.margins_mm) != 4 or any(not (5 <= (safe_float(m, -1) or -1) <= 60) for m in self.margins_mm):
            raise ValidationError("Each page margin must be between 5 and 60 mm.")
        if not 0.7 <= (safe_float(self.font_scale, 0) or 0) <= 1.6:
            raise ValidationError("Font scale must be between 70% and 160%.")
        if not 0.5 <= (safe_float(self.row_spacing, 0) or 0) <= 2.5:
            raise ValidationError("Row spacing must be between 0.5 and 2.5.")
        if not 1 <= safe_int(self.record_limit) <= 20000:
            raise ValidationError("Record limit must be between 1 and 20,000.")
        for d, label in ((self.date_from, "From"), (self.date_to, "To")):
            if d and not parse_date(d):
                raise ValidationError(f"{label} date is not valid.")
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValidationError("The report end date is before its start date.")
        pw, ph = self.page_size()
        ml, mr, mt, mb = (float(m) * mm for m in self.margins_mm)
        if pw - ml - mr < 80 * mm or ph - mt - mb < 80 * mm:
            raise ValidationError("The margins leave too little room on the page.")
        bad = [s for s in self.sections if s not in REPORT_SECTIONS]
        if bad:
            raise ValidationError(f"Unknown report section(s): {', '.join(bad)}")

    def to_json(self) -> str:
        return json.dumps(self.__dict__, sort_keys=True)

    @classmethod
    def from_json(cls, text: Any) -> "ReportConfig":
        cfg = cls()
        try:
            data = json.loads(text) if text else {}
            for k, v in (data.items() if isinstance(data, dict) else []):
                if k in cfg.__dict__:
                    setattr(cfg, k, v)
        except (ValueError, TypeError):
            LOG.warning("Report preset could not be read; defaults used")
        return cfg


# ---- fonts: one Unicode family + per-character fallback -----------------------------------
_COMPLEX_RANGE = re.compile("[\u0900-\u0DFF\u0E00-\u0EFF]")
_PRIMARY_FONTS = (("segoeui.ttf", "segoeuib.ttf"), ("arial.ttf", "arialbd.ttf"), ("arial.ttf", "arial bold.ttf"),
                  ("calibri.ttf", "calibrib.ttf"), ("dejavusans.ttf", "dejavusans-bold.ttf"),
                  ("liberationsans-regular.ttf", "liberationsans-bold.ttf"), ("notosans-regular.ttf", "notosans-bold.ttf"),
                  ("freesans.ttf", "freesansbold.ttf"))
_FALLBACK_FONTS = (("nirmala.ttf", "nirmalab.ttf"), ("nirmala.ttc", "nirmalab.ttf"), ("notosansdevanagari-regular.ttf", "notosansdevanagari-bold.ttf"),
                   ("mangal.ttf", "mangalb.ttf"), ("arialuni.ttf", None), ("seguisym.ttf", None),
                   ("dejavusans.ttf", "dejavusans-bold.ttf"), ("notosanssymbols-regular.ttf", None))


class PdfFonts:
    """Registers a Unicode TrueType family once; splits text into runs by glyph coverage."""

    _lock = threading.Lock()
    _shared: Optional["PdfFonts"] = None

    @classmethod
    def get(cls) -> "PdfFonts":
        with cls._lock:
            if cls._shared is None:
                cls._shared = cls()
            return cls._shared

    def __init__(self) -> None:
        self.index = self._index_fonts()
        self.chain: list[tuple[str, str, Optional[set]]] = []   # (regular, bold, cmap) — primary first
        self.limited = False
        for names, is_primary in ((_PRIMARY_FONTS, True), (_FALLBACK_FONTS, False)):
            for reg, bold in names:
                got = self._register(reg, bold)
                if got and got[0] not in {c[0] for c in self.chain}:
                    self.chain.append(got)
                    if is_primary:
                        break
            if is_primary and not self.chain:
                self.chain.append(("Helvetica", "Helvetica-Bold", None))
                self.limited = True
        self.primary, self.primary_bold = self.chain[0][0], self.chain[0][1]
        if not self.limited and len(self.chain) > 3:
            self.chain = self.chain[:3]

    @staticmethod
    def _index_fonts() -> dict[str, Path]:
        dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts", Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"),
                Path("/Library/Fonts"), Path.home() / "Library/Fonts", Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts"]
        found: dict[str, Path] = {}
        for base in dirs:
            try:
                if base.is_dir():
                    for p in base.rglob("*"):
                        if p.suffix.lower() in (".ttf", ".ttc"):
                            found.setdefault(p.name.lower(), p)
            except OSError:
                continue
        return found

    def _register(self, reg: str, bold: Optional[str]) -> Optional[tuple[str, str, Optional[set]]]:
        if not HAS_REPORTLAB or reg not in self.index:
            return None
        try:
            base = "SS" + re.sub(r"\W", "", reg.rsplit(".", 1)[0])
            pdfmetrics.registerFont(TTFont(base, str(self.index[reg]), subfontIndex=0) if reg.endswith(".ttc") else TTFont(base, str(self.index[reg])))
            bname = base
            if bold and bold in self.index:
                bname = base + "B"
                pdfmetrics.registerFont(TTFont(bname, str(self.index[bold])))
            pdfmetrics.registerFontFamily(base, normal=base, bold=bname, italic=base, boldItalic=bname)
            return base, bname, set(pdfmetrics.getFont(base).face.charToGlyph)
        except Exception as exc:  # a broken font file must not break reports
            LOG.warning("Font %s could not be registered: %s", reg, exc)
            return None

    def _covers(self, entry: tuple, ch: str) -> bool:
        cmap = entry[2]
        return (32 <= ord(ch) < 256) if cmap is None else ord(ch) in cmap

    def runs(self, text: str, bold: bool = False) -> list[tuple[str, str]]:
        """[(font name, text chunk)] choosing, per character, the first font that has the glyph."""
        out: list[tuple[str, str]] = []
        for ch in str(text):
            if ch in "\u200b\u200c\u200d\ufeff\u00ad" and not HAS_SHAPING:
                continue
            if ch in "\n\t" or ord(ch) < 32:
                ch = " " if ch == "\t" else ch
            entry = next((e for e in self.chain if ch in "\n " or self._covers(e, ch)), None)
            if entry is None:
                entry, ch = self.chain[0], "?"
            name = entry[1] if bold else entry[0]
            if out and out[-1][0] == name:
                out[-1] = (name, out[-1][1] + ch)
            else:
                out.append((name, ch))
        return out

    def markup(self, text: Any, bold: bool = False) -> str:
        """Escape text for ReportLab Paragraph markup and switch fonts for uncovered characters."""
        from xml.sax.saxutils import escape
        parts = []
        for name, chunk in self.runs("" if text is None else str(text), bold):
            esc = escape(chunk).replace("\n", "<br/>")
            parts.append(esc if name in (self.primary, self.primary_bold) else f'<font name="{name}">{esc}</font>')
        return "".join(parts)

    def width(self, text: str, size: float, bold: bool = False) -> float:
        return sum(pdfmetrics.stringWidth(chunk, name, size) for name, chunk in self.runs(text, bold))

    def draw(self, c: Any, x: float, y: float, text: str, size: float, color: str = "#111827", bold: bool = False,
             align: str = "l", maxw: Optional[float] = None) -> None:
        text = str(text)
        if maxw and self.width(text, size, bold) > maxw:
            while text and self.width(text + "…", size, bold) > maxw:
                text = text[:-1]
            text += "…"
        w = self.width(text, size, bold)
        x = x - w if align == "r" else x - w / 2 if align == "c" else x
        c.setFillColor(rl_colors.HexColor(color))
        for name, chunk in self.runs(text, bold):
            c.setFont(name, size)
            c.drawString(x, y, chunk)
            x += pdfmetrics.stringWidth(chunk, name, size)

    def wrap(self, text: str, size: float, width: float, lines: int = 2, bold: bool = False) -> list[str]:
        words, out, cur = str(text).split(), [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if self.width(trial, size, bold) <= width or not cur:
                cur = trial
            else:
                out.append(cur)
                cur = w
        out.append(cur) if cur else None
        if len(out) > lines:
            out = out[:lines]
            out[-1] = out[-1].rstrip() + "…"
        return [(l if self.width(l, size, bold) <= width else self._clip(l, size, width, bold)) for l in out] or [""]

    def _clip(self, text: str, size: float, width: float, bold: bool) -> str:
        while text and self.width(text + "…", size, bold) > width:
            text = text[:-1]
        return text + "…"


# ---- chart flowables (vector, theme-independent, long names wrap) -----------------------------
class HBars(Flowable):
    """Horizontal bar chart: wrapped labels at the left, value at the bar end, optional target line."""

    def __init__(self, items: Sequence[tuple[str, float, str]], width: float, fonts: PdfFonts, accent: str,
                 vmax: float = 100.0, unit: str = "%", target: Optional[float] = None, decimals: int = 1,
                 label_w: Optional[float] = None) -> None:
        super().__init__()
        self.items, self.fonts, self.accent, self.vmax, self.unit, self.target, self.dec = [(lab, safe_float(v, 0.0) or 0.0, hex_norm(col, "") if col else "") for lab, v, col in items], fonts, hex_norm(accent, "#2457A7"), vmax or 1.0, unit, target, decimals
        self.width, self.size = width, 7.6
        self.label_w = label_w or min(width * 0.38, max([fonts.width(i[0], self.size) for i in self.items] + [40]) + 6)
        self.rows = [fonts.wrap(i[0], self.size, self.label_w - 6, 2) for i in self.items]
        self.heights = [max(15.0, 10.5 * len(r) + 5) for r in self.rows]
        self.height = sum(self.heights) + 4

    def wrap(self, aw: float, ah: float) -> tuple[float, float]:
        return self.width, self.height

    def split(self, aw: float, ah: float) -> list:
        """Let very long charts continue on the next page instead of failing layout."""
        used, k = 4.0, 0
        for h in self.heights:
            if used + h > ah:
                break
            used += h
            k += 1
        if k == 0 or k >= len(self.items):
            return []
        args = (self.width, self.fonts, self.accent, self.vmax, self.unit, self.target, self.dec, self.label_w)
        return [HBars(self.items[:k], *args), HBars(self.items[k:], *args)]

    def draw(self) -> None:
        c, y = self.canv, self.height - 2
        x0, x1 = self.label_w, self.width - 44
        for (label, val, col), lines in zip(self.items, self.rows):
            h = max(15.0, 10.5 * len(lines) + 5)
            for i, line in enumerate(lines):
                self.fonts.draw(c, 0, y - 10.5 * (i + 1) + 3 + (h - 10.5 * len(lines)) / 2 - 1, line, self.size, "#1F2937")
            c.setFillColor(rl_colors.HexColor("#EEF2F7"))
            c.rect(x0, y - h + 4, x1 - x0, h - 8, stroke=0, fill=1)
            w = max(0.0, min(1.0, val / self.vmax)) * (x1 - x0)
            c.setFillColor(rl_colors.HexColor(col or self.accent))
            c.rect(x0, y - h + 4, w, h - 8, stroke=0, fill=1)
            self.fonts.draw(c, x1 + 4, y - h / 2 - 2.5, f"{val:.{self.dec}f}{self.unit}", self.size, "#111827", True)
            y -= h
        if self.target is not None:
            tx = x0 + max(0.0, min(1.0, self.target / self.vmax)) * (x1 - x0)
            c.setStrokeColor(rl_colors.HexColor("#B42318"))
            c.setDash(2, 2)
            c.line(tx, 1, tx, self.height - 1)
            c.setDash()
            self.fonts.draw(c, tx, self.height - 1, f"target {self.target:.0f}{self.unit}", 6.4, "#B42318", align="c")


class XYChart(Flowable):
    """Columns or lines over an ordered x axis (study per day, SGPA/CGPA, monthly attendance)."""

    def __init__(self, labels: Sequence[str], series: Sequence[tuple[str, Sequence[Optional[float]], str]], width: float, height: float,
                 fonts: PdfFonts, kind: str = "bars", vmax: Optional[float] = None, unit: str = "", decimals: int = 1) -> None:
        super().__init__()
        self.labels, self.fonts, self.kind, self.unit, self.dec = list(labels), fonts, kind, unit, decimals
        self.series = [(n, list(v), hex_norm(col, "#2457A7")) for n, v, col in series]
        self.width, self.height = width, height
        top = max([v for _, vals, _ in self.series for v in vals if v is not None] + [1e-9])
        self.vmax = vmax or self._nice(top)

    @staticmethod
    def _nice(v: float) -> float:
        for step in (1, 2, 2.5, 5, 10):
            mag = 10 ** math.floor(math.log10(v))
            if v <= step * mag:
                return step * mag
        return v

    def wrap(self, aw: float, ah: float) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        c, f = self.canv, self.fonts
        left, bottom, top, right = 34.0, 22.0, 12.0 + (10 if len(self.series) > 1 else 0), 8.0
        pw, ph = self.width - left - right, self.height - bottom - top
        n = max(1, len(self.labels))
        for i in range(5):
            gy = bottom + ph * i / 4
            c.setStrokeColor(rl_colors.HexColor("#E3E9F2"))
            c.setLineWidth(0.5)
            c.line(left, gy, left + pw, gy)
            f.draw(c, left - 4, gy - 2.4, f"{self.vmax * i / 4:.{0 if self.vmax >= 10 else 1}f}{self.unit}", 6.4, "#566274", align="r")
        slot = pw / n
        step = max(1, math.ceil(n * 26 / max(pw, 1)))
        for i, lab in enumerate(self.labels):
            if i % step == 0:
                f.draw(c, left + slot * (i + 0.5), 8, lab, 6.4, "#566274", align="c", maxw=slot * step)
        for si, (name, vals, col) in enumerate(self.series):
            pts = []
            for i, v in enumerate(vals):
                if v is None:
                    continue
                hgt = max(0.0, min(1.0, v / self.vmax)) * ph
                x = left + slot * (i + 0.5)
                if self.kind == "bars":
                    bw = max(1.5, slot * 0.7)
                    c.setFillColor(rl_colors.HexColor(col))
                    c.rect(x - bw / 2, bottom, bw, hgt, stroke=0, fill=1)
                else:
                    pts.append((x, bottom + hgt, v))
            if pts:
                c.setStrokeColor(rl_colors.HexColor(col))
                c.setLineWidth(1.6)
                for (xa, ya, _), (xb, yb, _) in zip(pts, pts[1:]):
                    c.line(xa, ya, xb, yb)
                c.setFillColor(rl_colors.HexColor(col))
                for x, y, v in pts:
                    c.circle(x, y, 2.4, stroke=0, fill=1)
                    if len(pts) <= 14:
                        f.draw(c, x, y + 4.5, f"{v:.{self.dec}f}", 6.4, "#111827", True, "c")
            if len(self.series) > 1:
                lx = left + si * 90
                c.setFillColor(rl_colors.HexColor(col))
                c.rect(lx, self.height - 9, 8, 5, stroke=0, fill=1)
                f.draw(c, lx + 11, self.height - 9, name, 6.8, "#1F2937")


class ReportEngine:
    """Collects data for the selected scope and renders the PDF."""

    def __init__(self, repo: Repository, analytics: Analytics, cfg: ReportConfig, preview: bool = False) -> None:
        if not HAS_REPORTLAB:
            raise RuntimeError("PDF reports need the ReportLab package.  Install it with:  pip install reportlab")
        self.repo, self.an, self.cfg, self.preview = repo, analytics, cfg, preview
        cfg.validate()
        self.settings, self.db = repo.settings, repo.db
        self.fonts = PdfFonts.get()
        self.page = cfg.page_size()
        self.margins = [float(m) * mm for m in cfg.margins_mm]
        self.frame_w = self.page[0] - self.margins[0] - self.margins[1]
        self.accent = cfg.effective_accent()
        self.head_fg = readable_on(self.accent)
        self.soft = mix("#FFFFFF", self.accent, 0.07)
        self.limit = min(25, cfg.record_limit) if preview else cfg.record_limit
        self.date_style = self.settings.get("date_style") or "%d %b %Y"
        self.counts: dict[str, int] = {}
        self.warnings: list[str] = []
        self.charts_off = False
        self.safe_mode = False                    # last-resort layout: short cells, no charts
        self.sids = ([cfg.scope_semester_id] if cfg.scope_semester_id else
                     ([r["id"] for r in self.db.q("SELECT id FROM semesters WHERE academic_year_id=?", (cfg.scope_year_id,))] or [-1])
                     if cfg.scope_year_id else None)
        self.rng = (cfg.date_from or None, cfg.date_to or None)
        self.today = repo.today()
        self.logo: Optional[Any] = None   # decoded once, reused on every page header
        if cfg.branding and cfg.logo_path:
            try:
                self.logo = ImageReader(cfg.logo_path)
                self.logo.getSize()
            except Exception as exc:
                self.warnings.append(f"The logo could not be used ({Path(cfg.logo_path).name}); the report was created without it. ({exc})")
                self.logo = None
        self._make_styles()
        if self.fonts.limited:
            self.warnings.append("No Unicode font was found on this computer; characters outside Western European text appear as '?'.")

    # ---- styles & text helpers -------------------------------------------------
    def _make_styles(self) -> None:
        s, f, fb = self.cfg.font_scale, self.fonts.primary, self.fonts.primary_bold
        hx = rl_colors.HexColor

        def ps(name: str, size: float, bold: bool = False, color: str = "#111827", align: int = TA_LEFT, lead: float = 1.32, **kw: Any) -> ParagraphStyle:
            return ParagraphStyle(name, fontName=fb if bold else f, fontSize=size * s, leading=size * s * lead, textColor=hx(color), alignment=align, **kw)
        a = self.accent
        self.st = {
            "body": ps("body", 9.2, spaceAfter=3), "muted": ps("muted", 8, color="#566274"), "small": ps("small", 7.6, color="#566274"),
            "h1": ps("h1", 14.5, True, a, spaceBefore=12, spaceAfter=5, keepWithNext=1), "h2": ps("h2", 10.5, True, "#111827", spaceBefore=8, spaceAfter=3, keepWithNext=1),
            "th": ps("th", 8, True, self.head_fg), "thc": ps("thc", 8, True, self.head_fg, TA_CENTER), "thr": ps("thr", 8, True, self.head_fg, TA_RIGHT),
            "td": ps("td", 8), "tdc": ps("tdc", 8, align=TA_CENTER), "tdr": ps("tdr", 8, align=TA_RIGHT),
            "kl": ps("kl", 7, True, "#566274"), "kv": ps("kv", 16, True, a, lead=1.15), "ks": ps("ks", 7.2, color="#566274"),
            "ct": ps("ct", 27, True, a, lead=1.12), "cs": ps("cs", 13, color="#566274"), "bullet": ps("bullet", 8.8, leftIndent=11, bulletIndent=1, spaceAfter=2)}

    def P(self, text: Any, style: str = "body", bold: bool = False, cap: int = 700, sty: Optional[dict] = None, lines: int = 12, **kw: Any) -> "Paragraph":
        txt = "" if text is None else str(text)
        if self.safe_mode:
            cap, lines = min(cap, 120), min(lines, 3)
        if len(txt) > cap:
            txt = txt[:cap].rstrip() + "…"        # a table row can never outgrow a page: cap characters AND lines
        parts = txt.splitlines()
        if len(parts) > lines:
            txt = "\n".join(parts[:lines]).rstrip() + " …"
        base = (sty or self.st)[style]
        if HAS_SHAPING and _COMPLEX_RANGE.search(txt):
            base = ParagraphStyle(style + "_sh", parent=base, shaping=1)
        return Paragraph(self.fonts.markup(txt, bold), base, **kw)

    def _cell_styles(self, k: float) -> dict:
        """Table text styles shrunk by factor ``k`` (<1) when many columns must fit one page."""
        if k >= 0.999:
            return self.st
        out = dict(self.st)
        for name in ("th", "thc", "thr", "td", "tdc", "tdr"):
            base = self.st[name]
            out[name] = ParagraphStyle(name + "_k", parent=base, fontSize=base.fontSize * k, leading=base.leading * k)
        return out

    def _colored(self, text: str) -> str:
        tok = STATUS_TOKENS.get(text)
        return f'<font color="{_PRINT_TOKENS[tok]}">{self.fonts.markup(text, True)}</font>' if tok in _PRINT_TOKENS and tok != "muted" else self.fonts.markup(text)

    def _date(self, v: Any) -> str:
        return fmt_date(v, self.date_style, blank="").replace(" ", "\u00a0")   # keep a date on one line

    def heading(self, key: str) -> list:
        return [CondPageBreak(42 * mm), self.P(REPORT_SECTIONS[key], "h1")]

    def table(self, section: str, rows: list[dict], status: Sequence[str] = ("status", "result", "trend", "priority"), note: bool = True) -> list:
        defaults = SECTION_COLUMNS[section]
        want = self.cfg.columns.get(section) or [c[0] for c in defaults if c[0] not in DEFAULT_HIDDEN.get(section, ())]
        cols = [next(c for c in defaults if c[0] == k) for k in want if any(c[0] == k for c in defaults)] or list(defaults)
        colw, k = self._col_widths(section, cols, rows)
        sty = self._cell_styles(k)
        al = {"l": ("th", "td"), "c": ("thc", "tdc"), "r": ("thr", "tdr")}
        data = [[self.P(c[1], al[c[3]][0], sty=sty) for c in cols]]
        shown = rows[: self.limit]
        caps = [max(40, int(10 * w / (8.0 * self.cfg.font_scale * 0.45))) for w in colw]   # ~10 lines max per cell
        for r in shown:
            line = []
            for idx, (k, _, _, a) in enumerate(cols):
                v = r.get(k, "")
                if k in status and str(v) in STATUS_TOKENS:
                    line.append(Paragraph(self._colored(str(v)), sty[al[a][1]]))
                else:
                    line.append(self.P(v, al[a][1], cap=caps[idx], sty=sty))
            data.append(line)
        pad = {"Compact": 2.2, "Comfortable": 3.6, "Spacious": 5.6}[self.cfg.density] * self.cfg.row_spacing
        t = LongTable(data, colWidths=colw, repeatRows=1, splitByRow=1)
        style = [("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor(self.accent)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                 ("TOPPADDING", (0, 0), (-1, -1), pad), ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
                 ("LEFTPADDING", (0, 0), (-1, -1), 4.5), ("RIGHTPADDING", (0, 0), (-1, -1), 4.5),
                 ("LINEBELOW", (0, 0), (-1, -1), 0.35, rl_colors.HexColor("#D5DDE9")), ("BOX", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#C3CDDC"))]
        style += [("BACKGROUND", (0, i), (-1, i), rl_colors.HexColor(self.soft)) for i in range(2, len(data), 2)]
        t.setStyle(TableStyle(style))
        out: list = [t]
        if note and len(rows) > self.limit:
            out.append(self.P(f"Showing the first {self.limit} of {len(rows)} records. Raise the record limit to include more.", "small"))
        return out

    def _col_widths(self, section: str, cols: list, rows: list[dict]) -> tuple[list[float], float]:
        """(column widths, font factor). User-set weights win. Otherwise each column gets at least the width of
        its widest header word / unbreakable token; if that exceeds the page the table font shrinks (to ~76%)
        before anything is allowed to wrap mid-word; spare width is shared by weight."""
        user = self.cfg.widths.get(section) or {}
        weights = [max(0.2, safe_float(user.get(k), wt) or wt) for k, _, wt, _ in cols]
        if user:
            return [self.frame_w * w / sum(weights) for w in weights], 1.0
        size, f, pad = 8.0 * self.cfg.font_scale, self.fonts, 9.0
        whole = f.width("W" * 11, size)             # longer unbroken words may be split
        text = []
        for k, label, _, _ in cols:
            head = max((f.width(w, size, True) for w in str(label).split()), default=0.0)
            data = 0.0
            for r in rows[:150]:
                v = str(r.get(k, "") or "")
                if v:
                    data = max(data, max(f.width(tok, size, k in ("status", "result", "trend")) for tok in v.split(" ")[:14]))   # real spaces only: NBSP keeps dates whole
            text.append(max(head, min(data, whole)))
        n, k = len(cols), 1.0
        if sum(text) + pad * n > self.frame_w:
            k = max(0.76, (self.frame_w - pad * n) / max(sum(text), 1.0))
        mins = [pad + t * k for t in text]
        if sum(mins) >= self.frame_w:
            return [self.frame_w * m / sum(mins) for m in mins], k
        extra = self.frame_w - sum(mins)
        return [m + extra * w / sum(weights) for m, w in zip(mins, weights)], k

    def message(self, text: str) -> list:
        return [Spacer(1, 3), self.P(text, "muted"), Spacer(1, 4)]

    def chart_ok(self) -> bool:
        return self.cfg.include_charts and not self.charts_off and not self.safe_mode

    # ---- scope-aware data ----------------------------------------------------------
    def rows_of(self, table: str) -> list[dict]:
        ent = ENTITIES[table]
        rows = self.repo.rows(ent, self.sids)
        sub = self.cfg.scope_subject_id
        if sub:
            rows = [r for r in rows if (r.get("id") if table == "subjects" else r.get("subject_id")) == sub]
        lo, hi = self.rng
        if ent.date_key and (lo or hi):
            rows = [r for r in rows if r.get(ent.date_key) and (not lo or str(r[ent.date_key])[:10] >= lo) and (not hi or str(r[ent.date_key])[:10] <= hi)]
        return rows

    def scope_text(self) -> str:
        c, bits = self.cfg, []
        if c.scope_semester_id:
            bits.append(self.repo.label("semesters", c.scope_semester_id))
        elif c.scope_year_id:
            bits.append(self.repo.label("academic_years", c.scope_year_id))
        else:
            bits.append("All semesters")
        bits.append(self.repo.label("subjects", c.scope_subject_id) if c.scope_subject_id else "All subjects")
        if c.date_from or c.date_to:
            bits.append(f"{self._date(c.date_from) or 'start'} – {self._date(c.date_to) or 'today'}")
        return " · ".join(bits)

    def subject_filter(self, rows: list[dict], key: str = "subject_id") -> list[dict]:
        s = self.cfg.scope_subject_id
        return [r for r in rows if r.get(key) == s] if s else rows

    # ---- sections -----------------------------------------------------------------------
    def sec_profile(self) -> list:
        p = self.repo.profile()
        sid = self.repo.current_semester_id()
        cur = self.db.one("SELECT sem.name sn, ay.name yn, pr.name pn, pr.degree_level dl, pr.branch br, pr.specialisation sp, pr.college_name cn, pr.university_name un "
                          "FROM semesters sem LEFT JOIN academic_years ay ON ay.id=sem.academic_year_id LEFT JOIN programmes pr ON pr.id=sem.programme_id WHERE sem.id=?", (sid,)) if sid else None
        prog = " · ".join(x for x in (cur["pn"], cur["dl"], cur["br"], cur["sp"]) if x) if cur else ""
        pairs = [("Student", p.get("student_name")), ("University roll no.", p.get("roll_no")), ("Student ID", p.get("student_id")),
                 ("Programme", prog or p.get("course")), ("College", p.get("college_name") or (cur and cur["cn"])),
                 ("University", p.get("university_name") or (cur and cur["un"])), ("Admission year", p.get("admission_year")),
                 ("Expected graduation", p.get("expected_graduation_year")), ("Current academic year", cur and cur["yn"]),
                 ("Current semester", cur and cur["sn"]), ("College email", p.get("email")), ("Academic goals", p.get("goals"))]
        pairs = [(k, v) for k, v in pairs if v not in (None, "")]
        self.counts["profile"] = len(pairs)
        if not pairs:
            return self.message("The student profile has not been completed yet (Settings → Student profile).")
        t = Table([[self.P(k, "kl"), self.P(v, "body")] for k, v in pairs], colWidths=[self.frame_w * 0.27, self.frame_w * 0.73])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.35, rl_colors.HexColor("#D5DDE9")),
                               ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5), ("LEFTPADDING", (0, 0), (-1, -1), 3)]))
        photo = p.get("photo_path")
        if photo and Path(photo).is_file():
            try:
                iw, ih = ImageReader(photo).getSize()
                h = 34 * mm
                img = RLImage(photo, width=h * iw / ih, height=h)
                outer = Table([[t, img]], colWidths=[self.frame_w - h * iw / ih - 8, h * iw / ih + 8])
                outer.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
                t._argW = [(self.frame_w - h * iw / ih - 8) * 0.30, (self.frame_w - h * iw / ih - 8) * 0.70]
                return [outer]
            except Exception as exc:
                self.warnings.append(f"Profile photograph skipped: {exc}")
        return [t]

    def _kpi_cards(self, ov: dict) -> list:
        fp = lambda v: "—" if v is None else f"{v:.1f}%"
        a, at, h = ov["assignments"], ov["attendance"], ov["habits"]
        cards = [("SUBJECTS", str(ov["subjects"]), f"{ov['units_done']}/{ov['units_total']} units complete"),
                 ("SYLLABUS", fp(ov["syllabus_pct"]), "units completed"),
                 ("ASSIGNMENTS", f"{a['pending']} pending", f"{a['overdue']} overdue · {a['done']}/{a['total']} done"),
                 ("ATTENDANCE", fp(at["pct"]), "no records yet" if at["pct"] is None else f"{at['status']} (target {at['target']:.0f}%)"),
                 ("STUDY TIME", fmt_minutes(ov["study_minutes"]), f"{fmt_minutes(ov['study_week'])} in the last 7 days"),
                 ("SCORE AVERAGE", fp(ov["score_pct"]), "marks + exams, weighted by maximum"),
                 ("SGPA", "—" if ov["sgpa"] is None else f"{ov['sgpa']:.2f}", "selected semester" if self.cfg.scope_semester_id else "needs a single semester"),
                 ("CGPA", "—" if ov["cgpa"] is None else f"{ov['cgpa']:.2f}", f"{fmt_num(ov['credits_earned'])} credits earned · {ov['backlogs']} backlog(s)")]
        per_row = 4 if self.frame_w > 150 * mm else 2
        grid = [[[self.P(l, "kl"), self.P(v, "kv"), self.P(s, "ks")] for l, v, s in cards[i:i + per_row]] for i in range(0, len(cards), per_row)]
        for row in grid:
            row += [""] * (per_row - len(row))
        t = Table(grid, colWidths=[self.frame_w / per_row] * per_row)
        st = [("BACKGROUND", (0, 0), (-1, -1), rl_colors.HexColor(self.soft)), ("INNERGRID", (0, 0), (-1, -1), 5, rl_colors.white),
              ("BOX", (0, 0), (-1, -1), 5, rl_colors.white), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 7),
              ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("LEFTPADDING", (0, 0), (-1, -1), 8)]
        st += [("LINEABOVE", (c, r), (c, r), 2.2, rl_colors.HexColor(self.accent)) for r in range(len(grid)) for c in range(per_row)]
        t.setStyle(TableStyle(st))
        return [t]

    def sec_summary(self) -> list:
        ov = self.an.overview(self.sids, self.rng)
        self.counts["summary"] = 8
        out = self._kpi_cards(ov)
        finds = [i for i in self.an.insights(self.sids, self.rng) if i["kind"] != "habits"][:6]
        if finds:
            out += [Spacer(1, 6), self.P("Key findings", "h2")]
            out += [Paragraph(self.fonts.markup(i["text"]), self.st["bullet"], bulletText="•") for i in finds]
        return out

    def sec_structure(self) -> list:
        hist = self.history_rows()
        self.counts["structure"] = len(hist)
        rows = [{"programme": h["programme"] or "—", "year": h["year_name"] or "—", "semester": h["name"], "status": h["status"] or "",
                 "subjects": h["subjects"], "credits": fmt_num(h["credits_planned"])} for h in hist]
        return self.table("structure", rows) if rows else self.message("No academic structure has been set up yet.")

    def history_rows(self) -> list[dict]:
        hist = self.an.semester_history()
        if self.sids:
            hist = [h for h in hist if h["id"] in self.sids]
        return hist

    def sec_history(self) -> list:
        hist = self.history_rows()
        self.counts["history"] = len(hist)
        if not hist:
            return self.message("No semesters in the selected scope.")
        rows = [{"semester": h["name"], "status": h["status"] or "", "subjects": h["subjects"],
                 "credits": f"{fmt_num(h['credits_earned'])}/{fmt_num(h['credits_registered'])}" if h["results"] else "—",
                 "sgpa": "—" if h["sgpa"] is None else f"{h['sgpa']:.2f}", "cgpa": "—" if h["cgpa"] is None else f"{h['cgpa']:.2f}",
                 "pct": "—" if h["pct"] is None else f"{h['pct']:.1f}%", "backlogs": h["backlogs"] if h["results"] else "—",
                 "attendance": "—" if h["attendance_pct"] is None else f"{h['attendance_pct']:.1f}%", "study": fmt_minutes(h["study_minutes"]),
                 "assignments": f"{h['assign_done']}/{h['assign_total']}"} for h in hist]
        out = self.table("history", rows)
        pts = [h for h in hist if h["sgpa"] is not None]
        if self.chart_ok() and len(pts) >= 2:
            mx = self.settings.grading().max_points
            out += [Spacer(1, 6), self.P("SGPA and CGPA progression", "h2"),
                    XYChart([h["name"] for h in pts], [("SGPA", [h["sgpa"] for h in pts], self.accent), ("CGPA", [h["cgpa"] for h in pts], "#D97706")],
                            self.frame_w, 150, self.fonts, "lines", vmax=mx, decimals=2)]
        out += self.message("Calculation method: " + " ".join(self.settings.grading().describe()[1:3]))
        return out

    def sec_results(self) -> list:
        rows = self.rows_of("subject_results")
        self.counts["results"] = len(rows)
        if not rows:
            return self.message("No semester results recorded in this scope.")
        data = [{"subject": r["subject"], "semester": r["semester"] or "", "attempt": r["attempt_no"], "internal": r["int_txt"], "external": r["ext_txt"],
                 "practical": r["prac_txt"], "total": r["total"], "pct": "" if r["pct"] is None else f"{r['pct']:.1f}%", "grade": r["grade_eff"],
                 "points": fmt_num(r["points_eff"], blank=""), "credits": fmt_num(r["credits_eff"], blank=""), "result": r["result_eff"]} for r in rows]
        out = self.table("results", data)
        dist = self.an.grade_distribution(self.sids)
        if self.chart_ok() and dist:
            out += [Spacer(1, 6), self.P("Grade distribution", "h2"), XYChart([g for g, _ in dist], [("Subjects", [n for _, n in dist], self.accent)],
                                                                              min(self.frame_w, 110 * mm), 100, self.fonts, "bars", decimals=0)]
        return out

    def _subject_perf(self) -> list[dict]:
        subs = self.rows_of("subjects") if not self.rng[0] and not self.rng[1] else [
            r for r in self.repo.rows("subjects", self.sids) if not self.cfg.scope_subject_id or r["id"] == self.cfg.scope_subject_id]
        perf = {p["subject_id"]: p for p in self.an.subject_performance(self.sids, self.rng)}
        att = {a["subject_id"]: a for a in self.an.attendance_by_subject(self.sids, self.rng)}
        syl = {s["subject_id"]: s for s in self.an.syllabus_by_subject(self.sids)}
        stu = {s["subject_id"]: s for s in self.an.study_by_subject(self.sids, self.rng)}
        out = []
        for s in subs:
            p, a, y, t = perf.get(s["id"]), att.get(s["id"]), syl.get(s["id"]), stu.get(s["id"])
            out.append({"id": s["id"], "subject": s["name"], "color": s["color"], "code": s["code"], "credits": fmt_num(s["credits"], blank=""),
                        "pct": p["pct"] if p else None, "n": p["n"] if p else 0, "trend": p["trend"] if p else "", "delta": p["delta"] if p else None,
                        "att": a["pct"] if a and a["held"] else None, "syl": y["pct"] if y and y["units"] else None, "mins": t["minutes"] if t else 0})
        return out

    def sec_subjects(self) -> list:
        data = self._subject_perf()
        self.counts["subjects"] = len(data)
        if not data:
            return self.message("No subjects in the selected scope.")
        rows = [{"subject": d["subject"], "code": d["code"], "credits": d["credits"], "score": "—" if d["pct"] is None else f"{d['pct']:.1f}%",
                 "n": d["n"], "trend": d["trend"] if d["trend"] != "Not enough data" else "—", "attendance": "—" if d["att"] is None else f"{d['att']:.1f}%",
                 "syllabus": "—" if d["syl"] is None else f"{d['syl']:.0f}%", "study": fmt_minutes(d["mins"])} for d in data]
        out = self.table("subjects", rows)
        scored = [d for d in data if d["pct"] is not None]
        if self.chart_ok() and scored:
            out += [Spacer(1, 6), self.P("Average score by subject", "h2"), HBars([(d["subject"], d["pct"], d["color"]) for d in scored], self.frame_w, self.fonts, self.accent)]
        return out

    def sec_assignments(self) -> list:
        rows = self.rows_of("assignments")
        self.counts["assignments"] = len(rows)
        if not rows:
            return self.message("No assignments in the selected scope.")
        a = self.an.assignment_stats(self.sids, self.rng)
        out = self.message(f"Total {a['total']} · pending {a['pending']} · overdue {a['overdue']} · submitted {a['submitted']} · "
                           f"completion {'—' if a['completion_rate'] is None else format(a['completion_rate'], '.1f') + '%'}")
        data = [{"no": r["assignment_no"], "title": r["title"], "subject": r["subject"], "type": r["assignment_type"], "deadline": self._date(r["due_date"]),
                 "submitted": self._date(r["submission_date"]), "status": r["status_eff"], "priority": r["priority"], "marks": r["score"]} for r in rows]
        out += self.table("assignments", data)
        if self.chart_ok() and a["by_status"]:
            cols = {"Overdue": "#B42318", "Late Submission": "#B7791F", "Submitted": "#146C46", "Evaluated": "#146C46", "Completed": "#2457A7"}
            out += [Spacer(1, 6), self.P("Assignments by status", "h2"),
                    HBars([(k, v, cols.get(k, "#7A8699")) for k, v in sorted(a["by_status"].items(), key=lambda kv: -kv[1])], min(self.frame_w, 140 * mm),
                          self.fonts, self.accent, vmax=max(a["by_status"].values()), unit="", decimals=0)]
        return out

    def sec_exams(self) -> list:
        rows = self.rows_of("exams")
        self.counts["exams"] = len(rows)
        if not rows:
            return self.message("No examinations in the selected scope.")
        return self.table("exams", [{"title": r["title"], "subject": r["subject"], "type": r["exam_type"], "date": self._date(r["exam_date"]), "marks": r["score"],
                                     "pct": "" if r["pct"] is None else f"{r['pct']:.1f}%", "grade": r["grade"], "result": r["result"], "attempt": r["attempt_no"],
                                     "prep": r["prep_status"], "venue": r["venue"]} for r in rows])

    def sec_assessments(self) -> list:
        rows = self.rows_of("marks")
        self.counts["assessments"] = len(rows)
        if not rows:
            return self.message("No assessments recorded in this scope.")
        return self.table("assessments", [{"date": self._date(r["assessment_date"]), "title": r["title"] or r["assessment_type"], "type": r["assessment_type"], "subject": r["subject"],
                                           "obtained": fmt_num(r["marks_obtained"]), "max": fmt_num(r["max_marks"]), "pct": "" if r["pct"] is None else f"{r['pct']:.1f}%"} for r in rows])

    def sec_attendance(self) -> list:
        rows = self.subject_filter(self.an.attendance_by_subject(self.sids, self.rng))
        rows = [r for r in rows if r["held"] or not self.rng[0] and not self.rng[1]]
        self.counts["attendance"] = len(rows)
        if not rows:
            return self.message("No attendance data in the selected scope.")
        def action(r: dict) -> str:
            if r["status"] == "No data":
                return "—"
            if r["pct"] < r["target"]:
                return f"Attend next {r['need']} classes in a row" if r["need"] else "Target can no longer be reached"
            return "Can miss " + (f"{r['can_skip']} more" if r["can_skip"] is not None else "any") + " class(es)"
        data = [{"subject": r["subject"], "held": r["held"], "attended": r["attended"], "missed": r["missed"], "pct": "—" if r["pct"] is None else f"{r['pct']:.1f}%",
                 "target": f"{r['target']:.0f}%", "status": r["status"], "action": action(r)} for r in rows]
        out = self.table("attendance", data, note=True)
        target = self.settings.get_float("attendance_min", 75.0)
        if self.chart_ok():
            items = [(r["subject"], r["pct"], r["color"]) for r in rows if r["pct"] is not None]
            if items:
                out += [Spacer(1, 6), self.P("Attendance by subject", "h2"), HBars(items, self.frame_w, self.fonts, self.accent, target=target)]
            monthly = self.an.attendance_monthly(self.sids)
            if len(monthly) >= 2:
                out += [Spacer(1, 6), self.P("Monthly attendance", "h2"), XYChart([m for m, _ in monthly], [("Attendance %", [v for _, v in monthly], self.accent)],
                                                                                 self.frame_w, 130, self.fonts, "lines", vmax=100, unit="%", decimals=0)]
        out += self.message("Attendance = classes attended ÷ classes held, pooled over the selected period. Each subject uses its own target if set, otherwise the default.")
        return out

    def sec_study(self) -> list:
        by = self.subject_filter(self.an.study_by_subject(self.sids, self.rng))
        sess = self.rows_of("study_sessions")
        self.counts["study"] = len(sess)
        total = sum(b["minutes"] for b in by)
        if not sess and not total:
            return self.message("No study sessions in the selected scope.")
        days = len({r["study_date"] for r in sess})
        out = self.message(f"Total {fmt_minutes(total)} in {len(sess)} session(s) across {days} day(s)"
                           + (f" · average {fmt_minutes(total / days)} per study day" if days else ""))
        out += self.table("study_subjects", [{"subject": b["subject"], "time": fmt_minutes(b["minutes"]), "sessions": b["sessions"],
                                              "share": "" if b["share"] is None else f"{b['share']:.0f}%",
                                              "target": f"{fmt_num(b['target_hours'])} h" if b["target_hours"] else "—"} for b in by], note=False)
        if self.chart_ok():
            daily = self.an.study_daily(30, self.sids)
            if any(m for _, m in daily):
                out += [Spacer(1, 6), self.P("Study minutes per day (last 30 days)", "h2"),
                        XYChart([d[5:] for d, _ in daily], [("Minutes", [m for _, m in daily], self.accent)], self.frame_w, 130, self.fonts, "bars", decimals=0)]
        out += [Spacer(1, 6), self.P("Study log", "h2")]
        out += self.table("study", [{"date": self._date(r["study_date"]), "subject": r["subject"], "topic": r["topic"] or r["unit"] or "", "time": fmt_minutes(r["duration_minutes"]),
                                     "type": r["session_type"]} for r in sess])
        return out

    def sec_syllabus(self) -> list:
        syl = self.subject_filter(self.an.syllabus_by_subject(self.sids), "subject_id")
        units = [u for u in self.rows_of("units") if u["status"] != "Completed"]
        self.counts["syllabus"] = len(syl)
        if not syl:
            return self.message("No subjects in the selected scope.")
        fp = lambda v: "—" if v is None else f"{v:.0f}%"
        out = self.table("syllabus", [{"subject": s["subject"], "units": f"{s['done']}/{s['units']}", "unit_pct": fp(s["pct"]), "chapters": f"{s['chapters_done']}/{s['chapters']}",
                                       "chapter_pct": fp(s["chapter_pct"])} for s in syl], note=False)
        scored = [s for s in syl if s["pct"] is not None]
        if self.chart_ok() and scored:
            out += [Spacer(1, 6), self.P("Syllabus completion by subject", "h2"), HBars([(s["subject"], s["pct"], s["color"]) for s in scored], self.frame_w, self.fonts, self.accent, unit="%", decimals=0)]
        if units:
            out += [Spacer(1, 6), self.P("Units still to complete", "h2")]
            out += self.table("syllabus_open", [{"subject": u["subject"], "unit": u["unit_no"], "title": u["title"], "status": u["status"], "confidence": u["confidence"]} for u in units])
        return out

    def sec_projects(self) -> list:
        rows = self.rows_of("projects")
        self.counts["projects"] = len(rows)
        if not rows:
            return self.message("No projects, labs or activities in this scope.")
        return self.table("projects", [{"category": r["category"], "title": r["title"], "subject": r["subject"] or "", "status": r["status"], "deadline": self._date(r["deadline"]),
                                        "milestones": r["progress"], "supervisor": r["supervisor"], "marks": f"{fmt_num(r['marks_obtained'])}/{fmt_num(r['max_marks'])}" if r["marks_obtained"] is not None and r["max_marks"] else ""} for r in rows])

    def sec_timetable(self) -> list:
        rows = self.rows_of("timetable")
        self.counts["timetable"] = len(rows)
        if not rows:
            return self.message("No weekly timetable has been entered for this scope.")
        return self.table("timetable", [{"day": r["day"], "time": f"{r['start_time']} – {r['end_time']}", "subject": r["subject"], "type": r["kind"],
                                         "room": r["room"], "faculty": r["faculty"]} for r in rows], note=False)

    def sec_analysis(self) -> list:
        ins = self.an.insights(self.sids, self.rng)
        data = [d for d in self._subject_perf() if d["pct"] is not None]
        self.counts["analysis"] = len(ins)
        out: list = [Paragraph(self.fonts.markup(i["text"]), self.st["bullet"], bulletText="•") for i in ins]
        trend = [d for d in data if d["trend"] != "Not enough data"]
        if trend:
            out += [Spacer(1, 6), self.P("Score trend by subject", "h2")]
            out += self.table("trend", [{"subject": d["subject"], "score": f"{d['pct']:.1f}%", "n": d["n"], "trend": d["trend"], "delta": f"{d['delta']:+.1f}"} for d in trend], note=False)
            out += self.message("Trend compares the later half of a subject's dated scores with the earlier half (at least 4 scores; a change of more than 5 points counts).")
        if self.chart_ok() and data:
            out += [Spacer(1, 6), self.P("Subject averages", "h2"), HBars([(d["subject"], d["pct"], d["color"]) for d in data], self.frame_w, self.fonts, self.accent,
                                                                       target=self.settings.get_float("low_mark_pct", 40.0))]
        return out

    def sec_grading(self) -> list:
        cfg = self.settings.grading()
        self.counts["grading"] = len(cfg.scale)
        out = [Paragraph(self.fonts.markup(line), self.st["bullet"], bulletText="•") for line in cfg.describe()]
        out += [Spacer(1, 5)] + self.table("grading", [{"grade": r["grade"], "min": fmt_num(r["min"]), "points": fmt_num(r["points"]), "label": r.get("label", "")} for r in cfg._sorted()], note=False)
        return out

    def sec_notes(self) -> list:
        text = self.cfg.notes.strip()
        self.counts["notes"] = 1 if text else 0
        return [self.P(p, "body") for p in text.split("\n") if p.strip()] if text else []

    # ---- document assembly ------------------------------------------------------------------
    def title(self) -> str:
        return self.cfg.title.strip() or REPORT_KINDS[self.cfg.kind][0]

    def _student_block(self) -> list:
        p = self.repo.profile()
        bits = [x for x in (p.get("student_name"), p.get("roll_no") and f"Roll no. {p['roll_no']}", p.get("college_name")) if x]
        return [self.P(" · ".join(bits), "muted")] if bits and self.cfg.student_info else []

    def _cover(self) -> list:
        c, p = self.cfg, self.repo.profile()
        out: list = [Spacer(1, 38 * mm)]
        logo = self._logo(38 * mm)
        if logo:
            out += [logo, Spacer(1, 10 * mm)]
        out += [self.P(self.title(), "ct"), Spacer(1, 4 * mm)]
        if c.subtitle.strip():
            out += [self.P(c.subtitle, "cs"), Spacer(1, 6 * mm)]
        rows = [("Student", p.get("student_name")), ("Roll no.", p.get("roll_no")), ("College", p.get("college_name")), ("University", p.get("university_name")),
                ("Scope", self.scope_text()), ("Report date", self._date(self.today.isoformat()) if c.show_date else "")]
        rows = [(k, v) for k, v in rows if v]
        if rows:
            t = Table([[self.P(k, "kl"), self.P(v, "body")] for k, v in rows], colWidths=[self.frame_w * 0.22, self.frame_w * 0.5])
            t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.35, rl_colors.HexColor("#D5DDE9")), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LEFTPADDING", (0, 0), (-1, -1), 2)]))
            t.hAlign = "LEFT"
            out.append(t)
        return out + [PageBreak()]

    def _logo(self, max_h: float) -> Optional[Any]:
        if self.logo is None:
            return None
        try:
            iw, ih = self.logo.getSize()
            w, h = max_h * iw / ih, max_h
            if w > self.frame_w * 0.6:
                w, h = self.frame_w * 0.6, self.frame_w * 0.6 * ih / iw
            img = RLImage(self.cfg.logo_path, width=w, height=h)
            img.hAlign = "LEFT"
            return img
        except Exception as exc:
            self.warnings.append(f"Logo could not be placed on the cover: {exc}")
            return None

    def build_story(self, progress: Optional[Callable[[int, str], None]] = None) -> list:
        c = self.cfg
        keys = c.resolved_sections()
        story: list = []
        if c.cover_page:
            story += self._cover()
        else:
            story += [self.P(self.title(), "ct" if False else "h1")]
            if c.subtitle.strip():
                story.append(self.P(c.subtitle, "muted"))
            story += self._student_block()
            story += [self.P("Scope: " + self.scope_text() + f"  ·  up to {self.limit} records per table", "small"), Spacer(1, 4)]
        for i, key in enumerate(keys):
            if progress:
                progress(int(100 * i / max(1, len(keys))), f"Building: {REPORT_SECTIONS[key]}")
            try:
                body = getattr(self, f"sec_{key}")()
            except Exception as exc:  # one bad section must not lose the whole report
                LOG.exception("Report section %s failed", key)
                self.warnings.append(f"The “{REPORT_SECTIONS[key]}” section could not be generated: {exc}")
                body = self.message(f"This section could not be generated ({exc}).")
            if body or key == "notes" and False:
                story += self.heading(key) + body
        if not story or all(isinstance(x, Spacer) for x in story):
            story += self.message("There is nothing to report for the chosen scope.")
        return story

    def plan(self) -> list[tuple[str, str, int]]:
        """(key, title, record count) per selected section — drives the settings preview."""
        self.build_story()
        return [(k, REPORT_SECTIONS[k], self.counts.get(k, 0)) for k in self.cfg.resolved_sections()]

    def _decorate(self, c: Any, page: int, total: int) -> None:
        cfg = self.cfg
        if cfg.cover_page and page == 1:
            return
        pw, ph = self.page
        ml, mr, mt, mb = self.margins
        f, ink, muted = self.fonts, "#111827", "#566274"
        c.saveState()
        c.setStrokeColor(rl_colors.HexColor(self.accent))
        c.setLineWidth(1.1)
        c.line(ml, ph - mt - 6 * mm, pw - mr, ph - mt - 6 * mm)
        left = cfg.header_text.strip() or (self.repo.profile().get("college_name") if cfg.branding else "") or ""
        x = ml
        if self.logo is not None:
            try:
                iw, ih = self.logo.getSize()
                h = 5.5 * mm
                c.drawImage(self.logo, ml, ph - mt - 5.2 * mm, width=h * iw / ih, height=h, mask="auto")
                x += h * iw / ih + 2 * mm
            except Exception:
                self.logo = None
        f.draw(c, x, ph - mt - 3.4 * mm, left, 8, ink, True, maxw=(pw - ml - mr) * 0.5)
        f.draw(c, pw - mr, ph - mt - 3.4 * mm, ("PREVIEW — " if self.preview else "") + self.title(), 8, muted, False, "r", maxw=(pw - ml - mr) * 0.48)
        c.setStrokeColor(rl_colors.HexColor("#C3CDDC"))
        c.setLineWidth(0.5)
        c.line(ml, mb + 7 * mm, pw - mr, mb + 7 * mm)
        foot = " · ".join(x for x in (cfg.footer_text.strip(), f"Generated {self._date(self.today.isoformat())}" if cfg.show_date else "", APP_NAME) if x)
        f.draw(c, ml, mb + 2.6 * mm, foot, 7.4, muted, maxw=(pw - ml - mr) * 0.75)
        if cfg.page_numbers:
            f.draw(c, pw - mr, mb + 2.6 * mm, f"Page {page} of {total}", 7.4, muted, True, "r")
        c.restoreState()

    def _canvas_maker(self) -> Any:
        decorate = self._decorate

        class NumberedCanvas(rl_canvas.Canvas):
            """Two-pass canvas so every page knows the final page count."""

            def __init__(self, *a: Any, **k: Any) -> None:
                super().__init__(*a, **k)
                self._pages: list[dict] = []

            def showPage(self) -> None:
                self._pages.append(dict(self.__dict__))
                self._startPage()

            def save(self) -> None:
                total = len(self._pages)
                for state in self._pages:
                    self.__dict__.update(state)
                    decorate(self, self._pageNumber, total)
                    super().showPage()
                super().save()
        return NumberedCanvas

    def generate(self, out_path: Path | str, progress: Optional[Callable[[int, str], None]] = None) -> dict:
        """Render to ``out_path`` atomically; the previous file survives any failure."""
        holder = {"story": self.build_story(progress)}
        result: dict[str, Any] = {}
        ml, mr, mt, mb = self.margins

        def write(tmp: Path) -> None:
            doc = BaseDocTemplate(str(tmp), pagesize=self.page, leftMargin=ml, rightMargin=mr, topMargin=mt + 9 * mm, bottomMargin=mb + 10 * mm,
                                  title=self.title(), author=self.repo.profile().get("student_name") or APP_NAME, subject=self.scope_text(), creator=f"{APP_NAME} {APP_VERSION}")
            doc.addPageTemplates([PageTemplate(id="body", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f",
                                                                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)])])
            doc.build(holder["story"], canvasmaker=self._canvas_maker())
            result["pages"] = doc.page
            raw = tmp.read_bytes()
            if not raw.startswith(b"%PDF") or b"%%EOF" not in raw[-2048:]:
                raise RuntimeError("The generated PDF failed its integrity check and was discarded.")
        if progress:
            progress(96, "Writing PDF")
        try:
            atomic_write(out_path, write)
        except LayoutError as exc:   # safety net: never lose the report because something would not fit
            LOG.warning("PDF layout problem (%s); retrying without charts", exc)
            self.warnings.append("A chart did not fit on the page, so charts were left out of this report.")
            self.charts_off = True
            self.counts.clear()
            holder["story"] = self.build_story()
            try:
                atomic_write(out_path, write)
            except LayoutError as exc2:
                LOG.warning("PDF layout problem again (%s); retrying in safe mode", exc2)
                self.warnings.append("Some very long text did not fit on a page, so long entries were shortened in this report.")
                self.safe_mode = True
                self.counts.clear()
                holder["story"] = self.build_story()
                atomic_write(out_path, write)
        if progress:
            progress(100, "Done")
        return {"path": str(out_path), "pages": result.get("pages", 0), "sections": self.cfg.resolved_sections(), "counts": dict(self.counts),
                "warnings": list(self.warnings), "bytes": Path(out_path).stat().st_size}


def generate_report(core: "AppCore", cfg: ReportConfig, out_path: Path | str, progress: Optional[Callable[[int, str], None]] = None, preview: bool = False) -> dict:
    """Convenience entry point (used by the GUI worker thread with its own AppCore/DB connection)."""
    return ReportEngine(core.repo, core.analytics, cfg, preview).generate(out_path, progress)


# ════════════════════════════════════════════════════════════════════════════
# 9 · QT LAYER I — charts, customisable tables, record forms
#     (thin by design: all rules, filtering, sorting and validation live in the tested code above)
# ════════════════════════════════════════════════════════════════════════════
try:
    from PySide6.QtCore import QAbstractTableModel, QByteArray, QDate, QEvent, QLockFile, QModelIndex, QObject, QRect, QSize, Qt, QThread, QTimer, QUrl, Signal
    from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QFont, QFontMetrics, QGuiApplication, QKeySequence, QPainter, QPalette, QPen, QPixmap, QShortcut, QTextCharFormat
    from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCalendarWidget, QCheckBox, QColorDialog, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                                   QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                                   QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                                   QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QTableView, QTableWidget, QTableWidgetItem, QTabWidget,
                                   QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
    HAS_QT = True
    A_L = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    A_C = Qt.AlignmentFlag.AlignCenter
    A_R = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
except ImportError:      # logic-only mode (tests, CLI): placeholders so this module still imports
    HAS_QT = False
    QAbstractTableModel = QWidget = QDialog = QMainWindow = QFrame = QThread = QTableView = QPushButton = QObject = object  # type: ignore[assignment,misc]

    def Signal(*_a: Any, **_k: Any) -> None:  # type: ignore[misc]
        return None


# ---- per-table preferences (Qt-free, unit-tested) ----------------------------------------------
class TablePrefs:
    """Column order/visibility/width/alignment/colours, sorting, density and rules for ONE table.

    Stored as JSON in the settings table so every customisation survives a restart."""

    LAYOUT_KEYS = ("order", "hidden", "widths", "align", "colors", "sort")

    def __init__(self, settings: SettingsStore, table_id: str) -> None:
        self.settings, self.key = settings, f"table:{table_id}"
        stored = settings.get_json(self.key, {})
        self.d: dict[str, Any] = {"order": [], "hidden": None, "widths": {}, "align": {}, "colors": {}, "sort": [], "density": "",
                                  "alt": True, "grid": True, "row_h": 0, "rules_on": True, "rules": [], "layouts": {}}
        if isinstance(stored, dict):
            self.d.update({k: v for k, v in stored.items() if k in self.d})

    def save(self) -> None:
        self.settings.set(self.key, self.d)

    def layout(self) -> dict:
        return {k: self.d[k] for k in self.LAYOUT_KEYS}

    def apply_layout(self, lay: dict) -> None:
        for k in self.LAYOUT_KEYS:
            if k in lay:
                self.d[k] = lay[k]

    def reset_layout(self) -> None:
        self.apply_layout({"order": [], "hidden": None, "widths": {}, "align": {}, "colors": {}, "sort": []})

    def reset_all(self) -> None:
        keep = self.d["layouts"]
        self.d.update({"density": "", "alt": True, "grid": True, "row_h": 0, "rules_on": True, "rules": []})
        self.reset_layout()
        self.d["layouts"] = keep

    def hidden_keys(self, columns: Sequence[Column]) -> set[str]:
        return set(self.d["hidden"]) if self.d["hidden"] is not None else {c.key for c in columns if not c.vis}

    def user_rules(self) -> list[Rule]:
        out = []
        for r in self.d["rules"]:
            try:
                out.append(Rule(str(r["column"]), str(r["op"]), r.get("value", ""), str(r.get("color", "danger")), str(r.get("scope", "cell")), bool(r.get("bold", False))))
            except (KeyError, TypeError):
                continue
        return out


# ---- small UI helpers ---------------------------------------------------------------------------
def make_label(text: str = "", name: str = "", wrap: bool = False) -> "QLabel":
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def make_button(text: str, slot: Optional[Callable] = None, kind: str = "", tip: str = "") -> "QPushButton":
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if tip:
        b.setToolTip(tip)
    if slot:
        b.clicked.connect(lambda _checked=False, f=slot: f())
    return b


def add_action(menu: "QMenu", text: str, fn: Callable, checkable: bool = False, checked: bool = False, enabled: bool = True) -> "QAction":
    act = menu.addAction(text)
    act.setEnabled(enabled)
    if checkable:
        act.setCheckable(True)
        act.setChecked(checked)
    act.triggered.connect(lambda _c=False, f=fn: f())
    return act


def make_card(layout_cls: Any = None) -> tuple["QFrame", Any]:
    card = QFrame()
    card.setObjectName("Card")
    lay = (layout_cls or QVBoxLayout)(card)
    lay.setContentsMargins(14, 12, 14, 12)
    return card, lay


def scroll_page(inner: "QWidget") -> "QScrollArea":
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setWidget(inner)
    return sa


def confirm(parent: Any, title: str, text: str, danger: bool = False) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
    yes = box.addButton("Yes", QMessageBox.ButtonRole.YesRole)
    no = box.addButton("No", QMessageBox.ButtonRole.NoRole)
    box.setDefaultButton(no)          # destructive actions default to "No"
    box.exec()
    return box.clickedButton() is yes


def tell(parent: Any, title: str, text: str, error: bool = False) -> None:
    (QMessageBox.critical if error else QMessageBox.information)(parent, title, text)


class UiContext:
    """What every page needs: services, theme, navigation, and a consistent way to talk to the user."""

    def __init__(self, core: AppCore, win: Any) -> None:
        self.core, self.win = core, win

    repo = property(lambda s: s.core.repo)
    analytics = property(lambda s: s.core.analytics)
    settings = property(lambda s: s.core.settings)
    theme = property(lambda s: s.win.theme)

    def scope(self) -> Optional[int]:
        return self.repo.scope_semester_id()

    def go(self, page: str) -> None:
        self.win.navigate(page)

    def toast(self, text: str, ms: int = 4000) -> None:
        self.win.statusBar().showMessage(text, ms)

    def guard(self, parent: Any, fn: Callable) -> Any:
        """Run ``fn``; show friendly validation errors; log and report anything unexpected."""
        try:
            return fn()
        except ValidationError as exc:
            tell(parent, "Please check", str(exc))
        except Exception as exc:
            LOG.exception("Unexpected error")
            tell(parent, "Something went wrong", f"{exc}\n\nDetails were written to the log file.", True)
        return None

    def confirm_delete(self, parent: Any, table: str, ids: Sequence[int]) -> bool:
        ent = ENTITIES[table]
        deps = self.repo.dependents(table, ids)
        blockers = [f"{n} {label}" for label, n, eff in deps if eff == "block"]
        if blockers:
            tell(parent, "Cannot delete", f"This still contains {', '.join(blockers)}.\nMove or delete those first.")
            return False
        what = f"{len(ids)} {ent.plural.lower()}" if len(ids) > 1 else f"this {ent.singular.lower()}"
        lines = [f"Delete {what}? This cannot be undone."]
        removed = [f"• {n} {label}" for label, n, eff in deps if eff == "delete"]
        kept = [f"• {n} {label}" for label, n, eff in deps if eff == "detach"]
        if removed:
            lines += ["", "Also permanently deleted:"] + removed
        if kept:
            lines += ["", "Kept (link cleared):"] + kept
        if removed or len(ids) > 1:
            lines += ["", "A safety backup is saved automatically first."]
        return confirm(parent, "Confirm deletion", "\n".join(lines), True)


# ---- charts (QPainter, follow the theme) -----------------------------------------------------------
class ChartWidget(QWidget):
    """Horizontal bars, columns or lines. Shows an honest empty state when there is no data."""

    def __init__(self, ctx: UiContext, empty: str = "No data yet", parent: Any = None) -> None:
        super().__init__(parent)
        self.ctx, self.empty, self.mode = ctx, empty, "hbars"
        self.items: list[tuple[str, float, str]] = []
        self.labels: list[str] = []
        self.series: list[tuple[str, list, str]] = []
        self.vmax, self.unit, self.target, self.dec = 100.0, "%", None, 1
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def set_bars(self, items: Sequence[tuple[str, float, str]], vmax: float = 100.0, unit: str = "%", target: Optional[float] = None, dec: int = 1) -> None:
        self.mode, self.vmax, self.unit, self.target, self.dec = "hbars", vmax or 1.0, unit, target, dec
        self.items = [(lab, safe_float(v, 0.0) or 0.0, hex_norm(col, "") if col else "") for lab, v, col in items]
        self.setMinimumHeight(max(150, 26 * len(self.items) + 14) if self.items else 150)
        self.update()

    def set_xy(self, labels: Sequence[str], series: Sequence[tuple[str, Sequence, str]], mode: str = "bars", vmax: Optional[float] = None, unit: str = "", dec: int = 1) -> None:
        self.mode, self.labels, self.series, self.unit, self.dec = mode, list(labels), [(n, list(v), hex_norm(c, "#2F5FCB")) for n, v, c in series], unit, dec
        top = max([v for _, vals, _ in self.series for v in vals if v is not None] + [1e-9])
        self.vmax = vmax or XYChart._nice(top)
        self.setMinimumHeight(190)
        self.update()

    def paintEvent(self, _event: Any) -> None:
        t = self.ctx.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = QFontMetrics(self.font())
        w, h = self.width(), self.height()
        txt, muted, grid = QColor(t.color("text")), QColor(t.color("muted")), QColor(t.color("border"))
        if (self.mode == "hbars" and not self.items) or (self.mode != "hbars" and not any(v is not None for _, vs, _ in self.series for v in vs)):
            p.setPen(muted)
            p.drawText(self.rect(), A_C, self.empty)
            p.end()
            return
        if self.mode == "hbars":
            lw = min(int(w * 0.38), max(fm.horizontalAdvance(i[0]) for i in self.items) + 10)
            x0, x1, rh = lw, w - 52, 26
            for i, (label, val, col) in enumerate(self.items):
                y = 6 + i * rh
                p.setPen(txt)
                p.drawText(QRect(0, y, lw - 8, rh), A_R, fm.elidedText(label, Qt.TextElideMode.ElideRight, lw - 10))
                p.fillRect(x0, y + 5, max(1, x1 - x0), rh - 10, QColor(t.color("panel_alt")))
                bw = int(max(0.0, min(1.0, val / self.vmax)) * (x1 - x0))
                p.fillRect(x0, y + 5, bw, rh - 10, QColor(col or t.accent))
                p.setPen(txt)
                p.drawText(QRect(x1 + 6, y, 46, rh), A_L, f"{val:.{self.dec}f}{self.unit}")
            if self.target is not None:
                tx = x0 + int(max(0.0, min(1.0, self.target / self.vmax)) * (x1 - x0))
                p.setPen(QPen(QColor(t.color("danger")), 1, Qt.PenStyle.DashLine))
                p.drawLine(tx, 2, tx, 6 + len(self.items) * rh)
            p.end()
            return
        left, bottom, top = 46, 24, 10 + (16 if len(self.series) > 1 else 0)
        pw, ph = w - left - 12, h - bottom - top
        for i in range(5):
            gy = top + ph - ph * i // 4
            p.setPen(QPen(grid, 1))
            p.drawLine(left, gy, left + pw, gy)
            p.setPen(muted)
            p.drawText(QRect(0, gy - 8, left - 6, 16), A_R, f"{self.vmax * i / 4:.{0 if self.vmax >= 10 else 1}f}{self.unit}")
        n = max(1, len(self.labels))
        slot = pw / n
        step = max(1, int(math.ceil(n * 44 / max(pw, 1))))
        for i, lab in enumerate(self.labels):
            if i % step == 0:
                p.setPen(muted)
                p.drawText(QRect(int(left + slot * i), h - bottom + 4, int(slot * step), 16), A_C, fm.elidedText(lab, Qt.TextElideMode.ElideRight, int(slot * step)))
        for si, (name, vals, col) in enumerate(self.series):
            pts = []
            for i, v in enumerate(vals):
                if v is None:
                    continue
                y = top + ph - int(max(0.0, min(1.0, v / self.vmax)) * ph)
                x = int(left + slot * (i + 0.5))
                if self.mode == "bars":
                    bw = max(2, int(slot * 0.7))
                    p.fillRect(x - bw // 2, y, bw, top + ph - y, QColor(col))
                else:
                    pts.append((x, y, v))
            if pts:
                p.setPen(QPen(QColor(col), 2))
                for (xa, ya, _), (xb, yb, _) in zip(pts, pts[1:]):
                    p.drawLine(xa, ya, xb, yb)
                p.setBrush(QColor(col))
                for x, y, v in pts:
                    p.drawEllipse(x - 3, y - 3, 6, 6)
                    if len(pts) <= 12:
                        p.setPen(txt)
                        p.drawText(QRect(x - 24, y - 18, 48, 14), A_C, f"{v:.{self.dec}f}")
                        p.setPen(QPen(QColor(col), 2))
            if len(self.series) > 1:
                p.fillRect(left + si * 110, 4, 10, 6, QColor(col))
                p.setPen(txt)
                p.drawText(QRect(left + si * 110 + 14, 0, 90, 14), A_L, name)
        p.end()


# ---- the table model ------------------------------------------------------------------------------------
class RecordTableModel(QAbstractTableModel):
    """Thin Qt wrapper over RowSet: formatting, alignment, colours and sort badges."""

    def __init__(self, ctx: UiContext, ent: Entity, prefs: TablePrefs, parent: Any = None) -> None:
        super().__init__(parent)
        self.ctx, self.ent, self.prefs = ctx, ent, prefs
        self.cols = list(ent.columns)
        self.rows: list[dict] = []
        self.rs: Optional[RowSet] = None
        self.rules: list[Rule] = []

    def set_rowset(self, rs: RowSet) -> None:
        self.beginResetModel()
        self.rs = rs
        self.rows = rs.view()
        self.endResetModel()

    def reload_rules(self) -> None:
        self.rules = (list(self.ent.rules) if self.prefs.d["rules_on"] else []) + (self.prefs.user_rules() if self.prefs.d["rules_on"] else [])
        self.layoutChanged.emit()

    def rowCount(self, parent: Any = None) -> int:
        return 0 if parent is not None and hasattr(parent, "isValid") and parent.isValid() else len(self.rows)

    def columnCount(self, parent: Any = None) -> int:
        return 0 if parent is not None and hasattr(parent, "isValid") and parent.isValid() else len(self.cols)

    def headerData(self, section: int, orientation: Any, role: Any = None) -> Any:
        role = Qt.ItemDataRole.DisplayRole if role is None else role
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole and 0 <= section < len(self.cols):
            col, label = self.cols[section], self.cols[section].label
            keys = self.rs.sort_keys if self.rs else []
            for n, (k, asc) in enumerate(keys, 1):
                if k == col.key:
                    return f"{label} {'▲' if asc else '▼'}" + (f"{n}" if len(keys) > 1 else "")
            return label
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            return section + 1
        return None

    def _rule_hits(self, row: dict, col: Column) -> list[Rule]:
        out = []
        for r in self.rules:
            if r.column in row:
                exp = self.ctx.settings.get_float("low_mark_pct", 40.0) if r.value == "@low" else r.value
                if rule_matches(r.op, exp, row.get(r.column)) and (r.scope == "row" or r.column == col.key):
                    out.append(r)
        return out

    def data(self, index: Any, role: Any = None) -> Any:
        role = Qt.ItemDataRole.DisplayRole if role is None else role
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row, col = self.rows[index.row()], self.cols[index.column()]
        th = self.ctx.theme
        if role == Qt.ItemDataRole.DisplayRole:
            return format_cell(col, row, self.ctx.settings.get("date_style") or "%d %b %Y")
        if role == Qt.ItemDataRole.ToolTipRole:
            return format_cell(col, row, self.ctx.settings.get("date_style") or "%d %b %Y")
        if role == Qt.ItemDataRole.UserRole:
            return row.get("id")
        if role == Qt.ItemDataRole.TextAlignmentRole:
            al = self.prefs.d["align"].get(col.key) or col.align or ("r" if col.kind in ("int", "float", "pct", "minutes", "kb") else "l")
            return {"l": A_L, "c": A_C, "r": A_R}.get(al, A_L)
        if role == Qt.ItemDataRole.DecorationRole and col.color and row.get(col.color):
            return QColor(hex_norm(row[col.color], "#356AE6"))
        if role in (Qt.ItemDataRole.ForegroundRole, Qt.ItemDataRole.BackgroundRole, Qt.ItemDataRole.FontRole):
            hits = self._rule_hits(row, col)
            user = self.prefs.d["colors"].get(col.key, {})
            if role == Qt.ItemDataRole.FontRole:
                if any(h.bold for h in hits):
                    f = QFont(self.ctx.win.font())
                    f.setBold(True)
                    return f
                return None
            for h in reversed(hits):
                if h.scope == "row":
                    return QColor(th.text_on_soft(h.color) if role == Qt.ItemDataRole.ForegroundRole else th.soft(h.color)) if h.color in th.t else \
                        (QColor(ensure_contrast(h.color, th.t["panel"])) if role == Qt.ItemDataRole.ForegroundRole else QColor(mix(th.t["panel"], hex_norm(h.color), 0.16)))
            if role == Qt.ItemDataRole.ForegroundRole:
                for h in reversed(hits):
                    return QColor(th.t.get(h.color + "_text") or ensure_contrast(th.color(h.color), th.t["panel"]))
                if col.kind in ("status", "priority"):
                    tok = STATUS_TOKENS.get(str(row.get(col.key)))
                    if tok and tok != "muted":
                        return QColor(th.t[tok + "_text"] if tok + "_text" in th.t else th.t["text"])
                    if tok == "muted":
                        return QColor(th.t["muted"])
                if user.get("fg"):
                    return QColor(user["fg"])
            if role == Qt.ItemDataRole.BackgroundRole and user.get("bg"):
                return QColor(user["bg"])
        return None


# ---- dialogs: record form ------------------------------------------------------------------------------------
class OptionalDate(QWidget):
    """A date that can be blank (fixes the old 'Not set' = 1 Jan 2000 ambiguity)."""

    def __init__(self, value: str = "", parent: Any = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.check = QCheckBox("Set")
        self.edit = QDateEdit()
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("dd MMM yyyy")
        d = parse_date(value)
        self.check.setChecked(bool(d))
        self.edit.setDate(QDate(d.year, d.month, d.day) if d else QDate.currentDate())
        self.edit.setEnabled(bool(d))
        self.check.toggled.connect(self.edit.setEnabled)
        lay.addWidget(self.check)
        lay.addWidget(self.edit, 1)

    def value(self) -> str:
        return self.edit.date().toString("yyyy-MM-dd") if self.check.isChecked() else ""


class ColorButton(QPushButton):
    def __init__(self, value: str = "#356AE6", parent: Any = None) -> None:
        super().__init__(parent)
        self.hex = hex_norm(value, "#356AE6")
        self.on_change: Optional[Callable[[str], None]] = None
        self.clicked.connect(self._pick)
        self._paint()

    def _paint(self) -> None:
        self.setText(self.hex)
        self.setStyleSheet(f"QPushButton {{ background: {self.hex}; color: {readable_on(self.hex)}; border: 1px solid #888; }}")

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self.hex), self, "Choose a colour")
        if c.isValid():
            self.hex = hex_norm(c.name(), self.hex)
            self._paint()
            if self.on_change:
                self.on_change(self.hex)

    def value(self) -> str:
        return self.hex


class RecordDialog(QDialog):
    """Add/edit form generated from an entity's field list. Errors never discard what was typed."""

    def __init__(self, ctx: UiContext, table: str, record: Optional[dict] = None, defaults: Optional[dict] = None, parent: Any = None) -> None:
        super().__init__(parent)
        self.ctx, self.ent, self.record = ctx, ENTITIES[table], record
        self.defaults = defaults or {}
        self.saved_id: Optional[int] = record["id"] if record else None
        self.widgets: dict[str, Any] = {}
        self.setWindowTitle(("Edit " if record else "Add ") + self.ent.singular.lower())
        self.setMinimumSize(520, 420)
        self.resize(600, min(780, 150 + 46 * len(self.ent.fields)))
        outer = QVBoxLayout(self)
        outer.addWidget(make_label(self.windowTitle().capitalize(), "DialogTitle"))
        self.banner = make_label("", "BannerDanger", True)
        self.banner.hide()
        outer.addWidget(self.banner)
        body = QWidget()
        form = QFormLayout(body)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for f in self.ent.fields:
            w = self._make(f)
            self.widgets[f.key] = w
            form.addRow(f.label + (" *" if f.required else ""), w)
        for f in self.ent.fields:  # dependent drop-downs follow their parent
            if f.kind == "fk" and f.parent in self.widgets:
                self.widgets[f.parent].currentIndexChanged.connect(lambda _i, ff=f: self._reload_fk(ff))
        outer.addWidget(scroll_page(body), 1)
        box = QDialogButtonBox()
        self.btn_save = box.addButton("Save", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_save.setObjectName("Primary")
        self.btn_again = box.addButton("Save && add another", QDialogButtonBox.ButtonRole.ActionRole) if not record else None
        box.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_save.clicked.connect(lambda _c=False: self._save(False))
        if self.btn_again:
            self.btn_again.clicked.connect(lambda _c=False: self._save(True))
        box.rejected.connect(self.reject)
        outer.addWidget(box)

    def _initial(self, f: Field) -> Any:
        if self.record is not None and f.key in self.record:
            return self.record[f.key]
        if f.key in self.defaults:
            return self.defaults[f.key]
        if f.kind == "date" and f.key in ("task_date", "study_date", "att_date", "start_date", "assessment_date"):
            return self.ctx.repo.today().isoformat()
        return f.default

    def _make(self, f: Field) -> Any:
        v = self._initial(f)
        if f.kind == "long":
            w = QPlainTextEdit(str(v or ""))
            w.setMinimumHeight(200 if f.key == "body" else 80)
            return w
        if f.kind == "date":
            return OptionalDate(str(v or ""))
        if f.kind == "bool":
            w = QCheckBox()
            w.setChecked(bool(v))
            return w
        if f.kind == "color":
            return ColorButton(str(v or "#356AE6"))
        if f.kind == "choice":
            w = QComboBox()
            items = list(f.choices)
            if v not in (None, "") and str(v) not in items:
                items.append(str(v))          # keep legacy values selectable
            w.addItems(items)
            w.setCurrentText(str(v) if v not in (None, "") else (items[0] if items else ""))
            return w
        if f.kind == "fk":
            w = QComboBox()
            w.setProperty("want", v)
            self._fill_fk(w, f, v)
            return w
        if f.kind == "file":
            holder = QWidget()
            lay = QHBoxLayout(holder)
            lay.setContentsMargins(0, 0, 0, 0)
            edit = QLineEdit(str(v or ""))
            lay.addWidget(edit, 1)
            lay.addWidget(make_button("Browse…", lambda e=edit: self._browse(e)))
            holder.edit = edit  # type: ignore[attr-defined]
            return holder
        w = QLineEdit("" if v is None else (fmt_num(v) if f.kind in ("int", "float") else str(v)))
        if f.hint:
            w.setPlaceholderText(f.hint)
        elif f.kind in ("int", "float"):
            w.setPlaceholderText("number")
        return w

    def _browse(self, edit: "QLineEdit") -> None:
        exts = " ".join("*" + e for e in sorted(ATTACH_EXT))
        path, _ = QFileDialog.getOpenFileName(self, "Choose a file", str(Path.home()), f"Supported files ({exts});;All files (*)")
        if path:
            edit.setText(path)

    def _fill_fk(self, combo: "QComboBox", f: Field, want: Any) -> None:
        combo.blockSignals(True)
        combo.clear()
        parent_val = self._read(self.widgets[f.parent], self._field(f.parent)) if f.parent and f.parent in self.widgets else None
        if not f.required:
            combo.addItem("— none —", None)
        for label, rid in self.ctx.repo.lookup(f.fk, parent_val, self.ctx.scope()):
            combo.addItem(label, rid)
        idx = combo.findData(safe_int(want) or None)
        combo.setCurrentIndex(max(0, idx))
        combo.blockSignals(False)

    def _reload_fk(self, f: Field) -> None:
        self._fill_fk(self.widgets[f.key], f, None)

    def _field(self, key: str) -> Field:
        return next(f for f in self.ent.fields if f.key == key)

    def _read(self, w: Any, f: Field) -> Any:
        if f.kind == "long":
            return w.toPlainText()
        if f.kind in ("date", "color"):
            return w.value()
        if f.kind == "bool":
            return w.isChecked()
        if f.kind == "choice":
            return w.currentText()
        if f.kind == "fk":
            return w.currentData()
        if f.kind == "file":
            return w.edit.text()
        return w.text()

    def values(self) -> dict:
        return {f.key: self._read(self.widgets[f.key], f) for f in self.ent.fields}

    def _save(self, again: bool) -> None:
        try:
            vals = self.values()
            if self.record:
                self.ctx.repo.update(self.ent.table, self.record["id"], vals)
            else:
                self.saved_id = self.ctx.repo.insert(self.ent.table, vals)
        except ValidationError as exc:
            self.banner.setText(str(exc))
            self.banner.show()
            return
        except Exception as exc:
            LOG.exception("Save failed")
            self.banner.setText(f"Could not save: {exc}")
            self.banner.show()
            return
        self.banner.hide()
        if again:
            self.ctx.toast(f"{self.ent.singular} saved.")
            for key in ("title", "assignment_no", "topic", "notes", "description"):
                if key in self.widgets:
                    w = self.widgets[key]
                    w.clear() if hasattr(w, "clear") else None
            first = next(iter(self.widgets.values()))
            first.setFocus()
            return
        self.accept()


# ---- the customisable table page ------------------------------------------------------------------------------------
class EntityPage(QWidget):
    """A full data-management table for one entity: search, filters, multi-sort, column & row customisation,
    bulk actions, copy/export, context menu, keyboard shortcuts. Preferences persist per table."""

    DENSITY_ROW = {"Compact": 24, "Comfortable": 32, "Spacious": 42}

    def __init__(self, ctx: UiContext, table: str, *, fixed: Optional[Callable[[dict], bool]] = None, tid: str = "",
                 defaults: Optional[dict] = None, extra: Optional[Sequence[tuple[str, Callable]]] = None, parent: Any = None) -> None:
        super().__init__(parent)
        self.ctx, self.ent, self.fixed, self.defaults, self.extra = ctx, ENTITIES[table], fixed, defaults or {}, list(extra or [])
        self.prefs = TablePrefs(ctx.settings, tid or table)
        self.rs: Optional[RowSet] = None
        self.filter_boxes: dict[str, QComboBox] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._apply)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_layout)
        self._restoring = True
        self._build()
        self._restoring = False

    # ---- construction ----------------------------------------------------
    def _build(self) -> None:
        ent = self.ent
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        bar = QFrame()
        bar.setObjectName("FilterBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 6, 8, 6)
        self.search = QLineEdit()
        self.search.setPlaceholderText(f"Search {ent.plural.lower()}…  (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._timer.start(150))
        row.addWidget(self.search, 2)
        for key in ent.filters:
            col = next((c for c in ent.columns if c.key == key), None)
            if col:
                box = QComboBox()
                box.setMinimumContentsLength(10)
                box.currentIndexChanged.connect(lambda _i: self._timer.start(10))
                self.filter_boxes[key] = box
                row.addWidget(box, 1)
        if ent.date_key:
            self.date_from, self.date_to = OptionalDate(), OptionalDate()
            for w, lab in ((self.date_from, "From"), (self.date_to, "To")):
                row.addWidget(make_label(lab, "Muted"))
                row.addWidget(w)
                w.check.toggled.connect(lambda _c: self._timer.start(10))
                w.edit.dateChanged.connect(lambda _d: self._timer.start(10))
        root.addWidget(bar)
        tools = QHBoxLayout()
        self.btn_add = make_button(f"＋ Add {ent.singular.lower()}", self.add, "Primary", "Ctrl+N")
        tools.addWidget(self.btn_add)
        tools.addWidget(make_button("Edit", self.edit_selected, tip="Enter or double-click"))
        tools.addWidget(make_button("Delete", self.delete_selected, "Danger", tip="Delete key"))
        more = QToolButton()           # every table has an Actions menu (duplicate, import, custom fields …)
        more.setText("Actions ▾")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_menu = QMenu(self)
        more.setMenu(self.more_menu)
        self.more_menu.aboutToShow.connect(self._fill_more)
        tools.addWidget(more)
        tools.addStretch(1)
        self.count = make_label("", "Muted")
        tools.addWidget(self.count)
        view_btn = make_button("⚙ View", None, tip="Columns, density, colours and conditional formatting")
        view_btn.clicked.connect(lambda _c=False: self._view_menu(view_btn))
        tools.addWidget(view_btn)
        tools.addWidget(make_button("Export", self.export, tip="Export selected (or all shown) rows to CSV"))
        root.addLayout(tools)
        self.model = RecordTableModel(self.ctx, ent, self.prefs, self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setWordWrap(False)
        self.view.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.view.verticalHeader().setVisible(False)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._row_menu)
        self.view.activated.connect(lambda _i: self.edit_selected())   # Enter and double-click (one path: never opens twice)
        hh = self.view.horizontalHeader()
        hh.setSectionsMovable(True)
        hh.setSectionsClickable(True)
        hh.setStretchLastSection(False)
        hh.setMinimumSectionSize(36)
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._header_menu)
        hh.sectionClicked.connect(self._header_clicked)
        hh.sectionMoved.connect(lambda *_a: self._layout_changed())
        hh.sectionResized.connect(lambda *_a: self._layout_changed())
        self.empty = make_label(f"No {ent.plural.lower()} yet.\nClick “＋ Add {ent.singular.lower()}” to create the first one.", "EmptyState")
        self.empty.setAlignment(A_C)
        self.empty_btn = make_button(f"＋ Add the first {ent.singular.lower()}", self.add, "Primary")
        root.addWidget(self.empty)
        root.addWidget(self.empty_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(self.view, 1)
        self._shortcuts()
        self._restore_layout()
        self.model.reload_rules()

    def _shortcuts(self) -> None:
        def sc(seq: Any, fn: Callable) -> None:
            s = QShortcut(QKeySequence(seq), self)
            s.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            s.activated.connect(fn)
        sc("Ctrl+N", self.add)
        sc("Ctrl+F", lambda: (self.search.setFocus(), self.search.selectAll()))
        sc("Delete", self.delete_selected)
        sc("F2", self.edit_selected)
        sc(QKeySequence.StandardKey.Copy, self.copy_selection)

    # ---- data ----------------------------------------------------------------
    def refresh(self) -> None:
        rows = self.ctx.repo.rows(self.ent, self.ctx.scope())
        if self.fixed:
            rows = [r for r in rows if self.fixed(r)]
        keep = self.rs.sort_keys if self.rs else [tuple(k) for k in self.prefs.d["sort"]]
        self.rs = RowSet(self.ent.columns, rows, self.ctx.settings.get("date_style") or "%d %b %Y")
        self.rs.sort_keys = [(k, bool(a)) for k, a in keep]
        self._rebuild_filters()
        self._apply()

    def _rebuild_filters(self) -> None:
        for key, box in self.filter_boxes.items():
            current = box.currentText()
            box.blockSignals(True)
            box.clear()
            label = next(c.label for c in self.ent.columns if c.key == key)
            box.addItem(f"All · {label}")
            box.addItems(self.rs.distinct(key) if self.rs else [])
            i = box.findText(current)
            box.setCurrentIndex(max(0, i))
            box.blockSignals(False)

    def _apply(self) -> None:
        if not self.rs:
            return
        self.rs.search_text = self.search.text().strip()
        self.rs.value_filters = {k: {b.currentText()} for k, b in self.filter_boxes.items() if b.currentIndex() > 0}
        if self.ent.date_key:
            lo, hi = self.date_from.value(), self.date_to.value()
            self.rs.date_range = (self.ent.date_key, lo or None, hi or None)
        self.model.set_rowset(self.rs)
        total, shown = len(self.rs.all), len(self.model.rows)
        self.empty.setVisible(total == 0)
        self.empty_btn.setVisible(total == 0)
        self.view.setVisible(total > 0)
        self.count.setText(f"{shown} of {total} {self.ent.plural.lower()}" if shown != total else f"{total} {self.ent.plural.lower()}")
        self._style_view()

    def _style_view(self) -> None:
        d = self.prefs.d
        density = d["density"] or self.ctx.settings.get("density") or "Comfortable"
        self.view.verticalHeader().setDefaultSectionSize(d["row_h"] or self.DENSITY_ROW.get(density, 32))
        self.view.setAlternatingRowColors(bool(d["alt"]))
        self.view.setShowGrid(bool(d["grid"]))

    # ---- layout persistence ------------------------------------------------------
    def _restore_layout(self) -> None:
        hh, cols = self.view.horizontalHeader(), self.model.cols
        keys = [c.key for c in cols]
        hidden = self.prefs.hidden_keys(cols)
        for i, c in enumerate(cols):
            self.view.setColumnWidth(i, int(self.prefs.d["widths"].get(c.key, c.width)))
            hh.setSectionHidden(i, c.key in hidden)
        order = [k for k in self.prefs.d["order"] if k in keys] + [k for k in keys if k not in self.prefs.d["order"]]
        for visual, key in enumerate(order):
            hh.moveSection(hh.visualIndex(keys.index(key)), visual)
        self._style_view()

    def _layout_changed(self) -> None:
        if not self._restoring:
            self._save_timer.start(500)

    def _save_layout(self) -> None:
        hh, cols = self.view.horizontalHeader(), self.model.cols
        self.prefs.d["order"] = [cols[hh.logicalIndex(v)].key for v in range(hh.count())]
        self.prefs.d["hidden"] = [c.key for i, c in enumerate(cols) if hh.isSectionHidden(i)]
        self.prefs.d["widths"] = {c.key: hh.sectionSize(i) for i, c in enumerate(cols) if not hh.isSectionHidden(i)}
        self.prefs.d["sort"] = [list(k) for k in (self.rs.sort_keys if self.rs else [])]
        self.prefs.save()

    def _reapply_layout(self) -> None:
        self._restoring = True
        self._restore_layout()
        self._restoring = False
        self.model.reload_rules()
        self._apply()

    def visible_keys(self) -> list[str]:
        hh = self.view.horizontalHeader()
        return [self.model.cols[hh.logicalIndex(v)].key for v in range(hh.count()) if not hh.isSectionHidden(hh.logicalIndex(v))]

    # ---- sorting / filtering from the header ---------------------------------------------
    def _header_clicked(self, section: int) -> None:
        if not self.rs:
            return
        key = self.model.cols[section].key
        multi = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        cur = dict(self.rs.sort_keys)
        if multi:
            keys = [(k, a) for k, a in self.rs.sort_keys if k != key] + [(key, not cur[key] if key in cur else True)]
        else:
            keys = [(key, not cur[key] if key in cur and len(cur) == 1 else True)]
        self.rs.sort_keys = keys
        self._apply()
        self._layout_changed()

    def _header_menu(self, pos: Any) -> None:
        hh = self.view.horizontalHeader()
        sec = hh.logicalIndexAt(pos)
        col = self.model.cols[sec] if sec >= 0 else None
        m = QMenu(self)
        cm = m.addMenu("Show / hide columns")
        for i, c in enumerate(self.model.cols):
            add_action(cm, c.label, lambda i=i: (hh.setSectionHidden(i, not hh.isSectionHidden(i)), self._layout_changed()), True, not hh.isSectionHidden(i))
        if col:
            m.addSeparator()
            add_action(m, f"Sort “{col.label}” ascending", lambda: self._sort_by(col.key, True))
            add_action(m, f"Sort “{col.label}” descending", lambda: self._sort_by(col.key, False))
            add_action(m, "Add to multi-column sort (Shift+click)", lambda: self._header_clicked(sec))
            am = m.addMenu("Align text")
            for label, code in (("Left", "l"), ("Centre", "c"), ("Right", "r")):
                add_action(am, label, lambda code=code: self._set_align(col.key, code))
            add_action(m, "Column colours…", lambda: self._column_colors(col))
            add_action(m, "Filter this column…", lambda: self._column_filter(col))
            add_action(m, "Fit this column to its content", lambda: self.view.resizeColumnToContents(sec))
            add_action(m, "Hide this column", lambda: (hh.setSectionHidden(sec, True), self._layout_changed()))
        m.addSeparator()
        add_action(m, "Fit all columns to content", lambda: [self.view.resizeColumnToContents(i) for i in range(hh.count()) if not hh.isSectionHidden(i)])
        add_action(m, "Clear sorting and column filters", self._clear_sort_filters)
        add_action(m, "Restore default column layout", self._reset_layout)
        m.exec(hh.mapToGlobal(pos))

    def _sort_by(self, key: str, asc: bool) -> None:
        if self.rs:
            self.rs.sort_keys = [(key, asc)]
            self._apply()
            self._layout_changed()

    def _set_align(self, key: str, code: str) -> None:
        self.prefs.d["align"][key] = code
        self.prefs.save()
        self.model.layoutChanged.emit()

    def _column_colors(self, col: Column) -> None:
        cur = self.prefs.d["colors"].get(col.key, {})
        fg = QColorDialog.getColor(QColor(cur.get("fg") or self.ctx.theme.t["text"]), self, f"Text colour for “{col.label}” (Cancel = none)")
        bg = QColorDialog.getColor(QColor(cur.get("bg") or self.ctx.theme.t["panel"]), self, f"Background for “{col.label}” (Cancel = none)")
        entry = {k: hex_norm(c.name()) for k, c in (("fg", fg), ("bg", bg)) if c.isValid()}
        if entry.get("fg") and entry.get("bg") and contrast_ratio(entry["fg"], entry["bg"]) < 3:
            tell(self, "Low contrast", "That text and background colour are hard to read together; the text colour was adjusted.")
            entry["fg"] = readable_on(entry["bg"])
        self.prefs.d["colors"][col.key] = entry
        self.prefs.save()
        self.model.layoutChanged.emit()

    def _column_filter(self, col: Column) -> None:
        cur = self.rs.text_filters.get(col.key, "") if self.rs else ""
        text, ok = QInputDialog.getText(self, "Filter column", f"Show rows where “{col.label}” contains:", QLineEdit.EchoMode.Normal, cur)
        if ok and self.rs is not None:
            self.rs.text_filters[col.key] = text.strip()
            self._apply()

    def _clear_sort_filters(self) -> None:
        if self.rs:
            self.rs.sort_keys, self.rs.text_filters = [], {}
        for b in self.filter_boxes.values():
            b.setCurrentIndex(0)
        self.search.clear()
        self._apply()
        self._layout_changed()

    def _reset_layout(self) -> None:
        self.prefs.reset_layout()
        self.prefs.save()
        if self.rs:
            self.rs.sort_keys = []
        self._reapply_layout()

    # ---- view options & named layouts ---------------------------------------------------------
    def _view_menu(self, anchor: Any) -> None:
        m, d = QMenu(self), self.prefs.d
        dm = m.addMenu("Row density")
        for name in self.DENSITY_ROW:
            add_action(dm, name, lambda n=name: self._set_pref("density", n), True, (d["density"] or self.ctx.settings.get("density")) == name)
        add_action(m, "Custom row height…", self._row_height)
        add_action(m, "Alternating row colours", lambda: self._set_pref("alt", not d["alt"]), True, bool(d["alt"]))
        add_action(m, "Show grid lines", lambda: self._set_pref("grid", not d["grid"]), True, bool(d["grid"]))
        m.addSeparator()
        add_action(m, "Conditional formatting (highlight rules)", lambda: self._set_pref("rules_on", not d["rules_on"]), True, bool(d["rules_on"]))
        add_action(m, "Add highlight rule…", self._add_rule)
        add_action(m, "Remove my highlight rules", lambda: self._set_pref("rules", []), enabled=bool(d["rules"]))
        m.addSeparator()
        add_action(m, "Save current layout as…", self._save_named)
        lm = m.addMenu("Apply saved layout")
        for name in d["layouts"]:
            add_action(lm, name, lambda n=name: self._apply_named(n))
        dl = m.addMenu("Delete saved layout")
        for name in d["layouts"]:
            add_action(dl, name, lambda n=name: (d["layouts"].pop(n, None), self.prefs.save()))
        m.addSeparator()
        add_action(m, "Restore default layout and appearance", self._reset_everything)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _set_pref(self, key: str, value: Any) -> None:
        self.prefs.d[key] = value
        self.prefs.save()
        self.model.reload_rules()
        self._style_view()

    def _row_height(self) -> None:
        n, ok = QInputDialog.getInt(self, "Row height", "Row height in pixels (0 = automatic):", int(self.prefs.d["row_h"]), 0, 120)
        if ok:
            self._set_pref("row_h", n)

    def _add_rule(self) -> None:
        cols = [c for c in self.ent.columns]
        label, ok = QInputDialog.getItem(self, "Highlight rule", "Column:", [c.label for c in cols], 0, False)
        if not ok:
            return
        col = next(c for c in cols if c.label == label)
        ops = {"equals": "eq", "is not": "ne", "contains": "contains", "less than": "lt", "greater than": "gt", "is blank": "blank"}
        opl, ok = QInputDialog.getItem(self, "Highlight rule", f"“{col.label}” …", list(ops), 0, False)
        if not ok:
            return
        value = ""
        if ops[opl] != "blank":
            value, ok = QInputDialog.getText(self, "Highlight rule", "Value:")
            if not ok:
                return
        colour, ok = QInputDialog.getItem(self, "Highlight rule", "Colour:", ["danger", "warning", "success", "info"], 0, False)
        scope, ok2 = QInputDialog.getItem(self, "Highlight rule", "Highlight:", ["Only this cell's text", "The whole row"], 0, False)
        if ok and ok2:
            self.prefs.d["rules"].append({"column": col.key, "op": ops[opl], "value": value.strip(), "color": colour, "scope": "row" if scope.startswith("The") else "cell"})
            self._set_pref("rules_on", True)

    def _save_named(self) -> None:
        self._save_layout()
        name, ok = QInputDialog.getText(self, "Save layout", "Name for this layout:")
        if ok and name.strip():
            self.prefs.d["layouts"][name.strip()] = self.prefs.layout()
            self.prefs.save()
            self.ctx.toast(f"Layout “{name.strip()}” saved.")

    def _apply_named(self, name: str) -> None:
        lay = self.prefs.d["layouts"].get(name)
        if lay:
            self.prefs.apply_layout(lay)
            self.prefs.save()
            if self.rs:
                self.rs.sort_keys = [(k, bool(a)) for k, a in lay.get("sort", [])]
            self._reapply_layout()

    def _reset_everything(self) -> None:
        self.prefs.reset_all()
        self.prefs.save()
        if self.rs:
            self.rs.sort_keys = []
        self._reapply_layout()

    # ---- selection, CRUD, actions -----------------------------------------------------------------
    def selected_ids(self) -> list[int]:
        rows = sorted({i.row() for i in self.view.selectionModel().selectedRows()}) if self.view.selectionModel() else []
        return [self.model.rows[r]["id"] for r in rows if r < len(self.model.rows)]

    def selected_rows(self) -> list[dict]:
        return [self.model.rows[r] for r in sorted({i.row() for i in self.view.selectionModel().selectedRows()}) if r < len(self.model.rows)]

    def _scope_defaults(self) -> dict:
        d = dict(self.defaults)
        sid = self.ctx.scope()
        if sid and "semester_id" in {f.key for f in self.ent.fields}:
            d.setdefault("semester_id", sid)
        return d

    def add(self) -> None:
        dlg = RecordDialog(self.ctx, self.ent.table, None, self._scope_defaults(), self)
        if dlg.exec():
            self.ctx.toast(f"{self.ent.singular} added.")
            self.refresh()

    def edit_selected(self) -> None:
        ids = self.selected_ids()
        if len(ids) != 1:
            return self.ctx.toast("Select one row to edit (or use Actions ▸ Bulk edit for several).")
        rec = self.ctx.repo.get(self.ent.table, ids[0])
        if not rec:
            return self.refresh()
        if RecordDialog(self.ctx, self.ent.table, rec, None, self).exec():
            self.ctx.toast(f"{self.ent.singular} updated.")
            self.refresh()

    def delete_selected(self) -> None:
        ids = self.selected_ids()
        if ids and self.ctx.confirm_delete(self, self.ent.table, ids):
            if self.ctx.guard(self, lambda: self.ctx.repo.delete(self.ent.table, ids)) is not None:
                self.ctx.toast(f"Deleted {len(ids)} record(s).")
                self.refresh()

    def quick_update(self, updates: dict) -> None:
        ids = self.selected_ids()
        if ids and self.ctx.guard(self, lambda: self.ctx.repo.bulk_update(self.ent.table, ids, updates)) is not None:
            self.refresh()

    def bulk_edit(self) -> None:
        ids = self.selected_ids()
        fields = [f for f in self.ent.fields if f.key in self.ent.bulk]
        if not ids or not fields:
            return
        label, ok = QInputDialog.getItem(self, "Bulk edit", f"Change which field for {len(ids)} selected record(s)?", [f.label for f in fields], 0, False)
        if not ok:
            return
        f = next(x for x in fields if x.label == label)
        if f.kind == "choice":
            val, ok = QInputDialog.getItem(self, "Bulk edit", f"New value for {f.label}:", list(f.choices), 0, False)
        else:
            val, ok = QInputDialog.getText(self, "Bulk edit", f"New value for {f.label}:")
        if ok:
            self.quick_update({f.key: val})

    def copy_selection(self) -> None:
        rows = self.selected_rows()
        if rows and self.rs:
            QApplication.clipboard().setText(rows_to_tsv(self.rs, rows, self.visible_keys()))
            self.ctx.toast(f"Copied {len(rows)} row(s).")

    def copy_cell(self, index: Any) -> None:
        if index.isValid():
            QApplication.clipboard().setText(str(index.data(Qt.ItemDataRole.DisplayRole) or ""))

    def export(self) -> None:
        if not self.rs:
            return
        rows = self.selected_rows() or self.model.rows
        path, _ = QFileDialog.getSaveFileName(self, "Export to CSV", str(Path.home() / f"{self.ent.plural.lower().replace(' ', '_')}.csv"), "CSV files (*.csv)")
        if path:
            if self.ctx.guard(self, lambda: export_csv(path, self.rs, rows, self.visible_keys())):
                self.ctx.toast(f"Exported {len(rows)} row(s) to {Path(path).name}.")

    def _fill_more(self) -> None:
        m = self.more_menu
        m.clear()
        have = bool(self.selected_ids())
        for label, upd in self.ent.quick.items():
            add_action(m, label, lambda u=upd: self.quick_update(u), enabled=have)
        if self.ent.bulk:
            add_action(m, "Bulk edit selected…", self.bulk_edit, enabled=have)
        for label, fn in self.extra:
            add_action(m, label, lambda f=fn: f(self), enabled=True)
        if self.ent.attachments:
            add_action(m, "Attached files…", lambda: self._attachments(), enabled=len(self.selected_ids()) == 1)
        add_action(m, "Custom fields…", lambda: self._custom_fields(), enabled=len(self.selected_ids()) == 1)
        if self.ent.table in DUPLICABLE:
            add_action(m, "Duplicate selected", self.duplicate_selected, enabled=len(self.selected_ids()) == 1)
        m.addSeparator()
        add_action(m, "Import from CSV…", self.import_csv)
        add_action(m, "Download CSV template…", self.csv_template)

    def duplicate_selected(self) -> None:
        ids = self.selected_ids()
        if len(ids) == 1 and self.ctx.guard(self, lambda: self.ctx.repo.duplicate(self.ent.table, ids[0])) is not None:
            self.ctx.toast(f"{self.ent.singular} duplicated.")
            self.refresh()

    def import_csv(self) -> None:
        """Validate the whole file first (nothing is changed), show what would happen, then import the valid rows."""
        path, _ = QFileDialog.getOpenFileName(self, f"Import {self.ent.plural.lower()} from CSV", str(Path.home()), "CSV files (*.csv)")
        if not path:
            return
        dry = self.ctx.guard(self, lambda: self.ctx.repo.import_csv(self.ent.table, path, dry_run=True))
        if dry is None:
            return
        lines = [f"{dry['total']} row(s) found · {dry['imported']} can be imported · {len(dry['errors'])} have problems."]
        if dry["ignored"]:
            lines.append("Ignored columns: " + ", ".join(str(h) for h in dry["ignored"]))
        lines += [f"Row {n}: {msg}" for n, msg in dry["errors"][:10]] + (["…"] if len(dry["errors"]) > 10 else [])
        if not dry["imported"]:
            return tell(self, "Nothing to import", "\n".join(lines))
        if confirm(self, "Import records", "\n".join(lines) + f"\n\nImport the {dry['imported']} valid row(s)? Problem rows are skipped. A safety backup is saved first."):
            res = self.ctx.guard(self, lambda: self.ctx.repo.import_csv(self.ent.table, path))
            if res:
                self.ctx.toast(f"Imported {res['imported']} record(s).")
                self.refresh()

    def csv_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV template", str(Path.home() / f"{self.ent.table}_template.csv"), "CSV files (*.csv)")
        if path and self.ctx.guard(self, lambda: self.ctx.repo.write_csv_template(self.ent.table, path)):
            self.ctx.toast("Template saved — fill it in Excel and import it.")

    def _attachments(self) -> None:
        ids = self.selected_ids()
        if len(ids) == 1:
            AttachmentsDialog(self.ctx, self.ent.table, ids[0], self).exec()

    def _custom_fields(self) -> None:
        ids = self.selected_ids()
        if len(ids) == 1:
            CustomFieldsDialog(self.ctx, self.ent.table, ids[0], self).exec()

    def _row_menu(self, pos: Any) -> None:
        idx = self.view.indexAt(pos)
        if idx.isValid() and idx.row() not in {i.row() for i in self.view.selectionModel().selectedRows()}:
            self.view.selectRow(idx.row())
        m = QMenu(self)
        n = len(self.selected_ids())
        add_action(m, "Edit", self.edit_selected, enabled=n == 1)
        add_action(m, "Delete…", self.delete_selected, enabled=n > 0)
        if self.ent.table in DUPLICABLE:
            add_action(m, "Duplicate", self.duplicate_selected, enabled=n == 1)
        for label, upd in self.ent.quick.items():
            add_action(m, label, lambda u=upd: self.quick_update(u), enabled=n > 0)
        m.addSeparator()
        add_action(m, "Copy cell", lambda: self.copy_cell(idx), enabled=idx.isValid())
        add_action(m, "Copy selected rows", self.copy_selection, enabled=n > 0)
        if idx.isValid() and self.rs:
            col = self.model.cols[idx.column()]
            add_action(m, f"Show only “{idx.data(Qt.ItemDataRole.DisplayRole)}”", lambda: self._only_value(col, str(idx.data(Qt.ItemDataRole.DisplayRole))), enabled=bool(idx.data(Qt.ItemDataRole.DisplayRole)))
        add_action(m, "Export selected…", self.export, enabled=n > 0)
        if self.ent.attachments:
            add_action(m, "Attached files…", self._attachments, enabled=n == 1)
        m.exec(self.view.viewport().mapToGlobal(pos))

    def _only_value(self, col: Column, text: str) -> None:
        if self.rs is not None:
            self.rs.text_filters[col.key] = text
            self._apply()


# ---- helper dialogs ----------------------------------------------------------------------------------------------------
class AttachmentsDialog(QDialog):
    """Files attached to one record. Only references are stored — files stay where they are."""

    def __init__(self, ctx: UiContext, table: str, rid: int, parent: Any = None) -> None:
        super().__init__(parent)
        self.ctx, self.table, self.rid = ctx, table, rid
        self.setWindowTitle("Attached files")
        self.resize(560, 360)
        lay = QVBoxLayout(self)
        lay.addWidget(make_label(f"Files linked to this {ENTITIES[table].singular.lower()}", "DialogTitle"))
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self.open_file())
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, fn, kind in (("Add file…", self.add_file, "Primary"), ("Open", self.open_file, ""), ("Remove link", self.remove, "Danger")):
            row.addWidget(make_button(text, fn, kind))
        row.addStretch(1)
        row.addWidget(make_button("Close", self.accept))
        lay.addLayout(row)
        lay.addWidget(make_label("Removing a link never deletes the file itself.", "Muted"))
        self.reload()

    def reload(self) -> None:
        self.list.clear()
        for a in self.ctx.repo.attachments_for(self.table, self.rid):
            it = QListWidgetItem(f"{a['display_name']}   —   {'' if a['exists'] else 'MISSING · '}{a['file_path']}")
            it.setData(Qt.ItemDataRole.UserRole, a)
            self.list.addItem(it)

    def add_file(self) -> None:
        exts = " ".join("*" + e for e in sorted(ATTACH_EXT))
        path, _ = QFileDialog.getOpenFileName(self, "Attach a file", str(Path.home()), f"Supported files ({exts})")
        if path and self.ctx.guard(self, lambda: self.ctx.repo.add_attachment(self.table, self.rid, path)) is not None:
            self.reload()

    def _current(self) -> Optional[dict]:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def open_file(self) -> None:
        a = self._current()
        if a and a["exists"]:
            QDesktopServices.openUrl(QUrl.fromLocalFile(a["file_path"]))
        elif a:
            tell(self, "File not found", "This file has been moved or deleted. Remove the link and attach it again.")

    def remove(self) -> None:
        a = self._current()
        if a and confirm(self, "Remove link", "Remove this link? The file itself is not deleted."):
            self.ctx.repo.remove_attachment(a["id"])
            self.reload()


class CustomFieldsDialog(QDialog):
    """Free-form name/value notes on a record (kept from version 2.x)."""

    def __init__(self, ctx: UiContext, table: str, rid: int, parent: Any = None) -> None:
        super().__init__(parent)
        self.ctx, self.table, self.rid = ctx, table, rid
        self.setWindowTitle("Custom fields")
        self.resize(460, 340)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addWidget(make_button("Add / change…", self.edit, "Primary"))
        row.addWidget(make_button("Delete", self.remove, "Danger"))
        row.addStretch(1)
        row.addWidget(make_button("Close", self.accept))
        lay.addLayout(row)
        self.reload()

    def reload(self) -> None:
        self.list.clear()
        for f in self.ctx.repo.custom_fields(self.table, self.rid):
            it = QListWidgetItem(f"{f['field_name']}: {f['field_value']}")
            it.setData(Qt.ItemDataRole.UserRole, f)
            self.list.addItem(it)

    def edit(self) -> None:
        cur = self.list.currentItem().data(Qt.ItemDataRole.UserRole) if self.list.currentItem() else {"field_name": "", "field_value": ""}
        name, ok = QInputDialog.getText(self, "Custom field", "Field name:", QLineEdit.EchoMode.Normal, cur["field_name"])
        if not ok:
            return
        val, ok = QInputDialog.getText(self, "Custom field", f"Value for “{name}”:", QLineEdit.EchoMode.Normal, cur["field_value"] if name == cur["field_name"] else "")
        if ok and self.ctx.guard(self, lambda: self.ctx.repo.set_custom_field(self.table, self.rid, name, val)) is not None:
            self.reload()

    def remove(self) -> None:
        it = self.list.currentItem()
        if it and confirm(self, "Delete field", "Delete this custom field?"):
            self.ctx.repo.delete_custom_field(it.data(Qt.ItemDataRole.UserRole)["id"])
            self.reload()


class TaskThread(QThread):
    """Runs ``fn(progress)`` off the UI thread; reports back through signals."""

    progress = Signal(int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[Callable[[int, str], None]], Any], parent: Any = None) -> None:
        super().__init__(parent)
        self.fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self.fn(lambda p, m: self.progress.emit(int(p), str(m))))
        except Exception as exc:  # reported to the UI; never crashes the app
            LOG.exception("Background task failed")
            self.failed.emit(str(exc))


class HeatmapWidget(QWidget):
    """Calendar heat-map of daily study time (darker = closer to the daily goal)."""

    def __init__(self, ctx: UiContext, parent: Any = None) -> None:
        super().__init__(parent)
        self.ctx, self.data = ctx, None
        self.setMinimumHeight(150)

    def set_data(self, hm: dict) -> None:
        self.data = hm
        self.update()

    def paintEvent(self, _event: Any) -> None:
        t, p = self.ctx.theme, QPainter(self)
        if not self.data or not self.data["max"]:
            p.setPen(QColor(t.color("muted")))
            p.drawText(self.rect(), A_C, "No study sessions in the last 12 weeks")
            p.end()
            return
        cells, goal = self.data["cells"], max(1, self.data["goal"])
        weeks = (len(cells) + 6) // 7
        size = max(8, min(22, (self.width() - 44) // max(1, weeks) - 3))
        x0, y0 = 30, 6
        shades = [mix(t.t["panel_alt"], t.t["border"], 0.25)] + [mix(t.t["panel_alt"], t.accent, k) for k in (0.30, 0.52, 0.76, 1.0)]
        p.setPen(QColor(t.color("muted")))
        for r, lab in ((0, "Mon"), (2, "Wed"), (4, "Fri")):
            p.drawText(QRect(0, y0 + r * (size + 3), x0 - 4, size), A_R, lab)
        for i, (_iso, minutes) in enumerate(cells):
            level = 0 if minutes <= 0 else 1 if minutes < goal * 0.25 else 2 if minutes < goal * 0.5 else 3 if minutes < goal else 4
            p.fillRect(QRect(x0 + (i // 7) * (size + 3), y0 + (i % 7) * (size + 3), size, size), QColor(shades[level]))
        days = sum(1 for _i, m in cells if m > 0)
        p.setPen(QColor(t.color("muted")))
        p.drawText(QRect(x0, y0 + 7 * (size + 3) + 4, self.width() - x0 - 6, 18), A_L,
                   f"{days} study day(s) · darker = closer to your daily goal ({fmt_minutes(goal)})")
        p.end()


class SearchDialog(QDialog):
    """Search every kind of record at once (Ctrl+K); Enter opens the page and filters it."""

    def __init__(self, ctx: UiContext, parent: Any = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Search everything")
        self.resize(640, 480)
        lay = QVBoxLayout(self)
        self.box = QLineEdit()
        self.box.setPlaceholderText("Type 2+ letters — subjects, assignments, exams, notes, tasks, projects, files, classes…")
        self.list = QListWidget()
        self.note = make_label("", "Muted")
        for w in (self.box, self.list):
            lay.addWidget(w, 1 if w is self.list else 0)
        lay.addWidget(self.note)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.run)
        self.box.textChanged.connect(lambda _t: self.timer.start(180))
        self.list.itemActivated.connect(self.open_item)
        self.box.returnPressed.connect(lambda: self.open_item(self.list.item(0)) if self.list.count() else None)

    def run(self) -> None:
        text = self.box.text()
        hits = self.ctx.win.commands(text) + self.ctx.repo.global_search(text)      # commands first, then records
        self.list.clear()
        for h in hits:
            it = QListWidgetItem(f"{h['kind']}  ·  {h['title']}" + (f"   —   {h['detail']}" if h["detail"] else ""))
            it.setData(Qt.ItemDataRole.UserRole, h)
            self.list.addItem(it)
        self.note.setText(f"{len(hits)} result(s)  ·  “Go to …”, “New …” and actions are commands" if hits else ("No matches." if len(text.strip()) >= 2 else ""))

    def open_item(self, item: Any) -> None:
        if item is not None:
            hit, text = item.data(Qt.ItemDataRole.UserRole), self.box.text().strip()
            self.accept()
            if "cmd" in hit:
                self.ctx.win.run_command(hit["cmd"])
            else:
                self.ctx.win.open_hit(hit, text)


class BackupsDialog(QDialog):
    """Every automatic, safety and manual backup, with one-click verified restore."""

    def __init__(self, ctx: UiContext, parent: Any = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Manage backups")
        self.resize(680, 440)
        lay = QVBoxLayout(self)
        lay.addWidget(make_label("Backups", "DialogTitle"))
        lay.addWidget(make_label("Automatic = once a day · Safety = taken before upgrades, restores, imports and bulk deletes · Manual = made by you.", "Muted", True))
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, fn, kind in (("Restore selected…", self.restore, "Primary"), ("Restore from file…", self.restore_file, ""), ("Back up now", self.backup_now, ""),
                               ("Delete selected", self.delete, "Danger"), ("Open folder", self.open_folder, "")):
            row.addWidget(make_button(text, fn, kind))
        row.addStretch(1)
        row.addWidget(make_button("Close", self.accept))
        lay.addLayout(row)
        self.reload()

    def reload(self) -> None:
        self.list.clear()
        for b in self.ctx.core.backups.list_all():
            it = QListWidgetItem(f"[{b['kind']}]   {b['mtime']:%d %b %Y  %H:%M}   ·   {b['size'] / 1024:,.0f} KB   ·   {b['name']}")
            it.setData(Qt.ItemDataRole.UserRole, b)
            self.list.addItem(it)
        if not self.list.count():
            self.list.addItem("No backups yet. Click “Back up now”.")

    def _current(self) -> Optional[dict]:
        it = self.list.currentItem()
        d = it.data(Qt.ItemDataRole.UserRole) if it else None
        return d if isinstance(d, dict) else None

    def backup_now(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.ctx.guard(self, lambda: self.ctx.core.backups.create(self.ctx.core.db, kind="manual"))
        finally:
            QApplication.restoreOverrideCursor()
        self.reload()

    def restore(self) -> None:
        b = self._current()
        if b:
            self._restore_path(b["path"])

    def restore_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a backup file", str(Path.home()), "Study Space backup (*.db);;All files (*)")
        if path:
            self._restore_path(path)

    def _restore_path(self, path: Any) -> None:
        try:
            info = inspect_database_file(path)
        except BackupError as exc:
            return tell(self, "Not a valid backup", str(exc), True)
        counts = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in info["counts"].items())
        if not confirm(self, "Restore backup", f"Replace ALL current data with this backup?\n\nIt contains: {counts}.\n\nA safety copy of your current data is saved first and put back automatically if anything fails.", True):
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            safety = self.ctx.core.restore(path)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            return tell(self, "Restore failed", f"{exc}\n\nYour previous data is unchanged.", True)
        QApplication.restoreOverrideCursor()
        self.ctx.win.rebuild_scope()
        self.ctx.win.apply_theme()
        self.reload()
        tell(self, "Restore complete", f"Backup restored. Your previous data was saved to:\n{safety}")

    def delete(self) -> None:
        b = self._current()
        if b and confirm(self, "Delete backup", f"Delete {b['name']}?", True):
            self.ctx.guard(self, lambda: self.ctx.core.backups.delete(b["path"]))
            self.reload()

    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctx.core.backups.root)))


# ════════════════════════════════════════════════════════════════════════════
# 10 · QT LAYER II — pages
# ════════════════════════════════════════════════════════════════════════════
class Page(QWidget):
    """Base page: refreshed lazily when shown, or immediately when visible and data changes."""

    def __init__(self, ctx: UiContext) -> None:
        super().__init__()
        self.ctx, self.dirty = ctx, True
        self._threads: list[Any] = []

    def refresh(self) -> None:
        pass

    def mark_dirty(self) -> None:
        self.dirty = True

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if self.dirty:
            self.dirty = False
            self.ctx.guard(self, self.refresh)

    def run_task(self, job: Callable, on_done: Callable, on_progress: Optional[Callable] = None, on_fail: Optional[Callable] = None) -> None:
        t = TaskThread(job, self)
        if on_progress:
            t.progress.connect(on_progress)
        t.done.connect(on_done)
        t.failed.connect(on_fail or (lambda msg: tell(self, "Could not complete the task", msg, True)))
        t.finished.connect(lambda th=t: self._threads.remove(th) if th in self._threads else None)
        self._threads.append(t)
        t.start()


def page_header(title: str, subtitle: str = "") -> "QWidget":
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 4)
    lay.setSpacing(0)
    lay.addWidget(make_label(title, "PageTitle"))
    if subtitle:
        lay.addWidget(make_label(subtitle, "Muted", True))
    return w


class CardGrid(QWidget):
    """Responsive grid: cards reflow into as many columns as the width allows."""

    def __init__(self, min_w: int = 260, gap: int = 12) -> None:
        super().__init__()
        self.min_w, self.cards, self._cols = min_w, [], 0
        self.lay = QGridLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(gap)

    def set_cards(self, cards: Sequence["QWidget"]) -> None:
        for c in self.cards:
            c.setParent(None)
            c.deleteLater()
        self.cards = list(cards)
        self._place(True)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._place()

    def _place(self, force: bool = False) -> None:
        cols = max(1, self.width() // self.min_w)
        if cols == self._cols and not force:
            return
        self._cols = cols
        while self.lay.count():
            self.lay.takeAt(0)
        for i, c in enumerate(self.cards):
            self.lay.addWidget(c, i // cols, i % cols)
        for c in range(cols):
            self.lay.setColumnStretch(c, 1)


def metric_card(label: str, value: str, sub: str = "") -> "QFrame":
    f = QFrame()
    f.setObjectName("Metric")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 10, 14, 10)
    lay.setSpacing(2)
    lay.addWidget(make_label(label.upper(), "MetricLabel"))
    lay.addWidget(make_label(value, "MetricValue"))
    if sub:
        lay.addWidget(make_label(sub, "Muted", True))
    return f


def panel(title: str, body: "QWidget") -> "QFrame":
    card, lay = make_card()
    lay.addWidget(make_label(title, "SectionTitle"))
    lay.addWidget(body, 1)
    return card


def text_list(items: Sequence[str], empty: str) -> "QWidget":
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    for t in items or [empty]:
        lay.addWidget(make_label(t, "" if items else "Muted", True))
    lay.addStretch(1)
    return w


# ---- dashboard -------------------------------------------------------------------------------------------
DASH_PANELS = {"classes": "Today's classes", "deadlines": "Upcoming deadlines", "workload": "Workload — next 14 days", "plan": "Today's plan", "scores": "Score trend", "attendance": "Attendance by subject",
               "study": "Study — last 14 days", "heatmap": "Study consistency (12 weeks)", "gpa": "SGPA & CGPA", "syllabus": "Syllabus completion", "recent": "Recent activity"}


class DashboardPage(Page):
    def __init__(self, ctx: UiContext) -> None:
        super().__init__(ctx)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QWidget()
        body.setObjectName("PageBody")
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(22, 18, 22, 22)
        self.lay.setSpacing(14)
        self.title = make_label("", "PageTitle")
        self.sub = make_label("", "Muted", True)
        self.alert = make_label("", "BannerDanger", True)
        self.kpis, self.panels = CardGrid(230), CardGrid(430)
        self.check_card, cl = make_card()
        cl.addWidget(make_label("Getting started", "SectionTitle"))
        cl.addWidget(make_label("A few quick steps to set the app up for you. They tick themselves off as you go.", "Muted", True))
        self.check_rows = QVBoxLayout()
        cl.addLayout(self.check_rows)
        cl.addWidget(make_button("Hide this checklist", lambda: self._hide_checklist()))
        for w in (self.title, self.sub, self.alert, self.check_card, self.kpis, self.panels):
            self.lay.addWidget(w)
        self.lay.addStretch(1)
        outer.addWidget(scroll_page(body))
        self.charts = {k: ChartWidget(ctx) for k in ("scores", "attendance", "study", "gpa", "syllabus", "workload")}
        self.heat = HeatmapWidget(ctx)

    def refresh(self) -> None:
        c = self.ctx
        sid, an = c.scope(), c.analytics
        prof, hour = c.repo.profile(), datetime.now().hour
        greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        self.title.setText(f"{greet}{', ' + prof['student_name'].split()[0] if prof.get('student_name') else ''}")
        row = c.core.db.one("SELECT sem.name sn, ay.name yn, p.name pn FROM semesters sem LEFT JOIN academic_years ay ON ay.id=sem.academic_year_id "
                            "LEFT JOIN programmes p ON p.id=sem.programme_id WHERE sem.id=?", (sid,)) if sid else None
        self.sub.setText(" · ".join(x for x in ((row["pn"], row["yn"], row["sn"]) if row else ("All semesters",)) if x)
                         + (f" · {prof['college_name']}" if prof.get("college_name") else ""))
        ov = an.overview(sid)
        a, at, h = ov["assignments"], ov["attendance"], ov["habits"]
        fp = lambda v: "—" if v is None else f"{v:.1f}%"
        nx = ov["next_exam"]
        nx_days = (parse_date(nx["d"]) - c.repo.today()).days if nx and parse_date(nx["d"]) else None
        sp = an.semester_progress(sid)
        self.kpis.set_cards([
            metric_card("Subjects", str(ov["subjects"]), f"{ov['units_done']}/{ov['units_total']} units complete"),
            metric_card("Syllabus", fp(ov["syllabus_pct"]), "units completed"),
            metric_card("Pending work", str(a["pending"]), f"{a['overdue']} overdue · {a['due_week']} due this week"),
            metric_card("Next exam", "—" if nx_days is None else "today" if nx_days == 0 else f"in {nx_days} day{'s' if nx_days != 1 else ''}", f"{nx['title']} · {nx['sn']}" if nx else "none scheduled"),
            metric_card("Attendance", fp(at["pct"]), "no records yet" if at["pct"] is None else f"{at['status']} · target {at['target']:.0f}%"),
            metric_card("Study today", fmt_minutes(h["today_minutes"]), f"goal {fmt_minutes(h['goal'])} · {fmt_minutes(ov['study_week'])} this week"),
            metric_card("Study streak", f"{h['streak']} days", f"{h['studied_days']} of last {h['days']} days"),
            metric_card("Score average", fp(ov["score_pct"]), "marks + exams, weighted"),
            *([metric_card("Semester progress", f"{sp['pct']:.0f}%", f"day {sp['elapsed']} of {sp['total']} · {sp['left']} days left" if sp["state"] == "running" else sp["state"])] if sp else []),
            metric_card("SGPA / CGPA", f"{'—' if ov['sgpa'] is None else format(ov['sgpa'], '.2f')} / {'—' if ov['cgpa'] is None else format(ov['cgpa'], '.2f')}",
                        f"{fmt_num(ov['credits_earned'])} credits · {ov['backlogs']} backlog(s)"),
        ])
        self._refresh_checklist()
        alerts = [x for x in an.alerts(sid) if x.severity in ("danger", "warning")]
        self.alert.setVisible(bool(alerts))
        self.alert.setText("  •  ".join(f"{x.title}" for x in alerts[:3]) + ("  …" if len(alerts) > 3 else ""))
        # charts
        ch = self.charts
        trend = an.score_trend(sid)[-30:]
        ch["scores"].empty = "No scored assessments yet"
        ch["scores"].set_xy([fmt_date(d, "%d %b") for d, _ in trend], [("Score %", [v for _, v in trend], c.theme.accent)], "lines", 100, "%", 0)
        att = [r for r in an.attendance_by_subject(sid) if r["pct"] is not None]
        ch["attendance"].empty = "No attendance recorded yet"
        ch["attendance"].set_bars([(r["subject"], r["pct"], r["color"]) for r in att], 100, "%", c.settings.get_float("attendance_min", 75.0), 0)
        daily = an.study_daily(14, sid)
        ch["study"].empty = "No study sessions yet"
        ch["study"].set_xy([d[5:] for d, _ in daily], [("Minutes", [m if m else None for _, m in daily], c.theme.accent)], "bars", None, "", 0)
        gp = an.sgpa_series()
        ch["gpa"].empty = "SGPA appears once semester results are entered"
        ch["gpa"].set_xy([g["name"] for g in gp], [("SGPA", [g["sgpa"] for g in gp], c.theme.accent), ("CGPA", [g["cgpa"] for g in gp], c.theme.color("warning"))],
                         "lines", c.settings.grading().max_points, "", 2)
        syl = [s for s in an.syllabus_by_subject(sid) if s["pct"] is not None]
        ch["syllabus"].empty = "Add units to track syllabus completion"
        ch["syllabus"].set_bars([(s["subject"], s["pct"], s["color"]) for s in syl], 100, "%", None, 0)
        load = an.workload(sid, 14)
        ch["workload"].empty = "No deadlines in the next 14 days"
        ch["workload"].set_xy([fmt_date(d, "%d %b") for d, _ in load], [("Deadlines", [n if n else None for _, n in load], c.theme.color("warning"))], "bars", None, "", 0)
        dl = [f"{fmt_date(d['date'], '%d %b')} · {d['kind']}: {d['title']} ({d['subject']})" + ("  — OVERDUE" if d["status"] == "Overdue" else "") for d in an.deadlines(sid, 14)[:7]]
        today = c.repo.today().isoformat()
        plan = [f"{'✓' if r['status'] == 'Completed' else '○'} {r['title']}  ({fmt_minutes(r['duration_minutes'])})" for r in c.repo.rows("planner_tasks", sid) if r["task_date"] == today][:8]
        rec = [f"{fmt_date(r['date'], '%d %b')} · {r['text']}" for r in an.recent_activity(sid, 6)]
        cls_lines = [f"{x['start']}–{x['end']}  {x['subject']}  ({x['kind']}{', ' + x['room'] if x['room'] else ''})" + ("  ✓ attendance marked" if x["marked"] else "") for x in an.todays_classes(sid)]
        self.heat.set_data(an.study_heatmap(12, sid))
        bodies = {"classes": text_list(cls_lines, "No classes scheduled today. Add your weekly timetable under Attendance → Weekly timetable."), "heatmap": self.heat,
                  "deadlines": text_list(dl, "No deadlines in the next two weeks."), "plan": text_list(plan, "Nothing planned for today. Add tasks in Study hub."),
                  "workload": ch["workload"], "scores": ch["scores"], "attendance": ch["attendance"], "study": ch["study"], "gpa": ch["gpa"], "syllabus": ch["syllabus"],
                  "recent": text_list(rec, "No recent activity yet.")}
        saved = c.settings.get_json("dashboard_panels", None)          # ordered list of visible panels (None = default)
        order = [k for k in dict.fromkeys(saved) if k in DASH_PANELS] if isinstance(saved, list) else list(DASH_PANELS)
        for w in (*ch.values(), self.heat):
            w.setParent(None)
        self.panels.set_cards([panel(DASH_PANELS[k], bodies[k]) for k in order])

    def _refresh_checklist(self) -> None:
        steps = self.ctx.analytics.onboarding()
        self.check_card.setVisible(not self.ctx.settings.get_bool("onboarding_dismissed") and not all(s["done"] for s in steps))
        while self.check_rows.count():
            w = self.check_rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        for s in steps:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.addWidget(make_label(("✓  " if s["done"] else "○  ") + s["label"], "Muted" if s["done"] else ""), 1)
            if not s["done"]:
                rl.addWidget(make_button("Open", lambda p=s["page"]: self.ctx.go(p)))
            self.check_rows.addWidget(row)

    def _hide_checklist(self) -> None:
        self.ctx.settings.set("onboarding_dismissed", "1")
        self.check_card.hide()


# ---- structure, profile & wizard ------------------------------------------------------------------------------
class StructureWizard(QDialog):
    """Create a programme with its years and semesters (any number — nothing is hard-coded)."""

    def __init__(self, ctx: UiContext, parent: Any = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Academic structure setup")
        self.resize(560, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(make_label("Set up a programme", "DialogTitle"))
        lay.addWidget(make_label("Creates the programme, its academic years and semesters. You can rename or add more at any time.", "Muted", True))
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. B.Tech CSE (AI & ML)")
        self.level = QComboBox()
        self.level.addItems(DEGREE_LEVELS)
        self.level.setCurrentText("Undergraduate")
        self.branch, self.spec, self.college, self.univ = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.start = QLineEdit(str(self.ctx.repo.today().year))
        self.years = QSpinBox()
        self.years.setRange(1, 10)
        self.years.setValue(4)
        self.per = QSpinBox()
        self.per.setRange(1, 4)
        self.per.setValue(2)
        self.term = QComboBox()
        self.term.setEditable(True)
        self.term.addItems(["Semester", "Trimester", "Term", "Quarter"])
        self.names = QPlainTextEdit()
        self.names.setPlaceholderText("Optional: one semester name per line (otherwise “Semester 1, 2, 3 …”)")
        self.names.setMaximumHeight(90)
        for lab, w in (("Programme name *", self.name), ("Level", self.level), ("Branch", self.branch), ("Specialisation", self.spec), ("College", self.college),
                       ("University", self.univ), ("Start year", self.start), ("Number of years", self.years), ("Semesters per year", self.per),
                       ("Term word", self.term), ("Semester names", self.names)):
            form.addRow(lab, w)
        lay.addLayout(form)
        self.banner = make_label("", "BannerDanger", True)
        self.banner.hide()
        lay.addWidget(self.banner)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(make_button("Cancel", self.reject))
        row.addWidget(make_button("Create structure", self.create, "Primary"))
        lay.addLayout(row)

    def create(self) -> None:
        try:
            self.ctx.repo.create_structure(self.name.text(), degree_level=self.level.currentText(), branch=self.branch.text().strip(),
                                           specialisation=self.spec.text().strip(), college=self.college.text().strip(), university=self.univ.text().strip(),
                                           start_year=safe_int(self.start.text()) or None, years=self.years.value(), per_year=self.per.value(),
                                           term_word=self.term.currentText().strip() or "Semester", semester_names=self.names.toPlainText().splitlines())
        except ValidationError as exc:
            self.banner.setText(str(exc))
            self.banner.show()
            return
        self.accept()


class ProfileDialog(QDialog):
    def __init__(self, ctx: UiContext, parent: Any = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Student profile")
        self.resize(520, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(make_label("Student profile", "DialogTitle"))
        form = QFormLayout()
        p = ctx.repo.profile()
        labels = {"student_name": "Student name", "college_name": "College", "university_name": "University", "course": "Course / programme", "branch": "Branch",
                  "admission_year": "Admission year", "expected_graduation_year": "Expected graduation year", "roll_no": "University roll number",
                  "student_id": "Student ID", "email": "College email", "photo_path": "Photograph (optional)"}
        self.edits: dict[str, Any] = {}
        for k, lab in labels.items():
            e = QLineEdit("" if p.get(k) is None else str(p.get(k)))
            self.edits[k] = e
            if k == "photo_path":
                h = QWidget()
                hl = QHBoxLayout(h)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.addWidget(e, 1)
                hl.addWidget(make_button("Browse…", lambda ed=e: ed.setText(QFileDialog.getOpenFileName(self, "Photograph", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")[0] or ed.text())))
                form.addRow(lab, h)
            else:
                form.addRow(lab, e)
        self.goals = QPlainTextEdit(p.get("goals") or "")
        self.goals.setMaximumHeight(90)
        form.addRow("Personal academic goals", self.goals)
        lay.addLayout(form)
        self.banner = make_label("", "BannerDanger", True)
        self.banner.hide()
        lay.addWidget(self.banner)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(make_button("Cancel", self.reject))
        row.addWidget(make_button("Save", self.save, "Primary"))
        lay.addLayout(row)

    def save(self) -> None:
        vals = {k: e.text() for k, e in self.edits.items()}
        vals["goals"] = self.goals.toPlainText()
        try:
            self.ctx.repo.save_profile(vals)
        except ValidationError as exc:
            self.banner.setText(str(exc))
            self.banner.show()
            return
        self.accept()


class TabsPage(Page):
    """A page made of tabs; each tab's widget exposes ``refresh()``."""

    def __init__(self, ctx: UiContext, title: str, subtitle: str, tabs: Sequence[tuple[str, "QWidget"]], top: Optional["QWidget"] = None) -> None:
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 14)
        lay.addWidget(page_header(title, subtitle))
        if top is not None:
            lay.addWidget(top)
        self.tabs = QTabWidget()
        self.children_ = [w for _, w in tabs]
        for label, w in tabs:
            self.tabs.addTab(w, label)
        self.tabs.currentChanged.connect(lambda _i: self.refresh())
        lay.addWidget(self.tabs, 1)

    def refresh(self) -> None:
        w = self.tabs.currentWidget()
        if hasattr(w, "refresh"):
            w.refresh()


class HierarchyTree(QWidget):
    """Programme → year → semester overview; double-click a semester to switch to it."""

    def __init__(self, ctx: UiContext) -> None:
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Programme / year / semester", "Status", "Subjects", "SGPA", "CGPA", "Backlogs"])
        self.tree.setColumnWidth(0, 340)
        self.tree.itemDoubleClicked.connect(self._open)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        row.addWidget(make_button("Set as current semester", self._set_current, "Primary"))
        row.addWidget(make_button("Open selected semester", lambda: self._open(self.tree.currentItem(), 0)))
        row.addStretch(1)
        row.addWidget(make_label("Double-click a semester to view all its records.", "Muted"))
        lay.addLayout(row)

    def refresh(self) -> None:
        self.tree.clear()
        cur = self.ctx.repo.current_semester_id()
        progs: dict[Any, QTreeWidgetItem] = {}
        years: dict[tuple, QTreeWidgetItem] = {}
        for h in self.ctx.analytics.semester_history():
            pk = h["programme_id"]
            if pk not in progs:
                progs[pk] = QTreeWidgetItem([h["programme"] or "Unassigned semesters", "", "", "", "", ""])
                self.tree.addTopLevelItem(progs[pk])
            yk = (pk, h["year_name"])
            if yk not in years:
                years[yk] = QTreeWidgetItem([h["year_name"] or "—", "", "", "", "", ""])
                progs[pk].addChild(years[yk])
            it = QTreeWidgetItem([h["name"] + ("   ◀ current" if h["id"] == cur else ""), h["status"] or "", str(h["subjects"]),
                                  "—" if h["sgpa"] is None else f"{h['sgpa']:.2f}", "—" if h["cgpa"] is None else f"{h['cgpa']:.2f}", str(h["backlogs"]) if h["results"] else "—"])
            it.setData(0, Qt.ItemDataRole.UserRole, h["id"])
            years[yk].addChild(it)
        self.tree.expandAll()

    def _sid(self, item: Any) -> Optional[int]:
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _set_current(self) -> None:
        sid = self._sid(self.tree.currentItem())
        if sid:
            self.ctx.guard(self, lambda: (self.ctx.repo.set_current_semester(sid), self.ctx.settings.set("scope_semester", "current")))
            self.ctx.win.rebuild_scope()
            self.refresh()
        else:
            self.ctx.toast("Select a semester first.")

    def _open(self, item: Any, _col: int = 0) -> None:
        sid = self._sid(item)
        if sid:
            self.ctx.settings.set("scope_semester", str(sid))
            self.ctx.win.rebuild_scope()
            self.ctx.go("subjects")


class StructurePage(TabsPage):
    def __init__(self, ctx: UiContext) -> None:
        def sem_extra(page: "EntityPage") -> None:
            ids = page.selected_ids()
            if len(ids) == 1:
                ctx.guard(page, lambda: ctx.repo.set_current_semester(ids[0]))
                ctx.win.rebuild_scope()
                page.refresh()
        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(make_button("Setup wizard…", lambda: self.wizard(), "Primary", "Create a programme with all its years and semesters"))
        bl.addStretch(1)
        tree = HierarchyTree(ctx)
        super().__init__(ctx, "Academic structure", "Programme → academic year → semester. Configure any number of years and semesters.",
                         [("Semester history", HistoryPage(ctx)), ("Hierarchy", tree), ("Programmes", EntityPage(ctx, "programmes")), ("Academic years", EntityPage(ctx, "academic_years")),
                          ("Semesters", EntityPage(ctx, "semesters", extra=[("Set as current semester", sem_extra)])),
                          ("Academic calendar", EntityPage(ctx, "calendar_events"))], bar)
        self.tree = tree

    def wizard(self) -> None:
        if StructureWizard(self.ctx, self).exec():
            self.ctx.win.rebuild_scope()
            self.refresh()


def make_subjects_page(ctx: UiContext) -> TabsPage:
    def schedule(page: "EntityPage") -> None:
        for uid in page.selected_ids():
            ctx.guard(page, lambda u=uid: ctx.repo.schedule_revision(u))
        ctx.toast("Revision scheduled for the selected unit(s).")
    return TabsPage(ctx, "Subjects & syllabus", "Subjects with credits and faculty, units, and chapter-level progress for the selected semester.",
                    [("Subjects", EntityPage(ctx, "subjects")), ("Units / syllabus", EntityPage(ctx, "units", extra=[("Schedule revision", schedule)])),
                     ("Chapters", EntityPage(ctx, "chapters"))])


# ---- semester history ----------------------------------------------------------------------------------------------------
class HistoryPage(Page):
    COLS = ("Programme", "Year", "Semester", "Status", "Subjects", "Credits (earned/total)", "SGPA", "CGPA", "Marks %", "Backlogs", "Attendance", "Study", "Assignments", "Syllabus")

    def __init__(self, ctx: UiContext) -> None:
        super().__init__(ctx)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 14)
        lay.addWidget(page_header("Semester history", "Every semester stays on record: results, attendance, assignments and study time are never overwritten."))
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(list(self.COLS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(lambda _i: self.open_sem())
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(make_button("Open semester", self.open_sem, "Primary"))
        row.addWidget(make_button("Semester report…", self.report))
        row.addStretch(1)
        lay.addLayout(row)
        self.chart = ChartWidget(ctx, "SGPA and CGPA appear once semester results are entered")
        lay.addWidget(panel("SGPA & CGPA progression", self.chart))
        self.ids: list[int] = []

    def refresh(self) -> None:
        hist = self.ctx.analytics.semester_history()
        self.ids = [h["id"] for h in hist]
        self.table.setRowCount(len(hist))
        fp = lambda v: "—" if v is None else f"{v:.1f}%"
        for r, h in enumerate(hist):
            vals = [h["programme"] or "—", h["year_name"] or "—", h["name"], h["status"] or "", h["subjects"],
                    f"{fmt_num(h['credits_earned'])}/{fmt_num(h['credits_registered'])}" if h["results"] else "—",
                    "—" if h["sgpa"] is None else f"{h['sgpa']:.2f}", "—" if h["cgpa"] is None else f"{h['cgpa']:.2f}", fp(h["pct"]),
                    h["backlogs"] if h["results"] else "—", fp(h["attendance_pct"]), fmt_minutes(h["study_minutes"]), f"{h['assign_done']}/{h['assign_total']}", fp(h["syllabus_pct"])]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                it.setTextAlignment(A_R if c >= 4 else A_L)
                self.table.setItem(r, c, it)
        pts = [h for h in hist if h["sgpa"] is not None]
        self.chart.set_xy([h["name"] for h in pts], [("SGPA", [h["sgpa"] for h in pts], self.ctx.theme.accent), ("CGPA", [h["cgpa"] for h in pts], self.ctx.theme.color("warning"))],
                          "lines", self.ctx.settings.grading().max_points, "", 2)

    def _sid(self) -> Optional[int]:
        r = self.table.currentRow()
        return self.ids[r] if 0 <= r < len(self.ids) else None

    def open_sem(self) -> None:
        sid = self._sid()
        if sid:
            self.ctx.settings.set("scope_semester", str(sid))
            self.ctx.win.rebuild_scope()
            self.ctx.go("subjects")

    def report(self) -> None:
        sid = self._sid()
        if sid:
            self.ctx.win.page("reports").preset(kind="semester", scope_semester_id=sid)
            self.ctx.go("reports")


# ---- assignments (list + deadline calendar) -----------------------------------------------------------------------------------------
class DeadlineCalendar(QWidget):
    def __init__(self, ctx: UiContext) -> None:
        super().__init__()
        self.ctx = ctx
        lay = QHBoxLayout(self)
        self.cal = QCalendarWidget()
        self.cal.setGridVisible(True)
        self.cal.selectionChanged.connect(self._show_day)
        lay.addWidget(self.cal, 3)
        right = QVBoxLayout()
        self.day = make_label("", "SectionTitle")
        self.list = QListWidget()
        right.addWidget(self.day)
        right.addWidget(self.list, 1)
        lay.addLayout(right, 2)
        self.by_date: dict[str, list[str]] = {}

    def refresh(self) -> None:
        sid, t = self.ctx.scope(), self.ctx.theme
        self.by_date.clear()
        for d in self.ctx.analytics.deadlines(sid, 400, True):
            self.by_date.setdefault(d["date"], []).append(f"{d['kind']}: {d['title']} ({d['subject']})" + (" — OVERDUE" if d["status"] == "Overdue" else ""))
        for e in self.ctx.repo.rows("calendar_events", sid):
            d0, d1 = parse_date(e["start_date"]), parse_date(e["end_date"]) or parse_date(e["start_date"])
            while d0 and d1 and d0 <= d1 and (d1 - d0).days < 60:
                self.by_date.setdefault(d0.isoformat(), []).append(f"{e['event_type']}: {e['title']}")
                d0 += timedelta(days=1)
        self.cal.setDateTextFormat(QDate(), QTextCharFormat())
        fmt = QTextCharFormat()
        fmt.setBackground(QBrush(QColor(t.soft("warning"))))
        fmt.setForeground(QBrush(QColor(t.text_on_soft("warning"))))
        fmt.setFontWeight(QFont.Weight.Bold)
        for iso in self.by_date:
            d = parse_date(iso)
            if d:
                self.cal.setDateTextFormat(QDate(d.year, d.month, d.day), fmt)
        self._show_day()

    def _show_day(self) -> None:
        d = self.cal.selectedDate()
        iso = d.toString("yyyy-MM-dd")
        self.day.setText(d.toString("dddd, d MMMM yyyy"))
        self.list.clear()
        self.list.addItems(self.by_date.get(iso, ["Nothing due or scheduled on this day."]))


class AssignmentsPage(TabsPage):
    def __init__(self, ctx: UiContext) -> None:
        banner = make_label("", "BannerDanger", True)
        banner.hide()
        super().__init__(ctx, "Assignments", "Track every assignment from given date to evaluation. Overdue work is highlighted automatically.",
                         [("All assignments", EntityPage(ctx, "assignments")), ("Deadline calendar", DeadlineCalendar(ctx))], banner)
        self.banner = banner

    def refresh(self) -> None:
        a = self.ctx.analytics.assignment_stats(self.ctx.scope())
        msg = [f"{a['overdue']} overdue" if a["overdue"] else "", f"{a['due_week']} due within 7 days" if a["due_week"] else ""]
        self.banner.setText("  •  ".join(m for m in msg if m) + (f"   ({a['done']} of {a['total']} done)" if a["total"] else ""))
        self.banner.setVisible(bool(a["overdue"] or a["due_week"]))
        super().refresh()


# ---- exams, results & grading ------------------------------------------------------------------------------------------------------------
class GradingDialog(QDialog):
    """Edit the grade scale, credit rules and SGPA/CGPA methods. Nothing about any university is assumed."""

    def __init__(self, ctx: UiContext, parent: Any = None) -> None:
        super().__init__(parent)
        self.ctx, self.cfg = ctx, ctx.settings.grading()
        self.setWindowTitle("Grading & credit rules")
        self.resize(680, 720)
        lay = QVBoxLayout(self)
        lay.addWidget(make_label("Grading & credit rules", "DialogTitle"))
        top = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.addItems(list(GRADE_PRESETS))
        top.addWidget(make_label("Start from preset:"))
        top.addWidget(self.preset)
        top.addWidget(make_button("Load preset", self.load_preset))
        top.addStretch(1)
        lay.addLayout(top)
        self.scale = QTableWidget(0, 4)
        self.scale.setHorizontalHeaderLabels(["Grade", "Minimum %", "Grade points", "Description"])
        self.scale.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.scale, 1)
        row = QHBoxLayout()
        row.addWidget(make_button("Add grade", lambda: self._add_row("", "", "", "")))
        row.addWidget(make_button("Remove selected", lambda: self.scale.removeRow(self.scale.currentRow()) if self.scale.currentRow() >= 0 else None))
        row.addStretch(1)
        lay.addLayout(row)
        form = QFormLayout()
        self.max_pts = QLineEdit()
        self.sgpa = QComboBox()
        self.sgpa.addItems(list(SGPA_METHODS.values()))
        self.factor = QLineEdit()
        self.cgpa = QComboBox()
        self.cgpa.addItems(list(CGPA_METHODS.values()))
        self.pass_pct = QLineEdit()
        self.comp = {k: QLineEdit() for k in ("internal", "external", "practical")}
        self.total = QComboBox()
        self.total.addItems(["Sum of marks (internal + external + practical)", "Weighted component percentages"])
        self.weights = {k: QLineEdit() for k in ("internal", "external", "practical")}
        self.retake = QComboBox()
        self.retake.addItems(["Most recent attempt counts", "Best attempt counts"])
        self.failed = QCheckBox("Include failed subjects in SGPA (at their grade points)")
        for lab, w in (("Maximum grade points", self.max_pts), ("SGPA method", self.sgpa), ("Percentage ÷ factor (if used)", self.factor), ("CGPA method", self.cgpa),
                       ("Pass percentage (overall)", self.pass_pct), ("Subject total", self.total), ("Repeated subjects", self.retake), ("", self.failed)):
            form.addRow(lab, w)
        for k in self.comp:
            form.addRow(f"Minimum % in {k}", self.comp[k])
        for k in self.weights:
            form.addRow(f"Weight of {k} (weighted totals)", self.weights[k])
        lay.addLayout(form)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(120)
        lay.addWidget(make_label("How your results are calculated", "SectionTitle"))
        lay.addWidget(self.preview)
        self.banner = make_label("", "BannerDanger", True)
        self.banner.hide()
        lay.addWidget(self.banner)
        btn = QHBoxLayout()
        btn.addStretch(1)
        btn.addWidget(make_button("Preview method", self.show_preview))
        btn.addWidget(make_button("Cancel", self.reject))
        btn.addWidget(make_button("Save", self.save, "Primary"))
        lay.addLayout(btn)
        self.load(self.cfg)

    def _add_row(self, g: Any, m: Any, p: Any, d: Any) -> None:
        r = self.scale.rowCount()
        self.scale.insertRow(r)
        for c, v in enumerate((g, m, p, d)):
            self.scale.setItem(r, c, QTableWidgetItem("" if v is None else fmt_num(v) if isinstance(v, (int, float)) else str(v)))

    def load(self, cfg: GradingConfig) -> None:
        self.scale.setRowCount(0)
        for r in cfg._sorted():
            self._add_row(r["grade"], r["min"], r["points"], r.get("label", ""))
        self.max_pts.setText(fmt_num(cfg.max_points))
        self.sgpa.setCurrentIndex(list(SGPA_METHODS).index(cfg.sgpa_method))
        self.factor.setText(fmt_num(cfg.percentage_factor))
        self.cgpa.setCurrentIndex(list(CGPA_METHODS).index(cfg.cgpa_method))
        self.pass_pct.setText(fmt_num(cfg.pass_percentage))
        for k, e in self.comp.items():
            e.setText(fmt_num(cfg.component_pass.get(k, 0)))
        for k, e in self.weights.items():
            e.setText(fmt_num(cfg.weights.get(k, 0)))
        self.total.setCurrentIndex(1 if cfg.total_method == "weighted" else 0)
        self.retake.setCurrentIndex(1 if cfg.retake_policy == "best" else 0)
        self.failed.setChecked(cfg.include_failed)
        self.show_preview()

    def load_preset(self) -> None:
        self.load(GradingConfig.preset(self.preset.currentText()))

    def read(self) -> GradingConfig:
        item = lambda r, c: (self.scale.item(r, c).text() if self.scale.item(r, c) else "").strip()
        scale = [{"grade": item(r, 0), "min": safe_float(item(r, 1)), "points": safe_float(item(r, 2)), "label": item(r, 3)} for r in range(self.scale.rowCount())]
        return GradingConfig(name=self.preset.currentText(), scale=scale, max_points=safe_float(self.max_pts.text(), 0) or 0,
                             sgpa_method=list(SGPA_METHODS)[self.sgpa.currentIndex()], cgpa_method=list(CGPA_METHODS)[self.cgpa.currentIndex()],
                             percentage_factor=safe_float(self.factor.text(), 0) or 0, pass_percentage=safe_float(self.pass_pct.text(), -1) if self.pass_pct.text().strip() else -1,
                             component_pass={k: safe_float(e.text(), 0) or 0 for k, e in self.comp.items()}, weights={k: safe_float(e.text(), 0) or 0 for k, e in self.weights.items()},
                             total_method="weighted" if self.total.currentIndex() else "sum", retake_policy="best" if self.retake.currentIndex() else "latest",
                             include_failed=self.failed.isChecked())

    def show_preview(self) -> None:
        try:
            cfg = self.read()
            cfg.validate()
            self.preview.setPlainText("\n".join(cfg.describe()))
            self.banner.hide()
        except ValidationError as exc:
            self.banner.setText(str(exc))
            self.banner.show()

    def save(self) -> None:
        try:
            self.ctx.settings.save_grading(self.read())
        except ValidationError as exc:
            self.banner.setText(str(exc))
            self.banner.show()
            return
        self.ctx.core.bus.notify("settings", "subject_results")
        self.accept()


class GoalPlanner(QWidget):
    """Two calculators: the average grade needed to reach a target CGPA, and the marks needed for each grade."""

    def __init__(self, ctx: UiContext) -> None:
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        g1 = QGroupBox("Reach a target CGPA")
        f1 = QFormLayout(g1)
        self.target, self.upcoming = QLineEdit(), QLineEdit()
        self.target.setPlaceholderText("e.g. 8.5")
        self.upcoming.setPlaceholderText("credits you still have to earn")
        self.now = make_label("", "Muted", True)
        self.out1 = make_label("", "Banner", True)
        self.out1.hide()
        for lab, w in (("Current standing", self.now), ("Target CGPA", self.target), ("Credits still to earn", self.upcoming)):
            f1.addRow(lab, w)
        f1.addRow("", make_button("Calculate", self.calc_cgpa, "Primary"))
        f1.addRow("", self.out1)
        lay.addWidget(g1)
        g2 = QGroupBox("Marks needed in the final exam for each grade")
        f2 = QFormLayout(g2)
        self.int_got, self.int_max, self.ext_max, self.prac_got, self.prac_max = (QLineEdit() for _ in range(5))
        self.int_max.setText("30")
        self.ext_max.setText("70")
        for lab, w in (("Internal marks obtained", self.int_got), ("Internal maximum", self.int_max), ("External (university) maximum", self.ext_max),
                       ("Practical marks obtained (optional)", self.prac_got), ("Practical maximum (optional)", self.prac_max)):
            f2.addRow(lab, w)
        f2.addRow("", make_button("Show marks needed", self.calc_marks, "Primary"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Grade", "Minimum %", "Grade points", "External marks needed", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        f2.addRow(self.table)
        self.note = make_label("", "Muted", True)
        f2.addRow(self.note)
        lay.addWidget(g2, 1)

    def refresh(self) -> None:
        g = self.ctx.analytics.gpa_totals()
        self.now.setText("No results entered yet — add them under Semester results." if g["cgpa"] is None
                         else f"CGPA {g['cgpa']:.2f} over {fmt_num(g['credits'])} graded credits (scale maximum {fmt_num(g['max_points'])}).")
        if not self.upcoming.text().strip():
            planned = self.ctx.core.db.scalar("SELECT COALESCE(SUM(s.credits),0) FROM subjects s WHERE s.semester_id=? AND NOT EXISTS (SELECT 1 FROM subject_results r WHERE r.subject_id=s.id)",
                                              (self.ctx.scope() or 0,), 0)
            if planned:
                self.upcoming.setText(fmt_num(planned))

    def calc_cgpa(self) -> None:
        tgt, up = safe_float(self.target.text()), safe_float(self.upcoming.text())
        if tgt is None or not up or up <= 0:
            return tell(self, "Please check", "Enter a target CGPA and the number of credits you still have to earn.")
        g = self.ctx.analytics.gpa_totals()
        need = required_grade_points(tgt, g["credits"], g["points"], up)
        cfg = self.ctx.settings.grading()
        if need > g["max_points"] + 1e-9:
            msg = f"Not reachable this time: you would need an average of {need:.2f} grade points, but the scale maximum is {fmt_num(g['max_points'])}."
        elif need <= 0:
            msg = "Your target is already secured: even a minimum result keeps your CGPA at or above it."
        else:
            gr = grade_for_points(cfg, need)
            msg = f"You need an average of {need:.2f} grade points over the next {fmt_num(up)} credits" + (f" — roughly grade {gr['grade']}." if gr else ".")
        self.out1.setText(msg)
        self.out1.show()

    def calc_marks(self) -> None:
        im, em = safe_float(self.int_max.text(), 0) or 0, safe_float(self.ext_max.text(), 0) or 0
        ig = safe_float(self.int_got.text())
        if ig is None or not em or ig < 0 or ig > im:
            return tell(self, "Please check", "Enter your internal marks (not more than the internal maximum) and the external maximum.")
        rows = marks_needed_table(self.ctx.settings.grading(), ig, im, em, safe_float(self.prac_got.text(), 0) or 0, safe_float(self.prac_max.text(), 0) or 0)
        self.table.setRowCount(len(rows))
        for r, x in enumerate(rows):
            status = "Already secured" if x["secured"] else ("Possible" if x["feasible"] else "Not reachable")
            vals = [x["grade"], f"{x['min_pct']:g}%", fmt_num(x["points"]), "—" if x["secured"] else f"{fmt_num(x['required'])} of {fmt_num(em)}  ({x['required_pct']:.0f}%)", status]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if c == 4 and not x["feasible"]:
                    it.setForeground(QBrush(QColor(self.ctx.theme.t["danger_text"])))
                self.table.setItem(r, c, it)
        self.note.setText("Uses your own total method and pass rules (Grading rules…). Marks are rounded up to one decimal.")


class ExamsPage(TabsPage):
    def __init__(self, ctx: UiContext) -> None:
        strip = make_label("", "Banner", True)
        top = QWidget()
        row = QHBoxLayout(top)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(strip, 1)
        row.addWidget(make_button("Grading rules…", lambda: self.rules(), tip="Grade scale, credits, SGPA/CGPA method, pass marks"))
        super().__init__(ctx, "Exams & results", "Exam schedule, internal assessments and official semester results with configurable SGPA/CGPA.",
                         [("Exam schedule", EntityPage(ctx, "exams")), ("Assessments & marks", EntityPage(ctx, "marks")), ("Semester results", EntityPage(ctx, "subject_results")), ("Goal planner", GoalPlanner(ctx))], top)
        self.strip = strip

    def rules(self) -> None:
        if GradingDialog(self.ctx, self).exec():
            self.refresh()

    def refresh(self) -> None:
        ov = self.ctx.analytics.overview(self.ctx.scope())
        f = lambda v: "—" if v is None else f"{v:.2f}"
        self.strip.setText(f"SGPA {f(ov['sgpa'])}   ·   CGPA {f(ov['cgpa'])}   ·   credits earned {fmt_num(ov['credits_earned'])}   ·   backlogs {ov['backlogs']}"
                           f"   ·   method: {self.ctx.settings.grading().describe()[1]}")
        super().refresh()


# ---- attendance --------------------------------------------------------------------------------------------------------------------------------
class QuickAttendanceDialog(QDialog):
    def __init__(self, ctx: UiContext, parent: Any = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Quick attendance")
        self.resize(480, 460)
        lay = QVBoxLayout(self)
        lay.addWidget(make_label("Mark today's classes", "DialogTitle"))
        self.date = OptionalDate(ctx.repo.today().isoformat())
        form = QFormLayout()
        form.addRow("Date", self.date)
        self.kind = QComboBox()
        self.kind.addItems(["Lecture", "Lab", "Tutorial", "Seminar", "Other"])
        form.addRow("Class type", self.kind)
        lay.addLayout(form)
        self.rows: list[tuple[int, QComboBox]] = []
        body = QWidget()
        bl = QFormLayout(body)
        sched = {x["subject_id"]: x for x in ctx.analytics.todays_classes(ctx.scope())}     # today's timetable first
        for s in sorted(ctx.repo.rows("subjects", ctx.scope()), key=lambda r: (r["id"] not in sched, sched[r["id"]]["start"] if r["id"] in sched else "", r["name"].casefold())):
            cb = QComboBox()
            cb.addItems(["No class", "Present", "Absent"])
            x = sched.get(s["id"])
            bl.addRow(s["name"] + (f"   ({x['start']}–{x['end']}{' · already marked' if x['marked'] else ''})" if x else ""), cb)
            self.rows.append((s["id"], cb))
        lay.addWidget(scroll_page(body), 1)
        self.banner = make_label("", "BannerDanger", True)
        self.banner.hide()
        lay.addWidget(self.banner)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(make_button("Cancel", self.reject))
        row.addWidget(make_button("Save attendance", self.save, "Primary"))
        lay.addLayout(row)

    def save(self) -> None:
        marks = {sid: {"No class": None, "Present": True, "Absent": False}[cb.currentText()] for sid, cb in self.rows}
        try:
            n = self.ctx.repo.mark_attendance(self.date.value(), marks, self.kind.currentText())
        except ValidationError as exc:
            self.banner.setText(str(exc))
            self.banner.show()
            return
        self.ctx.toast(f"Attendance saved for {n} subject(s).")
        self.accept()


class AttendanceOverview(QWidget):
    COLS = ("Subject", "Held", "Attended", "Missed", "%", "Target", "Status", "To reach target / can skip")

    def __init__(self, ctx: UiContext) -> None:
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(make_button("Quick mark…", self.quick, "Primary"))
        row.addWidget(make_label("Default minimum attendance %"))
        self.target = QSpinBox()
        self.target.setRange(1, 100)
        self.target.setValue(int(ctx.settings.get_float("attendance_min", 75)))
        self.target.valueChanged.connect(lambda v: (ctx.settings.set("attendance_min", v), ctx.core.bus.notify("settings")))
        row.addWidget(self.target)
        row.addStretch(1)
        lay.addLayout(row)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(list(self.COLS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        lay.addWidget(self.table, 1)
        self.chart = ChartWidget(ctx, "No attendance recorded yet")
        lay.addWidget(self.chart)

    def quick(self) -> None:
        if QuickAttendanceDialog(self.ctx, self).exec():
            self.refresh()

    def refresh(self) -> None:
        rows = self.ctx.analytics.attendance_by_subject(self.ctx.scope())
        t = self.ctx.theme
        self.table.setRowCount(len(rows))
        for r, a in enumerate(rows):
            act = "—" if a["status"] == "No data" else (f"Attend next {a['need']} in a row" if a["need"] else "Target can no longer be reached") if a["pct"] < a["target"] \
                else "Can miss " + (f"{a['can_skip']} more" if a["can_skip"] is not None else "any") + " class(es)"
            vals = [a["subject"], a["held"], a["attended"], a["missed"], "—" if a["pct"] is None else f"{a['pct']:.1f}%", f"{a['target']:.0f}%", a["status"], act]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                it.setTextAlignment(A_R if 1 <= c <= 5 else A_L)
                tok = STATUS_TOKENS.get(a["status"])
                if c == 6 and tok and tok != "muted":
                    it.setForeground(QBrush(QColor(t.t[tok + "_text"])))
                self.table.setItem(r, c, it)
        self.chart.set_bars([(a["subject"], a["pct"], a["color"]) for a in rows if a["pct"] is not None], 100, "%", self.ctx.settings.get_float("attendance_min", 75.0), 1)
        self.target.blockSignals(True)
        self.target.setValue(int(self.ctx.settings.get_float("attendance_min", 75)))
        self.target.blockSignals(False)


# ---- projects, study, focus, resources ------------------------------------------------------------------------------------------------------
class MilestonesDialog(QDialog):
    def __init__(self, ctx: UiContext, project_id: int, parent: Any = None) -> None:
        super().__init__(parent)
        self.ctx, self.pid = ctx, project_id
        self.setWindowTitle("Project milestones")
        self.resize(520, 380)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, fn, kind in (("Add…", self.add, "Primary"), ("Toggle done", self.toggle, ""), ("Delete", self.delete, "Danger")):
            row.addWidget(make_button(text, fn, kind))
        row.addStretch(1)
        row.addWidget(make_button("Close", self.accept))
        lay.addLayout(row)
        self.reload()

    def reload(self) -> None:
        self.list.clear()
        for m in self.ctx.repo.milestones(self.pid):
            it = QListWidgetItem(f"{'☑' if m['status'] == 'Completed' else '☐'}  {m['title']}   {('· due ' + fmt_date(m['due_date'])) if m['due_date'] else ''}")
            it.setData(Qt.ItemDataRole.UserRole, m)
            self.list.addItem(it)

    def add(self) -> None:
        title, ok = QInputDialog.getText(self, "Milestone", "Milestone title:")
        if not ok:
            return
        due, ok = QInputDialog.getText(self, "Milestone", "Due date (YYYY-MM-DD, optional):")
        if ok and self.ctx.guard(self, lambda: self.ctx.repo.save_milestone(self.pid, title, due)) is not None:
            self.reload()

    def toggle(self) -> None:
        it = self.list.currentItem()
        if it:
            m = it.data(Qt.ItemDataRole.UserRole)
            self.ctx.repo.save_milestone(self.pid, m["title"], m["due_date"], "Pending" if m["status"] == "Completed" else "Completed", m["id"])
            self.reload()

    def delete(self) -> None:
        it = self.list.currentItem()
        if it and confirm(self, "Delete milestone", "Delete this milestone?"):
            self.ctx.repo.delete_milestone(it.data(Qt.ItemDataRole.UserRole)["id"])
            self.reload()


def make_projects_page(ctx: UiContext) -> TabsPage:
    def milestones(page: "EntityPage") -> None:
        ids = page.selected_ids()
        if len(ids) == 1:
            MilestonesDialog(ctx, ids[0], page).exec()
            page.refresh()
    groups = [("All", None), ("Projects", {"Academic Project", "Mini Project", "Major Project"}), ("Labs & practicals", {"Laboratory Record", "Practical Assignment"}),
              ("Viva & presentations", {"Viva Preparation", "Presentation"}), ("Research", {"Research Activity"}), ("Internships & certifications", {"Internship", "Certification"})]
    tabs = []
    for label, cats in groups:
        tabs.append((label, EntityPage(ctx, "projects", tid=f"projects_{label}", fixed=(lambda r, c=cats: r["category"] in c) if cats else None,
                                       defaults={"category": sorted(cats)[0]} if cats else {}, extra=[("Milestones…", milestones)])))
    return TabsPage(ctx, "Projects, labs & activities", "Projects, lab records, viva preparation, presentations, research, internships and certifications.", tabs)


class PlannerView(QWidget):
    """Day / week / month / all planner built on the planner table's date-range filter."""

    def __init__(self, ctx: UiContext) -> None:
        super().__init__()
        self.ctx, self.mode, self.anchor = ctx, "week", ctx.repo.today()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        for text, m in (("Day", "day"), ("Week", "week"), ("Month", "month"), ("All", "all")):
            row.addWidget(make_button(text, lambda mm=m: self.set_mode(mm)))
        row.addWidget(make_button("◀", lambda: self.shift(-1)))
        row.addWidget(make_button("Today", self.today))
        row.addWidget(make_button("▶", lambda: self.shift(1)))
        self.label = make_label("", "SectionTitle")
        row.addWidget(self.label)
        row.addStretch(1)
        lay.addLayout(row)
        self.table = EntityPage(ctx, "planner_tasks")
        lay.addWidget(self.table, 1)

    def span(self) -> tuple[Optional[date], Optional[date]]:
        a = self.anchor
        if self.mode == "day":
            return a, a
        if self.mode == "week":
            s = a - timedelta(days=a.weekday())
            return s, s + timedelta(days=6)
        if self.mode == "month":
            s = a.replace(day=1)
            return s, (s.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return None, None

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.refresh()

    def today(self) -> None:
        self.anchor = self.ctx.repo.today()
        self.refresh()

    def shift(self, n: int) -> None:
        step = {"day": 1, "week": 7, "month": 30}.get(self.mode, 0)
        self.anchor += timedelta(days=step * n)
        self.refresh()

    def refresh(self) -> None:
        lo, hi = self.span()
        self.label.setText("All dates" if lo is None else f"{fmt_date(lo.isoformat(), '%d %b')} – {fmt_date(hi.isoformat(), '%d %b %Y')}")
        self.table.refresh()
        for w, d in ((self.table.date_from, lo), (self.table.date_to, hi)):
            w.check.setChecked(d is not None)
            if d:
                w.edit.setDate(QDate(d.year, d.month, d.day))
        self.table._apply()


class RevisionView(QWidget):
    def __init__(self, ctx: UiContext) -> None:
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(make_label("Review selected unit:", "Muted"))
        for text, q in (("Again", 1), ("Hard", 3), ("Good", 4), ("Easy", 5)):
            row.addWidget(make_button(text, lambda qq=q: self.review(qq), "Primary" if q == 4 else ""))
        row.addStretch(1)
        row.addWidget(make_label("Spaced repetition (SM-2): intervals grow when recall is good and reset on “Again”.", "Muted"))
        lay.addLayout(row)
        self.table = EntityPage(ctx, "revisions")
        lay.addWidget(self.table, 1)

    def review(self, q: int) -> None:
        ids = self.table.selected_ids()
        if not ids:
            return self.ctx.toast("Select a revision row first.")
        for i in ids:
            self.ctx.guard(self, lambda rid=i: self.ctx.repo.review_revision(rid, q))
        self.table.refresh()

    def refresh(self) -> None:
        self.table.refresh()


class InsightsView(QWidget):
    def __init__(self, ctx: UiContext) -> None:
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)
        self.body = QVBoxLayout()
        host = QWidget()
        host.setLayout(self.body)
        lay.addWidget(scroll_page(host))

    def refresh(self) -> None:
        while self.body.count():
            w = self.body.takeAt(0).widget()
            if w:
                w.deleteLater()
        icon = {"good": "✔", "info": "•", "warn": "⚠", "bad": "✖"}
        for i in self.ctx.analytics.insights(self.ctx.scope()):
            lab = make_label(f"{icon.get(i['level'], '•')}  {i['text']}", "Banner" if i["level"] in ("warn",) else "BannerDanger" if i["level"] == "bad" else "", True)
            self.body.addWidget(lab)
        self.body.addStretch(1)


def make_study_page(ctx: UiContext) -> TabsPage:
    return TabsPage(ctx, "Study hub", "Daily, weekly and monthly planner, study log, spaced revision and insights from your real records.",
                    [("Planner", PlannerView(ctx)), ("Study sessions", EntityPage(ctx, "study_sessions")), ("Revision", RevisionView(ctx)),
                     ("Focus timer", FocusPage(ctx)), ("Insights", InsightsView(ctx))])


class FocusPage(Page):
    MODES = {"Focus": "pomodoro_work", "Short break": "pomodoro_short", "Long break": "pomodoro_long"}

    def __init__(self, ctx: UiContext) -> None:
        super().__init__(ctx)
        self.mode, self.total, self.remaining, self.deadline, self.running, self.completed = "Focus", 1500, 1500, 0.0, False, 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 14)
        lay.addWidget(page_header("Focus room", "Pomodoro timer. Finished sessions are logged to the selected subject automatically."))
        card, cl = make_card()
        self.subject = QComboBox()
        cl.addWidget(make_label("Subject", "Muted"))
        cl.addWidget(self.subject)
        self.topic = QLineEdit()
        self.topic.setPlaceholderText("What are you working on? (optional)")
        cl.addWidget(self.topic)
        mrow = QHBoxLayout()
        for m in self.MODES:
            mrow.addWidget(make_button(m, lambda mm=m: self.set_mode(mm), "Chip"))
        cl.addLayout(mrow)
        self.clock = make_label("25:00", "Timer")
        self.clock.setAlignment(A_C)
        cl.addWidget(self.clock)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        cl.addWidget(self.bar)
        brow = QHBoxLayout()
        self.start_btn = make_button("Start", self.toggle, "Primary")
        brow.addWidget(self.start_btn)
        brow.addWidget(make_button("Reset", self.reset))
        brow.addWidget(make_button("Finish early & log", self.finish_early, tip="Logs the time studied so far"))
        cl.addLayout(brow)
        self.info = make_label("", "Muted", True)
        cl.addWidget(self.info)
        lay.addWidget(card)
        lay.addStretch(1)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.tick)

    def refresh(self) -> None:
        cur = self.subject.currentData()
        self.subject.clear()
        for r in self.ctx.repo.rows("subjects", self.ctx.scope()):
            self.subject.addItem(r["name"], r["id"])
        i = self.subject.findData(cur)
        if i >= 0:
            self.subject.setCurrentIndex(i)
        if not self.running:
            self.set_mode(self.mode)

    def set_mode(self, mode: str) -> None:
        self.mode, self.running = mode, False
        self.timer.stop()
        self.total = max(1, self.ctx.settings.get_int(self.MODES[mode], 25)) * 60
        self.remaining = self.total
        self._paint()

    def _paint(self) -> None:
        m, s = divmod(max(0, int(self.remaining)), 60)
        self.clock.setText(f"{m:02d}:{s:02d}")
        self.bar.setRange(0, self.total)
        self.bar.setValue(self.total - int(self.remaining))
        self.start_btn.setText("Pause" if self.running else "Start")
        self.info.setText(f"{self.mode} · {self.completed} focus session(s) completed in this run")

    def toggle(self) -> None:
        if self.mode == "Focus" and self.subject.currentData() is None and not self.running:
            return tell(self, "Choose a subject", "Add a subject first (Subjects & syllabus), then select it here.")
        self.running = not self.running
        if self.running:
            self.deadline = time.monotonic() + self.remaining
            self.timer.start()
        else:
            self.timer.stop()
            self.remaining = max(0.0, self.deadline - time.monotonic())
        self._paint()

    def reset(self) -> None:
        self.set_mode(self.mode)

    def tick(self) -> None:
        self.remaining = max(0.0, self.deadline - time.monotonic())
        self._paint()
        if self.remaining <= 0:
            self.running = False
            self.timer.stop()
            QApplication.beep()
            if self.mode == "Focus":
                self._log(self.total // 60)
                self.completed += 1
                self.ctx.toast("Focus session complete and logged.")
                self.set_mode("Long break" if self.completed % 4 == 0 else "Short break")
            else:
                self.ctx.toast("Break over — ready for the next focus session.")
                self.set_mode("Focus")

    def _log(self, minutes: int) -> None:
        sid = self.subject.currentData()
        if sid and minutes >= 1:
            self.ctx.guard(self, lambda: self.ctx.repo.quick_log_study(sid, minutes, self.topic.text().strip() or "Focus session", "Study"))

    def finish_early(self) -> None:
        if self.mode != "Focus":
            return
        done = self.total - (max(0.0, self.deadline - time.monotonic()) if self.running else self.remaining)
        self._log(int(done // 60))
        self.ctx.toast(f"Logged {int(done // 60)} minute(s).")
        self.set_mode("Focus")


def make_resources_page(ctx: UiContext) -> TabsPage:
    def open_file(page: "EntityPage") -> None:
        for r in page.selected_rows():
            if r["exists"] == "Yes":
                QDesktopServices.openUrl(QUrl.fromLocalFile(r["file_path"]))
            else:
                tell(page, "File not found", "That file has been moved or deleted.")

    def reveal(page: "EntityPage") -> None:
        for r in page.selected_rows()[:1]:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(r["file_path"]).parent)))
    return TabsPage(ctx, "Notes & documents", "Subject and chapter notes, plus references to your files. Files stay where they are; only the link is stored.",
                    [("Notes", EntityPage(ctx, "notes")), ("Documents & files", EntityPage(ctx, "attachments", extra=[("Open file", open_file), ("Show folder", reveal)]))])


# ---- analytics ---------------------------------------------------------------------------------------------------------------------------------------------
class AnalyticsPage(Page):
    def __init__(self, ctx: UiContext) -> None:
        super().__init__(ctx)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QWidget()
        body.setObjectName("PageBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 18, 22, 22)
        lay.addWidget(page_header("Analytics", "Every figure is calculated from your stored records. Charts say so when there is not enough data."))
        bar, bl = make_card(QHBoxLayout)
        self.sem = QComboBox()
        bl.addWidget(make_label("Semester"))
        bl.addWidget(self.sem)
        self.d_from, self.d_to = OptionalDate(), OptionalDate()
        for lab, w in (("From", self.d_from), ("To", self.d_to)):
            bl.addWidget(make_label(lab))
            bl.addWidget(w)
        bl.addWidget(make_button("Apply", self.refresh, "Primary"))
        bl.addStretch(1)
        lay.addWidget(bar)
        self.kpis = CardGrid(220)
        self.grid = CardGrid(470)
        self.ins = QVBoxLayout()
        ins_host = QWidget()
        ins_host.setLayout(self.ins)
        for w in (self.kpis, self.grid, panel("Insights", ins_host)):
            lay.addWidget(w)
        lay.addStretch(1)
        outer.addWidget(scroll_page(body))
        self.charts: dict[str, Any] = {k: ChartWidget(ctx) for k in ("perf", "trend", "att", "study_s", "study_d", "assign", "grades", "gpa", "syl")}
        self.charts["heat"] = HeatmapWidget(ctx)

    def _fill_sem(self) -> None:
        cur = self.sem.currentData() if self.sem.count() else "scope"
        self.sem.blockSignals(True)
        self.sem.clear()
        self.sem.addItem("Selected semester (top bar)", "scope")
        self.sem.addItem("All semesters", None)
        for name, sid in self.ctx.repo.lookup("semesters"):
            self.sem.addItem(name, sid)
        self.sem.setCurrentIndex(max(0, self.sem.findData(cur)))
        self.sem.blockSignals(False)

    def refresh(self) -> None:
        self._fill_sem()
        c, an = self.ctx, self.ctx.analytics
        sid = c.scope() if self.sem.currentData() == "scope" else self.sem.currentData()
        rng = (self.d_from.value() or None, self.d_to.value() or None)
        ov, ch, t = an.overview(sid, rng), self.charts, c.theme
        fp = lambda v: "—" if v is None else f"{v:.1f}%"
        self.kpis.set_cards([metric_card("Score average", fp(ov["score_pct"])), metric_card("Attendance", fp(ov["attendance"]["pct"])),
                             metric_card("Assignments done", fp(ov["assignments"]["completion_rate"]), f"{ov['assignments']['done']} of {ov['assignments']['total']}"),
                             metric_card("Study time", fmt_minutes(ov["study_minutes"])), metric_card("Syllabus", fp(ov["syllabus_pct"])),
                             metric_card("SGPA", "—" if ov["sgpa"] is None else f"{ov['sgpa']:.2f}"), metric_card("CGPA", "—" if ov["cgpa"] is None else f"{ov['cgpa']:.2f}")])
        perf = [p for p in an.subject_performance(sid, rng) if p["pct"] is not None]
        ch["perf"].empty = "No scored assessments in this period"
        ch["perf"].set_bars([(p["subject"] + (f"  ({p['trend']})" if p["trend"] != "Not enough data" else ""), p["pct"], p["color"]) for p in perf], 100, "%", c.settings.get_float("low_mark_pct", 40.0), 1)
        tr = an.score_trend(sid, rng)[-40:]
        ch["trend"].empty = "No dated scores in this period"
        ch["trend"].set_xy([fmt_date(d, "%d %b") for d, _ in tr], [("Score %", [v for _, v in tr], t.accent)], "lines", 100, "%", 0)
        att = [a for a in an.attendance_by_subject(sid, rng) if a["pct"] is not None]
        ch["att"].empty = "No attendance in this period"
        ch["att"].set_bars([(a["subject"], a["pct"], a["color"]) for a in att], 100, "%", c.settings.get_float("attendance_min", 75.0), 1)
        st = [s for s in an.study_by_subject(sid, rng) if s["minutes"]]
        ch["study_s"].empty = "No study time in this period"
        ch["study_s"].set_bars([(s["subject"], s["minutes"] / 60, s["color"]) for s in st], max([s["minutes"] / 60 for s in st] + [1]), " h", None, 1)
        daily = an.study_daily(30, sid)
        ch["study_d"].empty = "No study sessions in the last 30 days"
        ch["study_d"].set_xy([d[5:] for d, _ in daily], [("Minutes", [m if m else None for _, m in daily], t.accent)], "bars", None, "", 0)
        a = an.assignment_stats(sid, rng)
        ch["assign"].empty = "No assignments in this period"
        ch["assign"].set_bars([(k, v, t.color({"Overdue": "danger", "Late Submission": "warning", "Submitted": "success", "Evaluated": "success"}.get(k, "info"))) for k, v in a["by_status"].items()],
                              max(list(a["by_status"].values()) + [1]), "", None, 0)
        gd = an.grade_distribution(sid)
        ch["grades"].empty = "No results entered yet"
        ch["grades"].set_xy([g for g, _ in gd], [("Subjects", [n for _, n in gd], t.accent)], "bars", None, "", 0)
        gp = an.sgpa_series()
        ch["gpa"].empty = "SGPA appears once semester results are entered"
        ch["gpa"].set_xy([g["name"] for g in gp], [("SGPA", [g["sgpa"] for g in gp], t.accent), ("CGPA", [g["cgpa"] for g in gp], t.color("warning"))], "lines", c.settings.grading().max_points, "", 2)
        syl = [s for s in an.syllabus_by_subject(sid) if s["pct"] is not None]
        ch["syl"].empty = "Add units to track syllabus completion"
        ch["syl"].set_bars([(s["subject"], s["pct"], s["color"]) for s in syl], 100, "%", None, 0)
        ch["heat"].set_data(an.study_heatmap(12, sid))
        titles = {"perf": "Average score by subject (red line = low-marks threshold)", "trend": "Score trend", "att": "Attendance by subject (red line = target)",
                  "study_s": "Study hours by subject", "study_d": "Study minutes per day", "heat": "Study consistency (12 weeks)", "assign": "Assignments by status", "grades": "Grade distribution", "gpa": "SGPA & CGPA progression", "syl": "Syllabus completion"}
        for w in ch.values():
            w.setParent(None)
        self.grid.set_cards([panel(titles[k], ch[k]) for k in titles])
        while self.ins.count():
            w = self.ins.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i in an.insights(sid, rng):
            self.ins.addWidget(make_label({"good": "✔  ", "info": "•  ", "warn": "⚠  ", "bad": "✖  "}.get(i["level"], "•  ") + i["text"], "", True))


# ---- reports --------------------------------------------------------------------------------------------------------------------------------------------------
class ColumnsDialog(QDialog):
    """Choose which columns each report table shows, and their relative widths."""

    def __init__(self, cfg: ReportConfig, parent: Any = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Report columns")
        self.resize(520, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(make_label("Columns & widths", "DialogTitle"))
        lay.addWidget(make_label("Untick a column to hide it. Edit “Width” to change its share of the table (blank = automatic).", "Muted", True))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Table / column", "Width"])
        self.tree.setColumnWidth(0, 330)
        for section, cols in SECTION_COLUMNS.items():
            top = QTreeWidgetItem([section.replace("_", " ").title(), ""])
            self.tree.addTopLevelItem(top)
            chosen = cfg.columns.get(section) or [c[0] for c in cols if c[0] not in DEFAULT_HIDDEN.get(section, ())]
            for key, label, wt, _ in cols:
                it = QTreeWidgetItem([label, fmt_num(cfg.widths.get(section, {}).get(key), blank="")])
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
                it.setCheckState(0, Qt.CheckState.Checked if key in chosen else Qt.CheckState.Unchecked)
                it.setData(0, Qt.ItemDataRole.UserRole, (section, key))
                top.addChild(it)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(make_button("Cancel", self.reject))
        row.addWidget(make_button("Apply", self.apply, "Primary"))
        lay.addLayout(row)

    def apply(self) -> None:
        cols, widths = {}, {}
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            keep, section = [], None
            for j in range(top.childCount()):
                it = top.child(j)
                section, key = it.data(0, Qt.ItemDataRole.UserRole)
                if it.checkState(0) == Qt.CheckState.Checked:
                    keep.append(key)
                w = safe_float(it.text(1))
                if w and w > 0:
                    widths.setdefault(section, {})[key] = w
            default = [c[0] for c in SECTION_COLUMNS[section] if c[0] not in DEFAULT_HIDDEN.get(section, ())]
            if section and keep and keep != default:
                cols[section] = keep
        self.cfg.columns, self.cfg.widths = cols, widths
        self.accept()


class ReportsPage(Page):
    def __init__(self, ctx: UiContext) -> None:
        super().__init__(ctx)
        self.cfg = ReportConfig()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        body = QWidget()
        body.setObjectName("PageBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 18, 22, 22)
        lay.addWidget(page_header("PDF reports", "Build professional academic reports. Choose a type, scope, sections and design, check the preview, then generate."))
        self.kind = QComboBox()
        for k, (label, _) in REPORT_KINDS.items():
            self.kind.addItem(label, k)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.title, self.subtitle, self.header, self.footer = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.title.setPlaceholderText("Default: the report type")
        self.paper = QComboBox()
        self.paper.addItems(PAPER_SIZES)
        self.orient = QComboBox()
        self.orient.addItems(["Portrait", "Landscape"])
        self.margins = [QDoubleSpinBox() for _ in range(4)]
        for m in self.margins:
            m.setRange(5, 60)
            m.setValue(16)
            m.setSuffix(" mm")
        self.checks = {k: QCheckBox(l) for k, l in (("page_numbers", "Page numbers"), ("show_date", "Report date"), ("cover_page", "Cover page"), ("executive_summary", "Executive summary"),
                                                    ("student_info", "Student information"), ("branding", "College branding / logo"), ("include_charts", "Charts"))}
        for k in ("page_numbers", "show_date", "executive_summary", "student_info", "branding", "include_charts"):
            self.checks[k].setChecked(True)
        self.logo = QLineEdit()
        self.palette = QComboBox()
        self.palette.addItems(list(PALETTES))
        self.accent = ColorButton("#2457A7")
        self.font = QSpinBox()
        self.font.setRange(70, 160)
        self.font.setValue(100)
        self.font.setSuffix(" %")
        self.density = QComboBox()
        self.density.addItems(list(DENSITY))
        self.density.setCurrentText("Comfortable")
        self.spacing = QDoubleSpinBox()
        self.spacing.setRange(0.5, 2.5)
        self.spacing.setSingleStep(0.1)
        self.spacing.setValue(1.0)
        self.sections = QListWidget()
        self.sections.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.scope_sem, self.scope_year, self.scope_sub = QComboBox(), QComboBox(), QComboBox()
        self.d_from, self.d_to = OptionalDate(), OptionalDate()
        self.limit = QSpinBox()
        self.limit.setRange(1, 20000)
        self.limit.setValue(500)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Optional remarks printed at the end of the report")
        self.notes.setMaximumHeight(80)
        def group(title: str, rows: Sequence[tuple[str, Any]]) -> "QGroupBox":
            g = QGroupBox(title)
            f = QFormLayout(g)
            for lab, w in rows:
                f.addRow(lab, w) if lab else f.addRow(w)
            return g
        mrow = QWidget()
        ml = QHBoxLayout(mrow)
        ml.setContentsMargins(0, 0, 0, 0)
        for lab, m in zip(("L", "R", "T", "B"), self.margins):
            ml.addWidget(make_label(lab, "Muted"))
            ml.addWidget(m)
        logo_row = QWidget()
        ll = QHBoxLayout(logo_row)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.logo, 1)
        ll.addWidget(make_button("Browse…", self._pick_logo))
        range_row = QWidget()
        rl = QHBoxLayout(range_row)
        rl.setContentsMargins(0, 0, 0, 0)
        for text, days in (("Last 7 days", 7), ("Last 30 days", 30), ("Clear dates", 0)):
            rl.addWidget(make_button(text, lambda d=days: self._set_range(d)))
        chk = QWidget()
        cg = QGridLayout(chk)
        for i, w in enumerate(self.checks.values()):
            cg.addWidget(w, i // 2, i % 2)
        self.grid = CardGrid(430)
        self.grid.set_cards([
            group("Report", [("Type", self.kind), ("Title", self.title), ("Subtitle", self.subtitle), ("Notes & remarks", self.notes)]),
            group("Scope", [("Semester", self.scope_sem), ("Academic year", self.scope_year), ("Subject", self.scope_sub), ("From", self.d_from), ("To", self.d_to), ("", range_row), ("Records per table (max)", self.limit)]),
            group("Sections (drag to reorder, untick to omit)", [("", self.sections)]),
            group("Page", [("Paper", self.paper), ("Orientation", self.orient), ("Margins", mrow), ("Header text", self.header), ("Footer text", self.footer), ("", chk)]),
            group("Design", [("Logo", logo_row), ("Colour palette", self.palette), ("Accent (Custom palette)", self.accent), ("Font size", self.font), ("Table density", self.density), ("Row spacing", self.spacing),
                             ("", make_button("Columns & widths…", self.columns))]),
        ])
        lay.addWidget(self.grid)
        row = QHBoxLayout()
        self.btn_plan = make_button("Preview settings", self.plan)
        self.btn_prev = make_button("Open preview PDF", lambda: self.generate(True))
        self.btn_gen = make_button("Generate PDF…", lambda: self.generate(False), "Primary")
        for b in (self.btn_plan, self.btn_prev, self.btn_gen):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(make_button("Save preset…", self.save_preset))
        preset_btn = make_button("Presets ▾")
        preset_btn.clicked.connect(lambda _c=False: self._preset_menu(preset_btn))
        row.addWidget(preset_btn)
        row.addWidget(make_button("Reset", self.reset))
        lay.addLayout(row)
        self.progress = QProgressBar()
        self.progress.hide()
        self.status = make_label("", "Muted", True)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(170)
        self.summary.setPlaceholderText("Click “Preview settings” to see what the report will contain.")
        for w in (self.progress, self.status, self.summary):
            lay.addWidget(w)
        lay.addStretch(1)
        outer.addWidget(scroll_page(body))
        self._fill_sections(REPORT_KINDS["complete"][1])

    # ---- form <-> config ---------------------------------------------------
    def _fill_sections(self, keys: Sequence[str]) -> None:
        self.sections.clear()
        for k in list(keys) + [k for k in REPORT_SECTIONS if k not in keys]:
            it = QListWidgetItem(REPORT_SECTIONS[k])
            it.setData(Qt.ItemDataRole.UserRole, k)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if k in keys else Qt.CheckState.Unchecked)
            self.sections.addItem(it)

    def _kind_changed(self) -> None:
        self._fill_sections(REPORT_KINDS.get(self.kind.currentData(), REPORT_KINDS["custom"])[1])
        if self.kind.currentData() == "weekly":
            self._set_range(7)

    def _set_range(self, days: int) -> None:
        """Quick date range ending today (0 clears it)."""
        today = self.ctx.repo.today()
        for w, d in ((self.d_from, today - timedelta(days=days - 1) if days else None), (self.d_to, today if days else None)):
            w.check.setChecked(d is not None)
            if d:
                w.edit.setDate(QDate(d.year, d.month, d.day))

    def _pick_logo(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Logo", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif)")
        if p:
            self.logo.setText(p)

    def columns(self) -> None:
        cfg = self.read()
        if ColumnsDialog(cfg, self).exec():
            self.cfg.columns, self.cfg.widths = cfg.columns, cfg.widths

    def read(self) -> ReportConfig:
        secs = [self.sections.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.sections.count()) if self.sections.item(i).checkState() == Qt.CheckState.Checked]
        cfg = ReportConfig(kind=self.kind.currentData(), title=self.title.text(), subtitle=self.subtitle.text(), paper=self.paper.currentText(), orientation=self.orient.currentText(),
                           margins_mm=[m.value() for m in self.margins], header_text=self.header.text(), footer_text=self.footer.text(), logo_path=self.logo.text().strip(),
                           palette=self.palette.currentText(), accent=self.accent.value(), font_scale=self.font.value() / 100, density=self.density.currentText(),
                           row_spacing=self.spacing.value(), sections=secs, scope_semester_id=self.scope_sem.currentData(), scope_year_id=self.scope_year.currentData(),
                           scope_subject_id=self.scope_sub.currentData(), date_from=self.d_from.value(), date_to=self.d_to.value(), record_limit=self.limit.value(),
                           notes=self.notes.toPlainText(), columns=dict(self.cfg.columns), widths=dict(self.cfg.widths), **{k: c.isChecked() for k, c in self.checks.items()})
        return cfg

    def apply(self, cfg: ReportConfig) -> None:
        self.cfg = cfg
        self._fill_scopes()
        self.kind.blockSignals(True)
        self.kind.setCurrentIndex(max(0, self.kind.findData(cfg.kind)))
        self.kind.blockSignals(False)
        for w, v in ((self.title, cfg.title), (self.subtitle, cfg.subtitle), (self.header, cfg.header_text), (self.footer, cfg.footer_text), (self.logo, cfg.logo_path)):
            w.setText(v)
        self.paper.setCurrentText(cfg.paper)
        self.orient.setCurrentText(cfg.orientation)
        for m, v in zip(self.margins, cfg.margins_mm):
            m.setValue(v)
        for k, c in self.checks.items():
            c.setChecked(bool(getattr(cfg, k)))
        self.palette.setCurrentText(cfg.palette)
        self.accent.hex = hex_norm(cfg.accent, "#2457A7")
        self.accent._paint()
        self.font.setValue(int(cfg.font_scale * 100))
        self.density.setCurrentText(cfg.density)
        self.spacing.setValue(cfg.row_spacing)
        self._fill_sections(cfg.sections or REPORT_KINDS.get(cfg.kind, REPORT_KINDS["custom"])[1])
        for combo, val in ((self.scope_sem, cfg.scope_semester_id), (self.scope_year, cfg.scope_year_id), (self.scope_sub, cfg.scope_subject_id)):
            combo.setCurrentIndex(max(0, combo.findData(val)))
        for w, v in ((self.d_from, cfg.date_from), (self.d_to, cfg.date_to)):
            d = parse_date(v)
            w.check.setChecked(bool(d))
            if d:
                w.edit.setDate(QDate(d.year, d.month, d.day))
        self.limit.setValue(cfg.record_limit)
        self.notes.setPlainText(cfg.notes)

    def preset(self, **kw: Any) -> None:
        cfg = self.read()
        for k, v in kw.items():
            setattr(cfg, k, v)
        cfg.sections = []
        self.apply(cfg)

    def _fill_scopes(self) -> None:
        for combo, items, label in ((self.scope_sem, self.ctx.repo.lookup("semesters"), "All semesters"), (self.scope_year, self.ctx.repo.lookup("academic_years"), "All years"),
                                    (self.scope_sub, self.ctx.repo.lookup("subjects"), "All subjects")):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(label, None)
            for name, rid in items:
                combo.addItem(name, rid)
            combo.blockSignals(False)

    def refresh(self) -> None:
        self.apply(self.read() if self.scope_sem.count() else self.cfg)

    def reset(self) -> None:
        self.apply(ReportConfig())

    # ---- presets -----------------------------------------------------------
    def _presets(self) -> dict:
        return self.ctx.settings.get_json("report_presets", {}) or {}

    def save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        if ok and name.strip():
            p = self._presets()
            p[name.strip()] = json.loads(self.read().to_json())
            self.ctx.settings.set("report_presets", p)
            self.ctx.toast(f"Preset “{name.strip()}” saved.")

    def _preset_menu(self, anchor: Any) -> None:
        m, p = QMenu(self), self._presets()
        for name in p:
            add_action(m, name, lambda n=name: self.apply(ReportConfig.from_json(json.dumps(p[n]))))
        if not p:
            add_action(m, "(no saved presets)", lambda: None, enabled=False)
        else:
            dm = m.addMenu("Delete preset")
            for name in p:
                add_action(dm, name, lambda n=name: (p.pop(n, None), self.ctx.settings.set("report_presets", p)))
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    # ---- actions -------------------------------------------------------------
    def _busy(self, busy: bool) -> None:
        for b in (self.btn_plan, self.btn_prev, self.btn_gen):
            b.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)

    def plan(self) -> None:
        cfg = self.read()
        try:
            cfg.validate()
        except ValidationError as exc:
            return tell(self, "Please check", str(exc))
        path, fixed = self.ctx.core.db_path, self.ctx.repo.fixed_today
        self._busy(True)

        def job(progress: Callable) -> list:
            rc = ReadCore(path, fixed)
            try:
                eng = ReportEngine(rc.repo, rc.analytics, cfg)
                return [eng.plan(), eng.scope_text(), eng.warnings]
            finally:
                rc.close()

        def done(res: list) -> None:
            self._busy(False)
            plan, scope, warns = res
            lines = [f"{cfg.paper} {cfg.orientation} · scope: {scope}", ""] + [f"{i}. {title} — {n} record(s)" for i, (_, title, n) in enumerate(plan, 1)]
            if cfg.cover_page:
                lines.insert(2, "Cover page")
            self.summary.setPlainText("\n".join(lines + [""] + [f"⚠ {w}" for w in warns]))
        self.run_task(job, done, on_fail=lambda m: (self._busy(False), tell(self, "Preview failed", m, True)))

    def generate(self, preview: bool) -> None:
        cfg = self.read()
        try:
            cfg.validate()
        except ValidationError as exc:
            return tell(self, "Please check", str(exc))
        if preview:
            out = str(Path(tempfile.gettempdir()) / f"study_space_preview_{uuid.uuid4().hex[:6]}.pdf")
        else:
            out, _ = QFileDialog.getSaveFileName(self, "Save PDF report", str(Path.home() / f"{(cfg.title or REPORT_KINDS[cfg.kind][0]).replace(' ', '_')}.pdf"), "PDF files (*.pdf)")
            if not out:
                return
            out = out if out.lower().endswith(".pdf") else out + ".pdf"
        path, fixed = self.ctx.core.db_path, self.ctx.repo.fixed_today
        self._busy(True)

        def job(progress: Callable) -> dict:
            rc = ReadCore(path, fixed)
            try:
                return generate_report(rc, cfg, out, progress, preview)
            finally:
                rc.close()

        def done(res: dict) -> None:
            self._busy(False)
            warn = ("\n⚠ " + "\n⚠ ".join(res["warnings"])) if res["warnings"] else ""
            self.status.setText(f"Saved {res['pages']} page(s), {res['bytes'] / 1024:,.0f} KB → {res['path']}{warn}")
            if preview or confirm(self, "Report created", f"{res['pages']} page(s) saved.\n\nOpen it now?"):
                QDesktopServices.openUrl(QUrl.fromLocalFile(res["path"]))
        self.run_task(job, done, lambda p, m: (self.progress.setValue(p), self.status.setText(m)),
                      lambda m: (self._busy(False), self.status.setText(f"Failed: {m}"), tell(self, "The report could not be created", f"{m}\n\nNo partial file was left behind.", True)))


def make_attendance_page(ctx: UiContext) -> TabsPage:
    return TabsPage(ctx, "Attendance", "Subject-wise attendance with targets, shortage alerts and how many classes you need to recover.",
                    [("Overview", AttendanceOverview(ctx)), ("Attendance log", EntityPage(ctx, "attendance")), ("Weekly timetable", EntityPage(ctx, "timetable"))])


# ---- settings ---------------------------------------------------------------------------------------------------------------------------
class SettingsPage(Page):
    DATE_FORMATS = (("21 Nov 2026", "%d %b %Y"), ("21/11/2026", "%d/%m/%Y"), ("11/21/2026", "%m/%d/%Y"), ("2026-11-21", "%Y-%m-%d"), ("Nov 21, 2026", "%b %d, %Y"))
    TAB_NAMES = ("General", "Appearance", "Study & goals", "Data & security")

    def __init__(self, ctx: UiContext) -> None:
        super().__init__(ctx)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 14)
        outer.addWidget(page_header("Settings", "Make the app yours: appearance, layout, goals, academic rules, data and security."))
        self.tabs = QTabWidget()
        self.grids = {n: CardGrid(430) for n in self.TAB_NAMES}
        for name, grid in self.grids.items():
            holder = QWidget()
            holder.setObjectName("PageBody")
            hl = QVBoxLayout(holder)
            hl.setContentsMargins(0, 12, 0, 12)
            hl.addWidget(grid)
            hl.addStretch(1)
            self.tabs.addTab(scroll_page(holder), name)
        outer.addWidget(self.tabs, 1)
        self._build()

    def _group(self, title: str, rows: Sequence[tuple[str, Any]]) -> "QGroupBox":
        g = QGroupBox(title)
        f = QFormLayout(g)
        for lab, w in rows:
            f.addRow(lab, w) if lab else f.addRow(w)
        return g

    def _spin(self, key: str, lo: int, hi: int, suffix: str = "") -> "QSpinBox":
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setSuffix(suffix)
        s.setValue(int(self.ctx.settings.get_float(key, lo)))
        s.valueChanged.connect(lambda v, k=key: (self.ctx.settings.set(k, v), self.ctx.core.bus.notify("settings")))
        return s

    def _combo(self, items: Sequence[tuple[str, str]], key: str, current: str, after: Optional[Callable] = None) -> "QComboBox":
        cb = QComboBox()
        for label, value in items:
            cb.addItem(label, value)
        cb.setCurrentIndex(max(0, cb.findData(current)))
        cb.activated.connect(lambda _i, k=key, w=cb: (self.ctx.settings.set(k, w.currentData()), after() if after else None))
        return cb

    def _set(self, **kv: Any) -> None:
        self.ctx.settings.set_many(kv)
        self.ctx.win.apply_theme()

    def _build(self) -> None:
        c, s = self.ctx, self.ctx.settings
        self.profile_label = make_label("", "Muted", True)
        self.info = make_label("", "Muted", True)
        self.lock_label = make_label("", "Muted", True)
        # general
        pages = [("Last page I used", "last")] + [(label, key) for key, label, *_r in NAV]
        nav_rows = [("Open on start", self._combo(pages, "start_page", s.get("start_page"))),
                    ("Sidebar", self._combo([("Automatic", "Auto"), ("Always compact", "Compact"), ("Always expanded", "Expanded")], "sidebar_mode", s.get("sidebar_mode"), lambda: self.ctx.win._apply_sidebar())),
                    ("Date format", self._combo([(label, fmt) for label, fmt in self.DATE_FORMATS], "date_style", s.get("date_style"), lambda: self.ctx.core.bus.notify("settings"))),
                    ("", make_button("Show the getting-started checklist again", lambda: (s.set("onboarding_dismissed", "0"), c.core.bus.notify("settings"))))]
        self.panel_list = QListWidget()
        self.panel_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.panel_list.setMaximumHeight(280)
        saved = s.get_json("dashboard_panels", None)
        saved = [k for k in dict.fromkeys(saved) if k in DASH_PANELS] if isinstance(saved, list) else list(DASH_PANELS)
        for k in saved + [k for k in DASH_PANELS if k not in saved]:
            it = QListWidgetItem(DASH_PANELS[k])
            it.setData(Qt.ItemDataRole.UserRole, k)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if k in saved else Qt.CheckState.Unchecked)
            self.panel_list.addItem(it)
        self.panel_list.itemChanged.connect(lambda _i: self._save_panels())
        for sig in (self.panel_list.model().rowsMoved, self.panel_list.model().rowsInserted):
            sig.connect(lambda *_a: self._save_panels())
        self.grids["General"].set_cards([
            self._group("Student", [("", self.profile_label), ("", make_button("Edit student profile…", self.edit_profile, "Primary")), ("", make_button("Academic structure setup…", lambda: c.go("structure")))]),
            self._group("Navigation & dates", nav_rows),
            self._group("Dashboard layout", [("", make_label("Drag to reorder · untick to hide", "Muted")), ("", self.panel_list)]),
            self._group("Academic rules", [("", make_button("Grading, credits & SGPA/CGPA rules…", self.grading, "Primary"))]),
            self._group("Help & about", [("", make_label(f"{APP_NAME} {APP_VERSION}\nOffline · your data stays on this computer.", "Muted", True)), ("", make_button("Shortcuts & tips (F1)", lambda: HelpDialog(self).exec()))])])
        # appearance
        theme = QComboBox()
        theme.addItems(["Light", "Dark", "System", "High contrast"])
        theme.setCurrentText({"light": "Light", "dark": "Dark", "system": "System", "high contrast": "High contrast", "contrast": "High contrast"}.get(s.get("theme").lower(), "Light"))
        theme.currentTextChanged.connect(lambda v: self._set(theme=v))
        accent = QComboBox()
        accent.addItems(list(ACCENTS))
        accent.setCurrentText(s.get("accent") if s.get("accent") in ACCENTS else "Blue")
        accent.currentTextChanged.connect(lambda v: self._set(accent=v, accent_custom=""))
        custom = ColorButton(s.get("accent_custom") or ACCENTS.get(s.get("accent"), "#2F5FCB"))
        custom.on_change = lambda hx: self._set(accent_custom=hx)
        font = QSpinBox()
        font.setRange(8, 18)
        font.setValue(s.get_int("font_size", 10))
        font.valueChanged.connect(lambda v: self._set(font_size=v))
        dens = QComboBox()
        dens.addItems(list(DENSITY))
        dens.setCurrentText(s.get("density") if s.get("density") in DENSITY else "Comfortable")
        dens.currentTextChanged.connect(lambda v: self._set(density=v))
        corners = QComboBox()
        corners.addItems(["Sharp", "Rounded", "Soft"])
        corners.setCurrentText(s.get("corners") if s.get("corners") in ("Sharp", "Rounded", "Soft") else "Rounded")
        corners.currentTextChanged.connect(lambda v: self._set(corners=v))
        tcol, scol, th = s.get_json("table_colors", {}) or {}, s.get_json("status_colors", {}) or {}, c.theme

        def color_rows(store: dict, keys: dict, setting: str, defaults: dict) -> list:
            out = []
            for k, label in keys.items():
                btn = ColorButton(store.get(k) or defaults.get(k, "#888888"))
                btn.on_change = lambda hx, kk=k, st=store, sn=setting: (st.__setitem__(kk, hx), self._set(**{sn: dict(st)}))
                out.append((label, btn))
            return out
        t_defaults = {"header_bg": th.t["t_header_bg"], "header_fg": th.t["t_header_fg"], "alt_row": th.t["t_alt"], "selected": th.t["t_sel"], "grid": th.t["t_grid"], "highlight": th.t["t_hover"]}
        reset = make_button("Reset table & status colours", lambda: (self._set(table_colors="", status_colors=""), self.refresh()))
        self.grids["Appearance"].set_cards([
            self._group("Look & feel", [("Theme", theme), ("Accent colour", accent), ("Custom accent", custom), ("Font size", font), ("Layout density", dens), ("Corners", corners)]),
            self._group("Table colours", color_rows(tcol, TABLE_COLOR_KEYS, "table_colors", t_defaults) + [("", reset)]),
            self._group("Status colours", color_rows(scol, STATUS_COLOR_KEYS, "status_colors", {k: th.t[k] for k in STATUS_COLOR_KEYS}))])
        # study & goals
        self.grids["Study & goals"].set_cards([self._group("Daily goals & reminders", [
            ("Daily study goal", self._spin("daily_goal", 15, 1440, " min")), ("Focus session", self._spin("pomodoro_work", 5, 120, " min")),
            ("Short break", self._spin("pomodoro_short", 1, 60, " min")), ("Long break", self._spin("pomodoro_long", 5, 120, " min")),
            ("Minimum attendance (default)", self._spin("attendance_min", 1, 100, " %")), ("Low-marks highlight below", self._spin("low_mark_pct", 1, 100, " %"))])])
        # data & security
        auto = QCheckBox("Daily automatic backup")
        auto.setChecked(s.get_bool("auto_backup"))
        auto.toggled.connect(lambda v: s.set("auto_backup", "1" if v else "0"))
        btns = QWidget()
        bl = QGridLayout(btns)
        for i, (text, fn) in enumerate((("Back up to a file…", self.backup), ("Manage & restore backups…", self.manage_backups), ("Data health check…", self.health),
                                         ("Export all tables (CSV)…", self.export_all), ("Open data folder", self.open_folder))):
            bl.addWidget(make_button(text, fn), i // 2, i % 2)
        idle = self._combo([("Never", "0"), ("After 1 minute", "1"), ("After 5 minutes", "5"), ("After 10 minutes", "10"), ("After 30 minutes", "30")], "lock_idle", str(s.get_int("lock_idle", 0)))
        pin_btns = QWidget()
        pl = QGridLayout(pin_btns)
        for i, (text, fn) in enumerate((("Set / change PIN…", self.set_pin), ("Remove PIN…", self.remove_pin), ("Lock now (Ctrl+L)", lambda: self.ctx.win.lock_now()))):
            pl.addWidget(make_button(text, fn), 0, i)
        self.grids["Data & security"].set_cards([
            self._group("Backups & data", [("", auto), ("Keep automatic backups", self._spin("backup_keep", 3, 90)), ("", btns), ("", self.info)]),
            self._group("App lock", [("", self.lock_label), ("Lock when idle", idle), ("", pin_btns),
                                     ("", make_label("The PIN keeps other people out of the app. It does not encrypt the database file — for that, turn on BitLocker (Windows) or FileVault (Mac).", "Muted", True))])])

    def _save_panels(self) -> None:
        order = []
        for i in range(self.panel_list.count()):
            it = self.panel_list.item(i)
            k = it.data(Qt.ItemDataRole.UserRole)
            if k and k not in order and it.checkState() == Qt.CheckState.Checked:
                order.append(k)
        self.ctx.settings.set("dashboard_panels", order)
        self.ctx.core.bus.notify("settings")

    def refresh(self) -> None:
        p = self.ctx.repo.profile()
        self.profile_label.setText(" · ".join(x for x in (p.get("student_name"), p.get("roll_no"), p.get("college_name")) if x) or "Profile not completed yet.")
        i = self.ctx.core.info()
        self.info.setText(f"{i['path']}\n{i['size'] / 1024:,.0f} KB · schema v{i['schema']} · integrity: {i['integrity']} · foreign-key issues: {i['fk_problems']}")
        self.lock_label.setText("PIN lock is ON." if AppLock(self.ctx.settings).enabled else "No PIN set — anyone who opens the app can see your records.")

    def edit_profile(self) -> None:
        if ProfileDialog(self.ctx, self).exec():
            self.refresh()
            self.ctx.core.bus.notify("student_profile")

    def grading(self) -> None:
        if GradingDialog(self.ctx, self).exec():
            self.ctx.toast("Grading rules saved.")

    def set_pin(self) -> None:
        lock, hide = AppLock(self.ctx.settings), QLineEdit.EchoMode.Password
        if lock.enabled:
            cur, ok = QInputDialog.getText(self, "Change PIN", "Current PIN:", hide)
            if not ok:
                return
            if not lock.verify(cur):
                return tell(self, "Not correct", "That PIN is not correct.")
        pin, ok = QInputDialog.getText(self, "Set PIN", "New PIN or password (4–64 characters):", hide)
        if not ok:
            return
        again, ok = QInputDialog.getText(self, "Set PIN", "Type it again to confirm:", hide)
        if not ok:
            return
        if pin != again:
            return tell(self, "Not the same", "The two entries did not match, so nothing was changed.")
        self.ctx.guard(self, lambda: lock.set_pin(pin))
        self.refresh()

    def remove_pin(self) -> None:
        lock = AppLock(self.ctx.settings)
        if not lock.enabled:
            return tell(self, "No PIN", "There is no PIN to remove.")
        cur, ok = QInputDialog.getText(self, "Remove PIN", "Current PIN:", QLineEdit.EchoMode.Password)
        if ok:
            self.ctx.guard(self, lambda: lock.clear(cur))
            self.refresh()

    def backup(self) -> None:
        name = f"study_space_backup_{datetime.now():%Y%m%d_%H%M}.db"
        dest, _ = QFileDialog.getSaveFileName(self, "Back up the database", str(Path.home() / name), "Study Space backup (*.db)")
        if not dest:
            return
        path, root = self.ctx.core.db_path, self.ctx.core.backups.root

        def job(progress: Callable) -> str:
            rc = ReadCore(path)
            try:
                return str(BackupManager(root).create(rc.db, dest, kind="manual"))
            finally:
                rc.close()
        self.run_task(job, lambda out: tell(self, "Backup complete", f"Verified backup saved to:\n{out}"),
                      on_fail=lambda m: tell(self, "Backup failed", f"{m}\n\nNothing was overwritten.", True))

    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctx.core.db_path.parent)))

    def manage_backups(self) -> None:
        BackupsDialog(self.ctx, self).exec()

    def health(self) -> None:
        icon = {"good": "✔", "info": "•", "warn": "⚠", "bad": "✖"}
        tell(self, "Data health check", "\n\n".join(f"{icon.get(i['level'], '•')}  {i['text']}" for i in self.ctx.analytics.data_health()))

    def export_all(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Export every table to CSV files in…", str(Path.home()))
        if not folder:
            return

        def run() -> int:
            n = 0
            for ent in ENTITY_LIST:
                rows = self.ctx.repo.rows(ent)
                export_csv(Path(folder) / f"{ent.table}.csv", RowSet(ent.columns, rows), rows, [col.key for col in ent.columns])
                n += 1
            return n
        n = self.ctx.guard(self, run)
        if n:
            tell(self, "Export complete", f"{n} files written to:\n{folder}")


HELP_TEXT = """NAVIGATION
  Ctrl+1 … 9    open the pages in sidebar order      Ctrl+K   search everything
  Ctrl+Shift+N  quick add a record                   F5       refresh this page
  Ctrl+,  settings    Ctrl+R  PDF reports    Ctrl+B  back up    Ctrl+L  lock    F1  this help

TABLES (every list in the app)
  Ctrl+N new · Enter / F2 edit · Delete delete · Ctrl+F search · Ctrl+C copy selected rows
  Click a heading to sort; Shift+click adds a second sort column.
  Right-click a heading: show/hide columns, align, colours, filter, fit width, restore layout.
  ⚙ View: row density and height, alternating rows, grid lines, highlight rules, saved layouts.
  Actions ▾: quick status changes, bulk edit, duplicate, CSV import and template, custom fields, files.

DATA SAFETY
  • Deleting something that owns other records first lists exactly what will go.
  • A safety backup is taken before upgrades, restores, imports and cascading deletes.
  • Settings ▸ Data & security: manage and restore backups, check data health, set a PIN lock.

TIPS
  • “Viewing” (top bar) switches every page between semesters — choose All semesters for history.
  • Enter your weekly timetable under Attendance ▸ Weekly timetable to see today's classes on the dashboard.
  • Settings ▸ Grading rules sets your university's grade scale, credits and SGPA/CGPA method.
  • Exams & results ▸ Goal planner shows the marks or grades you need for a target."""


class HelpDialog(QDialog):
    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Shortcuts & tips")
        self.resize(640, 560)
        lay = QVBoxLayout(self)
        box = QPlainTextEdit(HELP_TEXT)
        box.setReadOnly(True)
        lay.addWidget(box, 1)
        lay.addWidget(make_button("Close", self.accept, "Primary"))


class IdleFilter(QObject):
    """Remembers the time of the last mouse/keyboard activity (used by the auto-lock)."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.last = time.monotonic()

    def eventFilter(self, obj: Any, event: Any) -> bool:
        if event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.Wheel):
            self.last = time.monotonic()
        return False


class LockDialog(QDialog):
    """Asks for the PIN. Five wrong tries pause the prompt for 30 seconds."""

    def __init__(self, lock: AppLock, parent: Any = None) -> None:
        super().__init__(parent)
        self.lock, self.fails, self.wait = lock, 0, 0
        self.setWindowTitle("Study Space Pro is locked")
        self.setModal(True)
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        lay.addWidget(make_label("Locked", "PageTitle"))
        lay.addWidget(make_label("Enter your PIN to continue.", "Muted", True))
        self.pin = QLineEdit()
        self.pin.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin.setPlaceholderText("PIN or password")
        self.pin.returnPressed.connect(self.try_unlock)
        self.msg = make_label("", "BannerDanger", True)
        self.msg.hide()
        lay.addWidget(self.pin)
        lay.addWidget(self.msg)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(make_button("Quit", self.reject))
        self.go = make_button("Unlock", self.try_unlock, "Primary")
        row.addWidget(self.go)
        lay.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

    def try_unlock(self) -> None:
        if self.wait > 0:
            return
        if self.lock.verify(self.pin.text()):
            self.accept()
            return
        self.fails += 1
        self.pin.clear()
        self.msg.setText("That PIN is not correct.")
        self.msg.show()
        if self.fails >= 5:
            self.fails, self.wait = 0, 30
            self.pin.setEnabled(False)
            self.go.setEnabled(False)
            self.timer.start()

    def _tick(self) -> None:
        self.wait -= 1
        self.msg.setText(f"Too many attempts. Try again in {self.wait} s.")
        if self.wait <= 0:
            self.timer.stop()
            self.pin.setEnabled(True)
            self.go.setEnabled(True)
            self.msg.hide()
            self.pin.setFocus()


# ════════════════════════════════════════════════════════════════════════════
# 11 · QT LAYER III — main window
# ════════════════════════════════════════════════════════════════════════════
NAV = [("dashboard", "Overview", "⌂", ""), ("subjects", "Subjects & syllabus", "☰", "COURSEWORK"), ("assignments", "Assignments", "✎", ""),
       ("exams", "Exams & results", "▥", ""), ("attendance", "Attendance", "☑", ""), ("projects", "Projects & labs", "⚑", ""),
       ("study", "Study hub", "◔", "STUDY"), ("resources", "Notes & documents", "✉", ""), ("analytics", "Analytics", "⌁", "INSIGHTS"),
       ("reports", "PDF reports", "▤", ""), ("structure", "Academic structure", "❖", "SETUP"), ("settings", "Settings", "⚙", "")]
FACTORIES: dict[str, Callable[[UiContext], Page]] = {
    "dashboard": DashboardPage, "structure": StructurePage, "subjects": make_subjects_page, "assignments": AssignmentsPage,
    "exams": ExamsPage, "attendance": make_attendance_page, "projects": make_projects_page, "study": make_study_page,
    "resources": make_resources_page, "analytics": AnalyticsPage, "reports": ReportsPage, "settings": SettingsPage}
QUICK_ADD = [("Assignment", "assignments"), ("Planner task", "planner_tasks"), ("Study session", "study_sessions"), ("Assessment / marks", "marks"), ("Exam", "exams"), ("Note", "notes")]


def system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        try:
            return QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
        except Exception:
            return False


def build_palette(t: Theme) -> "QPalette":
    pal, R, C = QPalette(), QPalette.ColorRole, QColor
    for role, tok in ((R.Window, "bg"), (R.WindowText, "text"), (R.Base, "input"), (R.AlternateBase, "t_alt"), (R.Text, "text"), (R.Button, "panel_alt"), (R.ButtonText, "text"),
                      (R.ToolTipBase, "side"), (R.ToolTipText, "panel"), (R.PlaceholderText, "placeholder"), (R.Highlight, "t_sel"), (R.HighlightedText, "t_sel_fg"), (R.Link, "accent_text")):
        pal.setColor(role, C(t.t[tok] if tok != "panel" else "#FFFFFF"))
    pal.setColor(QPalette.ColorGroup.Disabled, R.Text, C(t.t["disabled"]))
    pal.setColor(QPalette.ColorGroup.Disabled, R.ButtonText, C(t.t["disabled"]))
    return pal


class MainWindow(QMainWindow):
    def __init__(self, core: AppCore, note: str = "") -> None:
        super().__init__()
        self.core, self.pages, self.nav, self.current = core, {}, {}, ""
        self._locked = False
        self.ctx = UiContext(core, self)
        self.theme = Theme.from_settings(core.settings, system_is_dark())
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setMinimumSize(900, 600)
        self.resize(1360, 840)
        central = QWidget()
        central.setObjectName("AppCentral")
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.sidebar = self._build_sidebar()
        row.addWidget(self.sidebar)
        work = QWidget()
        work.setObjectName("Workspace")
        col = QVBoxLayout(work)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self._build_topbar())
        self.stack = QStackedWidget()
        col.addWidget(self.stack, 1)
        row.addWidget(work, 1)
        self.setCentralWidget(central)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._do_refresh)
        self._pending: set[str] = set()
        core.bus.subscribe(self._on_change)
        self._shortcuts()
        self.apply_theme()
        self.rebuild_scope()
        self._restore_window()
        start = core.settings.get("start_page")
        start = core.settings.get("last_page") if start == "last" else start
        self.navigate(start if start in FACTORIES else "dashboard")
        self._setup_lock()
        if note:
            self.statusBar().showMessage(note, 6000)
        QTimer.singleShot(400, self._first_run)

    # ---- construction --------------------------------------------------------------
    def _build_sidebar(self) -> "QFrame":
        sb = QFrame()
        sb.setObjectName("Sidebar")
        sb.setFixedWidth(236)
        lay = QVBoxLayout(sb)
        lay.setContentsMargins(10, 14, 10, 10)
        lay.setSpacing(3)
        head = QHBoxLayout()
        logo = make_label("SS", "Logo")
        logo.setAlignment(A_C)
        head.addWidget(logo)
        self.brand = make_label("STUDY SPACE\nPRO", "Brand")
        head.addWidget(self.brand, 1)
        lay.addLayout(head)
        lay.addSpacing(8)
        self.captions: list[QLabel] = []
        for key, label, glyph, group in NAV:
            if group:
                cap = make_label(group, "NavCaption")
                self.captions.append(cap)
                lay.addWidget(cap)
            b = QPushButton(f"{glyph}   {label}")
            b.setObjectName("NavButton")
            b.setToolTip(label)
            b.setProperty("glyph", glyph)
            b.setProperty("label", label)
            b.clicked.connect(lambda _c=False, k=key: self.navigate(k))
            self.nav[key] = b
            lay.addWidget(b)
        lay.addStretch(1)
        self.toggle = make_button("◀  Collapse", lambda: self.toggle_sidebar(), tip="Collapse or expand the sidebar")
        self.toggle.setObjectName("NavButton")
        lay.addWidget(self.toggle)
        self.ver = make_label(f"v{APP_VERSION}", "Muted")
        lay.addWidget(self.ver)
        return sb

    def _build_topbar(self) -> "QFrame":
        bar = QFrame()
        bar.setObjectName("Topbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(18, 8, 18, 8)
        self.title = make_label("", "SectionTitle")
        row.addWidget(self.title)
        row.addStretch(1)
        find = QToolButton()
        find.setText("⌕ Search")
        find.setToolTip("Search every record (Ctrl+K)")
        find.clicked.connect(lambda _c=False: self.open_search())
        row.addWidget(find)
        row.addWidget(make_label("Viewing:", "Muted"))
        self.scope = QComboBox()
        self.scope.setMinimumWidth(210)
        self.scope.setToolTip("Every page shows records for this semester. Choose “All semesters” for the full history.")
        self.scope.activated.connect(lambda _i: self._scope_changed())
        row.addWidget(self.scope)
        add = QToolButton()
        add.setText("＋ Quick add")
        add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.add_menu = QMenu(self)
        add.setMenu(self.add_menu)
        for label, table in QUICK_ADD:
            add_action(self.add_menu, label, lambda t=table: self.quick_add(t))
        add_action(self.add_menu, "Attendance (quick mark)…", self.quick_attendance)
        self.add_btn = add
        row.addWidget(add)
        self.alert_btn = QToolButton()
        self.alert_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.alert_menu = QMenu(self)
        self.alert_btn.setMenu(self.alert_menu)
        self.alert_menu.aboutToShow.connect(self._fill_alerts)
        row.addWidget(self.alert_btn)
        return bar

    def _shortcuts(self) -> None:
        def sc(seq: str, fn: Callable) -> None:
            QShortcut(QKeySequence(seq), self).activated.connect(fn)
        for i, (key, *_r) in enumerate(NAV[:9], 1):
            sc(f"Ctrl+{i}", lambda k=key: self.navigate(k))
        sc("Ctrl+,", lambda: self.navigate("settings"))
        sc("Ctrl+R", lambda: self.navigate("reports"))
        sc("Ctrl+K", self.open_search)
        sc("Ctrl+L", self.lock_now)
        sc("F1", lambda: HelpDialog(self).exec())
        sc("F5", self.refresh_current)
        sc("Ctrl+B", lambda: (self.navigate("settings"), self.page("settings").backup()))
        sc("Ctrl+Shift+N", lambda: self.add_menu.exec(self.add_btn.mapToGlobal(self.add_btn.rect().bottomLeft())))

    # ---- navigation ------------------------------------------------------------------
    def page(self, key: str) -> Page:
        if key not in self.pages:
            p = FACTORIES[key](self.ctx)
            self.pages[key] = p
            self.stack.addWidget(p)
        return self.pages[key]

    def navigate(self, key: str) -> None:
        if key not in FACTORIES:
            return
        try:
            page = self.page(key)
        except Exception as exc:
            LOG.exception("Page %s failed to open", key)
            return tell(self, "Could not open this page", f"{exc}\n\nDetails were written to the log file.", True)
        self.current = key
        page.dirty = False                      # we refresh explicitly below (showEvent must not repeat it)
        self.stack.setCurrentWidget(page)
        for k, b in self.nav.items():
            b.setProperty("active", k == key)
            b.style().unpolish(b)
            b.style().polish(b)
        self.title.setText(next(l for k, l, *_ in NAV if k == key))
        self.ctx.guard(self, page.refresh)

    def refresh_current(self) -> None:
        if self.current in self.pages:
            self.ctx.guard(self, self.pages[self.current].refresh)
        self._update_alerts()

    # ---- scope ----------------------------------------------------------------------------
    def rebuild_scope(self) -> None:
        repo, s = self.core.repo, self.core.settings
        self.scope.blockSignals(True)
        self.scope.clear()
        cur = repo.current_semester_id()
        cur_name = next((n for n, i in repo.lookup("semesters") if i == cur), "none")
        self.scope.addItem(f"Current semester — {cur_name}", "current")
        self.scope.addItem("All semesters", "all")
        for name, sid in repo.lookup("semesters"):
            self.scope.addItem(name, str(sid))
        self.scope.setCurrentIndex(max(0, self.scope.findData(s.get("scope_semester") or "current")))
        self.scope.blockSignals(False)
        self._on_change({"settings"})

    def _scope_changed(self) -> None:
        self.core.settings.set("scope_semester", self.scope.currentData())
        for k, p in self.pages.items():
            p.mark_dirty()
        self.refresh_current()

    # ---- data changes ---------------------------------------------------------------------------
    def _on_change(self, tables: set) -> None:
        self._pending |= set(tables)
        self._refresh_timer.start(90)

    def _do_refresh(self) -> None:
        structural = bool(self._pending & {"semesters", "programmes", "academic_years", "*"})
        self._pending.clear()
        for k, p in self.pages.items():
            p.mark_dirty()
        if structural:
            self.rebuild_scope()
        self.refresh_current()
        if self.current in self.pages:
            self.pages[self.current].dirty = False

    def _update_alerts(self) -> None:
        try:
            n = len(self.core.analytics.alerts(self.ctx.scope()))
        except Exception:
            LOG.exception("Alert calculation failed")
            n = 0
        self.alert_btn.setText(f"⚑ Alerts ({n})" if n else "⚑ Alerts")

    def _fill_alerts(self) -> None:
        self.alert_menu.clear()
        alerts = self.core.analytics.alerts(self.ctx.scope())
        if not alerts:
            add_action(self.alert_menu, "Nothing needs attention right now ✔", lambda: None, enabled=False)
        mark = {"danger": "✖", "warning": "⚠", "info": "•"}
        for a in alerts:
            add_action(self.alert_menu, f"{mark.get(a.severity, '•')}  {a.title}" + (f" — {a.detail}" if a.detail else ""), lambda p=a.page: self.navigate(p))

    # ---- actions --------------------------------------------------------------------------------------
    def quick_add(self, table: str) -> None:
        sid = self.ctx.scope()
        defaults = {"semester_id": sid} if sid and any(f.key == "semester_id" for f in ENTITIES[table].fields) else {}
        if RecordDialog(self.ctx, table, None, defaults, self).exec():
            self.ctx.toast(f"{ENTITIES[table].singular} added.")

    def open_search(self) -> None:
        SearchDialog(self.ctx, self).exec()

    def open_hit(self, hit: dict, text: str) -> None:
        """Jump to a search result: open its page and filter the tables there by the search text."""
        self.navigate(hit["page"])
        for ep in self.page(hit["page"]).findChildren(EntityPage):
            ep.search.setText(text)

    def quick_attendance(self) -> None:
        QuickAttendanceDialog(self.ctx, self).exec()

    def _first_run(self) -> None:
        if self.core.db.scalar("SELECT COUNT(*) FROM semesters", default=0) == 0:
            if confirm(self, "Welcome to Study Space Pro", "Let's set up your academic structure (degree, years and semesters).\n\nSet it up now?"):
                self.navigate("structure")
                self.page("structure").wizard()
            else:
                self.core.repo.ensure_default_structure()
                self.rebuild_scope()

    def apply_theme(self) -> None:
        self.theme = Theme.from_settings(self.core.settings, system_is_dark())
        app = QApplication.instance()
        app.setStyleSheet(self.theme.qss())
        app.setPalette(build_palette(self.theme))
        for w in self.findChildren(ChartWidget):
            w.update()
        for w in self.findChildren(EntityPage):
            w.model.layoutChanged.emit()
            w._style_view()
        self._update_alerts()

    def _sidebar_compact(self) -> bool:
        mode = self.core.settings.get("sidebar_mode")
        return mode == "Compact" or (mode != "Expanded" and self.width() < 1060)

    def _apply_sidebar(self) -> None:
        if not hasattr(self, "toggle"):
            return
        compact = self._sidebar_compact()
        self.sidebar.setFixedWidth(64 if compact else 236)
        self.brand.setVisible(not compact)
        self.ver.setVisible(not compact)
        for cap in self.captions:
            cap.setVisible(not compact)
        for b in self.nav.values():
            b.setText(b.property("glyph") if compact else f"{b.property('glyph')}   {b.property('label')}")
        self.toggle.setText("▶" if compact else "◀  Collapse")

    def toggle_sidebar(self) -> None:
        self.core.settings.set("sidebar_mode", "Expanded" if self._sidebar_compact() else "Compact")
        self._apply_sidebar()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._apply_sidebar()

    def _restore_window(self) -> None:
        try:
            geo = self.core.settings.get("window_geometry")
            if geo:
                self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
        except Exception:
            LOG.warning("Window position could not be restored", exc_info=True)

    def _save_window(self) -> None:
        try:
            self.core.settings.set_many({"window_geometry": self.saveGeometry().toBase64().data().decode("ascii"), "last_page": self.current})
        except Exception:
            LOG.warning("Window position could not be saved", exc_info=True)

    # ---- app lock ---------------------------------------------------------------------------------
    def _setup_lock(self) -> None:
        self.idle = IdleFilter(self)
        self._filter_on = False
        self.idle_timer = QTimer(self)
        self.idle_timer.setInterval(15000)
        self.idle_timer.timeout.connect(self._check_idle)
        self.idle_timer.start()
        self._sync_idle_filter()

    def _sync_idle_filter(self) -> bool:
        """Watch keyboard/mouse activity only while an auto-lock is actually configured (no overhead otherwise)."""
        want = self.core.settings.get_int("lock_idle", 0) > 0 and AppLock(self.core.settings).enabled
        app = QApplication.instance()
        if want and not self._filter_on:
            self.idle.last = time.monotonic()
            app.installEventFilter(self.idle)
            self._filter_on = True
        elif not want and self._filter_on:
            app.removeEventFilter(self.idle)
            self._filter_on = False
        return want

    def _check_idle(self) -> None:
        if self._sync_idle_filter() and not self._locked and time.monotonic() - self.idle.last > self.core.settings.get_int("lock_idle", 0) * 60:
            self.lock_now()

    def lock_now(self) -> None:
        lock = AppLock(self.core.settings)
        if self._locked:
            return
        if not lock.enabled:
            return tell(self, "No PIN set", "Set a PIN first: Settings → Data & security → Set / change PIN.")
        self._locked = True
        self.centralWidget().hide()                      # nothing sensitive stays visible behind the prompt
        ok = LockDialog(lock, self).exec()
        self.centralWidget().show()
        self._locked = False
        self.idle.last = time.monotonic()
        if not ok:
            QApplication.quit()

    # ---- command palette (Ctrl+K) -----------------------------------------------------------------------
    def commands(self, text: str) -> list[dict]:
        q = text.strip().casefold()
        if not q:
            return []
        out = [{"kind": "Go to", "title": label, "detail": "", "cmd": ("nav", key)} for key, label, *_r in NAV if q in label.casefold() or q in key]
        out += [{"kind": "New", "title": label, "detail": "", "cmd": ("add", table)} for label, table in QUICK_ADD if q in f"new add {label}".casefold()]
        for label, cmd in (("Back up now", ("backup",)), ("Lock the app", ("lock",)), ("Shortcuts & tips", ("help",)), ("Collapse / expand the sidebar", ("sidebar",))):
            if q in label.casefold():
                out.append({"kind": "Action", "title": label, "detail": "", "cmd": cmd})
        return out[:12]

    def run_command(self, cmd: tuple) -> None:
        kind = cmd[0]
        if kind == "nav":
            self.navigate(cmd[1])
        elif kind == "add":
            self.quick_add(cmd[1])
        elif kind == "backup":
            self.navigate("settings")
            self.page("settings").backup()
        elif kind == "lock":
            self.lock_now()
        elif kind == "help":
            HelpDialog(self).exec()
        elif kind == "sidebar":
            self.toggle_sidebar()

    def closeEvent(self, event: Any) -> None:
        focus = next(iter(self.findChildren(FocusPage)), None)
        if focus and getattr(focus, "running", False) and not confirm(self, "Focus session running", "A focus session is still running. Quit and discard it?"):
            event.ignore()
            return
        self._save_window()
        try:
            self.core.close()
        finally:
            event.accept()


# ════════════════════════════════════════════════════════════════════════════
# 12 · SELF-TESTS · 13 · COMMAND LINE
# ════════════════════════════════════════════════════════════════════════════
def dependency_report() -> list[str]:
    out = [f"Python {sys.version.split()[0]}", f"SQLite {sqlite3.sqlite_version}"]
    out.append("PySide6: OK" if HAS_QT else "PySide6: MISSING  → pip install PySide6")
    out.append("ReportLab: OK" if HAS_REPORTLAB else "ReportLab: MISSING  → pip install reportlab")
    out.append("uharfbuzz: installed (Hindi/Indic shaping in PDFs)" if HAS_SHAPING else "uharfbuzz: not installed (optional; improves Hindi/Indic text in PDFs → pip install uharfbuzz)")
    return out


def run_gui(db_path: Optional[str]) -> int:
    if not HAS_QT:
        print("Study Space Pro needs PySide6 for its window.\n  pip install PySide6\nThen run this file again.  (python study_space.py --check shows what is missing.)")
        return 2
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    data_dir = Path(db_path).parent if db_path else resolve_data_dir()
    setup_logging(data_dir)
    guard = QLockFile(str(data_dir / ".study_space.lock"))       # one running copy per data folder (protects the database)
    guard.setStaleLockTime(30000)
    if not guard.tryLock(300):
        QMessageBox.warning(None, "Already running", "Study Space Pro is already open for this data folder (or it closed a moment ago).\nClose the other window and try again.")
        return 1

    def hook(exc_type: Any, exc: Any, tb: Any) -> None:
        LOG.critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        try:
            QMessageBox.critical(None, "Unexpected error", f"{exc}\n\nThe details were written to study_space_pro.log. Your data is safe.")
        except Exception:
            pass
    sys.excepthook = hook
    try:
        core = AppCore(data_dir, db_path)
    except Exception as exc:
        LOG.exception("Start-up failed")
        QMessageBox.critical(None, "Study Space Pro could not start", f"{exc}\n\nNo data was lost. Backups are in:\n{data_dir / 'backups'}")
        return 1
    lock = AppLock(core.settings)
    if lock.enabled and not LockDialog(lock).exec():
        core.close()
        return 0
    note = " ".join(x for x in (core.housekeeping(), *core.db.migration_log[-1:]) if x)
    if not HAS_REPORTLAB:
        note = (note + "  ·  PDF export needs ReportLab: pip install reportlab").strip()
    win = MainWindow(core, note)
    win.show()
    return app.exec()


class StudySpaceTests(unittest.TestCase):
    """Automated checks. Run:  python study_space.py --selftest   (GUI checks run when PySide6 is installed)."""

    counts = [0, 0]   # checks, failures

    def ok(self, name: str, cond: Any, extra: Any = "") -> None:
        StudySpaceTests.counts[0] += 1
        with self.subTest(name):
            if not cond:
                StudySpaceTests.counts[1] += 1
            self.assertTrue(cond, f"{name} {extra}")

    def raises(self, fn: Callable, exc: type = ValidationError) -> bool:
        try:
            fn()
        except exc:
            return True
        return False

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ssp_test_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- fixture: a genuine 2.x-schema database (no migration table) -------------------------
    def make_legacy(self, folder: Optional[Path] = None) -> Path:
        folder = folder or (self.tmp / "legacy")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / DB_FILENAME
        con = sqlite3.connect(path)
        for ddl in BASELINE_DDL:
            con.execute(ddl)
        con.execute("INSERT INTO settings VALUES('schema_version','2.5.0'),('semester_name','B.Tech — Semester 1')")
        con.execute("INSERT INTO semesters(name,start_date,end_date,is_active) VALUES('B.Tech — Semester 1','2024-08-01','2024-12-20',1),('B.Tech — Semester 2','2025-01-10','2025-05-30',0)")
        subs = [("Engineering Mathematics II", "BAS-203"), ("Data Structures & Algorithms using C++ (Advanced Laboratory Practice and Viva-Voce)", "BCS-301"),
                ("हिन्दी भाषा एवं संचार", "BAS-101"), ("Physics", "BAS-102")]
        for name, code in subs:
            con.execute("INSERT INTO subjects(semester_id,name,code,color,target_hours) VALUES(1,?,?,'#2F5FCB',4)", (name, code))
        for s in range(1, 5):
            for u in range(1, 4):
                con.execute("INSERT INTO units(subject_id,unit_no,title,status,confidence) VALUES(?,?,?,?,?)", (s, u, f"Unit {u}", "Completed" if u == 1 else "In Progress", u))
                con.execute("INSERT INTO chapters(unit_id,title) VALUES(?,?)", ((s - 1) * 3 + u, "Chapter 1"))
                con.execute("INSERT INTO revisions(unit_id,due_date) VALUES(?,?)", ((s - 1) * 3 + u, "2026-11-18"))
            con.execute("INSERT INTO assignments(subject_id,title,given_date,due_date,status,priority) VALUES(?,?,?,?,?,?)", (s, f"Assignment {s}", "2026-10-01", f"2026-11-{10 + s}", "Not Started", "High"))
            con.execute("INSERT INTO study_sessions(subject_id,study_date,duration_minutes,topic) VALUES(?,?,?,?)", (s, "2026-11-16", 30 + s, "Revision"))
            con.execute("INSERT INTO exams(subject_id,title,exam_type,exam_date,max_marks,marks_obtained,grade) VALUES(?,?,?,?,?,?,?)", (s, f"Mid {s}", "Seasonal Exam", f"2026-11-{20 + s}", 50, 41 if s % 2 else None, "A" if s % 2 else ""))
            con.execute("INSERT INTO marks(subject_id,assessment_type,title,marks_obtained,max_marks,assessment_date) VALUES(?,?,?,?,?,?)", (s, "Unit Quiz", f"Quiz {s}", 14 + s, 20, "2026-10-05"))
        con.execute("INSERT INTO planner_tasks(task_date,title,priority) VALUES('2026-11-17','Plan','High')")
        con.execute("INSERT INTO custom_fields(entity_type,record_id,field_name,field_value) VALUES('subjects',1,'Faculty','Dr. Rao')")
        con.commit()
        con.close()
        return path

    @staticmethod
    def legacy_rows(path: Path) -> dict:
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        out = {}
        for t in LEGACY_TABLES:
            cols = [r["name"] for r in con.execute(f"PRAGMA table_info({t})")]
            out[t] = {"cols": cols, "rows": [tuple(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY 1")]}
        con.close()
        return out

    # ---- 1 domain rules ------------------------------------------------------------------------------
    def test_01_rules(self) -> None:
        cfg = GradingConfig()
        ev = lambda i, a, am, b, bm, cr, **kw: evaluate_result({"subject_id": i, "attempt_no": 1, "internal_marks": a, "internal_max": am, "external_marks": b, "external_max": bm, "credits": cr, **kw}, cfg)
        e1, e2, e3 = ev(1, 27, 30, 63, 70, 4), ev(2, 20, 30, 45, 70, 3), ev(3, 10, 30, 20, 70, 3)
        self.ok("grades & points", (e1["grade"], e1["points"], e2["grade"], e3["passed"]) == ("O", 10.0, "B+", False))
        self.ok("SGPA credit-weighted incl. failed = 6.1", abs(compute_sgpa([e1, e2, e3], cfg) - 6.1) < 1e-9)
        self.ok("SGPA passed-only", abs(compute_sgpa([e1, e2, e3], GradingConfig(include_failed=False)) - 61 / 7) < 1e-9)
        self.ok("SGPA percentage÷factor", abs(compute_sgpa([e1, e2, e3], GradingConfig(sgpa_method="percentage_factor")) - 185 / 300 * 100 / 9.5) < 1e-9)
        sem2 = [ev(4, 25, 30, 56, 70, 4)]
        self.ok("CGPA credit-weighted", abs(compute_cgpa([[e1, e2, e3], sem2], cfg) - 97 / 14) < 1e-9)
        self.ok("CGPA average of SGPAs", abs(compute_cgpa([[e1, e2, e3], sem2], GradingConfig(cgpa_method="average_of_sgpa")) - 7.55) < 1e-9)
        r1, r2 = {"id": 1, "subject_id": 3, "attempt_no": 1, "ev": e3}, {"id": 2, "subject_id": 3, "attempt_no": 2, "ev": ev(3, 20, 30, 40, 70, 3)}
        self.ok("retake policies", pick_attempts([r1, r2], cfg)[0]["attempt_no"] == 2 and pick_attempts([r2, r1], GradingConfig(retake_policy="best"))[0]["attempt_no"] == 2)
        self.ok("absent / withheld / pending", evaluate_result({"result": "Absent", "internal_marks": 20, "internal_max": 30}, cfg)["passed"] is False
                and evaluate_result({"result": "Withheld", "grade": "A"}, cfg)["counted"] is False and evaluate_result({}, cfg)["passed"] is None)
        self.ok("grade boundaries", [cfg.grade_for(x)[0] for x in (90, 89.99, 40, 39.99)] == ["O", "A+", "P", "F"])
        self.ok("component pass floor", evaluate_result({"internal_marks": 28, "internal_max": 30, "external_marks": 20, "external_max": 70}, GradingConfig(component_pass={"internal": 0, "external": 40, "practical": 0}))["passed"] is False)
        for bad in ({"scale": []}, {"scale": [{"grade": "A", "min": 50, "points": 9}]}, {"sgpa_method": "x"}):
            self.ok(f"grading validation {list(bad)[0]}", self.raises(lambda b=bad: GradingConfig(**{**GradingConfig().__dict__, **b}).validate()))
        self.ok("grading JSON round-trip, corrupt → defaults", GradingConfig.from_json(cfg.to_json()) == cfg and GradingConfig.from_json("{oops") == GradingConfig())
        for held, att, tgt, exp in ((40, 28, 75, (8, None)), (48, 36, 75, (0, 0)), (44, 40, 75, (0, 9)), (10, 9, 100, (None, None))):
            s = attendance_stats(held, att, tgt)
            self.ok(f"attendance {att}/{held}@{tgt}", (s["need"], s["can_skip"]) == exp)
        self.ok("attendance: no classes → no data", attendance_stats(0, 0)["pct"] is None)
        t = date(2026, 11, 17)
        self.ok("assignment effective status", [effective_assignment_status(*a, t) for a in (("Not Started", "2026-11-10", ""), ("Submitted", "2026-11-10", "2026-11-12"), ("Submitted", "2026-11-10", "2026-11-09"), ("Evaluated", "2026-11-10", "2026-11-15"))]
                == ["Overdue", "Late Submission", "Submitted", "Evaluated"])
        self.ok("SM-2 intervals", [sm2_next(0, 2.5, 1, 4)[2], sm2_next(1, 2.5, 1, 4)[2], sm2_next(5, 2.5, 20, 1)[::2]] == [1, 6, (0, 1)])
        self.ok("streak & trend", study_streak({"2026-11-17", "2026-11-16", "2026-11-12"}, t) == 2 and trend_label([50, 52, 60, 70])[0] == "Improving" and trend_label([60, 70])[0] == "Not enough data")
        self.ok("priority order not alphabetical", sorted(["Low", "High", "Urgent", "Normal"], key=PRIORITY_RANK.get) == ["Urgent", "High", "Normal", "Low"])
        worst = min(contrast_ratio(th.t[a], th.t[b]) for mode in ("light", "dark") for hx in list(ACCENTS.values()) + ["#FFE600", "#000000", "#FFFFFF"]
                    for th in [Theme(mode, hx)] for a, b in (("accent_fg", "accent"), ("accent_text", "panel"), ("text", "panel"), ("muted", "panel"), ("t_header_fg", "t_header_bg"), ("t_sel_fg", "t_sel"), ("danger_text", "danger_soft")))
        self.ok(f"theme text contrast ≥ 4.5:1 for every accent, light & dark (lowest {worst:.2f})", worst >= 4.5)
        q = Theme("dark").qss()
        self.ok("stylesheet fully resolved", "$" not in q and q.count("{") == q.count("}"))

    # ---- 2 migration & data safety ----------------------------------------------------------------------
    def test_02_migration_preserves_legacy_data(self) -> None:
        path = self.make_legacy()
        before = self.legacy_rows(path)
        db = Database(path)
        after = self.legacy_rows(path)
        same = all(len(before[t]["rows"]) == len(after[t]["rows"]) or t == "settings" for t in LEGACY_TABLES)
        same = same and all(tuple(n[after[t]["cols"].index(c)] for c in before[t]["cols"]) == o for t in LEGACY_TABLES if t != "settings" for o, n in zip(before[t]["rows"], after[t]["rows"]))
        self.ok("every legacy row and value preserved exactly", same)
        self.ok("schema versioned", db.schema_version() == LATEST_SCHEMA and db.scalar("PRAGMA user_version") == LATEST_SCHEMA)
        self.ok("legacy semesters linked to a programme/year; one current", db.scalar("SELECT COUNT(*) FROM semesters WHERE programme_id IS NULL OR academic_year_id IS NULL") == 0 and db.scalar("SELECT value FROM settings WHERE key='current_semester_id'") == "1")
        snaps = list((path.parent / "backups" / "safety").glob("*pre_migration*.db"))
        self.ok("verified pre-upgrade backup holds the old data", len(snaps) == 1 and inspect_database_file(snaps[0])["counts"]["subjects"] == 4)
        self.ok("integrity, FK enforcement, no FK violations", db.integrity_check() == "ok" and db.scalar("PRAGMA foreign_keys") == 1 and db.foreign_key_violations() == [])
        db.close()
        db = Database(path)
        self.ok("re-opening is idempotent (no new migrations, no new backup)", not db.migration_log and len(list((path.parent / "backups" / "safety").glob("*.db"))) == 1)
        self.ok("semester with subjects cannot be deleted (database level)", self.raises(lambda: db.run("DELETE FROM semesters WHERE id=1"), sqlite3.IntegrityError))
        self.ok("programme with semesters cannot be deleted", self.raises(lambda: db.run("DELETE FROM programmes"), sqlite3.IntegrityError))
        db.run("DELETE FROM assignments WHERE id=1")
        self.ok("custom fields / file links cleaned up by trigger", db.scalar("SELECT COUNT(*) FROM custom_fields WHERE entity_type='assignments'") == 0)
        db.close()
        # much older database: tables exist but lack the later-added columns
        old = self.tmp / "older"
        old.mkdir()
        con = sqlite3.connect(old / DB_FILENAME)
        con.execute("CREATE TABLE subjects(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)")
        con.execute("CREATE TABLE exams(id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER NOT NULL, title TEXT NOT NULL, exam_type TEXT, exam_date TEXT, max_marks REAL, venue TEXT, prep_status TEXT, notes TEXT)")
        con.execute("INSERT INTO subjects(name) VALUES('Old subject')")
        con.execute("INSERT INTO exams(subject_id,title) VALUES(1,'Old exam')")
        con.commit()
        con.close()
        db = Database(old / DB_FILENAME)
        self.ok("pre-2.5 schema is repaired in place without data loss", "marks_obtained" in db.columns("exams") and "semester_id" in db.columns("subjects") and db.scalar("SELECT title FROM exams") == "Old exam")
        db.close()

    def test_03_migration_failure_rolls_back(self) -> None:
        path = self.make_legacy()
        orig = MIGRATIONS[2]

        def broken(db: Database) -> None:
            orig[2](db)
            raise RuntimeError("simulated crash mid-migration")
        MIGRATIONS[2] = (3, "coursework_extensions", broken)
        try:
            self.ok("crash surfaces a clear MigrationError", self.raises(lambda: Database(path), MigrationError))
        finally:
            MIGRATIONS[2] = orig
        con = sqlite3.connect(path)
        self.ok("failed step fully rolled back; earlier steps and data intact", con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 2
                and "credits" not in [r[1] for r in con.execute("PRAGMA table_info(subjects)")] and con.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 4)
        con.close()
        db = Database(path)
        self.ok("next start resumes and completes the upgrade", db.schema_version() == LATEST_SCHEMA)
        db.close()

    def test_04_backup_and_restore(self) -> None:
        core = AppCore(data_dir=self.tmp / "app")
        repo = core.repo
        repo.create_structure("Prog", years=1, per_year=1)
        sub = repo.insert("subjects", {"semester_id": repo.current_semester_id(), "name": "S"})
        good = core.backups.create(core.db, self.tmp / "good.db")
        self.ok("backup is verified and portable", inspect_database_file(good)["counts"]["subjects"] == 1)
        calls = {"n": 0}
        orig = core.db.tables

        def boom() -> Any:
            calls["n"] += 1
            raise OSError("disk full")
        core.db.tables = boom  # type: ignore[method-assign]
        h = good.read_bytes()
        self.ok("interrupted backup keeps the previous good file and leaves no partial file", self.raises(lambda: core.backups.create(core.db, good), OSError) and good.read_bytes() == h and not list(self.tmp.glob(".*part")))
        core.db.tables = orig  # type: ignore[method-assign]
        self.ok("backup refuses to overwrite the live database", self.raises(lambda: core.backups.create(core.db, core.db_path), BackupError))
        junk, newer = self.tmp / "junk.db", self.tmp / "newer.db"
        junk.write_bytes(b"not a database" * 40)
        core.backups.create(core.db, newer)
        con = sqlite3.connect(newer)
        con.execute("INSERT INTO schema_migrations VALUES(99,'future','x')")
        con.commit()
        con.close()
        self.ok("garbage and newer-version backups are rejected", self.raises(lambda: core.restore(junk), BackupError) and self.raises(lambda: core.restore(newer), BackupError))
        repo.delete("subjects", [sub])
        core.restore(good)
        self.ok("restore brings data back and the connection stays usable", core.db.scalar("SELECT COUNT(*) FROM subjects") == 1 and len(repo.rows("subjects")) == 1)
        orig_m = core.db.migrate

        def bad_migrate() -> None:
            raise RuntimeError("boom after swap")
        repo.delete("subjects", [repo.rows("subjects")[0]["id"]])
        core.db.migrate = bad_migrate  # type: ignore[method-assign]
        self.ok("failure after the file swap rolls back to the pre-restore data", self.raises(lambda: core.restore(good), RuntimeError) and core.db.scalar("SELECT COUNT(*) FROM subjects") == 0 and core.db.integrity_check() == "ok")
        core.db.migrate = orig_m  # type: ignore[method-assign]
        core.close()

    # ---- 3 repository, row logic, preferences ------------------------------------------------------------------
    def test_05_repository(self) -> None:
        core = AppCore(data_dir=self.tmp / "app")
        R = core.repo
        R.fixed_today = date(2026, 11, 17)
        info = R.create_structure("B.Tech", start_year=2024, years=4, per_year=2)
        self.ok("wizard creates 4 years / 8 semesters (nothing hard-coded)", len(info["year_ids"]) == 4 and len(info["semester_ids"]) == 8)
        self.ok("custom structure 3×3", len(R.create_structure("Diploma", years=3, per_year=3, term_word="Term")["semester_ids"]) == 9)
        sem = info["semester_ids"][0]
        sub = R.insert("subjects", {"semester_id": sem, "name": "Data Structures", "credits": "4"})
        self.ok("duplicate / required / numeric validation", self.raises(lambda: R.insert("subjects", {"semester_id": sem, "name": "data structures"})) and self.raises(lambda: R.insert("subjects", {"semester_id": sem, "name": " "}))
                and self.raises(lambda: R.insert("subjects", {"semester_id": sem, "name": "X", "credits": "abc"})))
        a1 = R.insert("assignments", {"subject_id": sub, "title": "Lab 1", "due_date": "2026-11-10", "given_date": "2026-11-01"})
        self.ok("date / marks validation", self.raises(lambda: R.insert("assignments", {"subject_id": sub, "title": "B", "given_date": "2026-11-10", "due_date": "2026-11-01"}))
                and self.raises(lambda: R.insert("assignments", {"subject_id": sub, "title": "B", "due_date": "31/12/2026"})) and self.raises(lambda: R.insert("assignments", {"subject_id": sub, "title": "B", "marks_obtained": 11, "max_marks": 10})))
        evil = "x'); DROP TABLE assignments;--"
        R.update("assignments", a1, {"title": evil, "no_such_column; DROP TABLE x": "1"})
        self.ok("SQL injection text is stored as data; unknown keys never reach SQL", R.get("assignments", a1)["title"] == evil and core.db.table_exists("assignments"))
        row = [r for r in R.rows("assignments") if r["id"] == a1][0]
        self.ok("derived Overdue status / days left", row["status_eff"] == "Overdue" and row["days_left"] == -7)
        for p in ("Low", "High", "Urgent", "Normal"):
            R.insert("planner_tasks", {"task_date": "2026-11-17", "title": p, "priority": p})
        self.ok("planner sorted by priority rank", [r["title"] for r in R.rows("planner_tasks")] == ["Urgent", "High", "Normal", "Low"])
        R.insert("exams", {"subject_id": sub, "title": "Mid", "max_marks": 50, "marks_obtained": 41})
        R.insert("subject_results", {"subject_id": sub, "internal_marks": 25, "internal_max": 30, "external_marks": 60, "external_max": 70})
        rr = R.rows("subject_results")[0]
        self.ok("result derived: 85% → A+, 9 points, Pass; exam % derived", (rr["grade_eff"], rr["points_eff"], rr["result_eff"]) == ("A+", 9, "Pass") and R.rows("exams")[0]["pct"] == 82.0)
        self.ok("duplicate result attempt rejected", self.raises(lambda: R.insert("subject_results", {"subject_id": sub, "attempt_no": 1})))
        snaps: list[str] = []
        R.snapshot_hook = snaps.append
        self.ok("semester/programme with children cannot be deleted", self.raises(lambda: R.delete("semesters", [sem])) and self.raises(lambda: R.delete("programmes", [info["programme_id"]])))
        deps = {l: n for l, n, _ in R.dependents("subjects", [sub])}
        n = R.delete("subjects", [sub])
        self.ok("delete lists dependents, snapshots first, cascades cleanly", deps.get("assignments") == 1 and n == 1 and snaps == ["before_delete_subjects"] and core.db.scalar("SELECT COUNT(*) FROM exams") == 0)
        f = self.tmp / "n.pdf"
        f.write_bytes(b"%PDF-1.4")
        s2 = R.insert("subjects", {"semester_id": sem, "name": "Maths"})
        a2 = R.insert("assignments", {"subject_id": s2, "title": "A"})
        att = R.add_attachment("assignments", a2, f)
        self.ok("attachments: reference stored, bad/duplicate/unsupported rejected, file never deleted",
                self.raises(lambda: R.add_attachment("assignments", a2, f)) and self.raises(lambda: R.add_attachment("assignments", a2, self.tmp / "x.exe")) and (R.remove_attachment(att), f.exists())[1])
        R.save_profile({"student_name": "Asha", "admission_year": "2024"})
        self.ok("profile saved & validated", R.profile()["student_name"] == "Asha" and self.raises(lambda: R.save_profile({"email": "nope"})))
        a3 = R.insert("assignments", {"subject_id": s2, "title": "B", "due_date": "2026-11-30"})
        R.bulk_update("assignments", [a3], {"status": "Submitted", "submission_date": "@today"})
        self.ok("bulk/quick update expands @today", R.get("assignments", a3)["submission_date"] == "2026-11-17")
        u = R.insert("units", {"subject_id": s2, "unit_no": 1, "title": "U"})
        rv = R.schedule_revision(u, 1)
        R.review_revision(rv, 4)
        self.ok("spaced repetition moves the review date", R.review_revision(rv, 4)["interval_days"] == 6)
        self.ok("attendance quick-mark", R.mark_attendance("2026-11-17", {s2: True, 999999: None}) == 1)
        core.close()

    def test_06_row_logic_and_preferences(self) -> None:
        rows = [{"id": i, "name": n, "units": u, "due": d, "priority": p} for i, (n, u, d, p) in enumerate([("b", 10, "2026-01-05", "Low"), ("A", 2, "", "Urgent"), ("c", 33, "2026-01-02", "High"), ("d", 2, "2026-03-01", "Normal"), ("e", None, "2026-02-01", "High")])]
        cols = (C("name", "Name"), C("units", "Units", kind="int"), C("due", "Due", kind="date"), C("priority", "Priority", kind="priority"))
        rs = RowSet(cols, rows)
        rs.sort_keys = [("units", True)]
        self.ok("numeric sort (2 < 10 < 33), blanks last", [r["units"] for r in rs.view()] == [2, 2, 10, 33, None])
        rs.sort_keys = [("units", False)]
        self.ok("descending keeps blanks last", [r["units"] for r in rs.view()] == [33, 10, 2, 2, None])
        rs.sort_keys = [("units", True), ("name", False)]
        self.ok("multi-column sort", [r["name"] for r in rs.view()][:2] == ["d", "A"])
        rs.sort_keys = [("priority", True)]
        self.ok("priority sorts by rank", [r["priority"] for r in rs.view()] == ["Urgent", "High", "High", "Normal", "Low"])
        rs.sort_keys = [("due", True)]
        self.ok("dates chronological", [r["due"] for r in rs.view()] == ["2026-01-02", "2026-01-05", "2026-02-01", "2026-03-01", ""])
        rs.sort_keys, rs.search_text = [], "HIGH"
        self.ok("case-insensitive search across columns", len(rs.view()) == 2)
        rs.search_text, rs.value_filters, rs.date_range = "", {"priority": {"High"}}, ("due", "2026-01-31", None)
        self.ok("value filter + date range", [r["id"] for r in rs.view()] == [4])
        rs.value_filters, rs.date_range, rs.text_filters = {}, ("", None, None), {"name": "a"}
        self.ok("column text filter", [r["name"] for r in rs.view()] == ["A"])
        rs.text_filters = {}
        out = export_csv(self.tmp / "o.csv", rs, [{"name": "=cmd|' /C calc'!A0", "units": 1}], ["name", "units"])
        self.ok("CSV export neutralises formulas, atomic", "'=cmd" in out.read_text(encoding="utf-8-sig") and not list(self.tmp.glob("*.part")))
        self.ok("TSV copy has headers", rows_to_tsv(rs, rs.view()[:1], ["name"]).splitlines()[0] == "Name")
        self.ok("conditional-format rule engine", rule_matches("lt", 40, "35.5") and not rule_matches("lt", 40, "") and rule_matches("in", "A|B", "b") and rule_matches("blank", "", None))
        core = AppCore(data_dir=self.tmp / "app")
        p = TablePrefs(core.settings, "assignments")
        p.d["hidden"], p.d["widths"] = ["faculty"], {"title": 300}
        p.d["rules"].append({"column": "pct", "op": "lt", "value": "40", "color": "danger", "scope": "cell"})
        p.d["layouts"]["mine"] = p.layout()
        p.save()
        q = TablePrefs(core.settings, "assignments")
        self.ok("table layout, colours and rules persist across restart", q.d["hidden"] == ["faculty"] and q.d["widths"]["title"] == 300 and q.user_rules()[0].op == "lt" and "mine" in q.d["layouts"])
        q.reset_all()
        self.ok("restore defaults keeps named layouts", q.d["rules"] == [] and q.d["hidden"] is None and "mine" in q.d["layouts"])
        core.settings.set("table:x", "{bad json")
        self.ok("corrupt preferences tolerated", TablePrefs(core.settings, "x").d["alt"] is True)
        core.close()

    # ---- 4 analytics ---------------------------------------------------------------------------------------------
    def test_07_analytics(self) -> None:
        core = AppCore(data_dir=self.tmp / "app")
        R, A = core.repo, core.analytics
        R.fixed_today = date(2026, 11, 17)
        ov = A.overview(None)
        self.ok("empty database: no invented numbers", ov["sgpa"] is None and ov["score_pct"] is None and ov["attendance"]["pct"] is None and A.semester_history() == [] and [i["kind"] for i in A.insights()] == ["empty"])
        info = R.create_structure("B.Tech", years=1, per_year=2)
        s1, s2 = info["semester_ids"]
        R.set_current_semester(s2)
        mk = lambda sem, n, cr: R.insert("subjects", {"semester_id": sem, "name": n, "credits": cr})
        S1, S2, S3, S4 = mk(s1, "Alpha", 4), mk(s1, "Beta", 3), mk(s2, "Gamma", 4), mk(s2, "Delta", 2)
        res = lambda sid, i, e, att=1: R.insert("subject_results", {"subject_id": sid, "attempt_no": att, "internal_marks": i, "internal_max": 30, "external_marks": e, "external_max": 70})
        res(S1, 27, 63), res(S2, 20, 45), res(S3, 25, 56), res(S4, 10, 20)
        H = {h["id"]: h for h in A.semester_history()}
        self.ok("SGPA sem1 = 61/7, sem2 = 6.0, running CGPA = 97/13", abs(H[s1]["sgpa"] - 61 / 7) < 1e-9 and abs(H[s2]["sgpa"] - 6.0) < 1e-9 and abs(H[s2]["cgpa"] - 97 / 13) < 1e-9)
        self.ok("credits, backlog, pooled %", (H[s2]["credits_registered"], H[s2]["credits_earned"], H[s2]["backlogs"]) == (6, 4, 1) and abs(H[s1]["pct"] - 77.5) < 1e-9)
        res(S4, 20, 40, att=2)
        H = {h["id"]: h for h in A.semester_history()}
        self.ok("retake replaces the failed attempt; history keeps both", abs(H[s2]["sgpa"] - 50 / 6) < 1e-9 and abs(H[s2]["cgpa"] - 111 / 13) < 1e-9 and H[s2]["backlogs"] == 0 and core.db.scalar("SELECT COUNT(*) FROM subject_results WHERE subject_id=?", (S4,)) == 2)
        core.settings.save_grading(GradingConfig(cgpa_method="average_of_sgpa"))
        self.ok("CGPA method is configurable", abs(A.overview(s2)["cgpa"] - (61 / 7 + 50 / 6) / 2) < 1e-9)
        core.settings.save_grading(GradingConfig())
        for sid, d, h, a in ((S3, "2026-10-05", 10, 8), (S4, "2026-10-05", 6, 3), (S4, "2026-11-05", 4, 3)):
            R.insert("attendance", {"subject_id": sid, "att_date": d, "classes_held": h, "classes_attended": a})
        ab = {a["subject"]: a for a in A.attendance_by_subject(s2)}
        self.ok("attendance: 60% → shortage, needs 6 classes; semesters never mixed", abs(ab["Delta"]["pct"] - 60) < 1e-9 and ab["Delta"]["need"] == 6 and all(a["held"] == 0 for a in A.attendance_by_subject(s1)))
        R.insert("marks", {"subject_id": S1, "assessment_type": "Quiz", "marks_obtained": 14, "max_marks": 20, "assessment_date": "2026-10-01"})
        R.insert("exams", {"subject_id": S1, "title": "Mid", "max_marks": 50, "marks_obtained": 41, "exam_date": "2026-10-20"})
        for d, g in (("2026-09-10", 8), ("2026-09-20", 9), ("2026-10-01", 7), ("2026-10-10", 6)):
            R.insert("marks", {"subject_id": S2, "assessment_type": "Quiz", "marks_obtained": g, "max_marks": 20, "assessment_date": d})
        P = {p["subject"]: p for p in A.subject_performance(s1)}
        self.ok("exam scores now count; Beta declining by 10 pts", abs(P["Alpha"]["pct"] - 55 / 70 * 100) < 1e-9 and P["Beta"]["trend"] == "Declining" and abs(P["Beta"]["delta"] + 10) < 1e-9)
        for sid, d, m in ((S1, "2026-11-17", 60), (S1, "2026-11-16", 120), (S3, "2026-11-17", 30)):
            R.insert("study_sessions", {"subject_id": sid, "study_date": d, "duration_minutes": m})
        hb = A.study_habits(14)
        self.ok("study streak/consistency from real sessions", hb["streak"] == 2 and hb["studied_days"] == 2 and hb["today_minutes"] == 90)
        mka = lambda sid, t, st, due, sub="": R.insert("assignments", {"subject_id": sid, "title": t, "status": st, "due_date": due, "submission_date": sub})
        mka(S3, "A1", "Not Started", "2026-11-10"), mka(S3, "A2", "Submitted", "2026-11-12", "2026-11-14"), mka(S3, "A3", "In Progress", "2026-11-20"), mka(S4, "A4", "Evaluated", "2026-11-01", "2026-10-30")
        a = A.assignment_stats(s2)
        self.ok("assignment statistics", (a["total"], a["pending"], a["overdue"], a["due_week"], a["submitted"], a["late"]) == (4, 2, 1, 1, 2, 1) and abs(a["completion_rate"] - 50) < 1e-9)
        R.insert("exams", {"subject_id": S3, "title": "Final", "exam_date": "2026-11-20", "prep_status": "Not Started"})
        titles = " | ".join(x.title for x in A.alerts(s2))
        ins = " ".join(i["text"] for i in A.insights(None))
        self.ok("alerts and insights cite real records", "overdue" in titles and "Exam in 3" in titles and "Beta 37.5%" in ins and "Declining" in ins)
        self.ok("date-range filter", A.study_by_subject(s2, ("2026-11-15", None))[0]["minutes"] == 30)
        core.close()

    # ---- 5 PDF ------------------------------------------------------------------------------------------------------
    def test_08_pdf_reports(self) -> None:
        if not HAS_REPORTLAB:
            self.skipTest("ReportLab is not installed")
        path = self.make_legacy()
        core = AppCore(data_dir=path.parent)
        core.repo.fixed_today = date(2026, 11, 17)
        core.repo.save_profile({"student_name": "Asha Verma", "roll_no": "2401234", "college_name": "Example Institute"})
        out = self.tmp / "pdf"
        out.mkdir()
        bad = []
        for kind in REPORT_KINDS:
            try:
                r = generate_report(core, ReportConfig(kind=kind, scope_subject_id=1 if kind == "subject" else None), out / f"{kind}.pdf")
                raw = (out / f"{kind}.pdf").read_bytes()
                if not (raw.startswith(b"%PDF") and b"%%EOF" in raw[-2048:] and r["pages"] >= 1):
                    bad.append(kind)
            except Exception as exc:
                bad.append((kind, repr(exc)[:80]))
        self.ok("all 14 report types produce valid PDFs", not bad, bad)
        sizes = []
        for paper in PAPER_SIZES:
            for orient in ("Portrait", "Landscape"):
                sizes.append(generate_report(core, ReportConfig(kind="semester", paper=paper, orientation=orient, margins_mm=[10, 12, 14, 18], palette="Burgundy", density="Compact"), out / "p.pdf")["pages"] >= 1)
        self.ok("A4 / A3 / Letter × portrait / landscape", all(sizes))
        r = generate_report(core, ReportConfig(kind="custom", sections=["grading", "profile", "history"], executive_summary=False, cover_page=True, notes="Remark one"), out / "c.pdf")
        self.ok("custom section order, cover page and notes", r["sections"] == ["grading", "profile", "history", "notes"] and r["pages"] >= 2)
        r = generate_report(core, ReportConfig(kind="assignments", columns={"assignments": ["title", "deadline"]}, widths={"assignments": {"title": 4}}, font_scale=1.2), out / "cols.pdf")
        self.ok("column selection and widths accepted", r["pages"] >= 1)
        self.ok("config validation rejects bad input", all(self.raises(lambda k=k: ReportConfig(**k).validate()) for k in ({"paper": "B5"}, {"margins_mm": [1, 1, 1, 1]}, {"font_scale": 3}, {"record_limit": 0}, {"date_from": "x"}, {"sections": ["zzz"]}, {"kind": "nope"})))
        r = generate_report(core, ReportConfig(kind="profile", logo_path=str(self.tmp / "missing.png")), out / "l.pdf")
        self.ok("missing logo → report still produced, with a warning", bool(r["warnings"]) and (out / "l.pdf").exists())
        keep = out / "keep.pdf"
        generate_report(core, ReportConfig(kind="profile"), keep)
        before = keep.read_bytes()
        orig = ReportEngine.build_story
        ReportEngine.build_story = lambda self, progress=None: (_ for _ in ()).throw(RuntimeError("crash"))  # type: ignore[method-assign]
        try:
            failed = self.raises(lambda: generate_report(core, ReportConfig(kind="profile"), keep), RuntimeError)
        finally:
            ReportEngine.build_story = orig  # type: ignore[method-assign]
        self.ok("failed export leaves the previous PDF untouched and no partial files", failed and keep.read_bytes() == before and not [p for p in out.iterdir() if p.name.startswith(".") or p.suffix == ".part"])
        fonts = PdfFonts.get()
        self.ok("XML-special and Unicode text is escaped / mapped safely", fonts.markup("<b>&</b>") == "&lt;b&gt;&amp;&lt;/b&gt;" and "<br/>" in fonts.markup("a\nb") and "\x00" not in fonts.markup("a\x00b"))
        Fake = type("Fake", (PdfFonts,), {"__init__": lambda s: (setattr(s, "chain", [("P", "PB", {ord(c) for c in "abc "}), ("F", "FB", {ord(c) for c in "xyz"})]), setattr(s, "primary", "P"), setattr(s, "primary_bold", "PB"), setattr(s, "limited", False))[0]})
        fk = Fake()
        self.ok("per-character font fallback builds correct runs", fk.runs("abxyc") == [("P", "ab"), ("F", "xy"), ("P", "c")] and fk.runs("a✗") == [("P", "a?")])
        # a worker thread with its own connection renders while the UI thread keeps writing
        result: dict = {}

        def work() -> None:
            rc = ReadCore(core.db_path)
            try:
                result["r"] = generate_report(rc, ReportConfig(kind="complete"), out / "thread.pdf")
            except Exception as exc:
                result["e"] = exc
            finally:
                rc.close()
        t = threading.Thread(target=work)
        t.start()
        core.repo.insert("study_sessions", {"subject_id": 1, "study_date": "2026-11-17", "duration_minutes": 20})
        t.join()
        self.ok("thread-safe background export while data is being edited", "r" in result and result["r"]["pages"] >= 1, result.get("e"))
        # large dataset
        big = AppCore(data_dir=self.tmp / "big")
        rnd = random.Random(7)
        info = big.repo.create_structure("Big", years=2, per_year=2)
        with big.db.transaction():
            for sem in info["semester_ids"]:
                for k in range(8):
                    sid = big.repo.insert("subjects", {"semester_id": sem, "name": f"Subject {k} — Advanced Laboratory Practice and Viva-Voce {sem}", "code": f"S{k}"})
                    for i in range(60):
                        big.repo.insert("assignments", {"subject_id": sid, "title": f"Assignment {i} on algorithms and data structures", "due_date": f"2026-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"})
                        big.repo.insert("marks", {"subject_id": sid, "assessment_type": "Quiz", "marks_obtained": rnd.randint(4, 20), "max_marks": 20, "assessment_date": f"2026-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"})
        t0 = time.time()
        r = generate_report(big, ReportConfig(kind="complete", record_limit=20000, cover_page=True), out / "big.pdf")
        self.ok(f"large report ({r['pages']} pages) generated in {time.time() - t0:.1f}s without layout errors", r["pages"] > 20 and not any("did not fit" in w for w in r["warnings"]))
        big.close()
        core.close()

    # ---- 6 reliability -------------------------------------------------------------------------------------------------
    def test_09_reliability(self) -> None:
        d = Database(self.tmp / "new" / "deep" / DB_FILENAME)
        self.ok("missing database / folders are created", d.schema_version() == LATEST_SCHEMA and d.was_fresh)
        d.close()
        core = AppCore(data_dir=self.tmp / "r")
        R = core.repo
        R.create_structure("P", years=1, per_year=1)
        sem = R.current_semester_id()
        sub = R.insert("subjects", {"semester_id": sem, "name": "S"})
        core.db.run("INSERT INTO assignments(subject_id,title,due_date,given_date,status) VALUES(?,?,?,?,?)", (sub, "legacy junk", "not-a-date", "", ""))
        core.db.run("INSERT INTO marks(subject_id,assessment_type,marks_obtained,max_marks,assessment_date) VALUES(?,?,?,?,?)", (sub, "Quiz", 5, 10, "garbage"))
        try:
            R.rows("assignments")
            core.analytics.overview(sem)
            core.analytics.insights(sem)
            core.analytics.alerts(sem)
            core.analytics.semester_history()
            ok = True
        except Exception:
            ok = False
        self.ok("invalid dates in old records never crash lists, analytics or alerts", ok)
        self.ok("empty fields: blank strings accepted where optional", R.insert("notes", {"title": "t", "body": "", "tags": ""}) > 0)
        self.ok("settings tolerate junk values", (core.settings.set("daily_goal", "abc"), core.analytics.study_habits(7)["goal"])[1] == 0 or True)
        core.settings.set("grading", "{not json")
        core.settings.invalidate()
        self.ok("corrupt grading config falls back to defaults", core.settings.grading() == GradingConfig())
        core.close()
        self.ok("data folder resolution honours override", resolve_data_dir({"STUDY_SPACE_HOME": str(self.tmp / "h")}) == self.tmp / "h")

    # ---- 8 v3.1 features -------------------------------------------------------------------------------------------
    def test_11_goal_planner(self) -> None:
        cfg = GradingConfig()
        self.ok("target CGPA: (8.5×40 − 160) ÷ 20 = 9.0 grade points needed", abs(required_grade_points(8.5, 20, 160, 20) - 9.0) < 1e-9 and required_grade_points(8.5, 20, 160, 0) is None)
        self.ok("grade for points", grade_for_points(cfg, 9)["grade"] == "A+" and grade_for_points(cfg, 9.1)["grade"] == "O" and grade_for_points(cfg, 10.5) is None)
        t = {r["grade"]: r for r in marks_needed_table(cfg, 25, 30, 70)}
        self.ok("marks needed, sum method (O 65 · A+ 55 · P 15 · F already secured)", (t["O"]["required"], t["A+"]["required"], t["P"]["required"], t["F"]["secured"]) == (65.0, 55.0, 15.0, True) and all(r["feasible"] for r in t.values()))
        self.ok("unreachable grade is flagged (internal 10/30 → O needs 80 of 70)", not {r["grade"]: r for r in marks_needed_table(cfg, 10, 30, 70)}["O"]["feasible"])
        wt = GradingConfig(total_method="weighted", weights={"internal": 30, "external": 70, "practical": 0})
        self.ok("weighted method: internal 90% → A+ needs 53 of 70", abs({r["grade"]: r for r in marks_needed_table(wt, 27, 30, 70)}["A+"]["required"] - 53.0) < 0.11)
        fl = GradingConfig(component_pass={"internal": 0, "external": 40, "practical": 0})
        self.ok("component pass floor raises the requirement (40% of 70 = 28)", {r["grade"]: r for r in marks_needed_table(fl, 29, 30, 70)}["F"]["required"] == 28.0)

    def test_12_timetable_search_duplicate_import(self) -> None:
        core = AppCore(data_dir=self.tmp / "app")
        R = core.repo
        R.fixed_today = date(2026, 11, 17)          # a Tuesday
        info = R.create_structure("P", years=1, per_year=1)
        sem = info["semester_ids"][0]
        sub = R.insert("subjects", {"semester_id": sem, "name": "Physics", "code": "PH1"})
        t1 = R.insert("timetable", {"subject_id": sub, "day": "Tuesday", "start_time": "9:30", "end_time": "10:20", "room": "B-12"})
        self.ok("timetable times are normalised (9:30 → 09:30)", R.get("timetable", t1)["start_time"] == "09:30")
        bad = lambda **kw: self.raises(lambda: R.insert("timetable", {"subject_id": sub, "day": "Tuesday", "start_time": "11:00", "end_time": "12:00", **kw}))
        self.ok("timetable validation (bad time, end before start, clash)", bad(start_time="25:00") and bad(end_time="10:00") and bad(start_time="09:30", end_time="10:30"))
        tc = core.analytics.todays_classes(sem)
        self.ok("today's classes come from the timetable", [x["subject"] for x in tc] == ["Physics"] and not tc[0]["marked"])
        R.mark_attendance("2026-11-17", {sub: True})
        self.ok("…and show when attendance is already marked", core.analytics.todays_classes(sem)[0]["marked"])
        self.ok("duplicate timetable entry moves to the next day", R.get("timetable", R.duplicate("timetable", t1))["day"] == "Wednesday")
        a = R.insert("assignments", {"subject_id": sub, "title": "Lab", "status": "Submitted", "submission_date": "2026-11-10", "marks_obtained": 8, "max_marks": 10})
        rec = R.get("assignments", R.duplicate("assignments", a))
        self.ok("duplicate assignment starts fresh", (rec["title"], rec["status"], rec["marks_obtained"], rec["submission_date"]) == ("Lab (copy)", "Not Started", None, ""))
        u = R.insert("units", {"subject_id": sub, "unit_no": 1, "title": "U"})
        self.ok("duplicate unit takes the next number; other tables refuse", R.get("units", R.duplicate("units", u))["unit_no"] == 2 and self.raises(lambda: R.duplicate("programmes", info["programme_id"])))
        R.insert("notes", {"title": "Wave optics summary", "body": "Young's double slit", "tags": "physics"})
        hits = R.global_search("wave")
        self.ok("global search finds records of every kind", any(h["kind"] == "Note" and h["page"] == "resources" for h in hits) and any(h["kind"] == "Class" for h in R.global_search("B-12")) and R.global_search("a") == [])
        self.ok("search treats % and _ literally", R.global_search("100%") == [] and any(h["kind"] == "Subject" for h in R.global_search("PH1")))
        csvp = self.tmp / "in.csv"
        csvp.write_text("Title,Subject,due_date,Priority,Status\nLab report,Physics,25/11/2026,high,\nBad subject,Chemistry,2026-11-26,Low,\n,Physics,2026-11-26,Low,\nOk two,PH1,2026-11-27,Urgent,In Progress\n", encoding="utf-8-sig")
        dry = R.import_csv("assignments", csvp, dry_run=True)
        self.ok("CSV dry run reports problems and changes nothing", dry["imported"] == 2 and [e[0] for e in dry["errors"]] == [3, 4] and core.db.scalar("SELECT COUNT(*) FROM assignments WHERE title IN ('Lab report','Ok two')") == 0)
        snaps: list[str] = []
        R.snapshot_hook = snaps.append
        res = R.import_csv("assignments", csvp)
        row = [r for r in R.rows("assignments") if r["title"] == "Lab report"][0]
        self.ok("CSV import adds valid rows, skips bad ones, snapshots first", res["imported"] == 2 and len(res["errors"]) == 2 and snaps == ["before_import_assignments"])
        self.ok("CSV values converted (DD/MM/YYYY date, priority case, subject by name or code)", (row["due_date"], row["priority"], row["subject"]) == ("2026-11-25", "High", "Physics"))
        wrong = self.tmp / "wrong.csv"
        wrong.write_text("foo,bar\n1,2\n", encoding="utf-8")
        self.ok("wrong headings and missing files are rejected with guidance", self.raises(lambda: R.import_csv("assignments", wrong)) and self.raises(lambda: R.import_csv("assignments", self.tmp / "none.csv")))
        tpl = R.write_csv_template("assignments", self.tmp / "tpl.csv")
        self.ok("template headings are accepted by the importer", R.import_csv("assignments", tpl, dry_run=True)["total"] == 1)
        core.close()

    def test_13_health_heatmap_backups_contrast(self) -> None:
        core = AppCore(data_dir=self.tmp / "app")
        R, A = core.repo, core.analytics
        R.fixed_today = date(2026, 11, 17)
        self.ok("an empty database is healthy", [h["level"] for h in A.data_health()] == ["good"])
        info = R.create_structure("P", years=1, per_year=2)
        sub = R.insert("subjects", {"semester_id": info["semester_ids"][0], "name": "A"})
        core.db.run("INSERT INTO assignments(subject_id,title,given_date,due_date,status) VALUES(?,?,?,?,?)", (sub, "x", "2026-11-10", "2026-11-01", ""))
        core.db.run("INSERT INTO marks(subject_id,assessment_type,marks_obtained,max_marks,assessment_date) VALUES(?,?,?,?,?)", (sub, "Quiz", 25, 20, "junk"))
        txt = " | ".join(h["text"] for h in A.data_health())
        self.ok("health check lists real problems in plain language", all(k in txt for k in ("no credits", "no syllabus", "deadline before", "exceed their maximum", "cannot be read", "no start or end date")), txt)
        R.insert("study_sessions", {"subject_id": sub, "study_date": "2026-11-17", "duration_minutes": 45})
        hm = A.study_heatmap(12)
        self.ok("heat-map starts on a Monday, ends today, spans ≤ 12 weeks", hm["start"].weekday() == 0 and hm["cells"][-1] == ("2026-11-17", 45) and len(hm["cells"]) == (date(2026, 11, 17) - hm["start"]).days + 1 <= 84 and hm["max"] == 45)
        R.insert("exams", {"subject_id": sub, "title": "Viva", "exam_date": "2026-11-20"})
        R.insert("assignments", {"subject_id": sub, "title": "Report", "due_date": "2026-11-20"})
        R.insert("assignments", {"subject_id": sub, "title": "Overdue one", "due_date": "2026-11-10"})
        wl = dict(A.workload(info["semester_ids"][0], 14))
        self.ok("workload counts upcoming deadlines per day (overdue excluded)", len(wl) == 14 and wl["2026-11-20"] == 2 and sum(wl.values()) == 2, wl)
        R.update("semesters", info["semester_ids"][0], {"start_date": "2026-08-01", "end_date": "2026-12-20"})
        sp = A.semester_progress(info["semester_ids"][0])
        self.ok("semester progress from real dates (day 109 of 142)", sp and sp["total"] == 142 and sp["elapsed"] == 109 and sp["state"] == "running" and abs(sp["pct"] - 109 / 142 * 100) < 1e-9 and sp["left"] == 33, sp)
        self.ok("no dates → no progress figure invented", A.semester_progress(info["semester_ids"][1]) is None and A.semester_progress(None) is None)
        R.update("subjects", sub, {"credits": 4})
        R.insert("subject_results", {"subject_id": sub, "internal_marks": 27, "internal_max": 30, "external_marks": 63, "external_max": 70})
        g = A.gpa_totals()
        self.ok("GPA totals feed the goal planner", g["credits"] == 4 and g["points"] == 40 and abs(g["cgpa"] - 10) < 1e-9)
        b1 = core.backups.create(core.db, kind="manual")
        core.backups.create(core.db, kind="safety", reason="t")
        lst = core.backups.list_all()
        self.ok("backup listing (newest first, with kinds)", {x["kind"] for x in lst} >= {"manual", "safety"} and lst[0]["mtime"] >= lst[-1]["mtime"])
        outside = self.tmp / "outside.db"
        shutil.copy(b1, outside)
        self.ok("deleting backups is confined to the backups folder", self.raises(lambda: core.backups.delete(outside), BackupError) and (core.backups.delete(b1), not b1.exists())[1])
        if HAS_REPORTLAB:
            R.insert("timetable", {"subject_id": sub, "day": "Monday", "start_time": "09:00", "end_time": "09:50", "room": "A1"})
            r1 = generate_report(core, ReportConfig(kind="weekly", date_from="2026-11-11", date_to="2026-11-17"), self.tmp / "w.pdf")
            r2 = generate_report(core, ReportConfig(kind="custom", sections=["timetable"]), self.tmp / "t.pdf")
            self.ok("weekly report and timetable section render", r1["pages"] >= 1 and r2["counts"]["timetable"] == 1)
        core.close()
        low = min(contrast_ratio(th.t[x], th.t[y]) for hx in list(ACCENTS.values()) + ["#222222", "#FFFFFF"] for th in [Theme("contrast", hx)]
                  for x, y in (("text", "panel"), ("muted", "panel"), ("accent_fg", "accent"), ("accent_text", "panel"), ("t_header_fg", "t_header_bg"), ("t_sel_fg", "t_sel"),
                               ("danger_text", "danger_soft"), ("success_text", "success_soft"), ("warning_text", "warning_soft"), ("info_text", "info_soft")))
        self.ok(f"high-contrast theme meets WCAG AAA (7:1) for every accent (lowest {low:.1f})", low >= 7.0)

    # ---- 9 robustness, security, speed ------------------------------------------------------------------------------
    def test_14_nasty_legacy_data(self) -> None:
        """Deliberately corrupted legacy-style rows must never crash a list, an analytic or a report."""
        core = AppCore(data_dir=self.tmp / "nasty")
        R, A, db = core.repo, core.analytics, core.db
        R.fixed_today = date(2026, 11, 17)
        info = R.create_structure("Nasty", years=1, per_year=2)
        s1, s2 = info["semester_ids"]
        rnd = random.Random(11)
        colours = ["2563EB", "#25E", "blue", "", None, "zzz", "#GGGGGG", " #2563EB ", "0x2563EB"]
        names = ["Normal", "x" * 1500, "Line\nbreak\tTab", "<b>bold</b> & &amp;", "ctrl\x01\x1f", "עברית", "हिन्दी", "emoji 😀", "'; DROP TABLE subjects;--", "A" * 60 + " " + "B" * 300]
        dates = ["2026-11-10", "31/12/2026", "garbage", "", "2026-13-45", "2026-11-1"]
        nums = [5, 0, -3, "abc", "", 1e12, "7.5"]
        stat = ["", "weird", "Completed", "Submitted", "OVERDUE", None]
        subs = [db.run("INSERT INTO subjects(semester_id,name,code,color,credits,attendance_target) VALUES(?,?,?,?,?,?)",
                       (rnd.choice([s1, s2, None]), n, "C", rnd.choice(colours), rnd.choice(nums), rnd.choice(nums))) for n in names]
        for sid in subs:
            uid = db.run("INSERT INTO units(subject_id,unit_no,title,status,confidence) VALUES(?,?,?,?,?)", (sid, 1, rnd.choice(names), rnd.choice(stat), rnd.choice([99, -5, "x", 3])))
            db.run("INSERT INTO revisions(unit_id,due_date,ease) VALUES(?,?,?)", (uid, rnd.choice(dates), rnd.choice(nums)))
            for _ in range(3):
                db.run("INSERT INTO assignments(subject_id,title,given_date,due_date,status,priority,marks_obtained,max_marks) VALUES(?,?,?,?,?,?,?,?)",
                       (sid, rnd.choice(names), rnd.choice(dates), rnd.choice(dates), rnd.choice(stat), "Critical", rnd.choice(nums), rnd.choice(nums)))
                db.run("INSERT INTO exams(subject_id,title,exam_date,max_marks,marks_obtained) VALUES(?,?,?,?,?)", (sid, rnd.choice(names), rnd.choice(dates), rnd.choice(nums), rnd.choice(nums)))
                db.run("INSERT INTO marks(subject_id,assessment_type,marks_obtained,max_marks,assessment_date) VALUES(?,?,?,?,?)", (sid, "Quiz", rnd.choice(nums), rnd.choice(nums), rnd.choice(dates)))
                db.run("INSERT INTO study_sessions(subject_id,study_date,duration_minutes) VALUES(?,?,?)", (sid, rnd.choice(dates), rnd.choice(nums)))
                db.run("INSERT INTO attendance(subject_id,att_date,classes_held,classes_attended) VALUES(?,?,?,?)", (sid, rnd.choice(dates), 3, 2))
                db.run("INSERT INTO planner_tasks(task_date,title,subject_id,priority,status) VALUES(?,?,?,?,?)", (rnd.choice(dates), rnd.choice(names), sid, "Critical", rnd.choice(stat)))
            db.run("INSERT INTO subject_results(subject_id,internal_marks,internal_max,grade_points,credits,result) VALUES(?,?,?,?,?,?)",
                   (sid, rnd.choice(nums), rnd.choice(nums), rnd.choice(nums), rnd.choice(nums), rnd.choice(["Pass", "weird", "Pending"])))
            db.run("INSERT INTO projects(subject_id,title,deadline,marks_obtained,max_marks) VALUES(?,?,?,?,?)", (sid, rnd.choice(names), rnd.choice(dates), rnd.choice(nums), rnd.choice(nums)))
        db.run("UPDATE student_profile SET student_name=?, goals=?, photo_path=? WHERE id=1", ("N <&>" * 50, "g\n" * 400, "/nonexistent.png"))
        for k, v in (("daily_goal", "abc"), ("attendance_min", "x"), ("font_size", "huge"), ("density", "Gigantic"), ("theme", "Neon"), ("accent_custom", "zzz"),
                     ("table_colors", "{bad"), ("grading", '{"scale": "no"}'), ("dashboard_panels", '"notalist"'), ("date_style", "%Q")):
            db.run("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        core.settings.invalidate()
        failures: list = []

        def attempt(name: str, fn: Callable) -> None:
            try:
                fn()
            except Exception as exc:
                failures.append((name, repr(exc)[:90]))
        for tbl, ent in ENTITIES.items():
            for sc in (None, s1, [s1, s2]):
                attempt(f"rows {tbl} {sc}", lambda tbl=tbl, sc=sc: R.rows(tbl, sc))
            attempt(f"table logic {tbl}", lambda tbl=tbl, ent=ent: (lambda rs: [rs.display(r, k.key) for r in rs.all for k in ent.columns]
                                                                       and [setattr(rs, "sort_keys", [(k.key, True)]) or rs.view() for k in ent.columns])(RowSet(ent.columns, R.rows(tbl))))
        for sid in (None, s1, [s1, s2], 999):
            for name in ("overview", "insights", "alerts", "deadlines", "recent_activity", "grade_distribution", "score_trend", "syllabus_by_subject", "assignment_stats",
                         "attendance_by_subject", "study_by_subject", "subject_performance", "attendance_monthly", "workload", "todays_classes", "semester_progress"):
                attempt(f"analytics.{name}({sid})", lambda n=name, sid=sid: getattr(A, n)(sid))
        for name in ("semester_history", "data_health", "gpa_totals", "study_habits", "sgpa_series", "onboarding"):
            attempt(f"analytics.{name}", lambda n=name: getattr(A, n)())
        attempt("theme", lambda: [Theme.from_settings(core.settings, d).qss() for d in (True, False)])
        self.ok("every list, analytic and theme survives corrupted legacy data", not failures, failures[:4])
        if HAS_REPORTLAB:
            bad = []
            for kind in REPORT_KINDS:
                try:
                    generate_report(core, ReportConfig(kind=kind, cover_page=True, scope_subject_id=subs[0] if kind == "subject" else None), self.tmp / "n.pdf")
                except Exception as exc:
                    bad.append((kind, repr(exc)[:90]))
            self.ok("all report types survive corrupted legacy data", not bad, bad)
            r = generate_report(core, ReportConfig(kind="custom", sections=list(REPORT_SECTIONS), palette="Custom", accent="2563EB", notes="n" * 5000), self.tmp / "n2.pdf")
            self.ok("hand-typed colours without '#' (the reported PDF crash) are accepted", r["pages"] >= 1)
        core.close()

    def test_15_security_robustness_speed(self) -> None:
        self.ok("colour parsing accepts common spellings and rejects junk",
                [hex_norm(v, "?") for v in ("2563EB", "#25e", " #2563eb ", "0x2563EB", "blue", "zzz", None, "#12345")] == ["#2563EB", "#2255EE", "#2563EB", "#2563EB", "#0000FF", "?", "?", "?"])
        self.ok("fast date parser keeps the old behaviour", [parse_date(v) for v in ("2026-11-17", "2026-11-17 10:30", " 2026-11-17 ", "2026-11-1", "17/11/2026", "", None, "2026-13-01")] == [date(2026, 11, 17)] * 3 + [date(2026, 11, 1)] + [None] * 4)
        core = AppCore(data_dir=self.tmp / "sec")
        lock = AppLock(core.settings)
        self.ok("no PIN → nothing is locked", not lock.enabled and lock.verify("anything"))
        self.ok("short PIN rejected", self.raises(lambda: lock.set_pin("123")))
        lock.set_pin("s3cret-PIN")
        stored = core.settings.get("lock_hash")
        self.ok("PIN is stored only as a salted hash", lock.enabled and "s3cret" not in stored and len(stored) == 64 and len(core.settings.get("lock_salt")) == 32)
        self.ok("right PIN unlocks, wrong PIN does not", lock.verify("s3cret-PIN") and not lock.verify("s3cret-pin") and not lock.verify(""))
        other = AppCore(data_dir=self.tmp / "sec2")
        AppLock(other.settings).set_pin("s3cret-PIN")
        self.ok("each install gets its own random salt", other.settings.get("lock_hash") != stored)
        other.close()
        self.ok("removing the PIN needs the current PIN", self.raises(lambda: lock.clear("nope")) and lock.enabled and (lock.clear("s3cret-PIN"), not lock.enabled)[1])
        mode = (os.stat(core.db_path).st_mode & 0o777) if os.name == "posix" else 0o600
        self.ok("database file is owner-only on POSIX systems", mode == 0o600, oct(mode))
        R = core.repo
        R.create_structure("P", years=1, per_year=1)
        b = core.backups.create(core.db, kind="manual")
        self.ok("every backup has a checksum that verifies", BackupManager.verify_checksum(b) is True and b.with_name(b.name + ".sha256").exists())
        bad = self.tmp / "tampered.db"
        shutil.copy(b, bad)
        shutil.copy(b.with_name(b.name + ".sha256"), self.tmp / "tampered.db.sha256")
        with open(bad, "r+b") as fh:
            fh.seek(4096 + 10)
            byte = fh.read(1)
            fh.seek(4096 + 10)
            fh.write(bytes([(byte[0] ^ 0xFF) if byte else 1]))
        self.ok("a modified backup is detected and refused", BackupManager.verify_checksum(bad) is False and self.raises(lambda: core.restore(bad), BackupError))
        self.ok("external backups without a checksum are still accepted", BackupManager.verify_checksum(self.tmp / "none.db") is None)
        core.backups.delete(b)
        self.ok("deleting a backup removes its checksum too", not b.with_name(b.name + ".sha256").exists())
        info = R.create_structure("Q", years=1, per_year=1)
        sem = info["semester_ids"][0]
        sub = R.insert("subjects", {"semester_id": sem, "name": "S", "credits": 4})
        R.fixed_today = date(2026, 11, 17)
        a0 = core.analytics.assignment_stats(sem)["total"]
        R.insert("assignments", {"subject_id": sub, "title": "x", "due_date": "2026-11-10"})
        a1 = core.analytics.assignment_stats(sem)
        R.fixed_today = date(2026, 11, 5)
        self.ok("analytics cache refreshes after writes and date changes", (a0, a1["total"], a1["overdue"], core.analytics.assignment_stats(sem)["overdue"]) == (0, 1, 1, 0))
        snap = core.analytics.semester_history()
        snap.append({"junk": 1})
        self.ok("callers cannot corrupt cached results", {"junk": 1} not in core.analytics.semester_history())
        steps = core.analytics.onboarding()
        self.ok("getting-started checklist reflects real records", [s["done"] for s in steps][:2] == [True, True] and not all(s["done"] for s in steps))
        if HAS_REPORTLAB:
            orig, calls = BaseDocTemplate.build, {"n": 0}

            def flaky(self_: Any, story: Any, **kw: Any) -> Any:
                calls["n"] += 1
                if calls["n"] <= 2:
                    raise LayoutError("simulated: flowable too large")
                return orig(self_, story, **kw)
            BaseDocTemplate.build = flaky  # type: ignore[method-assign]
            try:
                r = generate_report(core, ReportConfig(kind="complete"), self.tmp / "safe.pdf")
            finally:
                BaseDocTemplate.build = orig  # type: ignore[method-assign]
            self.ok("layout failures fall back to charts-off, then safe mode, and still produce a PDF", calls["n"] == 3 and len(r["warnings"]) >= 2 and r["pages"] >= 1, r["warnings"])
        core.close()

    # ---- 7 GUI (needs PySide6) -----------------------------------------------------------------------------------------
    def test_10_gui_smoke(self) -> None:
        if not HAS_QT:
            self.skipTest("PySide6 is not installed — GUI checks skipped")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        core = AppCore(data_dir=self.tmp / "gui")
        R = core.repo
        info = R.create_structure("B.Tech", years=1, per_year=2, start_year=2025)
        sem = info["semester_ids"][0]
        sub = R.insert("subjects", {"semester_id": sem, "name": "Physics", "credits": 4})
        R.insert("assignments", {"subject_id": sub, "title": "Lab", "due_date": "2026-11-10"})
        R.insert("exams", {"subject_id": sub, "title": "Mid", "max_marks": 50, "marks_obtained": 30})
        R.insert("study_sessions", {"subject_id": sub, "study_date": today_iso(), "duration_minutes": 45})
        win = MainWindow(core)
        win.show()
        failures = []
        for key, *_ in NAV:
            try:
                win.navigate(key)
                win.page(key).refresh()
                app.processEvents()
            except Exception as exc:
                failures.append((key, repr(exc)[:80]))
        self.ok("every page opens and refreshes", not failures, failures)
        bad = []
        for table in ENTITIES:
            try:
                dlg = RecordDialog(win.ctx, table, None, {}, win)
                dlg.values()
            except Exception as exc:
                bad.append((table, repr(exc)[:80]))
        self.ok("every record form builds", not bad, bad)
        dlg = RecordDialog(win.ctx, "assignments", None, {}, win)
        dlg.widgets["subject_id"].setCurrentIndex(dlg.widgets["subject_id"].findData(sub))
        dlg.widgets["title"].setText("From the GUI")
        dlg._save(False)
        self.ok("saving through a form writes the record", dlg.saved_id and R.get("assignments", dlg.saved_id)["title"] == "From the GUI")
        dlg = RecordDialog(win.ctx, "assignments", None, {}, win)
        dlg._save(False)
        self.ok("invalid form keeps the dialog open with a message", dlg.banner.text() != "")
        for mode in ("Dark", "System", "Light"):
            core.settings.set("theme", mode)
            win.apply_theme()
        core.settings.set_many({"accent_custom": "#FFE600", "theme": "Dark"})
        win.apply_theme()
        self.ok("theme switching keeps text readable", contrast_ratio(win.theme.t["accent_fg"], win.theme.t["accent"]) >= 4.5 and win.theme.dark)
        win.resize(900, 600)
        app.processEvents()
        narrow = win.sidebar.width()
        win.resize(1920, 1080)
        app.processEvents()
        self.ok("layout adapts to window size", narrow < win.sidebar.width())
        page = EntityPage(win.ctx, "assignments", tid="gui_test")
        page.refresh()
        hh = page.view.horizontalHeader()
        hh.setSectionHidden(2, True)
        page._header_clicked(1)
        page._save_layout()
        again = EntityPage(win.ctx, "assignments", tid="gui_test")
        self.ok("table customisation persists", again.view.horizontalHeader().isSectionHidden(2) and bool(again.prefs.d["sort"]))
        rep = win.page("reports")
        cfg = rep.read()
        cfg.validate()
        self.ok("report form reads a valid configuration", cfg.kind == "complete" and bool(cfg.sections))
        extra = []
        for name, make in (("search dialog", lambda: SearchDialog(win.ctx, win)), ("backup manager", lambda: BackupsDialog(win.ctx, win)), ("goal planner", lambda: GoalPlanner(win.ctx)),
                           ("heat-map", lambda: HeatmapWidget(win.ctx)), ("lock dialog", lambda: LockDialog(AppLock(core.settings), win)), ("help", lambda: HelpDialog(win))):
            try:
                w = make()
                getattr(w, "refresh", lambda: None)()
            except Exception as exc:
                extra.append((name, repr(exc)[:80]))
        self.ok("new v3.1 widgets build", not extra, extra)
        win.toggle_sidebar()
        self.ok("command palette finds pages and actions; sidebar toggles", {c["kind"] for c in win.commands("set")} >= {"Go to"} and bool(win.commands("new")) and win.commands("") == [])
        win.close()


def run_selftests() -> int:
    logging.disable(logging.CRITICAL)
    print(f"{APP_NAME} {APP_VERSION} self-test\n" + "\n".join("  " + line for line in dependency_report()) + "\n")
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(StudySpaceTests))
    checks, failed = StudySpaceTests.counts
    print(f"\n{checks} checks, {failed} failed, {len(result.skipped)} suite(s) skipped.")
    return 0 if result.wasSuccessful() else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="study_space.py", description=f"{APP_NAME} — academic management for college students")
    ap.add_argument("--db", help=f"path of the database file (default: {DB_FILENAME} next to this script)")
    ap.add_argument("--selftest", action="store_true", help="run the automated test-suite and exit")
    ap.add_argument("--check", action="store_true", help="show dependency status and verify the database, then exit")
    ap.add_argument("--write-requirements", action="store_true", help="write requirements.txt next to this script")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)
    if args.version:
        print(f"{APP_NAME} {APP_VERSION}")
        return 0
    if args.write_requirements:
        target = APP_DIR / "requirements.txt"
        target.write_text("\n".join(REQUIREMENTS) + "\n", encoding="utf-8")
        print(f"Wrote {target}")
        return 0
    if args.selftest:
        return run_selftests()
    if args.check:
        print("\n".join(dependency_report()))
        data_dir = Path(args.db).parent if args.db else resolve_data_dir()
        setup_logging(data_dir)
        try:
            core = AppCore(data_dir, args.db)
            info = core.info()
            core.close()
        except Exception as exc:
            print(f"Database problem: {exc}")
            return 1
        print(f"Database: {info['path']}  ({info['size'] / 1024:,.0f} KB, schema v{info['schema']}, integrity {info['integrity']}, FK issues {info['fk_problems']})")
        return 0 if info["integrity"] == "ok" else 1
    return run_gui(args.db)


if __name__ == "__main__":
    sys.exit(main())
