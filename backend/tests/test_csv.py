"""CSV upload validation and import tests."""
import io


def _csv(rows: list[str]) -> dict:
    files = {"file": ("alerts.csv", io.BytesIO("\n".join(rows).encode()), "text/csv")}
    return files


def test_csv_import_happy_path(client, auth_headers):
    rows = [
        "service_name,title,severity,timestamp",
        "svc-web,Checkout 5xx spike,critical,2026-10-28T10:00:00Z",
        "svc-web,Checkout 5xx spike (db),high,2026-10-28T10:05:00Z",
    ]
    r = client.post("/api/alerts/import-csv", files=_csv(rows), headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["imported"] == 2
    assert data["errors"] == []


def test_csv_missing_required_columns_rejected(client, auth_headers):
    rows = ["foo,bar", "a,b"]
    r = client.post("/api/alerts/import-csv", files=_csv(rows), headers=auth_headers)
    assert r.status_code == 422


def test_csv_bad_rows_skipped_not_fatal(client, auth_headers):
    rows = [
        "service,title,severity",
        "svc-x,Good alert,high",
        "svc-y,,missing title",  # missing title -> skipped with error
    ]
    r = client.post("/api/alerts/import-csv", files=_csv(rows), headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["imported"] == 1
    assert len(data["errors"]) == 1


def test_csv_severity_alias_normalized(client, auth_headers):
    rows = ["service_name,title,severity", "svc-z,Disk pressure,SEV1"]
    r = client.post("/api/alerts/import-csv", files=_csv(rows), headers=auth_headers)
    assert r.json()["imported"] == 1
    list_r = client.get("/api/alerts?service=svc-z", headers=auth_headers)
    assert list_r.json()["items"][0]["severity"] == "critical"


def test_csv_rejects_non_csv_extension(client, auth_headers):
    files = {"file": ("malware.exe", io.BytesIO(b"MZ..."), "application/octet-stream")}
    r = client.post("/api/alerts/import-csv", files=files, headers=auth_headers)
    assert r.status_code == 422