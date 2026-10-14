# CareQueue — Hospital Emergency Management System

Educational Flask + SQLite prototype for patient queueing, doctor/bed status management, treatment history, and resource inventory.

## Run locally (macOS)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -v
python app.py
```
Open http://127.0.0.1:5000.

## Navigation
- `/` — Dashboard overview and visual insights only
- `/live-display` — Separate live queue board
- `/queue-management` — Priority waiting queue only
- `/patients` — Register patients and view status/waiting time
- `/doctors` — Doctor directory and availability
- `/resources` — Beds and equipment inventory
- `/history` — Treatment history
- `/history.csv` — CSV export

## Computational thinking concepts demonstrated in code
- **Decomposition and abstraction:** Flask routes and helper functions split registration, queueing, assignment, reporting, and resource rules into focused responsibilities.
- **Divide and conquer:** `merge_sort()` recursively divides waiting-patient records, sorts each half, and merges them by `(severity, created_at, id)`. Average/worst-case time is O(n log n), with O(n) auxiliary storage.
- **Greedy allocation:** `/treat-next` chooses the highest-priority waiting patient and then picks currently available resources using local rules. This is a practical greedy strategy, not a proof of globally optimal allocation.
- **Rule-based logic:** `doctor_suggestion()` and `resource_rules()` map symptom keywords to demo specialties/equipment. This is not a medical diagnosis or trained AI.
- **Generator:** `iter_csv_rows()` yields export rows one at a time for the CSV writer, avoiding an additional list of all formatted rows. The final response is still buffered in memory.
- **Database persistence and transaction safety:** SQLite stores patient/resource state; treatment-start operations use transactional database updates.
- **Software testing:** `tests/test_app.py` covers routes, validation, priority ordering, resource lifecycle, CSV export, and the Merge Sort/generator helpers.

## Important verification note
Run `python -m pytest -v` on your machine after installing requirements. The development environment used to prepare this update did not have Flask available and could not install it because network access was unavailable, so the full test suite could not be rerun here. Do not report a new test-pass count until the local run confirms it.

## Equipment inventory
The default inventory contains wheelchairs, oxygen cylinders, patient monitors, ventilators, and IV infusion pumps. Quantities and inventory status can be updated from Beds & Resources.

CareQueue is an educational prototype. Severity is entered by an operator and is not a validated clinical triage protocol. Do not use it to make real medical decisions.
