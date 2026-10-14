import csv
import io
import os
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from flask import Flask, flash, make_response, redirect, render_template, request, url_for

LABELS = {1: "Critical", 2: "Urgent", 3: "Normal"}
COLORS = {1: "critical", 2: "urgent", 3: "normal"}
DOCTOR_STATUSES = {"available", "busy", "off_duty"}
BED_STATUSES = {"available", "occupied", "cleaning", "reserved"}
GENERAL_RESOURCE_STATUSES = {"available", "in_use", "maintenance", "out_of_stock"}
DEFAULT_RESOURCES = [
    ("Wheelchairs", "Mobility support", 6),
    ("Oxygen cylinders", "Supplemental oxygen equipment", 8),
    ("Patient monitors", "Vital-sign monitoring equipment", 5),
    ("Ventilators", "Respiratory support equipment", 2),
    ("IV infusion pumps", "Controlled fluid and medication delivery", 6),
]

T = TypeVar("T")

def merge_sort(items: list[T], key: Callable[[T], Any]) -> list[T]:
    """Sort records with divide-and-conquer Merge Sort (O(n log n) time).

    The input is not modified. Equal keys preserve their original order.
    """
    if len(items) <= 1:
        return items.copy()
    middle = len(items) // 2
    left = merge_sort(items[:middle], key)
    right = merge_sort(items[middle:], key)
    merged: list[T] = []
    i = j = 0
    while i < len(left) and j < len(right):
        if key(left[i]) <= key(right[j]):
            merged.append(left[i])
            i += 1
        else:
            merged.append(right[j])
            j += 1
    merged.extend(left[i:])
    merged.extend(right[j:])
    return merged

def iter_csv_rows(rows):
    """Yield CSV rows one at a time instead of building a second row list."""
    yield ["Patient ID","Name","Phone","Age","Severity","Symptoms","Status","Registered at",
           "Treatment started","Treatment completed","Doctor","Bed","Outcome","Notes"]
    for r in rows:
        yield [r["id"],r["name"],r["phone"],r["age"],LABELS.get(r["severity"],"Unknown"),r["symptoms"],
               r["status"],r["created_at"],r["started_at"],r["completed_at"],r["doctor"],r["bed_name"],r["outcome"],r["notes"]]



