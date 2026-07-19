"""
tests/unit/test_compare.py
───────────────────────────
Unit tests for POST /api/v1/compare.

Strategy
────────
- All tests use Flask's test client — no real HTTP server.
- Test files are created in a per-test tmp directory (pytest tmp_path).
- Every format is tested for: identical files, changed content,
  added rows/keys, removed rows/keys, and structural differences.
- Request validation, error codes, response envelope shape, and
  HTTP method guards are also covered.
"""

import io
import json
import os
import pytest
import pandas as pd
from pathlib import Path

from app.main import create_app
from app.core.config import settings


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path):
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    settings.UPLOAD_DIR = str(tmp_path / "uploads")
    settings.OUTPUT_DIR = str(tmp_path / "outputs")
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    os.makedirs(settings.OUTPUT_DIR, exist_ok=True)
    yield flask_app
    settings.UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads/temp")
    settings.OUTPUT_DIR = os.getenv("OUTPUT_DIR", "outputs")


@pytest.fixture
def client(app):
    return app.test_client()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def write(filename: str, content, mode="w", encoding="utf-8") -> str:
    """Write content to UPLOAD_DIR and return absolute path."""
    path = os.path.join(settings.UPLOAD_DIR, filename)
    if isinstance(content, bytes):
        Path(path).write_bytes(content)
    else:
        Path(path).write_text(content, encoding=encoding)
    return path


def post_compare(client, path_a: str, path_b: str):
    return client.post(
        f"{settings.API_PREFIX}/compare",
        data=json.dumps({"file_a_path": path_a, "file_b_path": path_b}),
        content_type="application/json",
    )


