"""
STUDY SPACE PRO — PySide6 Edition · v2.5
=================================
Modern offline-first academic workspace for students.

Compatibility: preserves the existing study_space_pro.db database and records.
Run: python study_space_pro.py
"""
from __future__ import annotations

import sys, csv, sqlite3, os, subprocess, logging, math, time
from pathlib import Path
from datetime import date, datetime, timedelta
from contextlib import contextmanager

from PySide6.QtCore import Qt, QTimer, QDate
from PySide6.QtGui import QFont, QColor, QPalette, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QGridLayout, QFormLayout, QLabel, QPushButton, QLineEdit, QTextEdit,
    QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QStackedWidget, QFrame, QMessageBox,
    QFileDialog, QInputDialog, QProgressBar, QAbstractItemView, QScrollArea,
    QGroupBox, QCheckBox, QSizePolicy
)

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "study_space_pro.db"
APP_NAME = "Study Space Pro"
ACCENTS = {
    # Darkened slightly so white text on primary buttons remains readable.
    "Blue": "#2F5FCB", "Violet": "#7046C2", "Teal": "#087F75",
    "Rose": "#B83258", "Amber": "#955C00", "Indigo": "#4B4AB8"
}
TODAY = lambda: date.today().isoformat()
APP_VERSION = "2.5.0"
DB_TABLES = ("semesters", "subjects", "units", "chapters", "assignments",
             "study_sessions", "planner_tasks", "exams", "marks", "revisions", "custom_fields", "settings")

def format_minutes(minutes: int) -> str:
    minutes = max(0, int(minutes or 0))
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m" if hours else f"{mins}m"

