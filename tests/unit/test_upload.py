"""
tests/unit/test_upload.py
──────────────────────────
Unit tests for POST /api/v1/upload.

Every test uses Flask's test client — no real HTTP server, no real disk I/O
for invalid cases. Valid uploads do write to a tmp directory (cleaned up
in the fixture) so the service path is exercised end-to-end.
"""

import io
import os
import shutil
import tempfile
import pytest
from app.main import create_app
from app.core.config import settings


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path):
    """
    Create a fresh app instance for each test.
    Override UPLOAD_DIR to an isolated temporary directory so tests
    never touch the real uploads/temp folder and clean up after themselves.
    """
    flask_app = create_app()
    flask_app.config["TESTING"] = True

    # Point uploads at a per-test temp directory
    settings.UPLOAD_DIR = str(tmp_path / "uploads")

    yield flask_app

    # Teardown: restore original upload dir (good practice in suites)
    settings.UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads/temp")


@pytest.fixture
def client(app):
    return app.test_client()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_file(content: bytes = b"col1,col2\nval1,val2", filename: str = "test.csv"):
    """Return a (data, filename, mimetype) tuple for test_client data=."""
    return (io.BytesIO(content), filename, "application/octet-stream")


def post_file(client, filename="test.csv", content=b"col1,col2\nval1,val2",
              field="file"):
    """Helper: POST a file to the upload endpoint."""
    return client.post(
        f"{settings.API_PREFIX}/upload",
        data={field: _make_file(content, filename)},
        content_type="multipart/form-data",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Happy path — valid uploads
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadSuccess:

    def test_returns_201(self, client):
        r = post_file(client)
        assert r.status_code == 201

    def test_success_envelope(self, client):
        d = post_file(client).get_json()
        assert d["success"] is True
        assert "data" in d
        assert "message" in d

    def test_message_text(self, client):
        d = post_file(client).get_json()
        assert d["message"] == "File uploaded successfully"

    def test_data_has_file_id(self, client):
        d = post_file(client).get_json()["data"]
        assert "file_id" in d
        assert len(d["file_id"]) == 36          # UUID4 length

    def test_data_has_original_name(self, client):
        d = post_file(client, filename="report.csv").get_json()["data"]
        assert d["original_name"] == "report.csv"

    def test_data_has_extension(self, client):
        d = post_file(client, filename="data.json",
                      content=b'{"a":1}').get_json()["data"]
        assert d["extension"] == "json"

    def test_data_has_size_bytes(self, client):
        content = b"hello,world\n"
        d = post_file(client, content=content).get_json()["data"]
        assert d["size_bytes"] == len(content)

    def test_data_has_saved_name(self, client):
        d = post_file(client).get_json()["data"]
        assert "saved_name" in d
        assert d["saved_name"].endswith(".csv")

    def test_data_has_upload_path(self, client):
        d = post_file(client).get_json()["data"]
        assert "upload_path" in d

    def test_file_actually_saved_to_disk(self, client):
        d = post_file(client).get_json()["data"]
        assert os.path.isfile(d["upload_path"])

    def test_saved_file_content_matches(self, client):
        content = b"id,name\n1,Alice\n2,Bob\n"
        d = post_file(client, content=content).get_json()["data"]
        with open(d["upload_path"], "rb") as f:
            assert f.read() == content

    def test_saved_name_prefixed_with_file_id(self, client):
        d = post_file(client).get_json()["data"]
        assert d["saved_name"].startswith(d["file_id"])

    # Test each allowed extension
    @pytest.mark.parametrize("filename,content", [
        ("data.csv",  b"a,b\n1,2"),
        ("data.xlsx", b"PK\x03\x04fake-xlsx-bytes"),
        ("data.json", b'{"key": "value"}'),
        ("data.xml",  b"<root><item/></root>"),
        ("data.txt",  b"plain text content"),
        ("data.pdf",  b"%PDF-1.4 fake"),
    ])
    def test_all_allowed_extensions_accepted(self, client, filename, content):
        r = post_file(client, filename=filename, content=content)
        assert r.status_code == 201, \
            f"Expected 201 for {filename}, got {r.status_code}: {r.get_json()}"

    def test_two_uploads_get_unique_file_ids(self, client):
        id1 = post_file(client).get_json()["data"]["file_id"]
        id2 = post_file(client).get_json()["data"]["file_id"]
        assert id1 != id2

    def test_uppercase_extension_normalised(self, client):
        d = post_file(client, filename="DATA.CSV").get_json()["data"]
        assert d["extension"] == "csv"


# ─────────────────────────────────────────────────────────────────────────────
# Validation errors — missing / bad input
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadValidationErrors:

    def test_no_file_field_returns_400(self, client):
        r = client.post(
            f"{settings.API_PREFIX}/upload",
            data={},
            content_type="multipart/form-data",
        )
        assert r.status_code == 400

    def test_no_file_field_error_code(self, client):
        r = client.post(
            f"{settings.API_PREFIX}/upload",
            data={},
            content_type="multipart/form-data",
        )
        assert r.get_json()["error"] == "NO_FILE"

    def test_wrong_form_field_name_returns_400(self, client):
        r = post_file(client, field="upload")   # correct key is "file"
        assert r.status_code == 400

    def test_unsupported_extension_returns_400(self, client):
        r = post_file(client, filename="virus.exe", content=b"MZ\x90\x00")
        assert r.status_code == 400

    def test_unsupported_extension_error_code(self, client):
        d = post_file(client, filename="virus.exe",
                      content=b"MZ\x90\x00").get_json()
        assert d["error"] == "INVALID_EXTENSION"

    def test_unsupported_extension_message_mentions_accepted(self, client):
        d = post_file(client, filename="photo.png",
                      content=b"\x89PNG").get_json()
        assert "csv" in d["message"].lower() or "accepted" in d["message"].lower()

    def test_no_extension_returns_400(self, client):
        r = post_file(client, filename="README", content=b"some text")
        assert r.status_code == 400

    def test_no_extension_error_code(self, client):
        d = post_file(client, filename="README", content=b"some text").get_json()
        assert d["error"] in ("NO_EXTENSION", "INVALID_EXTENSION")

    def test_empty_file_returns_400(self, client):
        r = post_file(client, content=b"")
        assert r.status_code == 400

    def test_empty_file_error_code(self, client):
        d = post_file(client, content=b"").get_json()
        assert d["error"] == "EMPTY_FILE"

    def test_error_envelope_has_success_false(self, client):
        d = post_file(client, filename="bad.exe", content=b"x").get_json()
        assert d["success"] is False

    def test_error_envelope_has_message(self, client):
        d = post_file(client, filename="bad.exe", content=b"x").get_json()
        assert "message" in d and d["message"]


# ─────────────────────────────────────────────────────────────────────────────
# Size limit
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadSizeLimit:

    def test_oversized_file_returns_413(self, client):
        # Temporarily lower the limit so the test doesn't need 50 MB of data
        original = settings.MAX_UPLOAD_SIZE_MB
        settings.MAX_UPLOAD_SIZE_MB = 1             # 1 MB
        try:
            big = b"x" * (1 * 1024 * 1024 + 1)     # 1 MB + 1 byte
            r = post_file(client, content=big)
            assert r.status_code == 413
        finally:
            settings.MAX_UPLOAD_SIZE_MB = original

    def test_oversized_file_error_code(self, client):
        original = settings.MAX_UPLOAD_SIZE_MB
        settings.MAX_UPLOAD_SIZE_MB = 1
        try:
            big = b"x" * (1 * 1024 * 1024 + 1)
            d = post_file(client, content=big).get_json()
            assert d["error"] == "FILE_TOO_LARGE"
        finally:
            settings.MAX_UPLOAD_SIZE_MB = original

    def test_file_at_exact_limit_is_accepted(self, client):
        original = settings.MAX_UPLOAD_SIZE_MB
        settings.MAX_UPLOAD_SIZE_MB = 1
        try:
            exact = b"x" * (1 * 1024 * 1024)       # exactly 1 MB
            r = post_file(client, content=exact)
            assert r.status_code == 201
        finally:
            settings.MAX_UPLOAD_SIZE_MB = original


# ─────────────────────────────────────────────────────────────────────────────
# HTTP method guard
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadMethodGuard:

    def test_get_on_upload_returns_405(self, client):
        r = client.get(f"{settings.API_PREFIX}/upload")
        assert r.status_code == 405

    def test_405_has_json_envelope(self, client):
        r = client.get(f"{settings.API_PREFIX}/upload")
        d = r.get_json()
        assert d["success"] is False
        assert d["error"] == "METHOD_NOT_ALLOWED"