def make_pdf(path: str, lines: list[str]):
    """Create a real PDF with given lines using reportlab."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    c = canvas.Canvas(path, pagesize=A4)
    y = 750
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.save()
    return path


def make_xlsx(path: str, rows: list[dict]):
    """Create a real XLSX from list-of-dicts."""
    pd.DataFrame(rows).to_excel(path, index=False, engine="openpyxl")
    return path


# ─────────────────────────────────────────────────────────────────────────────
# 1. Request validation
# ─────────────────────────────────────────────────────────────────────────────

class TestRequestValidation:

    def test_no_body_returns_400(self, client):
        r = client.post(f"{settings.API_PREFIX}/compare")
        assert r.status_code == 400

    def test_no_body_error_code(self, client):
        assert client.post(
            f"{settings.API_PREFIX}/compare"
        ).get_json()["error"] == "MISSING_BODY"

    def test_missing_file_a_path(self, client):
        r = client.post(
            f"{settings.API_PREFIX}/compare",
            data=json.dumps({"file_b_path": "/tmp/b.csv"}),
            content_type="application/json",
        )
        assert r.status_code == 400
        assert r.get_json()["error"] == "MISSING_FILE_A_PATH"

    def test_missing_file_b_path(self, client):
        r = client.post(
            f"{settings.API_PREFIX}/compare",
            data=json.dumps({"file_a_path": "/tmp/a.csv"}),
            content_type="application/json",
        )
        assert r.status_code == 400
        assert r.get_json()["error"] == "MISSING_FILE_B_PATH"

    def test_file_a_not_found_returns_404(self, client):
        b = write("b.csv", "name\nBob\n")
        r = post_compare(client, "/no/such/file.csv", b)
        assert r.status_code == 404
        assert r.get_json()["error"] == "FILE_A_NOT_FOUND"

    def test_file_b_not_found_returns_404(self, client):
        a = write("a.csv", "name\nAlice\n")
        r = post_compare(client, a, "/no/such/file.csv")
        assert r.status_code == 404
        assert r.get_json()["error"] == "FILE_B_NOT_FOUND"

    def test_format_mismatch_returns_400(self, client):
        a = write("a.csv", "name\nAlice\n")
        b = write("b.json", '{"name":"Alice"}')
        r = post_compare(client, a, b)
        assert r.status_code == 400
        assert r.get_json()["error"] == "FORMAT_MISMATCH"

    def test_unsupported_format_a(self, client):
        a = write("a.docx", b"FAKEBINARY")
        b = write("b.docx", b"FAKEBINARY")
        r = post_compare(client, a, b)
        assert r.status_code == 400
        assert r.get_json()["error"] == "UNSUPPORTED_FORMAT_A"

    def test_get_returns_405(self, client):
        assert client.get(f"{settings.API_PREFIX}/compare").status_code == 405

    def test_405_json_envelope(self, client):
        d = client.get(f"{settings.API_PREFIX}/compare").get_json()
        assert d["success"] is False
        assert d["error"] == "METHOD_NOT_ALLOWED"

    def test_error_envelope_shape(self, client):
        r = client.post(f"{settings.API_PREFIX}/compare")
        d = r.get_json()
        assert "success" in d and "error" in d and "message" in d
        assert d["success"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. Response envelope shape (valid comparison)
# ─────────────────────────────────────────────────────────────────────────────

class TestResponseEnvelope:

    def test_returns_200(self, client):
        a = write("a.csv", "name,age\nAlice,30\n")
        b = write("b.csv", "name,age\nAlice,30\n")
        assert post_compare(client, a, b).status_code == 200

    def test_success_true(self, client):
        a = write("a.txt", "hello\n")
        b = write("b.txt", "hello\n")
        assert post_compare(client, a, b).get_json()["success"] is True

    def test_message_field(self, client):
        a = write("a.txt", "hello\n")
        b = write("b.txt", "hello\n")
        assert "compared" in post_compare(client, a, b).get_json()["message"].lower()

    def test_data_has_comparison_id(self, client):
        a = write("a.txt", "x\n")
        b = write("b.txt", "x\n")
        d = post_compare(client, a, b).get_json()["data"]
        assert len(d["comparison_id"]) == 36

    def test_data_has_files_match(self, client):
        a = write("a.txt", "x\n")
        b = write("b.txt", "x\n")
        assert "files_match" in post_compare(client, a, b).get_json()["data"]

    def test_data_has_similarity_percentage(self, client):
        a = write("a.txt", "x\n")
        b = write("b.txt", "x\n")
        d = post_compare(client, a, b).get_json()["data"]
        assert "similarity_percentage" in d
        assert isinstance(d["similarity_percentage"], float)

    def test_data_has_total_differences(self, client):
        a = write("a.txt", "x\n")
        b = write("b.txt", "x\n")
        assert "total_differences" in post_compare(client, a, b).get_json()["data"]

    def test_data_has_summary(self, client):
        a = write("a.txt", "x\n")
        b = write("b.txt", "x\n")
        assert "summary" in post_compare(client, a, b).get_json()["data"]

    def test_data_has_compared_at(self, client):
        a = write("a.txt", "x\n")
        b = write("b.txt", "x\n")
        d = post_compare(client, a, b).get_json()["data"]
        assert "compared_at" in d and d["compared_at"]

    def test_data_has_differences_list(self, client):
        a = write("a.txt", "x\n")
        b = write("b.txt", "x\n")
        d = post_compare(client, a, b).get_json()["data"]
        assert isinstance(d["differences"], list)

    def test_data_has_format(self, client):
        a = write("a.csv", "name\nAlice\n")
        b = write("b.csv", "name\nAlice\n")
        d = post_compare(client, a, b).get_json()["data"]
        assert d["format"] == "csv"

    def test_data_has_file_a_and_file_b(self, client):
        a = write("a.csv", "name\nAlice\n")
        b = write("b.csv", "name\nAlice\n")
        d = post_compare(client, a, b).get_json()["data"]
        assert "file_a" in d and "file_b" in d

    def test_two_comparisons_have_unique_ids(self, client):
        a = write("a.txt", "hi\n")
        b = write("b.txt", "hi\n")
        id1 = post_compare(client, a, b).get_json()["data"]["comparison_id"]
        id2 = post_compare(client, a, b).get_json()["data"]["comparison_id"]
        assert id1 != id2

    def test_similarity_is_0_to_100(self, client):
        a = write("a.txt", "aaa\n")
        b = write("b.txt", "zzz\n")
        d = post_compare(client, a, b).get_json()["data"]
        assert 0.0 <= d["similarity_percentage"] <= 100.0


# ─────────────────────────────────────────────────────────────────────────────
# 3. CSV comparison
# ─────────────────────────────────────────────────────────────────────────────

class TestCsvComparison:

    CSV_BASE = "name,age,city\nAlice,30,London\nBob,25,Paris\n"

    def test_identical_files_match(self, client):
        a = write("a.csv", self.CSV_BASE)
        b = write("b.csv", self.CSV_BASE)
        d = post_compare(client, a, b).get_json()["data"]
        assert d["files_match"] is True

    def test_identical_similarity_is_100(self, client):
        a = write("a.csv", self.CSV_BASE)
        b = write("b.csv", self.CSV_BASE)
        d = post_compare(client, a, b).get_json()["data"]
        assert d["similarity_percentage"] == 100.0

    def test_identical_zero_differences(self, client):
        a = write("a.csv", self.CSV_BASE)
        b = write("b.csv", self.CSV_BASE)
        assert post_compare(client, a, b).get_json()["data"]["total_differences"] == 0

    def test_changed_value_detected(self, client):
        a = write("a.csv", self.CSV_BASE)
        b = write("b.csv", "name,age,city\nAlice,31,London\nBob,25,Paris\n")
        d = post_compare(client, a, b).get_json()["data"]
        assert d["files_match"] is False
        assert d["total_differences"] >= 1
        types = [x["type"] for x in d["differences"]]
        assert "CHANGED" in types

    def test_changed_value_has_correct_fields(self, client):
        a = write("a.csv", self.CSV_BASE)
        b = write("b.csv", "name,age,city\nAlice,99,London\nBob,25,Paris\n")
        diffs = post_compare(client, a, b).get_json()["data"]["differences"]
        changed = [x for x in diffs if x["type"] == "CHANGED"]
        assert any(x["field"] == "age" for x in changed)
        assert any(x["file_a_value"] == "30" for x in changed)
        assert any(x["file_b_value"] == "99" for x in changed)

    def test_added_row_detected(self, client):
        a = write("a.csv", self.CSV_BASE)
        b = write("b.csv", self.CSV_BASE + "Carol,28,Berlin\n")
        d = post_compare(client, a, b).get_json()["data"]
        assert d["files_match"] is False
        types = [x["type"] for x in d["differences"]]
        assert "ADDED" in types

    def test_removed_row_detected(self, client):
        a = write("a.csv", self.CSV_BASE)
        b = write("b.csv", "name,age,city\nAlice,30,London\n")
        d = post_compare(client, a, b).get_json()["data"]
        assert d["files_match"] is False
        types = [x["type"] for x in d["differences"]]
        assert "REMOVED" in types

    def test_similarity_less_than_100_when_different(self, client):
        a = write("a.csv", self.CSV_BASE)
        b = write("b.csv", "name,age,city\nZoe,99,Tokyo\nKai,10,Oslo\n")
        d = post_compare(client, a, b).get_json()["data"]
        assert d["similarity_percentage"] < 100.0

    def test_summary_mentions_filenames(self, client):
        a = write("alpha.csv", self.CSV_BASE)
        b = write("beta.csv", self.CSV_BASE)
        d = post_compare(client, a, b).get_json()["data"]
        assert "alpha.csv" in d["summary"]
        assert "beta.csv" in d["summary"]

    def test_summary_says_identical_when_match(self, client):
        a = write("a.csv", self.CSV_BASE)
        b = write("b.csv", self.CSV_BASE)
        assert "identical" in post_compare(client, a, b).get_json()["data"]["summary"].lower()

    def test_row_number_reported_in_diff(self, client):
        a = write("a.csv", self.CSV_BASE)
        b = write("b.csv", "name,age,city\nAlice,30,London\nBob,99,Paris\n")
        diffs = post_compare(client, a, b).get_json()["data"]["differences"]
        assert any("row" in x for x in diffs)


# ─────────────────────────────────────────────────────────────────────────────
# 4. JSON comparison
# ─────────────────────────────────────────────────────────────────────────────

class TestJsonComparison:

    OBJ_A = '{"name": "Alice", "age": 30, "city": "London"}'
    OBJ_B = '{"name": "Alice", "age": 31, "city": "London"}'

    def test_identical_files_match(self, client):
        a = write("a.json", self.OBJ_A)
        b = write("b.json", self.OBJ_A)
        assert post_compare(client, a, b).get_json()["data"]["files_match"] is True

    def test_identical_similarity_100(self, client):
        a = write("a.json", self.OBJ_A)
        b = write("b.json", self.OBJ_A)
        assert post_compare(client, a, b).get_json()["data"]["similarity_percentage"] == 100.0

    def test_changed_value_detected(self, client):
        a = write("a.json", self.OBJ_A)
        b = write("b.json", self.OBJ_B)
        d = post_compare(client, a, b).get_json()["data"]
        assert d["files_match"] is False
        assert any(x["type"] == "CHANGED" for x in d["differences"])

    def test_changed_key_is_correct(self, client):
        a = write("a.json", self.OBJ_A)
        b = write("b.json", self.OBJ_B)
        diffs = post_compare(client, a, b).get_json()["data"]["differences"]
        changed = [x for x in diffs if x["type"] == "CHANGED"]
        assert any("age" in x.get("key", "") for x in changed)

    def test_added_key_detected(self, client):
        a = write("a.json", '{"name": "Alice"}')
        b = write("b.json", '{"name": "Alice", "email": "a@b.com"}')
        diffs = post_compare(client, a, b).get_json()["data"]["differences"]
        assert any(x["type"] == "ADDED" for x in diffs)

    def test_removed_key_detected(self, client):
        a = write("a.json", '{"name": "Alice", "extra": "val"}')
        b = write("b.json", '{"name": "Alice"}')
        diffs = post_compare(client, a, b).get_json()["data"]["differences"]
        assert any(x["type"] == "REMOVED" for x in diffs)

    def test_array_json_comparison(self, client):
        a = write("a.json", '[{"id":1},{"id":2}]')
        b = write("b.json", '[{"id":1},{"id":3}]')
        d = post_compare(client, a, b).get_json()["data"]
        assert d["files_match"] is False

    def test_nested_json_detects_change(self, client):
        a = write("a.json", '{"user": {"name": "Alice", "age": 30}}')
        b = write("b.json", '{"user": {"name": "Alice", "age": 31}}')
        d = post_compare(client, a, b).get_json()["data"]
        assert d["files_match"] is False
        assert any("age" in x.get("key", "") for x in d["differences"])

    def test_completely_different_similarity_low(self, client):
        a = write("a.json", '{"x": 1, "y": 2}')
        b = write("b.json", '{"a": "hello", "b": "world"}')
        d = post_compare(client, a, b).get_json()["data"]
        assert d["similarity_percentage"] < 50.0


# ─────────────────────────────────────────────────────────────────────────────
# 5. XML comparison
# ─────────────────────────────────────────────────────────────────────────────

class TestXmlComparison:

    XML_A = "<root><name>Alice</name><age>30</age></root>"
    XML_B = "<root><name>Alice</name><age>31</age></root>"

    def test_identical_files_match(self, client):
        a = write("a.xml", self.XML_A)
        b = write("b.xml", self.XML_A)
        assert post_compare(client, a, b).get_json()["data"]["files_match"] is True

    def test_identical_similarity_100(self, client):
        a = write("a.xml", self.XML_A)
        b = write("b.xml", self.XML_A)
        assert post_compare(client, a, b).get_json()["data"]["similarity_percentage"] == 100.0

    def test_changed_element_detected(self, client):
        a = write("a.xml", self.XML_A)
        b = write("b.xml", self.XML_B)
        d = post_compare(client, a, b).get_json()["data"]
        assert d["files_match"] is False
        assert any(x["type"] == "CHANGED" for x in d["differences"])

    def test_changed_element_has_values(self, client):
        a = write("a.xml", self.XML_A)
        b = write("b.xml", self.XML_B)
        diffs = post_compare(client, a, b).get_json()["data"]["differences"]
        changed = [x for x in diffs if x["type"] == "CHANGED"]
        assert any(x.get("file_a_value") == "30" for x in changed)
        assert any(x.get("file_b_value") == "31" for x in changed)

    def test_added_element_detected(self, client):
        a = write("a.xml", "<root><name>Alice</name></root>")
        b = write("b.xml", "<root><name>Alice</name><email>a@b.com</email></root>")
        diffs = post_compare(client, a, b).get_json()["data"]["differences"]
        assert any(x["type"] == "ADDED" for x in diffs)

    def test_removed_element_detected(self, client):
        a = write("a.xml", "<root><name>Alice</name><extra>val</extra></root>")
        b = write("b.xml", "<root><name>Alice</name></root>")
        diffs = post_compare(client, a, b).get_json()["data"]["differences"]
        assert any(x["type"] == "REMOVED" for x in diffs)

    def test_attribute_change_detected(self, client):
        a = write("a.xml", '<root id="1"><name>Alice</name></root>')
        b = write("b.xml", '<root id="2"><name>Alice</name></root>')
        d = post_compare(client, a, b).get_json()["data"]
        assert d["files_match"] is False

    def test_zero_differences_when_identical(self, client):
        a = write("a.xml", self.XML_A)
        b = write("b.xml", self.XML_A)
        assert post_compare(client, a, b).get_json()["data"]["total_differences"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 6. TXT comparison
# ─────────────────────────────────────────────────────────────────────────────

class TestTxtComparison:

    LINES_A = "Line one\nLine two\nLine three\n"

    def test_identical_files_match(self, client):
        a = write("a.txt", self.LINES_A)
        b = write("b.txt", self.LINES_A)
        assert post_compare(client, a, b).get_json()["data"]["files_match"] is True

    def test_identical_similarity_100(self, client):
        a = write("a.txt", self.LINES_A)
        b = write("b.txt", self.LINES_A)
        assert post_compare(client, a, b).get_json()["data"]["similarity_percentage"] == 100.0

    def test_changed_line_detected(self, client):
        a = write("a.txt", self.LINES_A)
        b = write("b.txt", "Line one\nLine TWO CHANGED\nLine three\n")
        d = post_compare(client, a, b).get_json()["data"]
        assert d["files_match"] is False
        assert d["total_differences"] >= 1

    def test_diff_type_is_replace(self, client):
        a = write("a.txt", self.LINES_A)
        b = write("b.txt", "Line one\nReplaced line\nLine three\n")
        diffs = post_compare(client, a, b).get_json()["data"]["differences"]
        assert any(x["type"] == "REPLACE" for x in diffs)

    def test_added_line_detected(self, client):
        a = write("a.txt", self.LINES_A)
        b = write("b.txt", self.LINES_A + "New extra line\n")
        diffs = post_compare(client, a, b).get_json()["data"]["differences"]
        assert any(x["type"] == "INSERT" for x in diffs)

    def test_removed_line_detected(self, client):
        a = write("a.txt", self.LINES_A)
        b = write("b.txt", "Line one\nLine three\n")
        diffs = post_compare(client, a, b).get_json()["data"]["differences"]
        assert any(x["type"] in ("DELETE", "REPLACE") for x in diffs)

    def test_completely_different_has_low_similarity(self, client):
        a = write("a.txt", "aaa\nbbb\nccc\n")
        b = write("b.txt", "xxx\nyyy\nzzz\n")
        d = post_compare(client, a, b).get_json()["data"]
        assert d["similarity_percentage"] < 50.0

    def test_diff_includes_line_numbers(self, client):
        a = write("a.txt", self.LINES_A)
        b = write("b.txt", "Line one\nChanged\nLine three\n")
        diffs = post_compare(client, a, b).get_json()["data"]["differences"]
        assert all("file_a_lines" in x and "file_b_lines" in x for x in diffs)

    def test_diff_includes_values(self, client):
        a = write("a.txt", self.LINES_A)
        b = write("b.txt", "Line one\nChanged\nLine three\n")
        diffs = post_compare(client, a, b).get_json()["data"]["differences"]
        assert all("file_a_value" in x and "file_b_value" in x for x in diffs)


# ─────────────────────────────────────────────────────────────────────────────
# 7. XLSX comparison
# ─────────────────────────────────────────────────────────────────────────────

class TestXlsxComparison:

    ROWS_A = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
    ROWS_B = [{"name": "Alice", "age": 31}, {"name": "Bob", "age": 25}]

    def test_identical_files_match(self, client):
        a = make_xlsx(os.path.join(settings.UPLOAD_DIR, "a.xlsx"), self.ROWS_A)
        b = make_xlsx(os.path.join(settings.UPLOAD_DIR, "b.xlsx"), self.ROWS_A)
        assert post_compare(client, a, b).get_json()["data"]["files_match"] is True

    def test_identical_similarity_100(self, client):
        a = make_xlsx(os.path.join(settings.UPLOAD_DIR, "a.xlsx"), self.ROWS_A)
        b = make_xlsx(os.path.join(settings.UPLOAD_DIR, "b.xlsx"), self.ROWS_A)
        assert post_compare(client, a, b).get_json()["data"]["similarity_percentage"] == 100.0

    def test_changed_cell_detected(self, client):
        a = make_xlsx(os.path.join(settings.UPLOAD_DIR, "a.xlsx"), self.ROWS_A)
        b = make_xlsx(os.path.join(settings.UPLOAD_DIR, "b.xlsx"), self.ROWS_B)
        d = post_compare(client, a, b).get_json()["data"]
        assert d["files_match"] is False
        assert any(x["type"] == "CHANGED" for x in d["differences"])

    def test_changed_field_name_is_correct(self, client):
        a = make_xlsx(os.path.join(settings.UPLOAD_DIR, "a.xlsx"), self.ROWS_A)
        b = make_xlsx(os.path.join(settings.UPLOAD_DIR, "b.xlsx"), self.ROWS_B)
        diffs = post_compare(client, a, b).get_json()["data"]["differences"]
        changed = [x for x in diffs if x["type"] == "CHANGED"]
        assert any(x.get("field") == "age" for x in changed)

    def test_added_row_detected(self, client):
        extra = self.ROWS_A + [{"name": "Carol", "age": 28}]
        a = make_xlsx(os.path.join(settings.UPLOAD_DIR, "a.xlsx"), self.ROWS_A)
        b = make_xlsx(os.path.join(settings.UPLOAD_DIR, "b.xlsx"), extra)
        diffs = post_compare(client, a, b).get_json()["data"]["differences"]
        assert any(x["type"] == "ADDED" for x in diffs)

    def test_removed_row_detected(self, client):
        a = make_xlsx(os.path.join(settings.UPLOAD_DIR, "a.xlsx"), self.ROWS_A)
        b = make_xlsx(os.path.join(settings.UPLOAD_DIR, "b.xlsx"), [self.ROWS_A[0]])
        diffs = post_compare(client, a, b).get_json()["data"]["differences"]
        assert any(x["type"] == "REMOVED" for x in diffs)

    def test_zero_diffs_when_identical(self, client):
        a = make_xlsx(os.path.join(settings.UPLOAD_DIR, "a.xlsx"), self.ROWS_A)
        b = make_xlsx(os.path.join(settings.UPLOAD_DIR, "b.xlsx"), self.ROWS_A)
        assert post_compare(client, a, b).get_json()["data"]["total_differences"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 8. PDF comparison
# ─────────────────────────────────────────────────────────────────────────────

class TestPdfComparison:

    LINES_A = ["Name: Alice", "Age: 30", "City: London"]
    LINES_B = ["Name: Alice", "Age: 31", "City: London"]

    def test_identical_files_match(self, client):
        a = make_pdf(os.path.join(settings.UPLOAD_DIR, "a.pdf"), self.LINES_A)
        b = make_pdf(os.path.join(settings.UPLOAD_DIR, "b.pdf"), self.LINES_A)
        assert post_compare(client, a, b).get_json()["data"]["files_match"] is True

    def test_identical_similarity_100(self, client):
        a = make_pdf(os.path.join(settings.UPLOAD_DIR, "a.pdf"), self.LINES_A)
        b = make_pdf(os.path.join(settings.UPLOAD_DIR, "b.pdf"), self.LINES_A)
        assert post_compare(client, a, b).get_json()["data"]["similarity_percentage"] == 100.0

    def test_changed_line_detected(self, client):
        a = make_pdf(os.path.join(settings.UPLOAD_DIR, "a.pdf"), self.LINES_A)
        b = make_pdf(os.path.join(settings.UPLOAD_DIR, "b.pdf"), self.LINES_B)
        d = post_compare(client, a, b).get_json()["data"]
        assert d["files_match"] is False
        assert d["total_differences"] >= 1

    def test_added_content_detected(self, client):
        extra = self.LINES_A + ["Extra line"]
        a = make_pdf(os.path.join(settings.UPLOAD_DIR, "a.pdf"), self.LINES_A)
        b = make_pdf(os.path.join(settings.UPLOAD_DIR, "b.pdf"), extra)
        d = post_compare(client, a, b).get_json()["data"]
        assert d["files_match"] is False

    def test_similarity_less_than_100_when_different(self, client):
        a = make_pdf(os.path.join(settings.UPLOAD_DIR, "a.pdf"), self.LINES_A)
        b = make_pdf(os.path.join(settings.UPLOAD_DIR, "b.pdf"), self.LINES_B)
        d = post_compare(client, a, b).get_json()["data"]
        assert d["similarity_percentage"] < 100.0

    def test_response_has_format_pdf(self, client):
        a = make_pdf(os.path.join(settings.UPLOAD_DIR, "a.pdf"), self.LINES_A)
        b = make_pdf(os.path.join(settings.UPLOAD_DIR, "b.pdf"), self.LINES_A)
        assert post_compare(client, a, b).get_json()["data"]["format"] == "pdf"


# ─────────────────────────────────────────────────────────────────────────────
# 9. Cross-format tests (all format pairs return 200)
# ─────────────────────────────────────────────────────────────────────────────

class TestAllFormats:

    @pytest.mark.parametrize("ext,content_fn", [
        ("csv",  lambda: ("name,age\nAlice,30\n",    "name,age\nAlice,30\n")),
        ("json", lambda: ('{"a":1}',                  '{"a":1}')),
        ("xml",  lambda: ("<r><a>1</a></r>",           "<r><a>1</a></r>")),
        ("txt",  lambda: ("hello world\n",             "hello world\n")),
    ])
    def test_identical_pair_returns_200_and_match(self, client, ext, content_fn):
        c_a, c_b = content_fn()
        a = write(f"a.{ext}", c_a)
        b = write(f"b.{ext}", c_b)
        d = post_compare(client, a, b).get_json()
        assert d["data"]["files_match"] is True
        assert d["data"]["similarity_percentage"] == 100.0

    def test_xlsx_identical_returns_match(self, client):
        rows = [{"x": 1, "y": 2}]
        a = make_xlsx(os.path.join(settings.UPLOAD_DIR, "a.xlsx"), rows)
        b = make_xlsx(os.path.join(settings.UPLOAD_DIR, "b.xlsx"), rows)
        assert post_compare(client, a, b).get_json()["data"]["files_match"] is True

    def test_pdf_identical_returns_match(self, client):
        lines = ["Report title", "Line one"]
        a = make_pdf(os.path.join(settings.UPLOAD_DIR, "a.pdf"), lines)
        b = make_pdf(os.path.join(settings.UPLOAD_DIR, "b.pdf"), lines)
        assert post_compare(client, a, b).get_json()["data"]["files_match"] is True