def safe_int(value, default=0):
    try: return int(value)
    except (TypeError, ValueError): return default

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS semesters (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
 start_date TEXT DEFAULT '', end_date TEXT DEFAULT '', is_active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS subjects (
 id INTEGER PRIMARY KEY AUTOINCREMENT, semester_id INTEGER, name TEXT NOT NULL,
 code TEXT DEFAULT '', color TEXT DEFAULT '#356AE6', target_hours REAL DEFAULT 3,
 notes TEXT DEFAULT '', FOREIGN KEY(semester_id) REFERENCES semesters(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS units (
 id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER NOT NULL, unit_no INTEGER NOT NULL,
 title TEXT NOT NULL, status TEXT DEFAULT 'Not Started', confidence INTEGER DEFAULT 1,
 notes TEXT DEFAULT '', FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
 UNIQUE(subject_id,unit_no));
CREATE TABLE IF NOT EXISTS chapters (
 id INTEGER PRIMARY KEY AUTOINCREMENT, unit_id INTEGER NOT NULL, title TEXT NOT NULL,
 status TEXT DEFAULT 'Not Started', priority TEXT DEFAULT 'Normal', notes TEXT DEFAULT '',
 FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS assignments (
 id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER NOT NULL, unit_id INTEGER,
 title TEXT NOT NULL, description TEXT DEFAULT '', given_date TEXT DEFAULT '',
 due_date TEXT DEFAULT '', submission_date TEXT DEFAULT '', status TEXT DEFAULT 'Not Started',
 priority TEXT DEFAULT 'Normal', remarks TEXT DEFAULT '',
 FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
 FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS study_sessions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER NOT NULL, unit_id INTEGER,
 study_date TEXT NOT NULL, duration_minutes INTEGER NOT NULL, topic TEXT DEFAULT '',
 notes TEXT DEFAULT '', session_type TEXT DEFAULT 'Study',
 FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
 FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS planner_tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_date TEXT NOT NULL, title TEXT NOT NULL,
 subject_id INTEGER, category TEXT DEFAULT 'Study', priority TEXT DEFAULT 'Normal',
 status TEXT DEFAULT 'Pending', duration_minutes INTEGER DEFAULT 30, notes TEXT DEFAULT '',
 FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS exams (
 id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER NOT NULL, title TEXT NOT NULL,
 exam_type TEXT DEFAULT 'Semester Exam', exam_date TEXT DEFAULT '', max_marks REAL DEFAULT 100,
 venue TEXT DEFAULT '', prep_status TEXT DEFAULT 'Not Started', notes TEXT DEFAULT '',
 FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS marks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id INTEGER NOT NULL, unit_id INTEGER,
 assessment_type TEXT NOT NULL, title TEXT DEFAULT '', marks_obtained REAL NOT NULL,
 max_marks REAL NOT NULL, assessment_date TEXT DEFAULT '', notes TEXT DEFAULT '',
 FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
 FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS revisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, unit_id INTEGER NOT NULL, due_date TEXT NOT NULL,
 interval_days INTEGER DEFAULT 1, ease REAL DEFAULT 2.5, repetitions INTEGER DEFAULT 0,
 status TEXT DEFAULT 'Due', last_reviewed TEXT DEFAULT '', notes TEXT DEFAULT '',
 FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS custom_fields (
 id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT NOT NULL, record_id INTEGER NOT NULL,
 field_name TEXT NOT NULL, field_value TEXT DEFAULT '', field_type TEXT DEFAULT 'Text',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(entity_type,record_id,field_name));
"""

# label, type, choices. Foreign-key combos are populated from SQLite.
CONFIG = {
    "semesters": [("name","Semester name","text"),("start_date","Start date","date"),("end_date","End date","date"),("is_active","Active","choice:1|0")],
    "subjects": [
        ("semester_id","Semester","fk:semesters"), ("name","Subject name","text"),
        ("code","Subject code","text"), ("color","Subject colour (hex)","text"),
        ("target_hours","Weekly target (hours)","float"), ("notes","Notes","long")],
    "units": [
        ("subject_id","Subject","fk:subjects"), ("unit_no","Unit number","int"),
        ("title","Unit title","text"), ("status","Status","choice:Not Started|In Progress|Completed"),
        ("confidence","Confidence (1–5)","int"), ("notes","Notes","long")],
    "chapters": [
        ("unit_id","Unit","fk:units"), ("title","Chapter title","text"),
        ("status","Status","choice:Not Started|In Progress|Completed"),
        ("priority","Priority","choice:Low|Normal|High"), ("notes","Notes","long")],
    "assignments": [
        ("subject_id","Subject","fk:subjects"), ("unit_id","Unit (optional)","fk?:units"),
        ("title","Assignment title","text"), ("description","Description","long"),
        ("given_date","Given date","date"), ("due_date","Due date","date"),
        ("submission_date","Submission date","date"), ("status","Status","choice:Not Started|In Progress|Submitted|Overdue"),
        ("priority","Priority","choice:Low|Normal|High|Urgent"), ("remarks","Remarks","long")],
    "study_sessions": [
        ("subject_id","Subject","fk:subjects"), ("unit_id","Unit (optional)","fk?:units"),
        ("study_date","Study date","date"), ("duration_minutes","Duration (minutes)","int"),
        ("topic","Topic","text"), ("session_type","Session type","choice:Study|Revision|Practice|Lecture|Project"),
        ("notes","Notes","long")],
    "planner_tasks": [
        ("task_date","Task date","date"), ("title","Task title","text"),
        ("subject_id","Subject (optional)","fk?:subjects"),
        ("category","Category","choice:Study|Assignment|Revision|Exam prep|Personal|Project"),
        ("priority","Priority","choice:Low|Normal|High|Urgent"),
        ("status","Status","choice:Pending|In Progress|Completed|Skipped"),
        ("duration_minutes","Planned duration (minutes)","int"), ("notes","Notes","long")],
    "exams": [
        ("subject_id","Subject","fk:subjects"), ("title","Exam name","text"),
        ("exam_type","Exam type","text"), ("exam_date","Exam date","date"),
        ("max_marks","Maximum marks","float"), ("marks_obtained","Marks obtained (optional)","float?"),
        ("grade","Grade / GPA","text"), ("result","Result","text"),
        ("weightage","Weightage (%)","float"), ("attempt","Attempt","text"),
        ("duration_minutes","Duration (minutes)","int"), ("exam_mode","Mode","choice:Offline|Online|Practical|Viva|"),
        ("venue","Venue","text"), ("room","Room","text"), ("examiner","Examiner","text"),
        ("prep_status","Preparation status","choice:Not Started|In Progress|Prepared|Completed"),
        ("notes","Notes","long")],
    "marks": [
        ("subject_id","Subject","fk:subjects"), ("unit_id","Unit (optional)","fk?:units"),
        ("assessment_type","Assessment type","choice:Unit Quiz|Seasonal Exam|Semester Exam|Assignment|Practical|Other"),
        ("title","Assessment title","text"), ("marks_obtained","Marks obtained","float"),
        ("max_marks","Maximum marks","float"), ("assessment_date","Assessment date","date"),
        ("notes","Notes","long")],
    "revisions": [
        ("unit_id","Unit","fk:units"), ("due_date","Next review date","date"),
        ("interval_days","Interval (days)","int"), ("ease","Ease factor","float"),
        ("repetitions","Successful repetitions","int"),
        ("status","Status","choice:Due|Reviewed|Paused"),
        ("last_reviewed","Last reviewed","date"), ("notes","Notes","long")]
}

PAGE_INFO = [
    ("Overview","◫","Your academic command centre"),
    ("Semesters","▣","Manage academic terms"),
    ("Subjects","▦","Manage semesters and subjects"),
    ("Syllabus","▤","Units and completion"),
    ("Chapters","▤","Chapter-level progress"),
    ("Assignments","☷","Deadlines and submissions"),
    ("Study Planner","▦","Daily tasks and schedule"),
    ("Study Sessions","◷","Track your learning time"),
    ("Focus Room","◎","Pomodoro focus timer"),
    ("Exams","▣","Exam timetable and results"),
    ("Marks & Results","▥","Assessments and performance"),
    ("Revision","↻","Spaced repetition queue"),
    ("Analytics","⌁","Progress and study insights"),
    ("Settings","⚙","Preferences, export and backup")
]

class Database:
    """Small SQLite repository with safe migrations and consistent commits."""
    def __init__(self, path=DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30)
        try:
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA busy_timeout=30000")
            self.conn.execute("PRAGMA temp_store=MEMORY")
            self.conn.execute("PRAGMA cache_size=-16000")
            self.conn.executescript(SCHEMA)
            self.migrate()
        except Exception:
            self.conn.close()
            raise

    def migrate(self):
        # CREATE IF NOT EXISTS is safe for existing databases and also repairs
        # older backups that predate a table. Existing records are never dropped.
        self.conn.executescript(SCHEMA)
        # Additive migrations only: existing student data is never dropped.
        additions = {
            "exams": {"marks_obtained":"REAL", "grade":"TEXT DEFAULT ''", "result":"TEXT DEFAULT ''",
                      "attempt":"TEXT DEFAULT ''", "examiner":"TEXT DEFAULT ''", "weightage":"REAL DEFAULT 0",
                      "duration_minutes":"INTEGER DEFAULT 0", "exam_mode":"TEXT DEFAULT ''", "room":"TEXT DEFAULT ''"},
            "subjects": {"semester_id":"INTEGER", "code":"TEXT DEFAULT ''", "color":"TEXT DEFAULT '#356AE6'",
                         "target_hours":"REAL DEFAULT 3", "notes":"TEXT DEFAULT ''"},
            "units": {"confidence":"INTEGER DEFAULT 1", "notes":"TEXT DEFAULT ''"},
            "assignments": {"description":"TEXT DEFAULT ''", "given_date":"TEXT DEFAULT ''",
                            "submission_date":"TEXT DEFAULT ''", "priority":"TEXT DEFAULT 'Normal'", "remarks":"TEXT DEFAULT ''"},
            "study_sessions": {"unit_id":"INTEGER", "topic":"TEXT DEFAULT ''", "notes":"TEXT DEFAULT ''", "session_type":"TEXT DEFAULT 'Study'"},
            "planner_tasks": {"subject_id":"INTEGER", "category":"TEXT DEFAULT 'Study'", "priority":"TEXT DEFAULT 'Normal'",
                              "status":"TEXT DEFAULT 'Pending'", "duration_minutes":"INTEGER DEFAULT 30", "notes":"TEXT DEFAULT ''"},
            "marks": {"unit_id":"INTEGER", "title":"TEXT DEFAULT ''", "assessment_date":"TEXT DEFAULT ''", "notes":"TEXT DEFAULT ''"},
            "revisions": {"interval_days":"INTEGER DEFAULT 1", "ease":"REAL DEFAULT 2.5", "repetitions":"INTEGER DEFAULT 0",
                          "status":"TEXT DEFAULT 'Due'", "last_reviewed":"TEXT DEFAULT ''", "notes":"TEXT DEFAULT ''"},
        }
        with self.transaction():
            for table, columns in additions.items():
                existing = {r["name"] for r in self.q(f"PRAGMA table_info({table})")}
                for name, definition in columns.items():
                    if name not in existing:
                        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            defaults = {"theme":"Light", "accent":"Blue", "daily_goal":"180",
                        "pomodoro_work":"25", "pomodoro_short":"5", "pomodoro_long":"15",
                        "semester_name":"B.Tech — Semester 1", "schema_version":APP_VERSION}
            for key, value in defaults.items():
                self.conn.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value "
                    if key == "schema_version" else
                    "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
                    (key, str(value))
                )
            if not self.one("SELECT id FROM semesters LIMIT 1"):
                self.conn.execute("INSERT INTO semesters(name,is_active) VALUES(?,1)", (defaults["semester_name"],))
            for sql in (
                "CREATE INDEX IF NOT EXISTS idx_units_subject ON units(subject_id)",
                "CREATE INDEX IF NOT EXISTS idx_tasks_date ON planner_tasks(task_date)",
                "CREATE INDEX IF NOT EXISTS idx_assignments_due ON assignments(due_date)",
                "CREATE INDEX IF NOT EXISTS idx_sessions_date ON study_sessions(study_date)",
                "CREATE INDEX IF NOT EXISTS idx_revisions_due ON revisions(due_date)",
            ): self.conn.execute(sql)

    @contextmanager
    def transaction(self):
        try:
            self.conn.execute("BEGIN")
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def q(self, sql, args=()): return self.conn.execute(sql, args).fetchall()
    def one(self, sql, args=()): return self.conn.execute(sql, args).fetchone()
    def run(self, sql, args=()):
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur.lastrowid
    def setting(self, key, default=""):
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default
    def set_setting(self, key, value):
        self.run("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    def set_settings(self, values):
        with self.transaction():
            self.conn.executemany(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(str(key), str(value)) for key, value in values.items()]
            )
    def delete_record(self, table, record_id):
        """Delete one supported entity and clean custom fields for cascaded rows."""
        if table not in DB_TABLES or table in ("settings", "custom_fields"):
            raise ValueError("Unsupported record type.")
        child_custom_fields = []
        if table == "subjects":
            unit_ids = [r[0] for r in self.q("SELECT id FROM units WHERE subject_id=?", (record_id,))]
            child_custom_fields.append(("units", unit_ids))
            if unit_ids:
                placeholders = ",".join("?" for _ in unit_ids)
                child_custom_fields.extend([
                    ("chapters", [r[0] for r in self.q(f"SELECT id FROM chapters WHERE unit_id IN ({placeholders})", unit_ids)]),
                    ("revisions", [r[0] for r in self.q(f"SELECT id FROM revisions WHERE unit_id IN ({placeholders})", unit_ids)]),
                ])
            for child_table in ("assignments", "study_sessions", "exams", "marks"):
                child_custom_fields.append((child_table, [r[0] for r in self.q(f"SELECT id FROM {child_table} WHERE subject_id=?", (record_id,))]))
        elif table == "units":
            child_custom_fields.extend([
                ("chapters", [r[0] for r in self.q("SELECT id FROM chapters WHERE unit_id=?", (record_id,))]),
                ("revisions", [r[0] for r in self.q("SELECT id FROM revisions WHERE unit_id=?", (record_id,))]),
            ])
        with self.transaction():
            self.conn.execute("DELETE FROM custom_fields WHERE entity_type=? AND record_id=?", (table, record_id))
            for entity, ids in child_custom_fields:
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    self.conn.execute(
                        f"DELETE FROM custom_fields WHERE entity_type=? AND record_id IN ({placeholders})",
                        [entity, *ids]
                    )
            self.conn.execute(f"DELETE FROM {table} WHERE id=?", (record_id,))
    def backup_to(self, destination):
        destination = Path(destination).expanduser().resolve()
        if destination == self.path.resolve():
            raise ValueError("Backup destination cannot be the active database file.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(destination), timeout=30)
        try:
            self.conn.backup(target)
            check = target.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise sqlite3.DatabaseError(f"Backup integrity check failed: {check}")
            target.commit()
        finally:
            target.close()
        return destination
    def integrity_check(self):
        return self.one("PRAGMA integrity_check")[0]
    def close(self):
        try: self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error: pass
        try: self.conn.close()
        except Exception: pass

def qdate(value):
    """Convert stored ISO dates; invalid/blank dates stay visibly unset."""
    if not value:
        return QDate(2000, 1, 1)
    d = QDate.fromString(str(value)[:10], "yyyy-MM-dd")
    return d if d.isValid() else QDate(2000, 1, 1)

class RecordForm(QDialog):
    def __init__(self, db, table, title, record=None, parent=None):
        super().__init__(parent)
        self.db, self.table = db, table
        self.record = dict(record or {})
        self.widgets={}
        self.setObjectName("RecordFormDialog")
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        self.resize(650, min(820, 190 + len(CONFIG[table]) * 56))

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        heading = QLabel(title)
        heading.setObjectName("DialogTitle")
        root.addWidget(heading)

        sub = QLabel("Fields marked with an asterisk should be completed.")
        sub.setObjectName("Muted")
        root.addWidget(sub)

        scroll = QScrollArea()
        scroll.setObjectName("RecordFormScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setAutoFillBackground(True)
        scroll.viewport().setObjectName("RecordFormViewport")
        scroll.viewport().setAutoFillBackground(True)

        body = QWidget()
        body.setObjectName("RecordFormBody")
        body.setAutoFillBackground(True)
        form = QFormLayout(body)
        form.setContentsMargins(12, 12, 16, 12)
        form.setVerticalSpacing(12)
        form.setHorizontalSpacing(18)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        required_fields = {
            "semesters": {"name"},
            "subjects": {"name"},
            "units": {"subject_id", "unit_no", "title"},
            "chapters": {"unit_id", "title"},
            "assignments": {"subject_id", "title"},
            "study_sessions": {"subject_id", "study_date", "duration_minutes"},
            "planner_tasks": {"task_date", "title"},
            "exams": {"subject_id", "title", "max_marks"},
            "marks": {"subject_id", "assessment_type", "marks_obtained", "max_marks"},
            "revisions": {"unit_id", "due_date"},
        }
        for key, label, kind in CONFIG[table]:
            value = self.record.get(key)
            widget = self.make_widget(key, kind, value)
            widget.setMinimumHeight(max(widget.minimumHeight(), 38))
            self.widgets[key] = (widget, kind)
            label_text = f"{label} *" if key in required_fields.get(table, set()) else label
            field_label = QLabel(label_text)
            field_label.setObjectName("RecordFieldLabel")
            field_label.setMinimumWidth(118)
            field_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            form.addRow(field_label, widget)

        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        if table in ("assignments", "study_sessions", "marks") and "subject_id" in self.widgets and "unit_id" in self.widgets:
            self.widgets["subject_id"][0].currentIndexChanged.connect(self.refresh_unit_options)
            self.refresh_unit_options()
        buttons=QHBoxLayout(); buttons.addStretch()
        cancel=QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        save=QPushButton("Save record"); save.setObjectName("Primary"); save.setDefault(True); save.clicked.connect(self.validate_and_accept)
        buttons.addWidget(cancel); buttons.addWidget(save); root.addLayout(buttons)

    def refresh_unit_options(self, *_):
        """Keep optional unit choices scoped to the selected subject."""
        if self.table not in ("assignments", "study_sessions", "marks"):
            return
        combo = self.widgets["unit_id"][0]
        subject_id = self.widgets["subject_id"][0].currentData()
        selected_id = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("— None —", None)
        if subject_id is not None:
            rows = self.db.q(
                "SELECT id, unit_no, title FROM units WHERE subject_id=? ORDER BY unit_no",
                (subject_id,)
            )
            for row in rows:
                combo.addItem(f"Unit {row['unit_no']}: {row['title']}", row["id"])
        index = combo.findData(selected_id)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def validate_and_accept(self):
        try:
            self.values()
        except ValueError as exc:
            QMessageBox.warning(self, "Check your entries", str(exc))
            return
        self.accept()

    def make_widget(self,key,kind,value):
        if kind.startswith("fk"):
            w=QComboBox(); optional=kind.startswith("fk?")
            if optional: w.addItem("— None —",None)
            target=kind.split(":")[1]
            rows=self.db.q(f"SELECT * FROM {target} ORDER BY id DESC")
            for r in rows:
                if target=="subjects": label=f"{r['name']} ({r['code']})" if r["code"] else r["name"]
                elif target=="semesters": label=r["name"]
                elif target=="units":
                    s=self.db.one("SELECT name FROM subjects WHERE id=?",(r["subject_id"],))
                    label=f"{s['name'] if s else 'Subject'} · Unit {r['unit_no']}: {r['title']}"
                else: label=str(r["id"])
                w.addItem(label,r["id"])
            if value is not None:
                ix=w.findData(value)
                if ix>=0: w.setCurrentIndex(ix)
            return w
        if kind.startswith("choice:"):
            w=QComboBox()
            choices=[x for x in kind.split(":",1)[1].split("|") if x]
            w.addItems(choices)
            if value is not None and str(value) not in choices: w.addItem(str(value))
            if value is not None: w.setCurrentText(str(value))
            return w
        if kind=="long":
            w=QTextEdit(); w.setFixedHeight(92); w.setPlainText(str(value or "")); return w
        if kind=="date":
            w=QDateEdit(); w.setCalendarPopup(True); w.setDisplayFormat("yyyy-MM-dd")
            w.setSpecialValueText("Not set"); w.setMinimumDate(QDate(2000,1,1)); w.setDate(qdate(value))
            return w
        if kind=="float?":
            w=QLineEdit("" if value is None else str(value))
            w.setPlaceholderText("Leave blank if not yet available")
            return w
        if kind in ("int","float"):
            if kind=="int":
                w=QSpinBox(); w.setRange(0,1000000)
                if key in ("unit_no", "interval_days", "duration_minutes"): w.setMinimum(1)
                if key=="confidence": w.setRange(1,5)
                if key=="interval_days": w.setMaximum(3650)
                if key=="duration_minutes": w.setMaximum(1440)
                w.setValue(max(w.minimum(), int(value if value is not None else w.minimum())))
            else:
                w=QDoubleSpinBox(); w.setRange(0,1000000); w.setDecimals(2)
                if key=="ease": w.setRange(1.3,5.0)
                w.setValue(max(w.minimum(), float(value if value is not None else w.minimum())))
            return w
        w=QLineEdit(str(value or "")); return w

    def values(self):
        result={}
        for key,(w,kind) in self.widgets.items():
            if kind.startswith("fk"):
                result[key]=w.currentData()
            elif kind.startswith("choice:"):
                result[key]=w.currentText()
            elif kind=="long":
                result[key]=w.toPlainText().strip()
            elif kind=="date":
                result[key]="" if w.date()==w.minimumDate() else w.date().toString("yyyy-MM-dd")
            elif kind=="int":
                result[key]=w.value()
            elif kind=="float":
                result[key]=w.value()
            elif kind=="float?":
                raw=w.text().strip()
                try:
                    parsed=None if raw=="" else float(raw)
                    if parsed is not None and not math.isfinite(parsed):
                        raise ValueError
                    result[key]=parsed
                except ValueError:
                    raise ValueError(f"{key.replace('_',' ').title()} must be a valid finite number.")
            else:
                result[key]=w.text().strip()

        required = {
            "semesters": ("name",),
            "subjects": ("name",),
            "units": ("subject_id", "unit_no", "title"),
            "chapters": ("unit_id", "title"),
            "assignments": ("subject_id", "title"),
            "study_sessions": ("subject_id", "study_date", "duration_minutes"),
            "planner_tasks": ("task_date", "title"),
            "exams": ("subject_id", "title", "max_marks"),
            "marks": ("subject_id", "assessment_type", "marks_obtained", "max_marks"),
            "revisions": ("unit_id", "due_date"),
        }
        for field in required.get(self.table, ()):
            value = result.get(field)
            if value in (None, ""):
                raise ValueError(f"{field.replace('_',' ').title()} is required.")
        positive_fields = {"unit_no", "duration_minutes", "interval_days"}
        for field in positive_fields.intersection(result):
            value = result[field]
            if value is not None and value <= 0:
                raise ValueError(f"{field.replace('_',' ').title()} must be greater than zero.")

        # Unit choices are constrained to the selected subject, both visually and
        # at validation time to protect existing databases from inconsistent links.
        if self.table in ("assignments", "study_sessions", "marks") and result.get("unit_id") is not None:
            linked = self.db.one("SELECT subject_id FROM units WHERE id=?", (result["unit_id"],))
            if not linked or linked["subject_id"] != result.get("subject_id"):
                raise ValueError("Choose a unit that belongs to the selected subject.")

        if self.table in ("marks", "exams"):
            got=result.get("marks_obtained")
            maximum=result.get("max_marks")
            if maximum is None or maximum <= 0:
                raise ValueError("Maximum marks must be greater than zero.")
            if got is not None and got < 0:
                raise ValueError("Obtained marks cannot be negative.")
            if got is not None and got > maximum:
                raise ValueError("Obtained marks cannot exceed maximum marks.")

        date_pairs = {
            "semesters": ("start_date", "end_date", "Semester end date cannot be before its start date."),
            "assignments": ("given_date", "due_date", "Due date cannot be before the given date."),
        }
        if self.table in date_pairs:
            first, second, message = date_pairs[self.table]
            if result.get(first) and result.get(second) and result[second] < result[first]:
                raise ValueError(message)

        if self.table == "units":
            duplicate = self.db.one(
                "SELECT id FROM units WHERE subject_id=? AND unit_no=? AND id<>?",
                (result["subject_id"], result["unit_no"], self.record.get("id", -1))
            )
            if duplicate:
                raise ValueError("That unit number already exists for this subject.")
        return result

class CustomFieldsDialog(QDialog):
    def __init__(self, db, entity, record_id, parent=None):
        super().__init__(parent); self.db=db; self.entity=entity; self.record_id=record_id
        self.setObjectName("CustomFieldsDialog")
        self.setWindowTitle("Custom fields"); self.resize(560,460)
        layout=QVBoxLayout(self)
        title=QLabel("Custom fields"); title.setObjectName("DialogTitle"); layout.addWidget(title)
        self.table=QTableWidget(0,3); self.table.setHorizontalHeaderLabels(["Field","Value","Type"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table)
        self.refresh()
        row=QHBoxLayout()
        add=QPushButton("＋ Add field"); add.setObjectName("Primary"); add.clicked.connect(self.add_field)
        edit=QPushButton("Edit"); edit.clicked.connect(self.edit_field)
        delete=QPushButton("Delete"); delete.clicked.connect(self.delete_field)
        row.addWidget(add); row.addWidget(edit); row.addWidget(delete); row.addStretch()
        close=QPushButton("Close"); close.clicked.connect(self.accept); row.addWidget(close); layout.addLayout(row)
    def refresh(self):
        rows=self.db.q("SELECT * FROM custom_fields WHERE entity_type=? AND record_id=? ORDER BY field_name",(self.entity,self.record_id))
        self.table.setRowCount(len(rows))
        for i,r in enumerate(rows):
            for j,k in enumerate(("field_name","field_value","field_type")):
                item=QTableWidgetItem(str(r[k] or "")); item.setData(Qt.UserRole,r["id"]); self.table.setItem(i,j,item)
    def add_field(self):
        name,ok=QInputDialog.getText(self,"New custom field","Field name:")
        if not ok or not name.strip(): return
        value,ok=QInputDialog.getText(self,"Custom field value",f"Value for {name.strip()}:")
        if not ok: return
        typ,ok=QInputDialog.getItem(self,"Field type","Type:",["Text","Number","Date","Yes/No","Long text","URL"],0,False)
        if not ok: return
        try:
            self.db.run("INSERT INTO custom_fields(entity_type,record_id,field_name,field_value,field_type) VALUES(?,?,?,?,?)",
                        (self.entity,self.record_id,name.strip(),value,typ))
            self.refresh()
        except sqlite3.IntegrityError: QMessageBox.warning(self,"Duplicate field","That field name already exists for this record.")
    def edit_field(self):
        row=self.table.currentRow()
        if row<0: return
        fid=self.table.item(row,0).data(Qt.UserRole)
        old=self.db.one("SELECT * FROM custom_fields WHERE id=?",(fid,))
        if not old:
            self.refresh()
            return
        val,ok=QInputDialog.getText(self,"Edit custom field",old["field_name"],text=old["field_value"])
        if ok:
            self.db.run("UPDATE custom_fields SET field_value=? WHERE id=?",(val,fid)); self.refresh()
    def delete_field(self):
        row=self.table.currentRow()
        if row<0: return
        fid=self.table.item(row,0).data(Qt.UserRole)
        if QMessageBox.question(self,"Delete field","Delete this custom field?")==QMessageBox.Yes:
            self.db.run("DELETE FROM custom_fields WHERE id=?",(fid,)); self.refresh()

class DataPage(QWidget):
    def __init__(self, app, table, title, description):
        super().__init__(); self.app=app; self.db=app.db; self.table=table
        self.title=title; self.description=description; self.rows=[]
        root=QVBoxLayout(self); root.setContentsMargins(30,26,30,26); root.setSpacing(16)
        head=QHBoxLayout(); textcol=QVBoxLayout()
        h=QLabel(title); h.setObjectName("PageTitle")
        d=QLabel(description); d.setObjectName("Muted")
        textcol.addWidget(h); textcol.addWidget(d); head.addLayout(textcol); head.addStretch()
        self.add_btn=QPushButton("＋  Add new"); self.add_btn.setObjectName("Primary"); self.add_btn.clicked.connect(self.add_record)
        head.addWidget(self.add_btn); root.addLayout(head)
        toolbar=QHBoxLayout()
        self.search=QLineEdit(); self.search.setPlaceholderText("Search records…"); self.search.textChanged.connect(self.filter_rows)
        self.search.setClearButtonEnabled(True); toolbar.addWidget(self.search,1)
        self.count=QLabel(""); self.count.setObjectName("Muted"); toolbar.addWidget(self.count)
        root.addLayout(toolbar)
        self.tableview=QTableWidget(); self.tableview.setAlternatingRowColors(True)
        self.tableview.setSelectionBehavior(QAbstractItemView.SelectRows); self.tableview.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tableview.setEditTriggers(QAbstractItemView.NoEditTriggers); self.tableview.verticalHeader().setVisible(False)
        self.tableview.setWordWrap(False); self.tableview.setShowGrid(False); self.tableview.setSortingEnabled(False)
        self.tableview.horizontalHeader().setStretchLastSection(True); self.tableview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tableview.doubleClicked.connect(self.edit_record); root.addWidget(self.tableview,1)
        self.empty_state = QLabel("No records yet. Select ‘Add new’ to create your first entry.")
        self.empty_state.setObjectName("EmptyState")
        self.empty_state.setAlignment(Qt.AlignCenter)
        root.addWidget(self.empty_state)
        actions=QHBoxLayout()
        for label,slot in [("Edit",self.edit_record),("Delete",self.delete_record),("Custom fields",self.custom_fields)]:
            b=QPushButton(label); b.clicked.connect(slot); actions.addWidget(b)
        if self.table == "planner_tasks":
            done_btn=QPushButton("✓ Mark complete"); done_btn.clicked.connect(self.complete_task); actions.addWidget(done_btn)
        if self.table == "revisions":
            review_btn=QPushButton("↻ Review selected"); review_btn.clicked.connect(self.review_selected); actions.addWidget(review_btn)
        actions.addStretch()
        export=QPushButton("Export CSV"); export.clicked.connect(self.export_csv); actions.addWidget(export)
        root.addLayout(actions)
        self.refresh()

    def query(self):
        # Human-readable joined rows, but keep record IDs in a hidden first column.
        t=self.table
        if t=="semesters":
            return self.db.q("SELECT id,name,start_date,end_date,is_active FROM semesters ORDER BY id DESC"),["ID","Semester","Start date","End date","Active"]
        if t=="subjects":
            return self.db.q("""SELECT s.id,s.name,s.code,COALESCE(sem.name,'—') semester,s.target_hours
                FROM subjects s LEFT JOIN semesters sem ON sem.id=s.semester_id ORDER BY s.name"""),["ID","Subject","Code","Semester","Weekly goal (h)"]
        if t=="units":
            return self.db.q("""SELECT u.id,s.name subject,u.unit_no,u.title,u.status,u.confidence
                FROM units u JOIN subjects s ON s.id=u.subject_id ORDER BY s.name,u.unit_no"""),["ID","Subject","Unit","Title","Status","Confidence"]
        if t=="chapters":
            return self.db.q("""SELECT c.id,u.title unit,s.name subject,c.title,c.status,c.priority
                FROM chapters c JOIN units u ON u.id=c.unit_id JOIN subjects s ON s.id=u.subject_id ORDER BY s.name,u.unit_no,c.id"""),["ID","Unit","Subject","Chapter","Status","Priority"]
        if t=="assignments":
            return self.db.q("""SELECT a.id,a.title,s.name subject,a.given_date,a.due_date,a.submission_date,a.status,a.priority
                FROM assignments a JOIN subjects s ON s.id=a.subject_id ORDER BY CASE WHEN a.due_date='' THEN 1 ELSE 0 END,a.due_date"""),["ID","Assignment","Subject","Given","Due","Submitted","Status","Priority"]
        if t=="study_sessions":
            return self.db.q("""SELECT x.id,x.study_date,s.name subject,COALESCE(x.topic,''),x.duration_minutes,x.session_type
                FROM study_sessions x JOIN subjects s ON s.id=x.subject_id ORDER BY x.study_date DESC,x.id DESC"""),["ID","Date","Subject","Topic","Minutes","Type"]
        if t=="planner_tasks":
            return self.db.q("""SELECT p.id,p.task_date,p.title,COALESCE(s.name,'—') subject,p.category,p.priority,p.status,p.duration_minutes
                FROM planner_tasks p LEFT JOIN subjects s ON s.id=p.subject_id ORDER BY p.task_date DESC,p.id DESC"""),["ID","Date","Task","Subject","Category","Priority","Status","Minutes"]
        if t=="exams":
            return self.db.q("""SELECT e.id,e.title,s.name subject,e.exam_type,e.exam_date,e.marks_obtained,e.max_marks,e.grade,e.prep_status
                FROM exams e JOIN subjects s ON s.id=e.subject_id ORDER BY CASE WHEN e.exam_date='' THEN 1 ELSE 0 END,e.exam_date"""),["ID","Exam","Subject","Type","Date","Score","Maximum","Grade","Preparation"]
        if t=="marks":
            return self.db.q("""SELECT m.id,m.assessment_type,m.title,s.name subject,COALESCE(u.title,'—') unit,m.marks_obtained,m.max_marks,m.assessment_date
                FROM marks m JOIN subjects s ON s.id=m.subject_id LEFT JOIN units u ON u.id=m.unit_id ORDER BY m.assessment_date DESC,m.id DESC"""),["ID","Type","Title","Subject","Unit","Obtained","Maximum","Date"]
        if t=="revisions":
            return self.db.q("""SELECT r.id,s.name subject,u.title unit,r.due_date,r.interval_days,r.repetitions,r.status,r.last_reviewed
                FROM revisions r JOIN units u ON u.id=r.unit_id JOIN subjects s ON s.id=u.subject_id ORDER BY r.due_date"""),["ID","Subject","Unit","Due","Interval","Repetitions","Status","Last reviewed"]
        return [],["ID"]

    def refresh(self):
        self.rows,headers=self.query()
        self.empty_state.setVisible(not self.rows)
        self.tableview.setVisible(bool(self.rows))
        self.tableview.setSortingEnabled(False); self.tableview.clear()
        self.tableview.setColumnCount(len(headers)); self.tableview.setHorizontalHeaderLabels(headers)
        self.tableview.setRowCount(len(self.rows))
        for i,r in enumerate(self.rows):
            for j,val in enumerate(tuple(r)):
                text="" if val is None else str(val)
                item=QTableWidgetItem(text)
                if j==0: item.setData(Qt.UserRole,int(r[0]))
                if j in (0,4,5,6) and text.replace(".","",1).isdigit(): item.setTextAlignment(Qt.AlignCenter)
                self.tableview.setItem(i,j,item)
        self.tableview.setColumnHidden(0,True)
        self.tableview.setSortingEnabled(True)
        self.filter_rows(self.search.text())
        self.count.setText(f"{len(self.rows)} records")
    def filter_rows(self,text):
        needle=text.casefold().strip()
        shown=0
        for row in range(self.tableview.rowCount()):
            match=not needle or any(self.tableview.item(row,col) and needle in self.tableview.item(row,col).text().casefold()
                                    for col in range(1,self.tableview.columnCount()))
            self.tableview.setRowHidden(row,not match)
            shown+=int(match)
        self.count.setText(f"{shown} of {len(self.rows)} records")
        has_matches = shown > 0
        self.tableview.setVisible(has_matches)
        self.empty_state.setVisible(not has_matches)
        self.empty_state.setText(
            "No records yet. Select ‘Add new’ to create your first entry."
            if not self.rows else "No matching records. Try a different search."
        )
    def selected_id(self):
        row=self.tableview.currentRow()
        if row<0: return None
        item=self.tableview.item(row,0)
        return item.data(Qt.UserRole) if item else None
    def add_record(self):
        singular={"semesters":"semester","subjects":"subject","units":"syllabus unit",
                  "chapters":"chapter","assignments":"assignment","study_sessions":"study session",
                  "planner_tasks":"planner task","exams":"exam","marks":"assessment",
                  "revisions":"revision"}.get(self.table,self.table.replace("_"," "))
        form=RecordForm(self.db,self.table,f"Add {singular}",parent=self)
        if form.exec()==QDialog.Accepted:
            try:
                vals=form.values(); keys=list(vals)
                self.db.run(f"INSERT INTO {self.table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                            tuple(vals[k] for k in keys))
                self.refresh(); self.app.refresh_dashboard_if_needed()
            except Exception as e:
                logging.exception("Could not save %s record", self.table)
                QMessageBox.warning(self,"Could not save",str(e))
    def edit_record(self,*_):
        rid=self.selected_id()
        if rid is None: return
        record=self.db.one(f"SELECT * FROM {self.table} WHERE id=?",(rid,))
        if not record: return
        form=RecordForm(self.db,self.table,"Edit record",record,parent=self)
        if form.exec()==QDialog.Accepted:
            try:
                vals=form.values(); keys=list(vals)
                self.db.run(f"UPDATE {self.table} SET "+",".join(f"{k}=?" for k in keys)+" WHERE id=?",
                            tuple(vals[k] for k in keys)+(rid,))
                self.refresh(); self.app.refresh_dashboard_if_needed()
            except Exception as e:
                logging.exception("Could not update %s record id=%s", self.table, rid)
                QMessageBox.warning(self,"Could not update",str(e))
    def delete_record(self):
        rid=self.selected_id()
        if rid is None: return
        if QMessageBox.question(self,"Delete record","Delete the selected record? This may also remove linked records.")!=QMessageBox.Yes: return
        try:
            self.db.delete_record(self.table, rid)
            self.refresh(); self.app.refresh_dashboard_if_needed()
        except Exception as e:
            logging.exception("Delete failed for %s id=%s", self.table, rid)
            QMessageBox.warning(self,"Delete failed",str(e))
    def custom_fields(self):
        rid=self.selected_id()
        if rid is None: return
        dlg=CustomFieldsDialog(self.db,self.table,rid,self); dlg.exec()
    def complete_task(self):
        rid=self.selected_id()
        if rid is None: return
        try:
            self.db.run("UPDATE planner_tasks SET status='Completed' WHERE id=?", (rid,))
            self.refresh(); self.app.refresh_dashboard_if_needed()
            self.app.statusBar().showMessage("Task marked as completed", 3000)
        except Exception as exc:
            logging.exception("Could not complete task id=%s", rid)
            QMessageBox.warning(self, "Update failed", str(exc))

    def review_selected(self):
        rid=self.selected_id()
        if rid is None: return
        row=self.db.one("SELECT * FROM revisions WHERE id=?", (rid,))
        if not row: return
        # Simple SM-2 inspired schedule: successful reviews grow the interval;
        # the user can still edit interval/ease manually at any time.
        repetitions=int(row["repetitions"] or 0)+1
        ease=max(1.3, float(row["ease"] or 2.5) + 0.1)
        old=max(1, int(row["interval_days"] or 1))
        interval=1 if repetitions==1 else 6 if repetitions==2 else max(old+1, round(old*ease))
        due=(date.today()+timedelta(days=interval)).isoformat()
        try:
            self.db.run("UPDATE revisions SET repetitions=?,ease=?,interval_days=?,due_date=?,last_reviewed=?,status='Due' WHERE id=?",
                        (repetitions,ease,interval,due,TODAY(),rid))
            self.refresh(); self.app.refresh_dashboard_if_needed()
            QMessageBox.information(self,"Review saved",f"Next review scheduled for {due} ({interval} days).")
        except Exception as exc:
            logging.exception("Could not save revision review id=%s", rid)
            QMessageBox.warning(self, "Review failed", str(exc))

    def export_csv(self):
        path,_=QFileDialog.getSaveFileName(self,"Export CSV",f"{self.table}.csv","CSV files (*.csv)")
        if not path: return
        try:
            headers=[self.tableview.horizontalHeaderItem(i).text() for i in range(1,self.tableview.columnCount())]
            with open(path,"w",newline="",encoding="utf-8-sig") as f:
                writer=csv.writer(f); writer.writerow(headers)
                for r in range(self.tableview.rowCount()):
                    if not self.tableview.isRowHidden(r):
                        writer.writerow([self.tableview.item(r,c).text() if self.tableview.item(r,c) else "" for c in range(1,self.tableview.columnCount())])
            QMessageBox.information(self,"Export complete",f"CSV saved to:\n{path}")
        except Exception as e: QMessageBox.warning(self,"Export failed",str(e))

class Dashboard(QWidget):
    def __init__(self, app):
        super().__init__(); self.app=app; self.db=app.db
        root=QVBoxLayout(self); root.setContentsMargins(28,24,28,26); root.setSpacing(16)
        header=QHBoxLayout(); left=QVBoxLayout()
        self.greeting=QLabel("Welcome back"); self.greeting.setObjectName("PageTitle")
        self.subtitle=QLabel("Your study progress, priorities and next steps — all in one place."); self.subtitle.setObjectName("Muted")
        left.addWidget(self.greeting); left.addWidget(self.subtitle); header.addLayout(left); header.addStretch()
        quick=QPushButton("＋ Add today's task"); quick.setObjectName("Primary"); quick.clicked.connect(self.quick_task); header.addWidget(quick)
        log=QPushButton("＋ Log study session"); log.clicked.connect(lambda:self.app.go("Study Sessions")); header.addWidget(log)
        root.addLayout(header)
        self.cards=QGridLayout(); self.cards.setHorizontalSpacing(12); self.cards.setVerticalSpacing(12); root.addLayout(self.cards)
        self.goal_frame=QFrame(); self.goal_frame.setObjectName("Card"); gl=QVBoxLayout(self.goal_frame); gl.setContentsMargins(18,14,18,14)
        top=QHBoxLayout(); goal_title=QLabel("DAILY STUDY GOAL"); goal_title.setObjectName("MetricLabel"); top.addWidget(goal_title); top.addStretch()
        self.goal_label=QLabel(""); self.goal_label.setObjectName("Muted"); top.addWidget(self.goal_label); gl.addLayout(top)
        self.goal_progress=QProgressBar(); self.goal_progress.setTextVisible(True); gl.addWidget(self.goal_progress); root.addWidget(self.goal_frame)
        body=QHBoxLayout(); body.setSpacing(14); root.addLayout(body,1)
        self.leftpanel=QFrame(); self.leftpanel.setObjectName("Card"); lv=QVBoxLayout(self.leftpanel); lv.setContentsMargins(16,14,16,14)
        title=QLabel("Today's plan"); title.setObjectName("SectionTitle"); lv.addWidget(title)
        self.priorities=QTableWidget(0,3); self.priorities.setHorizontalHeaderLabels(["Task","Subject","Status"]); self.setup_table(self.priorities); lv.addWidget(self.priorities)
        self.rightpanel=QFrame(); self.rightpanel.setObjectName("Card"); rv=QVBoxLayout(self.rightpanel); rv.setContentsMargins(16,14,16,14)
        upcoming=QLabel("Upcoming deadlines"); upcoming.setObjectName("SectionTitle"); rv.addWidget(upcoming)
        self.deadlines=QTableWidget(0,4); self.deadlines.setHorizontalHeaderLabels(["Assignment","Subject","Due","Status"]); self.setup_table(self.deadlines); rv.addWidget(self.deadlines)
        self.body_left=QLabel(""); self.body_left.setObjectName("Muted"); rv.addWidget(self.body_left)
        self.tasks_empty=QLabel("No tasks planned for today. Add a task to get started."); self.tasks_empty.setObjectName("EmptyState"); lv.addWidget(self.tasks_empty)
        self.deadlines_empty=QLabel("No outstanding assignments. You're all caught up!"); self.deadlines_empty.setObjectName("EmptyState"); rv.addWidget(self.deadlines_empty)
        body.addWidget(self.leftpanel,3); body.addWidget(self.rightpanel,3)
        self.refresh()

    def setup_table(self, table):
        table.verticalHeader().hide(); table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows); table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setAlternatingRowColors(True); table.setShowGrid(False); table.setMinimumHeight(230)

    def create_metric_card(self, title, value, subtitle, accent):
        frame=QFrame(); frame.setObjectName("Metric"); layout=QVBoxLayout(frame); layout.setContentsMargins(16,13,16,13)
        label=QLabel(title.upper()); label.setObjectName("MetricLabel")
        val=QLabel(str(value)); val.setObjectName("MetricValue"); val.setStyleSheet(f"color:{accent};")
        sub=QLabel(subtitle); sub.setObjectName("Muted")
        layout.addWidget(label); layout.addWidget(val); layout.addWidget(sub); return frame

    def quick_task(self):
        title,ok=QInputDialog.getText(self,"Add a task","What do you want to work on today?")
        if not ok or not title.strip(): return
        try:
            self.db.run("INSERT INTO planner_tasks(task_date,title,category,priority,status,duration_minutes) VALUES(?,?,?,?,?,?)",
                        (TODAY(),title.strip(),"Study","Normal","Pending",30))
            self.refresh(); self.app.statusBar().showMessage("Task added to today's plan",3000)
        except Exception as exc:
            logging.exception("Could not create a quick planner task")
            QMessageBox.warning(self, "Task not saved", str(exc))

    def refresh(self):
        while self.cards.count():
            item=self.cards.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        subjects=self.db.one("SELECT COUNT(*) n FROM subjects")["n"]
        total=self.db.one("SELECT COUNT(*) n FROM units")["n"]
        done=self.db.one("SELECT COUNT(*) n FROM units WHERE status='Completed'")["n"]
        minutes=self.db.one("SELECT COALESCE(SUM(duration_minutes),0) n FROM study_sessions")["n"]
        pending=self.db.one("SELECT COUNT(*) n FROM assignments WHERE status!='Submitted'")["n"]
        today_minutes=self.db.one("SELECT COALESCE(SUM(duration_minutes),0) n FROM study_sessions WHERE study_date=?",(TODAY(),))["n"]
        active_days={r["study_date"] for r in self.db.q("SELECT DISTINCT study_date FROM study_sessions WHERE study_date>=?",((date.today()-timedelta(days=365)).isoformat(),))}
        streak=0; cursor=date.today()
        if cursor.isoformat() not in active_days: cursor-=timedelta(days=1)
        while cursor.isoformat() in active_days:
            streak+=1; cursor-=timedelta(days=1)
        metrics=[("Subjects",subjects,"Courses in your workspace",self.app.accent),
                 ("Syllabus",f"{done}/{total}",f"{round(done/total*100) if total else 0}% completed","#159A91"),
                 ("Study time",format_minutes(minutes),"All recorded sessions","#8765D8"),
                 ("Pending work",pending,"Assignments not submitted","#C58A2B"),
                 ("Study streak",f"{streak} day(s)","Consecutive study days","#D65B7A")]
        for i,m in enumerate(metrics): self.cards.addWidget(self.create_metric_card(*m),0,i)
        hour=datetime.now().hour
        self.greeting.setText("Good morning" if hour<12 else "Good afternoon" if hour<18 else "Good evening")
        target=max(1,safe_int(self.db.setting("daily_goal","180"),180))
        self.goal_progress.setRange(0,target); self.goal_progress.setValue(min(today_minutes,target))
        self.goal_progress.setFormat(f"{today_minutes} / {target} min · {min(100,round(today_minutes/target*100))}%")
        self.goal_label.setText("Goal reached 🎉" if today_minutes>=target else f"{max(0,target-today_minutes)} min remaining")
        tasks=self.db.q("""SELECT p.title,COALESCE(s.name,'—') subject,p.status FROM planner_tasks p
                           LEFT JOIN subjects s ON s.id=p.subject_id WHERE p.task_date=?
                           ORDER BY CASE p.priority WHEN 'Urgent' THEN 0 WHEN 'High' THEN 1 WHEN 'Normal' THEN 2 ELSE 3 END,p.id DESC LIMIT 20""",(TODAY(),))
        self.priorities.setRowCount(len(tasks))
        self.tasks_empty.setVisible(not tasks)
        self.priorities.setVisible(bool(tasks))
        for i,r in enumerate(tasks):
            for j,v in enumerate(r): self.priorities.setItem(i,j,QTableWidgetItem(str(v)))
        deadlines=self.db.q("""SELECT a.title,s.name,a.due_date,a.status FROM assignments a JOIN subjects s ON s.id=a.subject_id
                               WHERE a.status!='Submitted' AND a.due_date!='' ORDER BY a.due_date LIMIT 10""")
        self.deadlines.setRowCount(len(deadlines))
        self.deadlines_empty.setVisible(not deadlines)
        self.deadlines.setVisible(bool(deadlines))
        for i,r in enumerate(deadlines):
            for j,v in enumerate(r): self.deadlines.setItem(i,j,QTableWidgetItem(str(v)))
        overdue = self.db.one(
            "SELECT COUNT(*) n FROM assignments WHERE status!='Submitted' AND due_date!='' AND due_date<?",
            (TODAY(),)
        )["n"]
        total_open = self.db.one("SELECT COUNT(*) n FROM assignments WHERE status!='Submitted'")["n"]
        self.body_left.setText(f"{overdue} overdue · {total_open} outstanding assignments")

class FocusPage(QWidget):
    def __init__(self,app):
        super().__init__(); self.app=app; self.db=app.db; self.remaining=25*60; self.running=False; self.mode="Focus"
        lay=QVBoxLayout(self); lay.setContentsMargins(32,30,32,30); lay.setSpacing(20)
        title=QLabel("Focus Room"); title.setObjectName("PageTitle"); lay.addWidget(title)
        desc=QLabel("Protect your attention. Small focused sessions add up."); desc.setObjectName("Muted"); lay.addWidget(desc)
        card=QFrame(); card.setObjectName("Card"); c=QVBoxLayout(card); c.setContentsMargins(30,30,30,30); c.setAlignment(Qt.AlignCenter)
        self.mode_label=QLabel("FOCUS SESSION"); self.mode_label.setObjectName("MetricLabel"); c.addWidget(self.mode_label,0,Qt.AlignCenter)
        self.clock=QLabel("25:00"); self.clock.setObjectName("Timer"); c.addWidget(self.clock,0,Qt.AlignCenter)
        self.progress=QProgressBar(); self.progress.setRange(0,1500); self.progress.setValue(1500); self.progress.setFixedWidth(360); c.addWidget(self.progress,0,Qt.AlignCenter)
        self.subject=QComboBox(); self.subject.setMinimumWidth(280); self.subject.setToolTip("Choose the subject to attach to this focus session"); c.addWidget(self.subject,0,Qt.AlignCenter)
        row=QHBoxLayout()
        for label,mode in [("Focus","Focus"),("Short break","Short"),("Long break","Long")]:
            b=QPushButton(label); b.clicked.connect(lambda checked=False,m=mode:self.set_mode(m)); row.addWidget(b)
        c.addLayout(row)
        controls=QHBoxLayout()
        self.start_btn=QPushButton("▶  Start"); self.start_btn.setObjectName("Primary"); self.start_btn.clicked.connect(self.toggle)
        reset=QPushButton("↺  Reset"); reset.clicked.connect(lambda:self.set_mode(self.mode))
        controls.addWidget(self.start_btn); controls.addWidget(reset); c.addLayout(controls)
        lay.addWidget(card,1)
        self._deadline = None
        self.timer=QTimer(self); self.timer.setTimerType(Qt.PreciseTimer); self.timer.setInterval(250); self.timer.timeout.connect(self.tick)
        self.refresh_subjects(); self.set_mode("Focus")
    def refresh_subjects(self):
        self.subject.clear()
        for r in self.db.q("SELECT id,name FROM subjects ORDER BY name"): self.subject.addItem(r["name"],r["id"])
    def set_mode(self,mode):
        self.timer.stop(); self.running=False; self._deadline=None; self.mode=mode
        key={"Focus":"pomodoro_work","Short":"pomodoro_short","Long":"pomodoro_long"}[mode]
        mins=int(self.db.setting(key,{"Focus":25,"Short":5,"Long":15}[mode]))
        self.remaining=mins*60; self.total=max(1,self.remaining)
        self.mode_label.setText("FOCUS SESSION" if mode=="Focus" else "SHORT BREAK" if mode=="Short" else "LONG BREAK")
        self.start_btn.setText("▶  Start"); self.render()
    def toggle(self):
        if self.running:
            self.remaining = max(0, math.ceil((self._deadline or time.monotonic()) - time.monotonic()))
            self.timer.stop(); self.running=False; self._deadline=None; self.start_btn.setText("▶  Resume")
            self.render()
        else:
            if self.mode == "Focus" and not self.subject.currentData():
                QMessageBox.information(self, "Choose a subject", "Add a subject in the Subjects section before starting a focus session.")
                return
            self._deadline = time.monotonic() + self.remaining
            self.timer.start(); self.running=True; self.start_btn.setText("Ⅱ  Pause")
    def tick(self):
        self.remaining = max(0, math.ceil((self._deadline or time.monotonic()) - time.monotonic()))
        if self.remaining > 0:
            self.render()
            return
        self.timer.stop(); self.running=False; self._deadline=None; self.start_btn.setText("▶  Start")
        if self.mode == "Focus":
            if self.subject.currentData():
                self.db.run("INSERT INTO study_sessions(subject_id,study_date,duration_minutes,topic,session_type) VALUES(?,?,?,?,?)",
                            (self.subject.currentData(), TODAY(), max(1, self.total // 60), "Pomodoro focus", "Study"))
                self.app.refresh_dashboard_if_needed()
                QMessageBox.information(self, "Focus session complete", "Excellent work! Your focus session has been saved.")
            else:
                QMessageBox.information(self, "Focus session complete", "Excellent work! Add a subject next time to save this session in your study history.")
            self.set_mode("Short")
        else:
            QMessageBox.information(self, "Break complete", "Break finished. Ready for another focus session?")
            self.set_mode("Focus")

    def render(self):
        self.clock.setText(f"{self.remaining//60:02d}:{self.remaining%60:02d}")
        self.progress.setRange(0,self.total); self.progress.setValue(self.remaining)


class PDFReportOptionsDialog(QDialog):
    """Responsive, scrollable and keyboard-friendly PDF report options."""
    SECTION_LABELS = [
        ("overview", "Executive overview"),
        ("subjects", "Subjects and syllabus progress"),
        ("assignments", "Assignments and deadlines"),
        ("exams", "Exams and timetable"),
        ("marks", "Marks and results"),
        ("study", "Study sessions and activity"),
        ("planner", "Planner tasks"),
        ("revisions", "Revision schedule"),
    ]
    PROFILES = {
        "Complete academic report": {key for key, _ in SECTION_LABELS},
        "Executive summary": {"overview", "subjects"},
        "Syllabus and subject progress": {"overview", "subjects"},
        "Assignments and exam planner": {"overview", "assignments", "exams", "planner"},
        "Marks and results": {"overview", "exams", "marks"},
        "Study activity report": {"overview", "study", "planner", "revisions"},
        "Custom selection": set(),
    }

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._updating_profile = False
        self._responsive_grids = []
        self._last_responsive_columns = None
        self.setObjectName("PDFReportOptionsDialog")
        self.setWindowTitle("PDF report options")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setSizeGripEnabled(True)

        # Size to the available display, but keep the content scrollable on
        # short displays instead of allowing Qt to compress form controls.
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        if available is not None:
            min_width = max(320, min(560, available.width() - 20))
            min_height = max(400, min(520, available.height() - 40))
            target_width = min(920, max(min_width, int(available.width() * 0.76)))
            target_height = min(900, max(min_height, int(available.height() * 0.86)))
        else:
            min_width, min_height = 560, 520
            target_width, target_height = 820, 780
        self.setMinimumSize(min_width, min_height)
        self.resize(target_width, target_height)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        # Header remains visible while the form itself scrolls.
        header = QFrame(self)
        header.setObjectName("PDFOptionsHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(2, 0, 2, 4)
        header_layout.setSpacing(4)
        title = QLabel("Build your academic report")
        title.setObjectName("DialogTitle")
        title.setWordWrap(True)
        subtitle = QLabel("Choose the content, scope and layout for your PDF report.")
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        root.addWidget(header)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("PDFOptionsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setMinimumHeight(160)

        content = QWidget()
        content.setObjectName("PDFOptionsScrollContents")
        content.setMinimumWidth(0)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(2, 4, 10, 8)
        content_layout.setSpacing(12)
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, 1)

        # ---- Report content -------------------------------------------------
        details = self._group("Report content")
        details_grid = QGridLayout(details)
        self._prepare_grid(details_grid)

        self.profile = QComboBox()
        self.profile.addItems(list(self.PROFILES))
        self.profile.setCurrentText("Complete academic report")
        self.report_title = QLineEdit("Academic Progress Report")
        self.report_title.setMaxLength(120)
        self.report_title.setClearButtonEnabled(True)
        self.subject = QComboBox()
        self.subject.addItem("All subjects", None)
        for row in self.db.q("SELECT id, name, code FROM subjects ORDER BY name"):
            label = row["name"] + (f" ({row['code']})" if row["code"] else "")
            self.subject.addItem(label, int(row["id"]))

        self._details_fields = [
            self._field("Report preset", self.profile),
            self._field("Report title", self.report_title),
            self._field("Subject filter", self.subject),
        ]
        self._responsive_grids.append((details_grid, self._details_fields, "fields"))
        content_layout.addWidget(details)

        # ---- Sections -------------------------------------------------------
        sections_group = self._group("Sections to include")
        sections_root = QVBoxLayout(sections_group)
        sections_root.setContentsMargins(12, 22, 12, 12)
        sections_root.setSpacing(8)
        section_actions = QHBoxLayout()
        section_actions.setSpacing(8)
        section_hint = QLabel("Choose the sections you want in this report.")
        section_hint.setObjectName("Muted")
        section_hint.setWordWrap(True)
        section_actions.addWidget(section_hint, 1)
        select_all = QPushButton("Select all")
        select_all.setObjectName("PDFSecondaryButton")
        select_all.setToolTip("Include every report section")
        clear_all = QPushButton("Clear")
        clear_all.setObjectName("PDFSecondaryButton")
        clear_all.setToolTip("Clear all section selections")
        select_all.clicked.connect(lambda: self._set_all_sections(True))
        clear_all.clicked.connect(lambda: self._set_all_sections(False))
        section_actions.addWidget(select_all)
        section_actions.addWidget(clear_all)
        sections_root.addLayout(section_actions)

        self.section_checks = {}
        section_widgets = []
        for key, label in self.SECTION_LABELS:
            check = QCheckBox(label)
            check.setMinimumHeight(32)
            check.setToolTip(f"Include {label.lower()} in the PDF")
            check.toggled.connect(self._section_selection_changed)
            self.section_checks[key] = check
            section_widgets.append(check)
        sections_grid = QGridLayout()
        self._prepare_grid(sections_grid)
        sections_root.addLayout(sections_grid)
        self._responsive_grids.append((sections_grid, section_widgets, "checks"))
        content_layout.addWidget(sections_group)

        # ---- Date range -----------------------------------------------------
        scope_group = self._group("Date range and detail")
        scope_layout = QVBoxLayout(scope_group)
        scope_layout.setContentsMargins(12, 22, 12, 12)
        scope_layout.setSpacing(8)
        self.filter_dates = QCheckBox("Filter dated records to this range")
        self.filter_dates.setMinimumHeight(32)
        self.filter_dates.setToolTip("Only include dated records between the selected start and end dates.")
        scope_layout.addWidget(self.filter_dates)

        self.date_from = QDateEdit(QDate.currentDate().addDays(-30))
        self.date_to = QDateEdit(QDate.currentDate())
        for widget in (self.date_from, self.date_to):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("dd MMM yyyy")
            widget.setMinimumDate(QDate(2000, 1, 1))
            widget.setMinimumHeight(40)
        self.date_from.setAccessibleName("Start date")
        self.date_to.setAccessibleName("End date")
        self.date_from.setEnabled(False)
        self.date_to.setEnabled(False)
        self._date_fields = [
            self._field("From", self.date_from),
            self._field("To", self.date_to),
        ]
        date_grid = QGridLayout()
        self._prepare_grid(date_grid)
        scope_layout.addLayout(date_grid)
        self._responsive_grids.append((date_grid, self._date_fields, "fields"))
        self.filter_dates.toggled.connect(self.date_from.setEnabled)
        self.filter_dates.toggled.connect(self.date_to.setEnabled)
        content_layout.addWidget(scope_group)

        # ---- PDF design ----------------------------------------------------
        display_group = self._group("PDF design and export")
        display_grid = QGridLayout(display_group)
        self._prepare_grid(display_grid)
        self.page_size = QComboBox()
        self.page_size.addItems(["A4", "Letter", "A3"])
        self.orientation = QComboBox()
        self.orientation.addItems(["Portrait", "Landscape"])
        self.report_palette = QComboBox()
        self.report_palette.addItems(["Professional blue", "Emerald", "Violet", "Monochrome"])
        self.density = QComboBox()
        self.density.addItems(["Comfortable", "Compact", "Detailed"])
        self.row_limit = QComboBox()
        self.row_limit.addItem("All records", None)
        self.row_limit.addItem("Up to 100 rows per section", 100)
        self.row_limit.addItem("Up to 500 rows per section", 500)
        self.row_limit.addItem("Up to 1,000 rows per section", 1000)
        self.row_limit.setCurrentIndex(2)
        self._display_fields = [
            self._field("Paper size", self.page_size),
            self._field("Orientation", self.orientation),
            self._field("Colour theme", self.report_palette),
            self._field("Table density", self.density),
            self._field("Maximum records per section", self.row_limit),
        ]
        self._responsive_grids.append((display_grid, self._display_fields, "fields"))
        content_layout.addWidget(display_group)

        # ---- Additional report options ------------------------------------
        extras_group = self._group("Additional options")
        extras_grid = QGridLayout(extras_group)
        self._prepare_grid(extras_grid)
        self.include_cover = QCheckBox("Add a separate cover page")
        self.include_cover.setChecked(False)
        self.include_charts = QCheckBox("Include progress and activity charts")
        self.include_charts.setChecked(True)
        self.include_notes = QCheckBox("Include notes and descriptions")
        self.include_notes.setChecked(False)
        self.include_completed = QCheckBox("Include completed / submitted items")
        self.include_completed.setChecked(True)
        self.include_completed.setToolTip("Turn this off to focus on pending assignments and unfinished planner tasks.")
        self.open_after = QCheckBox("Open the PDF after it is created")
        self.open_after.setChecked(True)
        self._extra_checks = [
            self.include_cover, self.include_charts, self.include_notes,
            self.include_completed, self.open_after,
        ]
        for check in self._extra_checks:
            check.setMinimumHeight(32)
        self._responsive_grids.append((extras_grid, self._extra_checks, "checks"))
        content_layout.addWidget(extras_group)
        content_layout.addStretch(1)

        # ---- Persistent footer ---------------------------------------------
        footer = QFrame(self)
        footer.setObjectName("PDFOptionsFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 8, 0, 0)
        footer_layout.setSpacing(10)
        footer_layout.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("PDFSecondaryButton")
        cancel.setMinimumWidth(104)
        cancel.clicked.connect(self.reject)
        create = QPushButton("Continue to save PDF")
        create.setObjectName("Primary")
        create.setMinimumWidth(190)
        create.setDefault(True)
        create.clicked.connect(self.validate_and_accept)
        footer_layout.addWidget(cancel)
        footer_layout.addWidget(create)
        root.addWidget(footer)

        self.profile.currentTextChanged.connect(self.apply_profile)
        self.apply_profile(self.profile.currentText())
        self._apply_dialog_style()
        self._reflow_responsive_layouts()

        # Useful keyboard behaviour: Escape cancels, Enter activates the default
        # action when focus is not inside a multi-line editor.
        self.setModal(True)

    @staticmethod
    def _prepare_grid(layout):
        layout.setContentsMargins(12, 22, 12, 12)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(12)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)

    def _group(self, title):
        group = QGroupBox(title, self)
        group.setObjectName("PDFOptionsGroup")
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        return group

    def _field(self, label_text, widget):
        container = QWidget()
        container.setObjectName("PDFFieldContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        label = QLabel(label_text)
        label.setObjectName("PDFFieldLabel")
        label.setWordWrap(True)
        layout.addWidget(label)
        widget.setMinimumHeight(40)
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(widget)
        return container

    def _apply_dialog_style(self):
        palette = super().palette()
        window = palette.color(QPalette.Window).name()
        base = palette.color(QPalette.Base).name()
        text = palette.color(QPalette.Text).name()
        muted = palette.color(QPalette.PlaceholderText).name()
        border = palette.color(QPalette.Mid).name()
        accent = palette.color(QPalette.Highlight).name()
        self.setStyleSheet(f"""
            QDialog#PDFReportOptionsDialog {{
                background-color: {window};
                color: {text};
            }}
            QFrame#PDFOptionsHeader, QFrame#PDFOptionsFooter,
            QWidget#PDFOptionsScrollContents, QWidget#PDFFieldContainer {{
                background-color: transparent;
                border: none;
            }}
            QScrollArea#PDFOptionsScroll {{
                background-color: transparent;
                border: none;
            }}
            QGroupBox#PDFOptionsGroup {{
                background-color: {base};
                color: {text};
                border: 1px solid {border};
                border-radius: 4px;
                margin-top: 12px;
                padding: 5px 6px 8px 6px;
                font-weight: 700;
            }}
            QGroupBox#PDFOptionsGroup::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: {text};
                background-color: {window};
            }}
            QLabel#PDFFieldLabel {{
                background-color: transparent;
                color: {text};
                font-size: 9pt;
                font-weight: 600;
                padding: 1px 1px;
            }}
            QComboBox, QLineEdit, QDateEdit {{
                background-color: {base};
                color: {text};
                border: 1px solid {border};
                border-radius: 3px;
                min-height: 26px;
                padding: 5px 8px;
                selection-background-color: {accent};
                selection-color: white;
            }}
            QComboBox:focus, QLineEdit:focus, QDateEdit:focus {{
                border: 1px solid {accent};
            }}
            QComboBox:disabled, QLineEdit:disabled, QDateEdit:disabled {{
                color: {muted};
            }}
            QComboBox QAbstractItemView {{
                background-color: {base};
                color: {text};
                border: 1px solid {border};
                selection-background-color: {accent};
                selection-color: white;
                outline: 0;
            }}
            QCheckBox {{
                background-color: transparent;
                color: {text};
                spacing: 8px;
                min-height: 28px;
                padding: 3px 2px;
            }}
            QPushButton {{
                min-height: 32px;
                border-radius: 4px;
                padding: 6px 12px;
            }}
            QPushButton#PDFSecondaryButton {{
                background-color: {base};
                color: {text};
                border: 1px solid {border};
                min-height: 34px;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: 600;
            }}
            QPushButton#PDFSecondaryButton:hover {{
                border-color: {accent};
            }}
            QScrollBar:vertical {{
                width: 10px;
                background-color: transparent;
                margin: 2px;
            }}
            QScrollBar::handle:vertical {{
                background-color: {border};
                min-height: 28px;
                border-radius: 3px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

    def _set_grid_items(self, layout, widgets, columns):
        while layout.count():
            item = layout.takeAt(0)
            # Widgets stay parented to their original container; only their
            # layout positions are rebuilt for the new available width.
            del item
        columns = max(1, int(columns))
        for index, widget in enumerate(widgets):
            layout.addWidget(widget, index // columns, index % columns)
        for column in range(max(2, columns)):
            layout.setColumnStretch(column, 1 if column < columns else 0)

    def _reflow_responsive_layouts(self):
        if not self._responsive_grids:
            return
        width = self.width()
        field_columns = 1 if width < 660 else 2
        check_columns = 1 if width < 590 else 2
        column_state = (field_columns, check_columns, len(self._responsive_grids))
        if column_state == self._last_responsive_columns:
            return
        self._last_responsive_columns = column_state
        for layout, widgets, kind in self._responsive_grids:
            columns = field_columns if kind == "fields" else check_columns
            self._set_grid_items(layout, widgets, columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_responsive_layouts()

    def _set_all_sections(self, checked):
        self._updating_profile = True
        try:
            for check in self.section_checks.values():
                check.setChecked(bool(checked))
        finally:
            self._updating_profile = False
        self.profile.setCurrentText("Custom selection")

    def _section_selection_changed(self, _checked=False):
        if not self._updating_profile and self.profile.currentText() != "Custom selection":
            self.profile.setCurrentText("Custom selection")

    def apply_profile(self, profile):
        if profile == "Custom selection":
            return
        selected = self.PROFILES.get(profile, self.PROFILES["Complete academic report"])
        self._updating_profile = True
        try:
            for key, check in self.section_checks.items():
                check.setChecked(key in selected)
        finally:
            self._updating_profile = False

    def validate_and_accept(self):
        if not self.report_title.text().strip():
            QMessageBox.warning(self, "Report title required", "Enter a title for your PDF report.")
            self.report_title.setFocus()
            return
        if self.filter_dates.isChecked() and self.date_from.date() > self.date_to.date():
            QMessageBox.warning(self, "Invalid date range", "The start date must be on or before the end date.")
            self.date_from.setFocus()
            return
        if not any(check.isChecked() for check in self.section_checks.values()):
            QMessageBox.warning(self, "Choose a section", "Select at least one report section.")
            self.scroll.ensureVisible(0, 0)
            return
        self.accept()

    def options(self):
        return {
            "profile": self.profile.currentText(),
            "title": self.report_title.text().strip(),
            "subject_id": self.subject.currentData(),
            "sections": {key: check.isChecked() for key, check in self.section_checks.items()},
            "filter_dates": self.filter_dates.isChecked(),
            "date_from": self.date_from.date().toString("yyyy-MM-dd"),
            "date_to": self.date_to.date().toString("yyyy-MM-dd"),
            "page_size": self.page_size.currentText(),
            "orientation": self.orientation.currentText(),
            "palette": self.report_palette.currentText(),
            "density": self.density.currentText(),
            "record_limit": self.row_limit.currentData(),
            "include_cover": self.include_cover.isChecked(),
            "include_charts": self.include_charts.isChecked(),
            "include_notes": self.include_notes.isChecked(),
            "include_completed": self.include_completed.isChecked(),
            "open_after": self.open_after.isChecked(),
        }

def create_academic_pdf_report(db, output_path, options):
    """Create a polished, paginated report from the local SQLite database."""
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4, A3, LETTER, landscape, portrait
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, LongTable, TableStyle,
        PageBreak, Flowable
    )
    import tempfile

    palette_options = {
        "Professional blue": {"primary": "#2457A7", "deep": "#17345F", "accent": "#4E83D9", "soft": "#EAF1FB", "surface": "#F7F9FC", "border": "#D9E2EF", "text": "#1D2939", "muted": "#667085", "good": "#16845B", "warning": "#B7791F"},
        "Emerald": {"primary": "#087F68", "deep": "#064E42", "accent": "#18A486", "soft": "#E7F5F1", "surface": "#F5FAF8", "border": "#D4E8E1", "text": "#1C302B", "muted": "#61766F", "good": "#16845B", "warning": "#A56B14"},
        "Violet": {"primary": "#6941A5", "deep": "#39245F", "accent": "#9169D1", "soft": "#F0EAF9", "surface": "#FAF8FD", "border": "#E1D8EF", "text": "#2B2438", "muted": "#70677D", "good": "#16845B", "warning": "#A56B14"},
        "Monochrome": {"primary": "#343A40", "deep": "#171A1D", "accent": "#737B84", "soft": "#ECEFF1", "surface": "#F7F8F9", "border": "#D9DDE1", "text": "#202428", "muted": "#626B73", "good": "#3D765D", "warning": "#8A6B35"},
    }
    palette = palette_options.get(options.get("palette"), palette_options["Professional blue"])
    C = {key: colors.HexColor(value) for key, value in palette.items()}

    page_sizes = {"A4": A4, "Letter": LETTER, "A3": A3}
    page_size = page_sizes.get(options.get("page_size", "A4"), A4)
    page_size = landscape(page_size) if options.get("orientation") == "Landscape" else portrait(page_size)

    # Embed a widely available Unicode font when possible; built-in Helvetica remains the fallback.
    regular_font, bold_font = "Helvetica", "Helvetica-Bold"
    font_pairs = [
        (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
        (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
    ]
    for regular_path, bold_path in font_pairs:
        if Path(regular_path).exists():
            try:
                if "StudySpaceRegular" not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont("StudySpaceRegular", regular_path))
                regular_font = "StudySpaceRegular"
                if Path(bold_path).exists():
                    if "StudySpaceBold" not in pdfmetrics.getRegisteredFontNames():
                        pdfmetrics.registerFont(TTFont("StudySpaceBold", bold_path))
                    bold_font = "StudySpaceBold"
                break
            except Exception:
                continue

    density = options.get("density", "Comfortable")
    compact = density == "Compact"
    detailed = density == "Detailed"
    body_size = 7.0 if compact else 8.1 if detailed else 7.6
    cell_pad = 3.4 if compact else 6.0 if detailed else 4.6
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="SSCoverBrand", fontName=bold_font, fontSize=8, leading=10, textColor=colors.white, spaceAfter=5))
    styles.add(ParagraphStyle(name="SSCoverTitle", fontName=bold_font, fontSize=23, leading=27, textColor=colors.white, spaceAfter=5))
    styles.add(ParagraphStyle(name="SSCoverSub", fontName=regular_font, fontSize=9, leading=13, textColor=colors.HexColor("#EAF1FB")))
    styles.add(ParagraphStyle(name="SSBody", fontName=regular_font, fontSize=8.2, leading=12, textColor=C["text"], spaceAfter=3))
    styles.add(ParagraphStyle(name="SSMuted", fontName=regular_font, fontSize=7.2, leading=10, textColor=C["muted"]))
    styles.add(ParagraphStyle(name="SSSection", fontName=bold_font, fontSize=12.2, leading=15, textColor=C["deep"], spaceBefore=12, spaceAfter=6, keepWithNext=True))
    styles.add(ParagraphStyle(name="SSTableHead", fontName=bold_font, fontSize=7.1, leading=9, textColor=colors.white))
    styles.add(ParagraphStyle(name="SSTableCell", fontName=regular_font, fontSize=body_size, leading=body_size + 2, textColor=C["text"]))
    styles.add(ParagraphStyle(name="SSTableSmall", fontName=regular_font, fontSize=max(6.3, body_size - 0.6), leading=max(8, body_size + 1), textColor=C["muted"]))
    styles.add(ParagraphStyle(name="SSKpiLabel", fontName=bold_font, fontSize=6.6, leading=8, textColor=C["muted"]))
    styles.add(ParagraphStyle(name="SSKpiValue", fontName=bold_font, fontSize=17, leading=19, textColor=C["deep"]))
    styles.add(ParagraphStyle(name="SSKpiNote", fontName=regular_font, fontSize=6.3, leading=8, textColor=C["muted"]))
    styles.add(ParagraphStyle(name="SSCallout", fontName=regular_font, fontSize=7.6, leading=11, textColor=C["text"]))

    def text_value(value):
        if value is None:
            return ""
        value = str(value)
        if regular_font == "Helvetica":
            replacements = {"–": "-", "—": "-", "•": "-", "·": "|", "’": "'", "‘": "'", "“": '"', "”": '"', "→": ">"}
            for old, new in replacements.items():
                value = value.replace(old, new)
        return escape(value).replace("\n", "<br/>")

    def display_date(value):
        if not value:
            return "-"
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%d/%m/%y")
        except Exception:
            return str(value)

    def display_number(value):
        if value is None:
            return "-"
        try:
            number = float(value)
            return str(int(number)) if number.is_integer() else f"{number:.2f}".rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            return str(value)

    def P(value, style="SSTableCell"):
        return Paragraph(text_value(value), styles[style])

    def date_clause(column, params):
        if not options.get("filter_dates"):
            return "", []
        return f" AND {column} >= ? AND {column} <= ?", [options["date_from"], options["date_to"]]

    selected_subject = options.get("subject_id")
    sections = options.get("sections", {})
    include_notes = bool(options.get("include_notes"))
    include_completed = bool(options.get("include_completed", True))
    today_iso = date.today().isoformat()

    def subject_clause(column):
        return (f" AND {column} = ?", [selected_subject]) if selected_subject is not None else ("", [])

    def scalar(sql, args=()):
        row = db.one(sql, tuple(args))
        return row[0] if row else 0

    subject_where, subject_args = subject_clause("s.id")
    subject_rows = db.q(
        "SELECT s.id, s.name, s.code, s.notes FROM subjects s WHERE 1=1" + subject_where + " ORDER BY s.name",
        subject_args,
    )
    subject_ids = [int(row["id"]) for row in subject_rows]
    if selected_subject is not None and not subject_ids:
        raise ValueError("The selected subject no longer exists. Choose All subjects and try again.")

    if subject_ids:
        placeholders = ",".join("?" for _ in subject_ids)
        total_units = scalar(f"SELECT COUNT(*) FROM units WHERE subject_id IN ({placeholders})", subject_ids)
        done_units = scalar(f"SELECT COUNT(*) FROM units WHERE status='Completed' AND subject_id IN ({placeholders})", subject_ids)
        assign_date_filter, assign_date_args = date_clause("due_date", [])
        assignment_total = scalar(f"SELECT COUNT(*) FROM assignments WHERE subject_id IN ({placeholders})" + assign_date_filter, [*subject_ids, *assign_date_args])
        pending_assignments = scalar(f"SELECT COUNT(*) FROM assignments WHERE status!='Submitted' AND subject_id IN ({placeholders})" + assign_date_filter, [*subject_ids, *assign_date_args])
        overdue_assignments = scalar(f"SELECT COUNT(*) FROM assignments WHERE status!='Submitted' AND due_date!='' AND due_date < ? AND subject_id IN ({placeholders})" + assign_date_filter, [today_iso, *subject_ids, *assign_date_args])
        exam_date_filter, exam_date_args = date_clause("exam_date", [])
        exam_total = scalar(f"SELECT COUNT(*) FROM exams WHERE subject_id IN ({placeholders})" + exam_date_filter, [*subject_ids, *exam_date_args])
        study_filter, study_params = date_clause("study_date", [])
        total_minutes = scalar(f"SELECT COALESCE(SUM(duration_minutes),0) FROM study_sessions WHERE subject_id IN ({placeholders})" + study_filter, [*subject_ids, *study_params])
        marks_date_filter, marks_date_args = date_clause("assessment_date", [])
        mark_totals = db.one(f"SELECT COALESCE(SUM(marks_obtained),0) got, COALESCE(SUM(max_marks),0) maximum FROM marks WHERE subject_id IN ({placeholders})" + marks_date_filter, [*subject_ids, *marks_date_args])
    else:
        total_units = done_units = assignment_total = pending_assignments = overdue_assignments = exam_total = total_minutes = 0
        mark_totals = {"got": 0, "maximum": 0}
    overall_progress = (done_units / total_units * 100) if total_units else 0
    marks_average = (float(mark_totals["got"]) / float(mark_totals["maximum"]) * 100) if mark_totals["maximum"] else None
    study_days = 0
    if subject_ids:
        study_days = scalar(
            f"SELECT COUNT(DISTINCT study_date) FROM study_sessions WHERE subject_id IN ({','.join('?' for _ in subject_ids)})" + study_filter,
            [*subject_ids, *study_params],
        )

    page_width, page_height = page_size
    margin_left = 16 * mm
    margin_right = 16 * mm
    report_width = page_size[0] - margin_left - margin_right
    story = []
    report_title = options.get("title", "Academic Progress Report")
    semester_label = db.setting("semester_name", "B.Tech Semester")
    generated_at = datetime.now().strftime("%d %B %Y, %I:%M %p")
    subject_label = subject_rows[0]["name"] if selected_subject is not None else "All subjects"
    period_label = f"{display_date(options['date_from'])} to {display_date(options['date_to'])}" if options.get("filter_dates") else "All available dates"

    class ProgressBar(Flowable):
        def __init__(self, value):
            super().__init__()
            self.value = max(0.0, min(100.0, float(value or 0)))
            self.height = 14
        def wrap(self, availWidth, availHeight):
            self.width = availWidth
            return availWidth, self.height
        def draw(self):
            self.canv.setFillColor(C["soft"])
            self.canv.roundRect(0, 5, self.width, 5, 2.5, fill=1, stroke=0)
            fill_width = self.width * self.value / 100
            if fill_width > 0.5:
                self.canv.setFillColor(C["accent"])
                self.canv.roundRect(0, 5, fill_width, 5, 2.5, fill=1, stroke=0)
            self.canv.setFillColor(C["muted"])
            self.canv.setFont(regular_font, 6.3)
            self.canv.drawRightString(self.width, 0, f"{self.value:.0f}%")

    class ActivityChart(Flowable):
        def __init__(self, rows):
            super().__init__()
            self.rows = rows
            self.height = max(28, len(rows) * 15 + 8)
        def wrap(self, availWidth, availHeight):
            self.width = availWidth
            return availWidth, self.height
        def draw(self):
            max_minutes = max([int(row[1] or 0) for row in self.rows] or [0]) or 1
            label_width = min(52, self.width * 0.22)
            value_width = 48
            bar_x = label_width + 5
            bar_width = max(20, self.width - label_width - value_width - 12)
            y = self.height - 12
            for label, minutes_value in self.rows:
                mins = int(minutes_value or 0)
                self.canv.setFont(regular_font, 6.6)
                self.canv.setFillColor(C["muted"])
                self.canv.drawString(0, y + 1, str(label)[:12])
                self.canv.setFillColor(C["soft"])
                self.canv.roundRect(bar_x, y, bar_width, 7, 3, fill=1, stroke=0)
                if mins:
                    self.canv.setFillColor(C["accent"])
                    self.canv.roundRect(bar_x, y, max(2, bar_width * mins / max_minutes), 7, 3, fill=1, stroke=0)
                self.canv.setFillColor(C["text"])
                self.canv.drawRightString(self.width, y + 1, f"{mins // 60}h {mins % 60:02d}m")
                y -= 15

    def add_hero():
        hero = Table([
            [P("STUDY SPACE PRO  /  ACADEMIC WORKSPACE", "SSCoverBrand")],
            [P(report_title, "SSCoverTitle")],
            [P(f"{semester_label}  |  {subject_label}\nGenerated {generated_at}", "SSCoverSub")],
        ], colWidths=[report_width])
        hero.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), C["deep"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 18), ("RIGHTPADDING", (0, 0), (-1, -1), 18),
            ("TOPPADDING", (0, 0), (-1, 0), 13), ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
            ("TOPPADDING", (0, 1), (-1, 1), 2), ("BOTTOMPADDING", (0, 1), (-1, 1), 3),
            ("TOPPADDING", (0, 2), (-1, 2), 2), ("BOTTOMPADDING", (0, 2), (-1, 2), 14),
        ]))
        story.append(hero)
        story.append(Spacer(1, 7 * mm))

    def kpi_card(label, value, note):
        inner = Table([[P(label.upper(), "SSKpiLabel")], [P(value, "SSKpiValue")], [P(note, "SSKpiNote")]], colWidths=[report_width / 4 - 12])
        inner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), C["surface"]),
            ("BOX", (0, 0), (-1, -1), 0.6, C["border"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, 0), 8), ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
            ("TOPPADDING", (0, 1), (-1, 1), 1), ("BOTTOMPADDING", (0, 1), (-1, 1), 1),
            ("TOPPADDING", (0, 2), (-1, 2), 1), ("BOTTOMPADDING", (0, 2), (-1, 2), 8),
        ]))
        return inner

    def add_kpis():
        cards = [
            kpi_card("Syllabus completed", f"{overall_progress:.0f}%", f"{done_units} of {total_units} units"),
            kpi_card("Study time", f"{total_minutes // 60}h {total_minutes % 60:02d}m", f"{study_days} study days in scope"),
            kpi_card("Pending work", str(pending_assignments), f"{overdue_assignments} overdue"),
            kpi_card("Assessment average", f"{marks_average:.1f}%" if marks_average is not None else "-", "Based on recorded marks"),
        ]
        outer = Table([cards], colWidths=[report_width / 4] * 4, hAlign="LEFT")
        outer.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(outer)
        story.append(Spacer(1, 5 * mm))

    def add_section_title(title, subtitle=None):
        story.append(Paragraph(text_value(title), styles["SSSection"]))
        if subtitle:
            story.append(Paragraph(text_value(subtitle), styles["SSMuted"]))
            story.append(Spacer(1, 2 * mm))

    def add_table(title, headers, rows, ratios=None):
        add_section_title(title)
        if not rows:
            story.append(Paragraph("No records match the selected report options.", styles["SSMuted"]))
            story.append(Spacer(1, 2 * mm))
            return
        ncols = len(headers)
        if ratios is None:
            ratios = [1] * ncols
        total_ratio = sum(ratios) or 1
        widths = [report_width * ratio / total_ratio for ratio in ratios]
        data = [[P(header, "SSTableHead") for header in headers]]
        status_colors = {
            "completed": "#16845B", "submitted": "#16845B", "reviewed": "#16845B", "prepared": "#16845B",
            "overdue": "#B42318", "urgent": "#B42318", "high": C["warning"].hexval(),
            "pending": C["warning"].hexval(), "due": C["warning"].hexval(),
            "in progress": C["primary"].hexval(), "paused": C["muted"].hexval(), "skipped": C["muted"].hexval(),
        }
        for row in rows:
            cooked = []
            for cell in row:
                if isinstance(cell, Flowable):
                    cooked.append(cell)
                elif str(cell or "").strip().casefold() in status_colors:
                    status_text = str(cell).strip()
                    color = status_colors[status_text.casefold()]
                    cooked.append(Paragraph(f'<font color="{color}"><b>{text_value(status_text)}</b></font>', styles["SSTableCell"]))
                else:
                    cooked.append(P(cell, "SSTableSmall" if detailed and len(str(cell or "")) > 70 else "SSTableCell"))
            data.append(cooked)
        table = LongTable(data, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), C["primary"]),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, C["surface"]]),
            ("LINEBELOW", (0, 0), (-1, 0), 0.7, C["primary"]),
            ("LINEBELOW", (0, 1), (-1, -1), 0.3, C["border"]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, 0), 7), ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
            ("TOPPADDING", (0, 1), (-1, -1), cell_pad), ("BOTTOMPADDING", (0, 1), (-1, -1), cell_pad),
        ]))
        story.append(table)
        story.append(Spacer(1, 3 * mm))

    def query_rows(sql, args):
        limit = options.get("record_limit")
        if limit:
            sql = sql.rstrip().rstrip(";") + " LIMIT ?"
            args = [*args, int(limit)]
        return db.q(sql, tuple(args))

    add_hero()
    if options.get("include_cover"):
        story.append(Spacer(1, 8 * mm))
        meta = [
            [P("REPORT SCOPE", "SSKpiLabel"), P("DATE RANGE", "SSKpiLabel"), P("SUBJECT FILTER", "SSKpiLabel")],
            [P(options.get("profile", "Custom report")), P(period_label), P(subject_label)],
        ]
        meta_table = Table(meta, colWidths=[report_width / 3] * 3)
        meta_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), C["soft"]), ("BOX", (0, 0), (-1, -1), 0.6, C["border"]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(meta_table)
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph("A structured snapshot of your subjects, syllabus progress, assessments and study activity.", styles["SSBody"]))
        story.append(PageBreak())
        add_hero()

    if sections.get("overview", False):
        row_scope = f"Up to {options['record_limit']} rows per section" if options.get("record_limit") else "All matching records"
        add_section_title("At a glance", f"Scope: {period_label}  |  Subject: {subject_label}  |  Records: {row_scope}")
        add_kpis()
    elif options.get("record_limit"):
        story.append(Paragraph(f"Record limit: up to {int(options['record_limit'])} rows per section.", styles["SSMuted"]))
        story.append(Spacer(1, 2 * mm))
    if overdue_assignments and sections.get("overview", False):
        callout = Table([[P(f"Attention: {overdue_assignments} assignment(s) are overdue. Review the deadlines section and update their status after submission.", "SSCallout")]], colWidths=[report_width])
        callout.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF7E8")),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#E8C98E")),
            ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(callout)
        story.append(Spacer(1, 3 * mm))

    if sections.get("subjects", False):
        progress_rows = []
        for subject_row in subject_rows:
            sid = int(subject_row["id"])
            unit_stats = db.one("SELECT COUNT(*) total, SUM(CASE WHEN status='Completed' THEN 1 ELSE 0 END) done FROM units WHERE subject_id=?", (sid,))
            minutes_stats = db.one("SELECT COALESCE(SUM(duration_minutes),0) total FROM study_sessions WHERE subject_id=?" + (" AND study_date >= ? AND study_date <= ?" if options.get("filter_dates") else ""), (sid, options["date_from"], options["date_to"]) if options.get("filter_dates") else (sid,))
            marks_date_sql = " AND assessment_date >= ? AND assessment_date <= ?" if options.get("filter_dates") else ""
            marks_date_values = (sid, options["date_from"], options["date_to"]) if options.get("filter_dates") else (sid,)
            mark_stats = db.one("SELECT COALESCE(SUM(marks_obtained),0) got, COALESCE(SUM(max_marks),0) maximum FROM marks WHERE subject_id=?" + marks_date_sql, marks_date_values)
            total = int(unit_stats["total"] or 0)
            done = int(unit_stats["done"] or 0)
            pct = done / total * 100 if total else 0
            avg = (float(mark_stats["got"]) / float(mark_stats["maximum"]) * 100) if mark_stats["maximum"] else None
            row = [subject_row["name"], subject_row["code"] or "-", f"{done}/{total}", ProgressBar(pct), format_minutes(minutes_stats["total"]), f"{avg:.1f}%" if avg is not None else "-"]
            if include_notes:
                row.append(subject_row["notes"] or "-")
            progress_rows.append(row)
        headers = ["Subject", "Code", "Units", "Progress", "Study time", "Marks"]
        ratios = [2.0, 0.8, 0.7, 1.6, 1.0, 0.8]
        if include_notes:
            headers.append("Notes"); ratios.append(1.7)
        add_table("Subject and syllabus progress", headers, progress_rows, ratios)

        if options.get("filter_dates"):
            syllabus_note = "Syllabus completion is cumulative; the date filter applies to dated study activity, tasks, assessments, exams and revision items."
            story.append(Paragraph(text_value(syllabus_note), styles["SSMuted"]))
        if detailed or include_notes:
            if subject_ids:
                placeholders = ",".join("?" for _ in subject_ids)
                units_data = query_rows(
                    f"SELECT s.name subject, u.unit_no, u.title, u.status, u.confidence, COUNT(c.id) chapters FROM units u JOIN subjects s ON s.id=u.subject_id LEFT JOIN chapters c ON c.unit_id=u.id WHERE u.subject_id IN ({placeholders}) GROUP BY u.id ORDER BY s.name,u.unit_no",
                    subject_ids,
                )
            else:
                units_data = []
            unit_rows = []
            for row in units_data:
                unit_rows.append([row["subject"], row["unit_no"], row["title"], row["status"], f"{row['confidence']}/5", row["chapters"]])
            add_table("Unit and chapter breakdown", ["Subject", "Unit", "Unit title", "Status", "Conf.", "Chapters"], unit_rows, [1.5, 0.45, 2.8, 1.0, 0.8, 0.7])
            chapter_data = query_rows(
                f"SELECT s.name subject, u.unit_no, u.title unit_title, c.title chapter, c.status, c.priority, c.notes FROM chapters c JOIN units u ON u.id=c.unit_id JOIN subjects s ON s.id=u.subject_id WHERE u.subject_id IN ({placeholders}) ORDER BY s.name,u.unit_no,c.id",
                subject_ids,
            ) if subject_ids else []
            if chapter_data:
                chapter_rows = []
                for row in chapter_data:
                    chapter_row = [row["subject"], f"Unit {row['unit_no']}: {row['unit_title']}", row["chapter"], row["status"], row["priority"]]
                    if include_notes:
                        chapter_row.append(row["notes"] or "-")
                    chapter_rows.append(chapter_row)
                chapter_headers = ["Subject", "Unit", "Chapter", "Status", "Priority"]
                chapter_ratios = [1.2, 1.7, 1.9, 1.5, 0.9]
                if include_notes:
                    chapter_headers.append("Notes"); chapter_ratios.append(1.5)
                add_table("Chapter-level completion", chapter_headers, chapter_rows, chapter_ratios)

    if sections.get("assignments", False):
        where = " WHERE 1=1"
        args = []
        clause, values = subject_clause("a.subject_id"); where += clause; args += values
        clause, values = date_clause("a.due_date", args); where += clause; args += values
        if not include_completed:
            where += " AND a.status!='Submitted'"
        assignment_data = query_rows(
            "SELECT a.title, s.name subject, a.given_date, a.due_date, a.submission_date, a.status, a.priority, a.description, a.remarks FROM assignments a JOIN subjects s ON s.id=a.subject_id" + where + " ORDER BY CASE WHEN a.due_date='' THEN 1 ELSE 0 END, a.due_date, a.priority DESC",
            args,
        )
        rows = []
        for row in assignment_data:
            status = "Overdue" if row["status"] != "Submitted" and row["due_date"] and row["due_date"] < today_iso else row["status"]
            item = [row["title"], row["subject"], display_date(row["due_date"]), display_date(row["submission_date"]), status, row["priority"]]
            if include_notes:
                item.append("\n".join(filter(None, [row["description"], row["remarks"]])) or "-")
            rows.append(item)
        headers = ["Assignment", "Subject", "Due", "Submitted", "Status", "Priority"]
        ratios = [1.8, 1.1, 0.8, 0.9, 0.9, 0.8]
        if include_notes: headers.append("Description / remarks"); ratios.append(2.0)
        add_table("Assignments and deadlines", headers, rows, ratios)

    if sections.get("exams", False):
        where = " WHERE 1=1"
        args = []
        clause, values = subject_clause("e.subject_id"); where += clause; args += values
        clause, values = date_clause("e.exam_date", args); where += clause; args += values
        if not include_completed:
            where += " AND (e.exam_date >= ? OR e.prep_status NOT IN ('Completed','Prepared'))"; args.append(today_iso)
        exam_data = query_rows(
            "SELECT e.title, s.name subject, e.exam_type, e.exam_date, e.marks_obtained, e.max_marks, e.grade, e.prep_status, e.venue, e.room, e.notes FROM exams e JOIN subjects s ON s.id=e.subject_id" + where + " ORDER BY CASE WHEN e.exam_date='' THEN 1 ELSE 0 END, e.exam_date",
            args,
        )
        rows = []
        for row in exam_data:
            score = f"{display_number(row['marks_obtained'])}/{display_number(row['max_marks'])}" if row["marks_obtained"] is not None else "-"
            item = [row["title"], row["subject"], row["exam_type"], display_date(row["exam_date"]), score, row["grade"] or "-", row["prep_status"]]
            if include_notes:
                item.append(" | ".join(filter(None, [row["venue"], row["room"], row["notes"]])) or "-")
            rows.append(item)
        headers = ["Exam", "Subject", "Type", "Date", "Score", "Gr.", "Prep"]
        ratios = [1.45, 1.0, 0.9, 0.9, 1.05, 0.45, 0.95]
        if include_notes: headers.append("Venue / notes"); ratios.append(1.8)
        add_table("Exams and timetable", headers, rows, ratios)

    if sections.get("marks", False):
        where = " WHERE 1=1"
        args = []
        clause, values = subject_clause("m.subject_id"); where += clause; args += values
        clause, values = date_clause("m.assessment_date", args); where += clause; args += values
        mark_data = query_rows(
            "SELECT m.assessment_type, m.title, s.name subject, u.title unit, m.marks_obtained, m.max_marks, m.assessment_date, m.notes FROM marks m JOIN subjects s ON s.id=m.subject_id LEFT JOIN units u ON u.id=m.unit_id" + where + " ORDER BY CASE WHEN m.assessment_date='' THEN 1 ELSE 0 END, m.assessment_date DESC",
            args,
        )
        rows = []
        for row in mark_data:
            pct = (float(row["marks_obtained"]) / float(row["max_marks"]) * 100) if row["max_marks"] else 0
            item = [row["assessment_type"], row["title"] or "-", row["subject"], row["unit"] or "-", display_number(row["marks_obtained"]), display_number(row["max_marks"]), f"{pct:.1f}%", display_date(row["assessment_date"])]
            if include_notes: item.append(row["notes"] or "-")
            rows.append(item)
        headers = ["Type", "Assessment", "Subject", "Unit", "Marks", "Max", "Score", "Date"]
        ratios = [0.85, 1.35, 1.0, 0.9, 0.7, 0.7, 0.8, 1.15]
        if include_notes: headers.append("Notes"); ratios.append(1.5)
        add_table("Marks and assessment results", headers, rows, ratios)

    if sections.get("study", False):
        where = " WHERE 1=1"
        args = []
        clause, values = subject_clause("x.subject_id"); where += clause; args += values
        clause, values = date_clause("x.study_date", args); where += clause; args += values
        session_data = query_rows(
            "SELECT x.study_date, s.name subject, x.topic, x.duration_minutes, x.session_type, x.notes FROM study_sessions x JOIN subjects s ON s.id=x.subject_id" + where + " ORDER BY x.study_date DESC, x.id DESC",
            args,
        )
        if options.get("include_charts"):
            daily = {}
            for row in session_data:
                daily[row["study_date"]] = daily.get(row["study_date"], 0) + int(row["duration_minutes"] or 0)
            chart_rows = []
            for day_key in sorted(daily.keys())[-14:]:
                try: label = datetime.strptime(day_key, "%Y-%m-%d").strftime("%d %b")
                except Exception: label = day_key
                chart_rows.append((label, daily[day_key]))
            add_section_title("Study activity", "Daily study time for the most recent 14 active dates in the selected scope.")
            if chart_rows:
                chart_box = Table([[ActivityChart(chart_rows)]], colWidths=[report_width])
                chart_box.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), C["surface"]), ("BOX", (0, 0), (-1, -1), 0.5, C["border"]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]))
                story.append(chart_box)
            else:
                story.append(Paragraph("No study sessions are recorded for this scope.", styles["SSMuted"]))
        session_rows = []
        for row in session_data:
            item = [display_date(row["study_date"]), row["subject"], row["topic"] or "-", format_minutes(row["duration_minutes"]), row["session_type"]]
            if include_notes: item.append(row["notes"] or "-")
            session_rows.append(item)
        headers = ["Date", "Subject", "Topic", "Duration", "Type"]
        ratios = [0.9, 1.2, 2.3, 0.8, 0.9]
        if include_notes: headers.append("Notes"); ratios.append(2.0)
        add_table("Study session log", headers, session_rows, ratios)

    if sections.get("planner", False):
        where = " WHERE 1=1"
        args = []
        clause, values = subject_clause("p.subject_id"); where += clause; args += values
        clause, values = date_clause("p.task_date", args); where += clause; args += values
        if not include_completed:
            where += " AND p.status!='Completed'"
        planner_data = query_rows(
            "SELECT p.task_date, p.title, COALESCE(s.name,'Personal') subject, p.category, p.priority, p.status, p.duration_minutes, p.notes FROM planner_tasks p LEFT JOIN subjects s ON s.id=p.subject_id" + where + " ORDER BY p.task_date, p.priority DESC, p.id",
            args,
        )
        rows = []
        for row in planner_data:
            item = [display_date(row["task_date"]), row["title"], row["subject"], row["category"], row["priority"], row["status"], format_minutes(row["duration_minutes"])]
            if include_notes: item.append(row["notes"] or "-")
            rows.append(item)
        headers = ["Date", "Task", "Subject", "Category", "Prio.", "Status", "Plan"]
        ratios = [0.9, 1.7, 1.1, 1.0, 0.7, 0.9, 0.8]
        if include_notes: headers.append("Notes"); ratios.append(1.6)
        add_table("Planner tasks", headers, rows, ratios)

    if sections.get("revisions", False):
        where = " WHERE 1=1"
        args = []
        clause, values = subject_clause("s.id"); where += clause; args += values
        clause, values = date_clause("r.due_date", args); where += clause; args += values
        if not include_completed:
            where += " AND r.status='Due'"
        revision_data = query_rows(
            "SELECT s.name subject, u.unit_no, u.title unit, r.due_date, r.interval_days, r.repetitions, r.status, r.last_reviewed, r.notes FROM revisions r JOIN units u ON u.id=r.unit_id JOIN subjects s ON s.id=u.subject_id" + where + " ORDER BY r.due_date, s.name, u.unit_no",
            args,
        )
        rows = []
        for row in revision_data:
            item = [row["subject"], f"Unit {row['unit_no']}: {row['unit']}", display_date(row["due_date"]), row["interval_days"], row["repetitions"], row["status"], display_date(row["last_reviewed"])]
            if include_notes: item.append(row["notes"] or "-")
            rows.append(item)
        headers = ["Subject", "Unit", "Next date", "Gap", "Rev.", "Status", "Last date"]
        ratios = [1.1, 2.0, 0.9, 0.7, 0.6, 0.8, 0.9]
        if include_notes: headers.append("Notes"); ratios.append(1.7)
        add_table("Spaced revision schedule", headers, rows, ratios)

    if options.get("include_notes"):
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph("Notes and descriptions are included only where a record contains saved text. Empty note fields are shown as a dash.", styles["SSMuted"]))

    if not story:
        story.append(Paragraph("No report sections were selected.", styles["SSBody"]))

    def page_decor(canvas, document):
        canvas.saveState()
        width, height = page_size
        if document.page > 1:
            canvas.setFillColor(C["deep"])
            canvas.rect(0, height - 8 * mm, width, 8 * mm, fill=1, stroke=0)
            canvas.setFillColor(colors.white)
            canvas.setFont(bold_font, 7)
            canvas.drawString(margin_left, height - 5.2 * mm, "STUDY SPACE PRO")
            canvas.setFont(regular_font, 7)
            canvas.drawRightString(width - margin_right, height - 5.2 * mm, str(semester_label)[:90])
        canvas.setStrokeColor(C["border"])
        canvas.setLineWidth(0.5)
        canvas.line(margin_left, 13 * mm, width - margin_right, 13 * mm)
        canvas.setFillColor(C["muted"])
        canvas.setFont(regular_font, 7)
        canvas.drawString(margin_left, 8.5 * mm, f"Study Space Pro  |  Generated {date.today().strftime('%d %b %Y')}")
        canvas.drawRightString(width - margin_right, 8.5 * mm, f"Page {document.page}")
        canvas.restoreState()

    output_path = Path(output_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_handle = tempfile.NamedTemporaryFile(
        prefix="studyspace_report_", suffix=".pdf", dir=str(output_path.parent), delete=False
    )
    temp_path = Path(temp_handle.name)
    temp_handle.close()
    doc = SimpleDocTemplate(
        str(temp_path), pagesize=page_size,
        rightMargin=margin_right, leftMargin=margin_left,
        topMargin=23 * mm, bottomMargin=19 * mm,
        title=options.get("title", "Academic Progress Report"),
        author="Study Space Pro", subject="Academic progress and study activity",
    )
    try:
        doc.build(story, onFirstPage=page_decor, onLaterPages=page_decor)
        os.replace(str(temp_path), str(output_path))
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            logging.exception("Could not remove incomplete temporary PDF")
        raise
    return {"path": str(output_path), "subjects": len(subject_rows), "units": total_units, "completed_units": done_units, "study_minutes": total_minutes, "assignments": assignment_total, "exams": exam_total}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db=Database(); self.accent=ACCENTS.get(self.db.setting("accent","Blue"),ACCENTS["Blue"])
        integrity=self.db.integrity_check()
        if integrity != "ok": raise sqlite3.DatabaseError(f"Database integrity check failed: {integrity}")
        self.setWindowTitle(f"{APP_NAME} — Academic Command Centre · v{APP_VERSION}")
        self.resize(1440,900); self.setMinimumSize(980,680)
        self.pages={}; self.nav_buttons={}
        central=QWidget(); central.setObjectName("AppCentral"); self.setCentralWidget(central)
        shell=QHBoxLayout(central); shell.setContentsMargins(0,0,0,0); shell.setSpacing(0)
        self.sidebar=QFrame(); self.sidebar.setObjectName("Sidebar"); self.sidebar.setFixedWidth(248)
        side=QVBoxLayout(self.sidebar); side.setContentsMargins(16,24,16,18); side.setSpacing(7)
        brand=QHBoxLayout(); logo=QLabel("S"); logo.setObjectName("Logo"); brand.addWidget(logo)
        brandtext=QVBoxLayout(); name=QLabel("STUDY SPACE"); name.setObjectName("Brand")
        edition=QLabel("PRO  ·  PYSIDE6  ·  v2.3"); edition.setObjectName("BrandSub"); brandtext.addWidget(name); brandtext.addWidget(edition)
        brand.addLayout(brandtext); brand.addStretch(); side.addLayout(brand)
        side.addSpacing(20)
        navlabel=QLabel("WORKSPACE"); navlabel.setObjectName("NavCaption"); side.addWidget(navlabel)
        nav_scroll=QScrollArea(); nav_scroll.setWidgetResizable(True); nav_scroll.setFrameShape(QFrame.NoFrame)
        nav_host=QWidget(); nav_layout=QVBoxLayout(nav_host); nav_layout.setContentsMargins(0,0,0,0); nav_layout.setSpacing(7)
        for title,icon,desc in PAGE_INFO:
            b=QPushButton(f"{icon}   {title}"); b.setObjectName("NavButton"); b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(desc); b.clicked.connect(lambda checked=False,t=title:self.go(t))
            nav_layout.addWidget(b); self.nav_buttons[title]=b
        nav_layout.addStretch(); nav_scroll.setWidget(nav_host); side.addWidget(nav_scroll,1)
        dbstatus=QLabel("●  OFFLINE DATABASE\n   Saved on this device"); dbstatus.setObjectName("DatabaseStatus"); side.addWidget(dbstatus)
        self.workspace=QWidget(); self.workspace.setObjectName("Workspace"); work=QVBoxLayout(self.workspace); work.setContentsMargins(0,0,0,0); work.setSpacing(0)
        top=QFrame(); top.setObjectName("Topbar"); top.setFixedHeight(62)
        topl=QHBoxLayout(top); topl.setContentsMargins(28,8,28,8)
        self.breadcrumb=QLabel("Overview"); self.breadcrumb.setObjectName("TopTitle"); topl.addWidget(self.breadcrumb)
        topl.addStretch()
        todaylabel=QLabel(date.today().strftime("%A, %d %B %Y")); todaylabel.setObjectName("Muted"); topl.addWidget(todaylabel)
        report=QPushButton("↗  PDF report"); report.setObjectName("Primary"); report.clicked.connect(self.generate_pdf); topl.addWidget(report)
        work.addWidget(top)
        self.stack=QStackedWidget(); work.addWidget(self.stack,1)
        shell.addWidget(self.sidebar); shell.addWidget(self.workspace,1)
        self.apply_style()
        self.build_pages(); self.go("Overview")
        self.install_shortcuts()
        self.statusBar().showMessage(f"Ready · Offline database · Version {APP_VERSION}",5000)

    def install_shortcuts(self):
        self._shortcuts = []
        for sequence, callback in [
            ("F5", self.refresh_current_page), ("Ctrl+B", self.backup),
            ("Ctrl+E", self.export_all), ("Ctrl+1", lambda:self.go("Overview")),
            ("Ctrl+2", lambda:self.go("Study Planner")), ("Ctrl+3", lambda:self.go("Focus Room")),
        ]:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

    def refresh_current_page(self):
        page=self.stack.currentWidget()
        if isinstance(page, DataPage): page.refresh()
        elif page is self.pages.get("Overview"): page.refresh()
        elif page is self.pages.get("Focus Room"): page.refresh_subjects()
        elif self.breadcrumb.text()=="Analytics":
            old=self.pages.get("Analytics")
            if old: self.stack.removeWidget(old); old.deleteLater()
            self.pages["Analytics"]=self.analytics_page(); self.stack.addWidget(self.pages["Analytics"]); self.stack.setCurrentWidget(self.pages["Analytics"])
        self.statusBar().showMessage("Workspace refreshed",2000)

    def apply_style(self):
        """Apply a consistent, high-contrast theme.

        Important: QLabel backgrounds are explicitly transparent. A global
        QWidget background rule makes Qt paint a rectangle behind every label,
        which caused the grey text bands visible in the previous UI.
        """
        dark = self.db.setting("theme", "Light").strip().lower() == "dark"

        if dark:
            colors = {
                "bg": "#0F172A",
                "panel": "#172235",
                "panel_alt": "#202F45",
                "text": "#E6EDF7",
                "muted": "#AAB8CC",
                "border": "#34445D",
                "side": "#0B1220",
                "side_hover": "#1A2940",
                "input": "#1C2B40",
                "table_alt": "#1B2A3E",
                "header": "#223249",
                "placeholder": "#8291A7",
                "disabled": "#8290A4",
                "selection_text": "#FFFFFF",
            }
        else:
            colors = {
                "bg": "#F4F7FB",
                "panel": "#FFFFFF",
                "panel_alt": "#EEF2F8",
                "text": "#182230",
                "muted": "#5F6B7A",
                "border": "#D9E1EC",
                "side": "#17243B",
                "side_hover": "#263A58",
                "input": "#FFFFFF",
                "table_alt": "#F7F9FC",
                "header": "#EAF0F7",
                "placeholder": "#8793A5",
                "disabled": "#8A96A8",
                "selection_text": "#FFFFFF",
            }

        c = colors
        accent = self.accent
        accent_hover = accent
        # Use an application-level stylesheet: dialogs are top-level windows and
        # must not fall back to the operating system's palette for their contents.
        app = QApplication.instance()
        if app is not None:
            palette = app.palette()
            palette.setColor(QPalette.Window, QColor(c["bg"]))
            palette.setColor(QPalette.WindowText, QColor(c["text"]))
            palette.setColor(QPalette.Base, QColor(c["input"]))
            palette.setColor(QPalette.AlternateBase, QColor(c["table_alt"]))
            palette.setColor(QPalette.Text, QColor(c["text"]))
            palette.setColor(QPalette.Button, QColor(c["panel_alt"]))
            palette.setColor(QPalette.ButtonText, QColor(c["text"]))
            palette.setColor(QPalette.Highlight, QColor(accent))
            palette.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
            palette.setColor(QPalette.PlaceholderText, QColor(c["placeholder"]))
            palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(c["disabled"]))
            palette.setColor(QPalette.Disabled, QPalette.Text, QColor(c["disabled"]))
            palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(c["disabled"]))
            app.setPalette(palette)

        stylesheet = f"""
            /* Base typography only. Do not paint a background on every QWidget. */
            QWidget {{
                color: {c["text"]};
                font-family: "Segoe UI";
                font-size: 10pt;
            }}
            QMainWindow,
            QWidget#AppCentral,
            QWidget#Workspace,
            QStackedWidget {{
                background-color: {c["bg"]};
            }}
            QDialog {{
                background-color: {c["bg"]};
                color: {c["text"]};
            }}
            QDialog#RecordFormDialog {{
                background-color: {c["bg"]};
                color: {c["text"]};
            }}
            QWidget#RecordFormBody,
            QWidget#RecordFormViewport {{
                background-color: {c["panel"]};
                color: {c["text"]};
            }}
            QScrollArea#RecordFormScroll {{
                background-color: {c["panel"]};
                border: 1px solid {c["border"]};
                border-radius: 9px;
            }}
            QLabel#RecordFieldLabel {{
                background-color: transparent;
                color: {c["text"]};
                font-size: 9pt;
                font-weight: 600;
                padding: 4px 2px;
            }}

            /* Text widgets must remain transparent so they do not create bands. */
            QLabel {{
                background-color: transparent;
                border: none;
                color: {c["text"]};
            }}
            QLabel#Muted {{
                background-color: transparent;
                color: {c["muted"]};
                font-size: 9pt;
            }}
            QLabel#PageTitle {{
                background-color: transparent;
                color: {c["text"]};
                font-size: 23pt;
                font-weight: 800;
            }}
            QLabel#SectionTitle {{
                background-color: transparent;
                color: {c["text"]};
                font-size: 12pt;
                font-weight: 750;
            }}
            QLabel#DialogTitle {{
                background-color: transparent;
                color: {c["text"]};
                font-size: 17pt;
                font-weight: 800;
            }}
            QLabel#EmptyState {{
                background-color: {c["panel"]};
                color: {c["muted"]};
                border: 1px dashed {c["border"]};
                border-radius: 9px;
                padding: 18px;
                font-size: 9pt;
            }}
            QLabel#MetricLabel {{
                background-color: transparent;
                color: {c["muted"]};
                font-size: 8pt;
                font-weight: 750;
                letter-spacing: 1px;
            }}
            QLabel#MetricValue {{
                background-color: transparent;
                font-size: 23pt;
                font-weight: 800;
            }}
            QLabel#Timer {{
                background-color: transparent;
                color: {accent};
                font-size: 54pt;
                font-weight: 800;
                padding: 12px;
            }}

            /* Sidebar and navigation */
            QFrame#Sidebar {{
                background-color: {c["side"]};
                border: none;
            }}
            QLabel#Logo {{
                background-color: {accent};
                color: #FFFFFF;
                font-size: 20pt;
                font-weight: 800;
                border-radius: 12px;
                min-width: 42px;
                max-width: 42px;
                min-height: 42px;
                max-height: 42px;
                qproperty-alignment: AlignCenter;
            }}
            QLabel#Brand {{
                background-color: transparent;
                color: #FFFFFF;
                font-size: 11pt;
                font-weight: 800;
                letter-spacing: 1px;
            }}
            QLabel#BrandSub {{
                background-color: transparent;
                color: #C1CDDD;
                font-size: 8pt;
            }}
            QLabel#NavCaption {{
                background-color: transparent;
                color: #AAB9CD;
                font-size: 8pt;
                font-weight: 700;
                padding: 6px 9px;
            }}
            QPushButton#NavButton {{
                background-color: transparent;
                color: #DCE5F1;
                text-align: left;
                border: 1px solid transparent;
                border-radius: 9px;
                padding: 10px 12px;
                min-height: 20px;
                font-weight: 600;
            }}
            QPushButton#NavButton:hover {{
                background-color: {c["side_hover"]};
                color: #FFFFFF;
                border-color: {c["side_hover"]};
            }}
            QPushButton#NavButton[active="true"] {{
                background-color: {accent};
                color: #FFFFFF;
                border-color: {accent};
                font-weight: 700;
            }}
            QPushButton#NavButton:focus {{
                border: 1px solid #FFFFFF;
            }}
            QLabel#DatabaseStatus {{
                background-color: transparent;
                color: #C1CDDD;
                font-size: 8pt;
                padding: 10px 5px;
            }}

            /* Header and content cards */
            QFrame#Topbar {{
                background-color: {c["panel"]};
                border: none;
                border-bottom: 1px solid {c["border"]};
            }}
            QLabel#TopTitle {{
                background-color: transparent;
                color: {c["text"]};
                font-size: 11pt;
                font-weight: 700;
            }}
            QFrame#Card,
            QFrame#Metric {{
                background-color: {c["panel"]};
                border: 1px solid {c["border"]};
                border-radius: 13px;
            }}

            /* Inputs */
            QLineEdit,
            QTextEdit,
            QPlainTextEdit,
            QComboBox,
            QSpinBox,
            QDoubleSpinBox,
            QDateEdit,
            QTimeEdit,
            QDateTimeEdit {{
                background-color: {c["input"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                border-radius: 8px;
                padding: 8px 10px;
                selection-background-color: {accent};
                selection-color: #FFFFFF;
            }}
            QLineEdit:focus,
            QTextEdit:focus,
            QPlainTextEdit:focus,
            QComboBox:focus,
            QSpinBox:focus,
            QDoubleSpinBox:focus,
            QDateEdit:focus,
            QTimeEdit:focus,
            QDateTimeEdit:focus {{
                border: 1px solid {accent};
            }}
            QLineEdit:disabled,
            QTextEdit:disabled,
            QComboBox:disabled,
            QSpinBox:disabled,
            QDoubleSpinBox:disabled,
            QDateEdit:disabled,
            QTimeEdit:disabled {{
                background-color: {c["panel_alt"]};
                color: {c["disabled"]};
            }}
            QLineEdit {{
                placeholder-text-color: {c["placeholder"]};
            }}
            QComboBox QAbstractItemView {{
                background-color: {c["panel"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                selection-background-color: {accent};
                selection-color: #FFFFFF;
                outline: 0;
                padding: 4px;
            }}

            /* Buttons */
            QPushButton {{
                background-color: {c["panel_alt"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                border-radius: 8px;
                padding: 9px 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {c["header"]};
                border-color: {accent};
            }}
            QPushButton:pressed {{
                background-color: {c["header"]};
                padding-top: 10px;
                padding-bottom: 8px;
            }}
            QPushButton:disabled {{
                background-color: {c["panel_alt"]};
                color: {c["disabled"]};
                border-color: {c["border"]};
            }}
            QPushButton#Primary {{
                background-color: {accent};
                color: #FFFFFF;
                border: 1px solid {accent};
                padding: 10px 16px;
                font-weight: 700;
            }}
            QPushButton#Primary:hover {{
                background-color: {accent_hover};
                border-color: {accent_hover};
            }}
            QPushButton#Primary:disabled {{
                background-color: {c["panel_alt"]};
                color: {c["disabled"]};
                border-color: {c["border"]};
            }}

            /* Tables: explicit text and surface colours in both themes. */
            QTableWidget,
            QTableView,
            QTreeWidget,
            QTreeView,
            QListWidget,
            QListView {{
                background-color: {c["panel"]};
                color: {c["text"]};
                alternate-background-color: {c["table_alt"]};
                border: 1px solid {c["border"]};
                border-radius: 10px;
                gridline-color: {c["border"]};
                selection-background-color: {accent};
                selection-color: {c["selection_text"]};
                outline: 0;
            }}
            QHeaderView {{
                background-color: {c["header"]};
            }}
            QHeaderView::section {{
                background-color: {c["header"]};
                color: {c["text"]};
                border: none;
                border-bottom: 1px solid {c["border"]};
                padding: 10px 8px;
                font-weight: 700;
            }}
            QTableWidget::item,
            QTreeWidget::item,
            QListWidget::item {{
                color: {c["text"]};
                padding: 8px;
                border-bottom: 1px solid {c["border"]};
            }}
            QTableWidget::item:selected,
            QTreeWidget::item:selected,
            QListWidget::item:selected {{
                background-color: {accent};
                color: #FFFFFF;
            }}

            /* Group boxes, checks, tabs and progress indicators. */
            QGroupBox {{
                background-color: {c["panel"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                border-radius: 10px;
                margin-top: 12px;
                padding: 16px 12px 12px 12px;
                font-weight: 700;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: {c["text"]};
                background-color: {c["panel"]};
            }}
            QCalendarWidget QWidget {{
                background-color: {c["panel"]};
                color: {c["text"]};
            }}
            QCalendarWidget QToolButton {{
                background-color: {c["panel_alt"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                border-radius: 5px;
                padding: 5px;
            }}
            QCalendarWidget QMenu {{
                background-color: {c["panel"]};
                color: {c["text"]};
            }}
            QCheckBox {{
                background-color: transparent;
                color: {c["text"]};
                spacing: 8px;
            }}
            QProgressBar {{
                background-color: {c["panel_alt"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                border-radius: 6px;
                min-height: 13px;
                text-align: center;
                font-size: 8pt;
            }}
            QProgressBar::chunk {{
                background-color: {accent};
                border-radius: 5px;
            }}
            QTabWidget::pane {{
                background-color: {c["panel"]};
                border: 1px solid {c["border"]};
                border-radius: 8px;
            }}
            QTabBar::tab {{
                background-color: {c["panel_alt"]};
                color: {c["text"]};
                padding: 9px 14px;
                border-top-left-radius: 7px;
                border-top-right-radius: 7px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background-color: {accent};
                color: #FFFFFF;
            }}

            /* Scroll areas, menus, tooltips and status bar. */
            QScrollArea {{
                background-color: transparent;
                border: none;
            }}
            QScrollArea QWidget#qt_scrollarea_viewport {{
                background-color: transparent;
            }}
            QScrollBar:vertical {{
                background-color: {c["bg"]};
                width: 11px;
                margin: 2px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background-color: {c["border"]};
                border-radius: 5px;
                min-height: 24px;
            }}
            QScrollBar::handle:vertical:hover {{
                background-color: {c["muted"]};
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
                border: none;
            }}
            QMenu {{
                background-color: {c["panel"]};
                color: {c["text"]};
                border: 1px solid {c["border"]};
                padding: 5px;
            }}
            QMenu::item {{
                padding: 7px 22px;
                border-radius: 5px;
            }}
            QMenu::item:selected {{
                background-color: {accent};
                color: #FFFFFF;
            }}
            QToolTip {{
                background-color: {c["side"]};
                color: #FFFFFF;
                border: 1px solid {c["border"]};
                padding: 6px 8px;
            }}
            QStatusBar {{
                background-color: {c["panel"]};
                color: {c["muted"]};
                border-top: 1px solid {c["border"]};
            }}
        """
        if app is not None:
            app.setStyleSheet(stylesheet)

    def build_pages(self):
        self.pages["Overview"]=Dashboard(self); self.stack.addWidget(self.pages["Overview"])
        mapping={
            "Semesters":("semesters","Semesters","Create and manage your academic terms."),
            "Subjects":("subjects","Manage subjects","Organise your semester, course codes and weekly study targets."),
            "Syllabus":("units","Syllabus units","Track unit completion and confidence."),
            "Chapters":("chapters","Chapters","Break each syllabus unit into manageable chapters."),
            "Assignments":("assignments","Assignments","Keep every brief, deadline and submission status in one place."),
            "Study Planner":("planner_tasks","Daily planner","Plan study blocks, assignments, revision and personal tasks."),
            "Study Sessions":("study_sessions","Study sessions","Log actual study time and what you covered."),
            "Exams":("exams","Exams & timetable","Record exam schedules, preparation and results."),
            "Marks & Results":("marks","Marks & results","Track unit quizzes, seasonal exams and other assessments."),
            "Revision":("revisions","Revision queue","Schedule spaced-repetition reviews for syllabus units.")
        }
        for title,(table,name,desc) in mapping.items():
            page=DataPage(self,table,name,desc); self.pages[title]=page; self.stack.addWidget(page)
        self.pages["Focus Room"]=FocusPage(self); self.stack.addWidget(self.pages["Focus Room"])
        self.pages["Analytics"]=self.analytics_page(); self.stack.addWidget(self.pages["Analytics"])
        self.pages["Settings"]=self.settings_page(); self.stack.addWidget(self.pages["Settings"])

    def go(self,title):
        if title=="Syllabus": title="Syllabus"
        page=self.pages.get(title)
        if not page: return
        self.stack.setCurrentWidget(page); self.breadcrumb.setText(title)
        for name,b in self.nav_buttons.items():
            b.setProperty("active",name==title); b.style().unpolish(b); b.style().polish(b)
        if title=="Overview": self.pages[title].refresh()
        elif title=="Focus Room": self.pages[title].refresh_subjects()
        elif title=="Analytics":
            old=self.pages.get("Analytics")
            if old:
                self.stack.removeWidget(old); old.deleteLater()
            self.pages["Analytics"]=self.analytics_page(); self.stack.addWidget(self.pages["Analytics"]); self.stack.setCurrentWidget(self.pages["Analytics"])
        elif isinstance(page,DataPage): page.refresh()

    def refresh_dashboard_if_needed(self):
        if "Overview" in self.pages: self.pages["Overview"].refresh()

    def analytics_page(self):
        page=QWidget(); root=QVBoxLayout(page); root.setContentsMargins(30,26,30,26); root.setSpacing(18)
        title=QLabel("Analytics"); title.setObjectName("PageTitle"); root.addWidget(title)
        desc=QLabel("Understand your consistency, syllabus completion and assessment performance."); desc.setObjectName("Muted"); root.addWidget(desc)
        cards=QGridLayout(); root.addLayout(cards)
        def card(label,value,sub):
            f=QFrame(); f.setObjectName("Metric"); l=QVBoxLayout(f); a=QLabel(label.upper()); a.setObjectName("MetricLabel")
            v=QLabel(value); v.setObjectName("MetricValue"); v.setStyleSheet(f"color:{self.accent};")
            s=QLabel(sub); s.setObjectName("Muted"); l.addWidget(a); l.addWidget(v); l.addWidget(s); return f
        subjects=self.db.q("SELECT id,name FROM subjects ORDER BY name")
        total=self.db.one("SELECT COUNT(*) n FROM units")["n"]; done=self.db.one("SELECT COUNT(*) n FROM units WHERE status='Completed'")["n"]
        mins=self.db.one("SELECT COALESCE(SUM(duration_minutes),0) n FROM study_sessions")["n"]
        marks=self.db.one("SELECT COALESCE(SUM(marks_obtained),0) got,COALESCE(SUM(max_marks),0) mx FROM marks")
        pct=marks["got"]/marks["mx"]*100 if marks["mx"] else 0
        for i,m in enumerate([("Syllabus completion",f"{done/total*100:.1f}%" if total else "0%","Completed units"),
                              ("Study time",f"{mins/60:.1f} h","Total logged time"),
                              ("Assessment average",f"{pct:.1f}%" if marks["mx"] else "—","Weighted by total marks"),
                              ("Subjects",str(len(subjects)),"Configured courses")]):
            cards.addWidget(card(*m),0,i)
        table=QTableWidget(0,5); table.setHorizontalHeaderLabels(["Subject","Units done","Total units","Study hours","Marks %"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); table.verticalHeader().hide(); table.setAlternatingRowColors(True)
        for s in subjects:
            u=self.db.one("SELECT COUNT(*) total,SUM(status='Completed') done FROM units WHERE subject_id=?",(s["id"],))
            st=self.db.one("SELECT COALESCE(SUM(duration_minutes),0) n FROM study_sessions WHERE subject_id=?",(s["id"],))
            m=self.db.one("SELECT COALESCE(SUM(marks_obtained),0) got,COALESCE(SUM(max_marks),0) mx FROM marks WHERE subject_id=?",(s["id"],))
            row=table.rowCount(); table.insertRow(row)
            vals=[s["name"],str(u["done"] or 0),str(u["total"]),f"{st['n']/60:.1f}",f"{m['got']/m['mx']*100:.1f}%" if m["mx"] else "—"]
            for col,val in enumerate(vals): table.setItem(row,col,QTableWidgetItem(val))
        root.addWidget(QLabel("Subject performance"),0); root.addWidget(table,1)
        return page

    def settings_page(self):
        page=QWidget(); root=QVBoxLayout(page); root.setContentsMargins(30,26,30,26); root.setSpacing(18)
        title=QLabel("Settings & data"); title.setObjectName("PageTitle"); root.addWidget(title)
        desc=QLabel("Personalise your workspace and protect your local academic records."); desc.setObjectName("Muted"); root.addWidget(desc)
        group=QGroupBox("Appearance & study preferences"); form=QFormLayout(group)
        self.theme=QComboBox(); self.theme.addItems(["Light","Dark"]); self.theme.setCurrentText(self.db.setting("theme","Light"))
        self.accentbox=QComboBox(); self.accentbox.addItems(ACCENTS); self.accentbox.setCurrentText(self.db.setting("accent","Blue"))
        self.goal=QSpinBox(); self.goal.setRange(15,1440); self.goal.setSuffix(" minutes"); self.goal.setValue(int(self.db.setting("daily_goal","180")))
        self.work=QSpinBox(); self.work.setRange(1,120); self.work.setSuffix(" min"); self.work.setValue(int(self.db.setting("pomodoro_work","25")))
        self.short=QSpinBox(); self.short.setRange(1,60); self.short.setSuffix(" min"); self.short.setValue(int(self.db.setting("pomodoro_short","5")))
        self.long=QSpinBox(); self.long.setRange(1,90); self.long.setSuffix(" min"); self.long.setValue(int(self.db.setting("pomodoro_long","15")))
        self.semester=QLineEdit(self.db.setting("semester_name","B.Tech — Semester 1"))
        for label,w in [("Theme",self.theme),("Accent colour",self.accentbox),("Daily study goal",self.goal),
                        ("Pomodoro focus",self.work),("Short break",self.short),("Long break",self.long),("Semester label",self.semester)]:
            form.addRow(label,w)
        root.addWidget(group)
        save=QPushButton("Save preferences"); save.setObjectName("Primary"); save.clicked.connect(self.save_settings); root.addWidget(save,0,Qt.AlignLeft)
        data=QGroupBox("Database & reports"); grid=QGridLayout(data); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(10)
        for index,(label,fn) in enumerate([("Create database backup",self.backup),("Restore from backup",self.restore_backup),
                         ("Export all tables (CSV)",self.export_all),("Generate PDF report",self.generate_pdf),
                         ("Open database folder",self.open_db_folder)]):
            b=QPushButton(label); b.clicked.connect(fn); grid.addWidget(b,index//2,index%2)
        root.addWidget(data); root.addStretch()
        return page

    def save_settings(self):
        semester_label = self.semester.text().strip()
        if not semester_label:
            QMessageBox.warning(self, "Semester label required", "Enter a name for your current semester.")
            self.semester.setFocus()
            return
        values = {
            "theme": self.theme.currentText(),
            "accent": self.accentbox.currentText(),
            "daily_goal": self.goal.value(),
            "pomodoro_work": self.work.value(),
            "pomodoro_short": self.short.value(),
            "pomodoro_long": self.long.value(),
            "semester_name": semester_label,
        }
        try:
            self.db.set_settings(values)
            self.accent=ACCENTS.get(self.accentbox.currentText(), ACCENTS["Blue"])
            self.apply_style()
            self.statusBar().showMessage("Preferences saved", 3000)
            QMessageBox.information(self,"Preferences saved","Your preferences have been saved.")
        except Exception as exc:
            logging.exception("Could not save application settings")
            QMessageBox.critical(self, "Settings not saved", str(exc))

    def backup(self):
        path,_=QFileDialog.getSaveFileName(self,"Back up database",str(APP_DIR/"study_space_backup.db"),"SQLite database (*.db)")
        if not path: return
        if not path.lower().endswith(".db"): path += ".db"
        if Path(path).resolve() == self.db.path.resolve():
            QMessageBox.warning(self,"Invalid backup location","Choose a different file from the active database."); return
        try:
            self.db.backup_to(path)
            QMessageBox.information(self,"Backup complete",f"A consistent SQLite backup was saved:\n{path}")
        except Exception as e: QMessageBox.warning(self,"Backup failed",str(e))

    def restore_backup(self):
        path,_=QFileDialog.getOpenFileName(self,"Choose database backup",str(APP_DIR),"SQLite database (*.db *.sqlite *.sqlite3)")
        if not path: return
        if Path(path).resolve()==self.db.path.resolve():
            QMessageBox.warning(self,"Invalid backup","Choose a backup file other than the currently open database."); return
        if QMessageBox.question(self,"Restore database",
            "This replaces the current local database with the selected backup. A safety backup will be created first. Continue?") != QMessageBox.Yes: return
        source = None
        try:
            source = sqlite3.connect(path, timeout=30)
            source.row_factory = sqlite3.Row
            integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise sqlite3.DatabaseError(f"The selected backup failed integrity_check: {integrity}")
            table_names = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required_tables = {"semesters", "subjects", "units", "chapters", "assignments",
                               "study_sessions", "planner_tasks", "exams", "marks", "revisions"}
            missing = required_tables - table_names
            if missing:
                raise sqlite3.DatabaseError("This file is not a compatible Study Space database. Missing: " + ", ".join(sorted(missing)))

            safety=APP_DIR/f"study_space_safety_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            self.db.backup_to(safety)
            try:
                source.backup(self.db.conn)
                self.db.conn.commit()
                self.db.migrate()
                restored_integrity = self.db.integrity_check()
                if restored_integrity != "ok":
                    raise sqlite3.DatabaseError(f"Restored database integrity check failed: {restored_integrity}")
            except Exception:
                safety_conn = sqlite3.connect(str(safety), timeout=30)
                try:
                    safety_conn.backup(self.db.conn)
                    self.db.conn.commit()
                    self.db.migrate()
                finally:
                    safety_conn.close()
                raise
            finally:
                source.close()
                source = None

            self.accent = ACCENTS.get(self.db.setting("accent", "Blue"), ACCENTS["Blue"])
            self.theme.setCurrentText(self.db.setting("theme", "Light"))
            self.accentbox.setCurrentText(self.db.setting("accent", "Blue"))
            self.goal.setValue(safe_int(self.db.setting("daily_goal", "180"), 180))
            self.work.setValue(safe_int(self.db.setting("pomodoro_work", "25"), 25))
            self.short.setValue(safe_int(self.db.setting("pomodoro_short", "5"), 5))
            self.long.setValue(safe_int(self.db.setting("pomodoro_long", "15"), 15))
            self.semester.setText(self.db.setting("semester_name", "B.Tech — Semester 1"))
            self.apply_style()
            QMessageBox.information(self,"Restore complete",f"Database restored. Safety backup:\n{safety}\n\nYour workspace has been refreshed.")
            self.refresh_current_page()
        except Exception as e:
            logging.exception("Database restore failed")
            QMessageBox.critical(self,"Restore failed",str(e))
        finally:
            if source is not None:
                source.close()

    def export_all(self):
        folder=QFileDialog.getExistingDirectory(self,"Choose export folder")
        if not folder: return
        tables=list(DB_TABLES)
        try:
            for table in tables:
                rows=self.db.q(f"SELECT * FROM {table}")
                columns=[r["name"] for r in self.db.q(f"PRAGMA table_info({table})")]
                with open(Path(folder)/f"{table}.csv","w",newline="",encoding="utf-8-sig") as f:
                    w=csv.writer(f); w.writerow(columns); w.writerows([tuple(r) for r in rows])
            QMessageBox.information(self,"Export complete",f"CSV files saved in:\n{folder}")
        except Exception as e: QMessageBox.warning(self,"Export failed",str(e))

    def generate_pdf(self):
        dialog = PDFReportOptionsDialog(self.db, self)
        if dialog.exec() != QDialog.Accepted:
            return
        options = dialog.options()
        title_slug = "_".join("".join(ch for ch in part if ch.isalnum()) for part in options["title"].split())
        default_name = f"StudySpace_{title_slug or 'Report'}_{TODAY()}.pdf"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save academic PDF report", str(APP_DIR / default_name), "PDF files (*.pdf)"
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            result = create_academic_pdf_report(self.db, path, options)
            self.statusBar().showMessage("Academic PDF report created", 5000)
            QMessageBox.information(
                self, "PDF report created",
                f"Your report has been saved successfully.\n\n{path}\n\n"
                f"Subjects: {result['subjects']}  |  Units: {result['completed_units']}/{result['units']}  |  "
                f"Study time: {format_minutes(result['study_minutes'])}",
            )
            if options.get("open_after"):
                try:
                    if sys.platform == "win32":
                        os.startfile(str(Path(path).resolve()))
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", str(Path(path).resolve())])
                    else:
                        subprocess.Popen(["xdg-open", str(Path(path).resolve())])
                except Exception:
                    logging.exception("Could not open generated PDF automatically")
                    QMessageBox.information(self, "Report saved", "The PDF was saved, but your system could not open it automatically.")
        except ImportError:
            QMessageBox.warning(
                self, "PDF support required",
                "Install PDF support with:\npython -m pip install reportlab",
            )
        except Exception as exc:
            logging.exception("PDF report generation failed")
            QMessageBox.critical(self, "PDF report failed", f"The report could not be created:\n\n{exc}")

    def open_db_folder(self):
        try:
            if sys.platform=="win32": os.startfile(str(APP_DIR))
            elif sys.platform=="darwin": subprocess.Popen(["open",str(APP_DIR)])
            else: subprocess.Popen(["xdg-open",str(APP_DIR)])
        except Exception as e: QMessageBox.warning(self,"Could not open folder",str(e))

    def closeEvent(self,event):
        try:
            focus=self.pages.get("Focus Room")
            if focus and focus.timer.isActive(): focus.timer.stop()
        except Exception: pass
        try:
            self.db.close()
        except Exception:
            logging.exception("Database close failed")
        event.accept()

def configure_logging():
    """Write diagnostic details to a local log without exposing them in the UI."""
    try:
        log_path = APP_DIR / "study_space_pro.log"
        logging.basicConfig(
            filename=str(log_path),
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            encoding="utf-8",
        )
        return log_path
    except Exception:
        fallback = Path.home() / ".study_space_pro.log"
        logging.basicConfig(
            filename=str(fallback), level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        return fallback


def main():
    log_path = configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Study Space Pro")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))

    def handle_unhandled(exc_type, exc_value, exc_tb):
        logging.critical("Unhandled application error", exc_info=(exc_type, exc_value, exc_tb))
        details = "An unexpected error occurred. Your saved database should remain intact.\n\n"
        details += f"Diagnostic log: {log_path}"
        try:
            QMessageBox.critical(None, "Study Space Pro error", details)
        except Exception:
            sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = handle_unhandled
    try:
        window = MainWindow()
        window.show()
        return app.exec()
    except Exception as exc:
        logging.exception("Application startup failed")
        QMessageBox.critical(
            None,
            "Study Space Pro could not start",
            f"The application could not start: {exc}\n\n"
            f"See the diagnostic log for details:\n{log_path}",
        )
        return 1

if __name__=="__main__":
    raise SystemExit(main())
