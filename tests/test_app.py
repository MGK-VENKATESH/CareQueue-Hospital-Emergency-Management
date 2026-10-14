import csv
import io
import pytest
from app import create_app

@pytest.fixture()
def client(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.db"), "SECRET_KEY": "test"})
    with app.test_client() as client:
        yield client

def register(client, name, age=30, severity=3, symptoms="routine checkup"):
    return client.post("/patients", data={"name": name, "age": str(age),
        "severity": str(severity), "symptoms": symptoms, "phone": "9876543210"}, follow_redirects=True)

def test_dashboard_and_resources(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Dashboard insights" in response.data
    assert b"Priority waiting queue" not in response.data
    assert b"Resource availability" not in response.data
    assert b"Recommended next assignment" not in response.data

def test_critical_alert(client):
    response = register(client, "Asha", 32, 1)
    assert b"EMERGENCY ALERT" in response.data

def test_priority_and_specialty_assignment(client):
    register(client, "Normal", 40, 3, "routine checkup")
    register(client, "Injury", 25, 2, "injury after accident")
    register(client, "Critical", 60, 1, "chest pain")
    response = client.post("/treat-next", follow_redirects=True)
    assert b"Treatment started for Critical" in response.data
    assert b"Dr. Meera" in response.data

def test_search_and_severity_filter(client):
    register(client, "Asha", 30, 1)
    register(client, "Ravi", 30, 3)
    assert b"Asha" in client.get("/queue-management?q=Asha").data
    response = client.get("/queue-management?severity=1")
    assert b"Asha" in response.data
    assert b"Ravi" not in response.data

def test_edit_waiting_patient(client):
    register(client, "Ravi")
    response = client.post("/patients/1/edit", data={"name":"Ravi Updated","severity":"2","symptoms":"updated symptoms"}, follow_redirects=True)
    assert b"Waiting patient updated" in response.data
    assert b"Ravi Updated" in response.data

def test_invalid_age_and_severity(client):
    assert b"Age must be between 0 and 120" in register(client, "Invalid", 150, 1).data
    assert b"Choose a valid severity level" in register(client, "Invalid", 20, 9).data

def test_duplicate_active_patient_rejected(client):
    register(client, "Asha", 32, 2)
    response = register(client, "asha", 32, 2)
    assert b"already active" in response.data

def test_complete_releases_resources_and_stores_notes(client):
    register(client, "Patient One", 44, 1)
    client.post("/treat-next")
    response = client.post("/patients/1/complete", data={"outcome":"Discharged","notes":"Demo note"}, follow_redirects=True)
    assert b"Treatment completed" in response.data
    history = client.get("/history")
    assert b"Demo note" in history.data
    assert b"Discharged" in history.data
    dashboard = client.get("/")
    assert b"Doctors available" in dashboard.data and b">3</div>" in dashboard.data

def test_resource_status_updates(client):
    response = client.post("/resources/doctor/1", data={"status":"off_duty"}, follow_redirects=True)
    assert b"Doctor status updated" in response.data
    response = client.post("/resources/bed/1", data={"status":"cleaning"}, follow_redirects=True)
    assert b"Bed status updated" in response.data

def test_csv_export(client):
    register(client, "CSV Patient", 28, 2)
    response = client.get("/history.csv")
    assert response.status_code == 200
    assert "text/csv" in response.content_type
    assert b"CSV Patient" in response.data

def test_treatment_history_shows_start_time(client):
    register(client, "History Patient", 35, 2)
    client.post("/treat-next")
    response = client.get("/history")
    assert b"Started" in response.data
    assert b"History Patient" in response.data


def test_patient_modal_and_phone_field(client):
    response = client.get("/")
    assert b"Register patient" in client.get("/patients").data
    assert b"patient-phone" in client.get("/patients").data
    register(client, "Phone Patient", 26, 2, "headache")
    response = client.get("/")
    response = client.get("/patients")
    assert b"9876543210" in response.data

def test_phone_validation(client):
    response = client.post("/patients", data={"name":"Bad Phone", "phone":"12", "age":"20", "severity":"3", "symptoms":"checkup"}, follow_redirects=True)
    assert b"valid phone number" in response.data


def test_sidebar_navigation_and_live_allocation_preview(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Live Display" in response.data
    assert b"Queue Management" in response.data
    assert b"Beds &amp; Resources" in response.data or b"Beds & Resources" in response.data
    assert b"Reports / CSV" in response.data
    assert b"Recommended next assignment" not in response.data
    assert b"Register patient" not in response.data

def test_allocation_preview_updates_when_patient_registered(client):
    register(client, "Preview Patient", 29, 2, "injury")
    response = client.get("/")
    assert b"Preview Patient" not in response.data
    assert b"Preview Patient" in client.get("/queue-management").data


def test_live_display_is_separate_page(client):
    response = client.get("/live-display")
    assert response.status_code == 200
    assert b"Public-facing queue board" in response.data
    assert b"Next patients" in response.data


def test_resources_page_can_add_doctor_and_bed(client):
    response = client.get("/resources")
    assert response.status_code == 200
    response = client.post("/resources/doctors/add", data={"name": "Dr. Priya", "specialty": "Cardiology"}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Dr. Priya" in response.data
    response = client.post("/resources/beds/add", data={"bed_name": "Bed E"}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Bed E" in response.data


def test_resource_duplicate_and_busy_delete_protection(client):
    client.post("/resources/doctors/add", data={"name": "Dr. Duplicate", "specialty": "General Medicine"})
    response = client.post("/resources/doctors/add", data={"name": "Dr. Duplicate", "specialty": "Cardiology"}, follow_redirects=True)
    assert b"already exists" in response.data
    register(client, "Resource Patient", 40, 1)
    client.post("/treat-next")
    response = client.post("/resources/doctor/1/delete", follow_redirects=True)
    assert b"Cannot remove a doctor" in response.data


def test_separate_navigation_pages_and_five_equipment_categories(client):
    dashboard = client.get("/")
    assert b"Priority waiting queue" not in dashboard.data
    assert b"Resource availability" not in dashboard.data
    assert b"System ready" not in dashboard.data
    queue = client.get("/queue-management")
    assert b"Priority waiting queue" in queue.data
    assert b"Resource availability" not in queue.data
    patients = client.get("/patients")
    assert b"Patient details and status" in patients.data
    doctors = client.get("/doctors")
    assert b"Doctor directory" in doctors.data
    assert b"Bed inventory" not in doctors.data
    resources = client.get("/resources")
    assert b"Bed inventory" in resources.data
    for name in (b"Wheelchairs", b"Oxygen cylinders", b"Patient monitors", b"Ventilators", b"IV infusion pumps"):
        assert name in resources.data
    assert b"Doctor directory" not in resources.data


def test_general_resource_inventory_update(client):
    response = client.get("/resources")
    assert response.status_code == 200
    response = client.post("/resources/inventory/1", data={"quantity":"4", "status":"available"}, follow_redirects=True)
    assert b"Wheelchairs inventory updated" in response.data
    assert b'value="4"' in response.data

def test_live_display_queue_link_and_start_treatment_action(client):
    from flask import url_for
    client_app = None
    # Route destination is exercised by submitting the live-display return target below.
    # This assertion checks the public page offers the dedicated queue route, not dashboard anchor.
    # Client fixture already exposes the application through its test client context.
    response = register(client, "Breathing Patient", 50, 1, "shortness of breath")
    live = client.get("/live-display")
    assert b"Open queue management" in live.data
    assert b"/queue-management" in live.data
    assert b"Start next treatment &amp; allocate resources" in live.data or b"Start next treatment & allocate resources" in live.data
    response = client.post("/treat-next", data={"return_to": "live_display"}, follow_redirects=True)
    assert b"Treatment started for Breathing Patient" in response.data
    assert b"Equipment reserved" in response.data
    assert "/live-display" in response.request.path


def test_equipment_assignment_is_recorded_and_released(client):
    register(client, "Mobility Patient", 36, 2, "fracture after fall")
    response = client.post("/treat-next", follow_redirects=True)
    assert b"Wheelchairs" in response.data
    history = client.get("/history")
    assert b"Equipment: Wheelchairs" in history.data
    client.post("/patients/1/complete", data={"outcome": "Completed", "notes": ""})
    db = __import__("sqlite3").connect(client.application.config["DATABASE"])
    released = db.execute("SELECT released_at FROM patient_resource_assignments WHERE patient_id=1").fetchone()
    db.close()
    assert released and released[0]


def test_merge_sort_orders_priority_and_stable_ties():
    from app import merge_sort
    records = [
        {"name": "normal", "severity": 3, "time": "10:00", "id": 3},
        {"name": "urgent-later", "severity": 2, "time": "10:02", "id": 2},
        {"name": "critical", "severity": 1, "time": "10:03", "id": 4},
        {"name": "urgent-first", "severity": 2, "time": "10:01", "id": 1},
    ]
    ordered = merge_sort(records, key=lambda row: (row["severity"], row["time"], row["id"]))
    assert [row["name"] for row in ordered] == ["critical", "urgent-first", "urgent-later", "normal"]
    assert [row["name"] for row in records][0] == "normal"  # input remains unchanged

def test_csv_export_uses_generator_and_keeps_header(client):
    from app import iter_csv_rows
    register(client, "Generator CSV Patient", 35, 2, "checkup")
    response = client.get("/history.csv")
    assert response.status_code == 200
    assert b"Patient ID,Name,Phone,Age,Severity" in response.data
    assert b"Generator CSV Patient" in response.data
    generator = iter_csv_rows([])
    assert next(generator)[0] == "Patient ID"