def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def create_app(config: Optional[dict[str, Any]] = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "development-key-change-me"),
        DATABASE=str(Path(__file__).with_name("hospital.db")),
    )
    if config:
        app.config.update(config)

    def db_connect() -> sqlite3.Connection:
        db = sqlite3.connect(app.config["DATABASE"], timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def init_db() -> None:
        db = db_connect()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS patients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                age INTEGER NOT NULL CHECK(age BETWEEN 0 AND 120),
                severity INTEGER NOT NULL CHECK(severity IN (1,2,3)),
                symptoms TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'waiting'
                    CHECK(status IN ('waiting','in_treatment','treated')),
                created_at TEXT NOT NULL,
                treated_at TEXT,
                doctor_id INTEGER,
                bed_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS doctors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                specialty TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'available'
            );
            CREATE TABLE IF NOT EXISTS beds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bed_name TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'available'
            );
            CREATE TABLE IF NOT EXISTS general_resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
                status TEXT NOT NULL DEFAULT 'available'
            );
            CREATE TABLE IF NOT EXISTS patient_resource_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
                resource_id INTEGER NOT NULL REFERENCES general_resources(id),
                quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity > 0),
                assigned_at TEXT NOT NULL,
                released_at TEXT
            );
        """)
        # Safe schema migration for databases created by the earlier version.
        patient_columns = {r["name"] for r in db.execute("PRAGMA table_info(patients)")}
        for column, definition in [
            ("phone", "TEXT NOT NULL DEFAULT ''"),
            ("started_at", "TEXT"),
            ("completed_at", "TEXT"),
            ("outcome", "TEXT"),
            ("notes", "TEXT NOT NULL DEFAULT ''"),
        ]:
            if column not in patient_columns:
                db.execute(f"ALTER TABLE patients ADD COLUMN {column} {definition}")

        if db.execute("SELECT COUNT(*) FROM doctors").fetchone()[0] == 0:
            db.executemany("INSERT INTO doctors(name,specialty,status) VALUES(?,?,?)", [
                ("Dr. Meera", "Emergency Medicine", "available"),
                ("Dr. Arjun", "General Medicine", "available"),
                ("Dr. Sana", "Trauma Care", "available"),
            ])
        if db.execute("SELECT COUNT(*) FROM beds").fetchone()[0] == 0:
            db.executemany("INSERT INTO beds(bed_name,status) VALUES(?,?)", [
                ("Bed A", "available"), ("Bed B", "available"),
                ("Bed C", "available"), ("Bed D", "available"),
            ])
        if db.execute("SELECT COUNT(*) FROM general_resources").fetchone()[0] == 0:
            db.executemany(
                "INSERT INTO general_resources(name,description,quantity,status) VALUES(?,?,?,'available')",
                DEFAULT_RESOURCES,
            )
        db.commit()
        db.close()

    def waiting_query(search: str = "", severity_filter: str = ""):
        db = db_connect()
        sql = "SELECT * FROM patients WHERE status='waiting'"
        params: list[Any] = []
        if search:
            term = f"%{search}%"
            sql += " AND (name LIKE ? OR CAST(id AS TEXT) LIKE ? OR symptoms LIKE ? OR phone LIKE ?)"
            params.extend([term, term, term, term])
        if severity_filter in {"1", "2", "3"}:
            sql += " AND severity=?"
            params.append(int(severity_filter))
        # Fetch matching rows, then apply our explicit divide-and-conquer Merge Sort.
        # Severity ASC means Critical (1), Urgent (2), Normal (3); timestamps and IDs break ties.
        rows = db.execute(sql, params).fetchall()
        db.close()
        return merge_sort(list(rows), key=lambda row: (row["severity"], row["created_at"], row["id"]))

    def doctor_suggestion(symptoms: str) -> str:
        text = symptoms.lower()
        if any(word in text for word in ("injury", "accident", "trauma", "fracture", "bleeding", "burn")):
            return "Trauma Care"
        if any(word in text for word in ("chest", "breath", "fever", "faint", "emergency")):
            return "Emergency Medicine"
        return "General Medicine"

    def resource_rules(symptoms: str, severity: int) -> list[str]:
        """Illustrative keyword rules for demo equipment suggestions, not clinical guidance."""
        text = symptoms.lower()
        requested: list[str] = []
        if any(word in text for word in ("breath", "breathing", "oxygen", "shortness of breath")):
            requested.extend(["Oxygen cylinders", "Patient monitors"])
        if any(word in text for word in ("chest", "faint", "dizzy", "heart", "palpitation")):
            requested.append("Patient monitors")
        if any(word in text for word in ("fracture", "injury", "accident", "fall", "cannot walk", "mobility")):
            requested.append("Wheelchairs")
        if any(word in text for word in ("dehydration", "fluid", "infusion", "iv drip")):
            requested.append("IV infusion pumps")
        if any(word in text for word in ("ventilation", "ventilator")):
            requested.append("Ventilators")
        # Critical is an operator-entered priority, not a diagnosis; monitoring is a demo default.
        if severity == 1:
            requested.append("Patient monitors")
        return list(dict.fromkeys(requested))

    @app.get("/")
    def index():
        search = request.args.get("q", "").strip()[:100]
        severity_filter = request.args.get("severity", "")
        db = db_connect()
        waiting = db.execute("SELECT COUNT(*) FROM patients WHERE status='waiting'").fetchone()[0]
        critical = db.execute("SELECT COUNT(*) FROM patients WHERE status='waiting' AND severity=1").fetchone()[0]
        in_treatment = db.execute("SELECT COUNT(*) FROM patients WHERE status='in_treatment'").fetchone()[0]
        treated = db.execute("SELECT COUNT(*) FROM patients WHERE status='treated'").fetchone()[0]
        doctors_available = db.execute("SELECT COUNT(*) FROM doctors WHERE status='available'").fetchone()[0]
        beds_available = db.execute("SELECT COUNT(*) FROM beds WHERE status='available'").fetchone()[0]
        beds_total = db.execute("SELECT COUNT(*) FROM beds").fetchone()[0]
        beds_allocated = db.execute("""SELECT COUNT(*) FROM beds b
            WHERE b.status='occupied' OR EXISTS (
                SELECT 1 FROM patients p WHERE p.bed_id=b.id AND p.status='in_treatment'
            )""").fetchone()[0]
        doctors = db.execute("SELECT * FROM doctors ORDER BY id").fetchall()
        beds = db.execute("SELECT * FROM beds ORDER BY id").fetchall()

        # Inventory breakdown: total, currently assigned/in use, and available quantity by class.
        resource_stats = db.execute("""SELECT gr.id, gr.name, gr.quantity,
            COALESCE((SELECT SUM(pra.quantity) FROM patient_resource_assignments pra
                WHERE pra.resource_id=gr.id AND pra.released_at IS NULL), 0) AS assigned_quantity,
            MAX(0, gr.quantity-COALESCE((SELECT SUM(pra.quantity) FROM patient_resource_assignments pra
                WHERE pra.resource_id=gr.id AND pra.released_at IS NULL), 0)) AS available_quantity
            FROM general_resources gr ORDER BY gr.id""").fetchall()

        # Show which resources are assigned to which active patient, including beds and doctors.
        active_allocations = db.execute("""SELECT p.id AS patient_id, p.name AS patient_name,
            p.severity, d.name AS doctor_name, b.bed_name,
            gr.name AS resource_name, pra.quantity AS resource_quantity
            FROM patients p
            LEFT JOIN doctors d ON p.doctor_id=d.id
            LEFT JOIN beds b ON p.bed_id=b.id
            LEFT JOIN patient_resource_assignments pra ON pra.patient_id=p.id AND pra.released_at IS NULL
            LEFT JOIN general_resources gr ON gr.id=pra.resource_id
            WHERE p.status='in_treatment'
            ORDER BY p.started_at ASC, p.id ASC, gr.name ASC""").fetchall()
        treated_today = db.execute("SELECT COUNT(*) FROM patients WHERE status='treated' AND substr(completed_at,1,10)=?",
                                  (date.today().isoformat(),)).fetchone()[0]
        wait_rows = db.execute("SELECT created_at FROM patients WHERE status IN ('waiting','in_treatment','treated')").fetchall()
        durations = []
        for row in wait_rows:
            try:
                durations.append(max(0, (datetime.now().astimezone() - datetime.fromisoformat(row["created_at"])).total_seconds() / 60))
            except (ValueError, TypeError):
                pass
        avg_wait = round(sum(durations) / len(durations), 1) if durations else 0
        severity_counts = [db.execute("SELECT COUNT(*) FROM patients WHERE severity=? AND status!='treated'", (s,)).fetchone()[0]
                           for s in (1, 2, 3)]
        hour_counts = [db.execute("SELECT COUNT(*) FROM patients WHERE substr(created_at,12,2)=?", (f"{h:02d}",)).fetchone()[0]
                       for h in range(24)]
        # Average time from registration until treatment starts, grouped by registration hour.
        # For patients still waiting, elapsed time is measured up to the current moment.
        wait_hour_samples = [[] for _ in range(24)]
        wait_chart_rows = db.execute(
            "SELECT created_at, started_at, treated_at, completed_at, status FROM patients"
        ).fetchall()
        now_for_chart = datetime.now().astimezone()
        for chart_row in wait_chart_rows:
            try:
                created_dt = datetime.fromisoformat(chart_row["created_at"])
                hour = created_dt.hour
                start_value = chart_row["started_at"] or chart_row["treated_at"]
                if start_value:
                    end_dt = datetime.fromisoformat(start_value)
                elif chart_row["status"] == "waiting":
                    end_dt = now_for_chart
                else:
                    continue
                if created_dt.tzinfo is None and end_dt.tzinfo is not None:
                    created_dt = created_dt.replace(tzinfo=end_dt.tzinfo)
                elif created_dt.tzinfo is not None and end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=created_dt.tzinfo)
                wait_hour_samples[hour].append(max(0, (end_dt-created_dt).total_seconds()/60))
            except (ValueError, TypeError):
                continue
        wait_by_hour = [
            round(sum(values)/len(values), 1) if values else 0
            for values in wait_hour_samples
        ]
        alerts = []
        for p in db.execute("SELECT id,name,created_at,severity FROM patients WHERE status='waiting'").fetchall():
            try:
                mins = (datetime.now().astimezone() - datetime.fromisoformat(p["created_at"])).total_seconds() / 60
                if p["severity"] == 1 and mins >= 5:
                    alerts.append(f"Critical patient {p['name']} (#{p['id']}) has waited over 5 minutes.")
                elif mins >= 15:
                    alerts.append(f"{p['name']} (#{p['id']}) has waited over 15 minutes.")
            except (ValueError, TypeError):
                pass
        if waiting and beds_available == 0:
            alerts.append("No beds are currently available.")
        if waiting and doctors_available == 0:
            alerts.append("No doctors are currently available.")

        # Live display and allocation preview: recommendation only, no resource is reserved.
        now_serving = db.execute("""SELECT p.id, p.name, p.severity, d.name AS doctor_name, b.bed_name
            FROM patients p LEFT JOIN doctors d ON p.doctor_id=d.id
            LEFT JOIN beds b ON p.bed_id=b.id
            WHERE p.status='in_treatment'
            ORDER BY p.started_at ASC LIMIT 1""").fetchone()
        next_patient = db.execute("""SELECT id, name, severity, symptoms, created_at
            FROM patients WHERE status='waiting'
            ORDER BY severity ASC, created_at ASC, id ASC LIMIT 1""").fetchone()
        allocation_preview = None
        if next_patient:
            suggested_specialty = ("Emergency Medicine" if next_patient["severity"] == 1 else doctor_suggestion(next_patient["symptoms"]))
            preview_doctor = db.execute("""SELECT name, specialty FROM doctors
                WHERE status='available'
                ORDER BY CASE WHEN specialty=? THEN 0 ELSE 1 END, id LIMIT 1""",
                (suggested_specialty,)).fetchone()
            preview_bed = db.execute("SELECT bed_name FROM beds WHERE status='available' ORDER BY id LIMIT 1").fetchone()
            position = db.execute("""SELECT COUNT(*) FROM patients
                WHERE status='waiting' AND (severity < ? OR
                (severity = ? AND (created_at < ? OR (created_at = ? AND id < ?))))""",
                (next_patient["severity"], next_patient["severity"], next_patient["created_at"],
                 next_patient["created_at"], next_patient["id"])).fetchone()[0]
            allocation_preview = {
                "patient": next_patient["name"],
                "severity": LABELS[next_patient["severity"]],
                "specialty": suggested_specialty,
                "doctor": preview_doctor["name"] if preview_doctor else None,
                "bed": preview_bed["bed_name"] if preview_bed else None,
                "estimated_wait": int((position + in_treatment) * 15),
                "ready": bool(preview_doctor and preview_bed),
            }
        db.close()
        return render_template(
            "index.html", patients=waiting_query(search, severity_filter), waiting=waiting,
            critical=critical, in_treatment=in_treatment, treated=treated,
            doctors_available=doctors_available, beds_available=beds_available,
            doctors=doctors, beds=beds, labels=LABELS, colors=COLORS,
            search=search, severity_filter=severity_filter, treated_today=treated_today,
            avg_wait=avg_wait, severity_counts=severity_counts, hour_counts=hour_counts, wait_by_hour=wait_by_hour,
            alerts=alerts, doctor_suggestion=doctor_suggestion,
            now_serving=now_serving, allocation_preview=allocation_preview,
            beds_total=beds_total, beds_allocated=beds_allocated,
            resource_stats=resource_stats, active_allocations=active_allocations,
        )


    @app.get("/queue-management")
    def queue_management():
        search = request.args.get("q", "").strip()[:100]
        severity_filter = request.args.get("severity", "")
        patients = waiting_query(search, severity_filter)
        return render_template("queue.html", patients=patients, labels=LABELS,
            colors=COLORS, search=search, severity_filter=severity_filter)

    @app.get("/patients")
    def patients_page():
        db = db_connect()
        rows = db.execute("""SELECT * FROM patients
            ORDER BY CASE status WHEN 'waiting' THEN 0 WHEN 'in_treatment' THEN 1 ELSE 2 END,
            severity ASC, created_at ASC, id ASC""").fetchall()
        db.close()
        return render_template("patients.html", patients=rows, labels=LABELS, colors=COLORS)

    @app.get("/doctors")
    def doctors_page():
        db = db_connect()
        doctors = db.execute("""SELECT d.*,
            (SELECT COUNT(*) FROM patients p WHERE p.doctor_id=d.id AND p.status='in_treatment') AS active_cases
            FROM doctors d ORDER BY d.id""").fetchall()
        db.close()
        return render_template("doctors.html", doctors=doctors)

    @app.get("/live-display")
    def live_display():
        db = db_connect()
        now_serving = db.execute("""SELECT p.id, p.name, p.severity, d.name AS doctor_name, b.bed_name, p.started_at
            FROM patients p LEFT JOIN doctors d ON p.doctor_id=d.id
            LEFT JOIN beds b ON p.bed_id=b.id WHERE p.status='in_treatment'
            ORDER BY p.started_at ASC LIMIT 1""").fetchone()
        active_resources = []
        if now_serving:
            active_resources = db.execute("""SELECT gr.name, pra.quantity FROM patient_resource_assignments pra
                JOIN general_resources gr ON gr.id=pra.resource_id
                WHERE pra.patient_id=? AND pra.released_at IS NULL ORDER BY gr.name""",
                (now_serving['id'],)).fetchall()
        waiting_patients = db.execute("""SELECT id, name, severity, created_at FROM patients
            WHERE status='waiting' ORDER BY severity ASC, created_at ASC, id ASC LIMIT 8""").fetchall()
        next_patient = waiting_patients[0] if waiting_patients else None
        waiting_count = db.execute("SELECT COUNT(*) FROM patients WHERE status='waiting'").fetchone()[0]
        db.close()
        return render_template("live_display.html", now_serving=now_serving,
            waiting_patients=waiting_patients, next_patient=next_patient,
            waiting_count=waiting_count, labels=LABELS, colors=COLORS,
            active_resources=active_resources)

    @app.get("/resources")
    def resources():
        db = db_connect()
        beds = db.execute("""SELECT b.*, p.name AS patient_name, p.id AS patient_id
            FROM beds b LEFT JOIN patients p
            ON p.bed_id=b.id AND p.status='in_treatment'
            ORDER BY b.id""").fetchall()
        resources = db.execute("""SELECT gr.*,
            COALESCE((SELECT SUM(pra.quantity) FROM patient_resource_assignments pra
                WHERE pra.resource_id=gr.id AND pra.released_at IS NULL),0) AS assigned_quantity,
            MAX(0, gr.quantity-COALESCE((SELECT SUM(pra.quantity) FROM patient_resource_assignments pra
                WHERE pra.resource_id=gr.id AND pra.released_at IS NULL),0)) AS available_quantity
            FROM general_resources gr ORDER BY gr.id""").fetchall()
        db.close()
        return render_template("resources.html", beds=beds, resources=resources,
            resource_statuses=sorted(GENERAL_RESOURCE_STATUSES))

    @app.post("/resources/doctors/add")
    def add_doctor():
        name = request.form.get("name", "").strip()
        specialty = request.form.get("specialty", "").strip()
        if not name or not specialty or len(name) > 100 or len(specialty) > 100:
            flash("Enter a doctor name and specialty (maximum 100 characters each).", "error")
            return redirect(url_for("doctors_page"))
        db = db_connect()
        try:
            db.execute("INSERT INTO doctors(name,specialty,status) VALUES(?,?, 'available')", (name, specialty))
            db.commit()
            flash(f"{name} added as an available doctor.", "success")
        except sqlite3.IntegrityError:
            flash("A doctor with that name already exists.", "error")
        finally:
            db.close()
        return redirect(url_for("doctors_page"))

    @app.post("/resources/beds/add")
    def add_bed():
        bed_name = request.form.get("bed_name", "").strip()
        if not bed_name or len(bed_name) > 60:
            flash("Enter a bed name (maximum 60 characters).", "error")
            return redirect(url_for("resources"))
        db = db_connect()
        try:
            db.execute("INSERT INTO beds(bed_name,status) VALUES(?, 'available')", (bed_name,))
            db.commit()
            flash(f"{bed_name} added and marked available.", "success")
        except sqlite3.IntegrityError:
            flash("A bed with that name already exists.", "error")
        finally:
            db.close()
        return redirect(url_for("resources"))

    @app.post("/resources/doctor/<int:doctor_id>/delete")
    def delete_doctor(doctor_id: int):
        db = db_connect()
        row = db.execute("SELECT name,status FROM doctors WHERE id=?", (doctor_id,)).fetchone()
        assigned = db.execute("SELECT COUNT(*) FROM patients WHERE doctor_id=? AND status='in_treatment'", (doctor_id,)).fetchone()[0]
        if row is None:
            flash("Doctor not found.", "error")
        elif row["status"] == "busy" or assigned:
            flash("Cannot remove a doctor with an active treatment. Complete the treatment first.", "error")
        else:
            db.execute("DELETE FROM doctors WHERE id=?", (doctor_id,))
            db.commit()
            flash(f"{row['name']} removed.", "success")
        db.close()
        return redirect(url_for("resources"))

    @app.post("/resources/bed/<int:bed_id>/delete")
    def delete_bed(bed_id: int):
        db = db_connect()
        row = db.execute("SELECT bed_name,status FROM beds WHERE id=?", (bed_id,)).fetchone()
        assigned = db.execute("SELECT COUNT(*) FROM patients WHERE bed_id=? AND status='in_treatment'", (bed_id,)).fetchone()[0]
        if row is None:
            flash("Bed not found.", "error")
        elif row["status"] == "occupied" or assigned:
            flash("Cannot remove an occupied bed. Complete the assigned treatment first.", "error")
        else:
            db.execute("DELETE FROM beds WHERE id=?", (bed_id,))
            db.commit()
            flash(f"{row['bed_name']} removed.", "success")
        db.close()
        return redirect(url_for("resources"))

    @app.post("/resources/inventory/<int:resource_id>")
    def update_general_resource(resource_id: int):
        status = request.form.get("status", "")
        quantity_text = request.form.get("quantity", "").strip()
        if status not in GENERAL_RESOURCE_STATUSES:
            flash("Choose a valid resource status.", "error")
            return redirect(url_for("resources"))
        try:
            quantity = int(quantity_text)
            if quantity < 0 or quantity > 10000:
                raise ValueError
        except ValueError:
            flash("Quantity must be a whole number between 0 and 10000.", "error")
            return redirect(url_for("resources"))
        db = db_connect()
        row = db.execute("SELECT name FROM general_resources WHERE id=?", (resource_id,)).fetchone()
        if row is None:
            flash("Resource not found.", "error")
        else:
            if quantity == 0 and status == "available":
                status = "out_of_stock"
            db.execute("UPDATE general_resources SET quantity=?, status=? WHERE id=?",
                (quantity, status, resource_id))
            db.commit()
            flash(f"{row['name']} inventory updated.", "success")
        db.close()
        return redirect(url_for("resources"))

    @app.post("/patients")
    def add_patient():
        name = request.form.get("name", "").strip()
        age_text = request.form.get("age", "").strip()
        severity_text = request.form.get("severity", "").strip()
        symptoms = request.form.get("symptoms", "").strip()
        phone = request.form.get("phone", "").strip()
        if not all((name, age_text, severity_text, symptoms, phone)):
            flash("Please fill in all fields.", "error")
            return redirect(url_for("patients_page"))
        try:
            age, severity = int(age_text), int(severity_text)
        except ValueError:
            flash("Age and severity must be numbers.", "error")
            return redirect(url_for("patients_page"))
        if not 0 <= age <= 120:
            flash("Age must be between 0 and 120.", "error")
            return redirect(url_for("patients_page"))
        if severity not in LABELS:
            flash("Choose a valid severity level.", "error")
            return redirect(url_for("patients_page"))
        if len(name) > 100 or len(symptoms) > 500 or len(phone) > 20:
            flash("Name limit is 100 characters, phone limit is 20, and symptoms limit is 500.", "error")
            return redirect(url_for("patients_page"))
        if not phone.replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "").isdigit() or len(phone.replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")) < 7:
            flash("Enter a valid phone number (at least 7 digits).", "error")
            return redirect(url_for("patients_page"))
        db = db_connect()
        duplicate = db.execute("""SELECT id FROM patients WHERE lower(name)=lower(?) AND age=?
            AND status IN ('waiting','in_treatment')""", (name, age)).fetchone()
        if duplicate:
            db.close()
            flash(f"A patient with this name and age is already active (ID #{duplicate['id']}).", "error")
            return redirect(url_for("patients_page"))
        db.execute("""INSERT INTO patients(name,phone,age,severity,symptoms,status,created_at,notes)
            VALUES(?,?,?,?,?,'waiting',?,'')""", (name, phone, age, severity, symptoms, now_iso()))
        db.commit()
        db.close()
        if severity == 1:
            flash(f"EMERGENCY ALERT: {name} registered as Critical. Follow approved clinical procedures.", "error")
        else:
            flash(f"{name} registered. Severity recorded as {LABELS[severity]}.", "success")
        return redirect(url_for("patients_page"))

    @app.post("/patients/<int:patient_id>/edit")
    def edit_patient(patient_id: int):
        name = request.form.get("name", "").strip()
        symptoms = request.form.get("symptoms", "").strip()
        phone = request.form.get("phone", "").strip()
        severity_text = request.form.get("severity", "").strip()
        if not name or not symptoms or len(name) > 100 or len(symptoms) > 500 or len(phone) > 20:
            flash("Enter a valid name and symptoms (maximum 100 and 500 characters).", "error")
            return redirect(url_for("queue_management"))
        try:
            severity = int(severity_text)
        except ValueError:
            severity = 0
        if severity not in LABELS:
            flash("Choose a valid severity level.", "error")
            return redirect(url_for("queue_management"))
        if phone and (not phone.replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "").isdigit() or len(phone.replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")) < 7):
            flash("Enter a valid phone number (at least 7 digits).", "error")
            return redirect(url_for("queue_management"))
        db = db_connect()
        cur = db.execute("""UPDATE patients SET name=?, phone=?, symptoms=?, severity=?
            WHERE id=? AND status='waiting'""", (name, phone, symptoms, severity, patient_id))
        db.commit()
        db.close()
        flash("Waiting patient updated." if cur.rowcount else "Only waiting patients can be edited.",
              "success" if cur.rowcount else "error")
        return redirect(url_for("queue_management"))

    @app.post("/treat-next")
    def treat_next():
        return_to = request.form.get("return_to", "queue_management")
        destination = "live_display" if return_to == "live_display" else "queue_management"
        db = db_connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            patient = db.execute("""SELECT id,name,severity,symptoms FROM patients WHERE status='waiting'
                ORDER BY severity ASC, created_at ASC, id ASC LIMIT 1""").fetchone()
            if patient is None:
                db.rollback()
                flash("There are no patients waiting.", "error")
                return redirect(url_for(destination))
            suggestion = ("Emergency Medicine" if patient["severity"] == 1 else doctor_suggestion(patient["symptoms"]))
            doctor = db.execute("""SELECT id,name,specialty FROM doctors WHERE status='available'
                ORDER BY CASE WHEN specialty=? THEN 0 ELSE 1 END, id LIMIT 1""", (suggestion,)).fetchone()
            bed = db.execute("SELECT id,bed_name FROM beds WHERE status='available' ORDER BY id LIMIT 1").fetchone()
            if doctor is None or bed is None:
                db.rollback()
                flash("No available doctor or bed. Update resource status or complete another treatment.", "error")
                return redirect(url_for(destination))
            now = now_iso()
            db.execute("""UPDATE patients SET status='in_treatment', started_at=?, treated_at=?,
                doctor_id=?, bed_id=? WHERE id=? AND status='waiting'""",
                (now, now, doctor["id"], bed["id"], patient["id"]))
            db.execute("UPDATE doctors SET status='busy' WHERE id=?", (doctor["id"],))
            db.execute("UPDATE beds SET status='occupied' WHERE id=?", (bed["id"],))

            # Apply simple, transparent demo rules to reserve available equipment.
            assigned_names = []
            unavailable_names = []
            for resource_name in resource_rules(patient["symptoms"], patient["severity"]):
                resource = db.execute("""SELECT gr.id, gr.quantity, gr.status,
                    COALESCE((SELECT SUM(pra.quantity) FROM patient_resource_assignments pra
                      WHERE pra.resource_id=gr.id AND pra.released_at IS NULL),0) AS allocated
                    FROM general_resources gr WHERE gr.name=?""", (resource_name,)).fetchone()
                if resource and resource["status"] == "available" and resource["quantity"] - resource["allocated"] >= 1:
                    db.execute("""INSERT INTO patient_resource_assignments(patient_id,resource_id,quantity,assigned_at)
                        VALUES(?,?,1,?)""", (patient["id"], resource["id"], now))
                    assigned_names.append(resource_name)
                else:
                    unavailable_names.append(resource_name)
            db.commit()
            resource_text = ", ".join(assigned_names) if assigned_names else "no symptom-rule equipment matched"
            message = (f"Treatment started for {patient['name']}. Suggested specialty: {suggestion}. "
                       f"Assigned {doctor['name']} and {bed['bed_name']}. Equipment reserved: {resource_text}. "
                       "Equipment rules are illustrative and require staff confirmation.")
            if unavailable_names:
                message += " Not available: " + ", ".join(unavailable_names) + "."
            flash(message, "success")
        finally:
            db.close()
        return redirect(url_for(destination))

    @app.post("/patients/<int:patient_id>/complete")
    def complete_treatment(patient_id: int):
        outcome = request.form.get("outcome", "Completed").strip()[:200] or "Completed"
        notes = request.form.get("notes", "").strip()[:2000]
        db = db_connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            patient = db.execute("""SELECT doctor_id,bed_id,name FROM patients
                WHERE id=? AND status='in_treatment'""", (patient_id,)).fetchone()
            if patient is None:
                db.rollback()
                flash("Patient is not currently in treatment.", "error")
            else:
                db.execute("""UPDATE patients SET status='treated',completed_at=?,outcome=?,notes=? WHERE id=?""",
                            (now_iso(), outcome, notes, patient_id))
                if patient["doctor_id"] is not None:
                    db.execute("UPDATE doctors SET status='available' WHERE id=? AND status='busy'", (patient["doctor_id"],))
                if patient["bed_id"] is not None:
                    db.execute("UPDATE beds SET status='available' WHERE id=? AND status='occupied'", (patient["bed_id"],))
                db.execute("UPDATE patient_resource_assignments SET released_at=? WHERE patient_id=? AND released_at IS NULL",
                           (now_iso(), patient_id))
                db.commit()
                flash(f"Treatment completed for {patient['name']}; assigned resources have been released.", "success")
        finally:
            db.close()
        return redirect(url_for("history"))

    @app.post("/patients/<int:patient_id>/delete")
    def delete_patient(patient_id: int):
        db = db_connect()
        cur = db.execute("DELETE FROM patients WHERE id=? AND status='waiting'", (patient_id,))
        db.commit()
        db.close()
        flash("Waiting patient removed." if cur.rowcount else "Patient not found or already in treatment.",
              "success" if cur.rowcount else "error")
        return redirect(url_for("queue_management"))

    @app.post("/resources/doctor/<int:doctor_id>")
    def update_doctor(doctor_id: int):
        status = request.form.get("status", "")
        if status not in DOCTOR_STATUSES:
            flash("Invalid doctor status.", "error")
        else:
            db = db_connect()
            row = db.execute("SELECT status FROM doctors WHERE id=?", (doctor_id,)).fetchone()
            if row is None:
                flash("Doctor not found.", "error")
            elif row["status"] == "busy" and status != "busy":
                flash("Complete the assigned treatment before changing a busy doctor's status.", "error")
            else:
                db.execute("UPDATE doctors SET status=? WHERE id=?", (status, doctor_id))
                db.commit()
                flash("Doctor status updated.", "success")
            db.close()
        return redirect(url_for("doctors_page"))

    @app.post("/resources/bed/<int:bed_id>")
    def update_bed(bed_id: int):
        status = request.form.get("status", "")
        if status not in BED_STATUSES:
            flash("Invalid bed status.", "error")
        else:
            db = db_connect()
            row = db.execute("SELECT status FROM beds WHERE id=?", (bed_id,)).fetchone()
            if row is None:
                flash("Bed not found.", "error")
            elif row["status"] == "occupied" and status != "occupied":
                flash("Complete the assigned treatment before changing an occupied bed.", "error")
            else:
                db.execute("UPDATE beds SET status=? WHERE id=?", (status, bed_id))
                db.commit()
                flash("Bed status updated.", "success")
            db.close()
        return redirect(url_for("resources"))

    @app.get("/history")
    def history():
        db = db_connect()
        patients = db.execute("""SELECT p.*, d.name AS doctor_name, b.bed_name,
            (SELECT GROUP_CONCAT(gr.name, ', ') FROM patient_resource_assignments pra
              JOIN general_resources gr ON gr.id=pra.resource_id WHERE pra.patient_id=p.id) AS equipment_names
            FROM patients p LEFT JOIN doctors d ON p.doctor_id=d.id
            LEFT JOIN beds b ON p.bed_id=b.id
            WHERE p.status IN ('in_treatment','treated')
            ORDER BY CASE p.status WHEN 'in_treatment' THEN 0 ELSE 1 END,
            COALESCE(p.completed_at,p.started_at,p.created_at) DESC, p.id DESC""").fetchall()
        db.close()
        return render_template("history.html", patients=patients, labels=LABELS, colors=COLORS)

    @app.get("/history.csv")
    def export_history():
        db = db_connect()
        rows = db.execute("""SELECT p.id,p.name,p.phone,p.age,p.severity,p.symptoms,p.status,p.created_at,
            p.started_at,p.completed_at,p.outcome,p.notes,d.name AS doctor,b.bed_name
            FROM patients p LEFT JOIN doctors d ON p.doctor_id=d.id
            LEFT JOIN beds b ON p.bed_id=b.id ORDER BY p.id""").fetchall()
        db.close()
        output = io.StringIO()
        writer = csv.writer(output)
        # Generator yields one export row at a time (memory-friendly row processing).
        writer.writerows(iter_csv_rows(rows))
        response = make_response("\ufeff" + output.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = "attachment; filename=carequeue_treatment_history.csv"
        return response

    with app.app_context():
        init_db()
    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
