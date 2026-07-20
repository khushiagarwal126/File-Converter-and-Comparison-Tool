"""
tests/unit/test_files.py
─────────────────────────
Unit tests for the file management endpoints:

    GET    /api/v1/files
    GET    /api/v1/files/<file_id>
    GET    /api/v1/files/<file_id>/download
    DELETE /api/v1/files/<file_id>

All tests use Flask's test client. Files are written to a per-test
tmp_path directory; nothing touches real uploads/temp or outputs/.
"""

import io
import json
import os
import uuid
import pytest
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

def _make_upload_file(content: bytes = b"name,age\nAlice,30\n",
                      filename: str = "report.csv") -> dict:
    """Write a UUID-prefixed file to UPLOAD_DIR and return its metadata."""
    file_id    = str(uuid.uuid4())
    saved_name = f"{file_id}_{filename}"
    path       = Path(settings.UPLOAD_DIR) / saved_name
    path.write_bytes(content)
    return {
        "file_id":   file_id,
        "filename":  saved_name,
        "path":      str(path),
        "extension": filename.rsplit(".", 1)[-1].lower(),
    }


def _make_output_file(content: bytes = b'[{"name":"Alice"}]',
                      filename: str = "report.json") -> dict:
    """Write a UUID-prefixed file to OUTPUT_DIR and return its metadata."""
    conv_id    = str(uuid.uuid4())
    saved_name = f"{conv_id}_{filename}"
    path       = Path(settings.OUTPUT_DIR) / saved_name
    path.write_bytes(content)
    return {
        "file_id":  conv_id,
        "filename": saved_name,
        "path":     str(path),
    }


def _url(suffix: str = "") -> str:
    return f"{settings.API_PREFIX}/files{suffix}"


# ─────────────────────────────────────────────────────────────────────────────
# 1. GET /api/v1/files  — list all files
# ─────────────────────────────────────────────────────────────────────────────

class TestListFiles:

    def test_returns_200_when_empty(self, client):
        assert client.get(_url()).status_code == 200

    def test_success_envelope_when_empty(self, client):
        d = client.get(_url()).get_json()
        assert d["success"] is True
        assert "data" in d and "message" in d

    def test_empty_dirs_return_zero_count(self, client):
        assert client.get(_url()).get_json()["data"]["count"] == 0

    def test_empty_dirs_return_empty_list(self, client):
        assert client.get(_url()).get_json()["data"]["files"] == []

    def test_one_upload_shows_in_list(self, client):
        _make_upload_file()
        d = client.get(_url()).get_json()["data"]
        assert d["count"] == 1
        assert len(d["files"]) == 1

    def test_two_uploads_both_appear(self, client):
        _make_upload_file()
        _make_upload_file(filename="data.json", content=b"{}")
        d = client.get(_url()).get_json()["data"]
        assert d["count"] == 2

    def test_upload_and_output_both_appear(self, client):
        _make_upload_file()
        _make_output_file()
        d = client.get(_url()).get_json()["data"]
        assert d["count"] == 2

    def test_file_type_uploaded_set_correctly(self, client):
        _make_upload_file()
        files = client.get(_url()).get_json()["data"]["files"]
        assert files[0]["file_type"] == "uploaded"

    def test_file_type_converted_set_correctly(self, client):
        _make_output_file()
        files = client.get(_url()).get_json()["data"]["files"]
        assert files[0]["file_type"] == "converted"

    def test_file_entry_has_all_required_fields(self, client):
        _make_upload_file()
        entry = client.get(_url()).get_json()["data"]["files"][0]
        for field in ("file_id", "filename", "original_name",
                      "extension", "file_type", "size_bytes",
                      "path", "created_at"):
            assert field in entry, f"Missing field: {field}"

    def test_original_name_strips_uuid_prefix(self, client):
        _make_upload_file(filename="report.csv")
        entry = client.get(_url()).get_json()["data"]["files"][0]
        assert entry["original_name"] == "report.csv"
        assert entry["original_name"] != entry["filename"]

    def test_size_bytes_is_accurate(self, client):
        content = b"name,age\nAlice,30\n"
        _make_upload_file(content=content)
        entry = client.get(_url()).get_json()["data"]["files"][0]
        assert entry["size_bytes"] == len(content)

    def test_extension_extracted_correctly(self, client):
        _make_upload_file(filename="data.xlsx", content=b"PK")
        entry = client.get(_url()).get_json()["data"]["files"][0]
        assert entry["extension"] == "xlsx"

    def test_gitkeep_files_excluded(self, client):
        # .gitkeep files should not appear in listings
        gitkeep = Path(settings.UPLOAD_DIR) / ".gitkeep"
        gitkeep.write_bytes(b"")
        d = client.get(_url()).get_json()["data"]
        assert d["count"] == 0

    def test_message_contains_count(self, client):
        _make_upload_file()
        msg = client.get(_url()).get_json()["message"]
        assert "1" in msg

    def test_post_method_not_allowed(self, client):
        assert client.post(_url()).status_code == 405


# ─────────────────────────────────────────────────────────────────────────────
# 2. GET /api/v1/files/<file_id>  — get metadata for one file
# ─────────────────────────────────────────────────────────────────────────────

class TestGetFileMetadata:

    def test_returns_200_for_valid_upload(self, client):
        info = _make_upload_file()
        assert client.get(_url(f"/{info['file_id']}")).status_code == 200

    def test_success_true_for_valid_file(self, client):
        info = _make_upload_file()
        d = client.get(_url(f"/{info['file_id']}")).get_json()
        assert d["success"] is True

    def test_returns_correct_file_id(self, client):
        info = _make_upload_file()
        d = client.get(_url(f"/{info['file_id']}")).get_json()
        assert d["data"]["file_id"] == info["file_id"]

    def test_returns_correct_filename(self, client):
        info = _make_upload_file()
        d = client.get(_url(f"/{info['file_id']}")).get_json()
        assert d["data"]["filename"] == info["filename"]

    def test_returns_correct_extension(self, client):
        info = _make_upload_file(filename="data.json", content=b"{}")
        d = client.get(_url(f"/{info['file_id']}")).get_json()
        assert d["data"]["extension"] == "json"

    def test_returns_correct_file_type_uploaded(self, client):
        info = _make_upload_file()
        d = client.get(_url(f"/{info['file_id']}")).get_json()
        assert d["data"]["file_type"] == "uploaded"

    def test_returns_correct_file_type_converted(self, client):
        info = _make_output_file()
        d = client.get(_url(f"/{info['file_id']}")).get_json()
        assert d["data"]["file_type"] == "converted"

    def test_returns_correct_size_bytes(self, client):
        content = b"x" * 128
        info = _make_upload_file(content=content)
        d = client.get(_url(f"/{info['file_id']}")).get_json()
        assert d["data"]["size_bytes"] == 128

    def test_has_created_at_field(self, client):
        info = _make_upload_file()
        d = client.get(_url(f"/{info['file_id']}")).get_json()
        assert d["data"]["created_at"]     # non-empty string

    def test_nonexistent_id_returns_404(self, client):
        fake_id = str(uuid.uuid4())
        r = client.get(_url(f"/{fake_id}"))
        assert r.status_code == 404

    def test_nonexistent_id_error_code(self, client):
        fake_id = str(uuid.uuid4())
        assert client.get(_url(f"/{fake_id}")).get_json()["error"] == "FILE_NOT_FOUND"

    def test_invalid_id_returns_400(self, client):
        r = client.get(_url("/not-a-uuid"))
        assert r.status_code == 400

    def test_invalid_id_error_code(self, client):
        assert client.get(_url("/not-a-uuid")).get_json()["error"] == "INVALID_FILE_ID"

    def test_path_traversal_attempt_rejected(self, client):
        r = client.get(_url("/../../etc/passwd"))
        # Flask will 404 on the URL or 400 from service — both are safe
        assert r.status_code in (400, 404)

    def test_different_ids_return_different_files(self, client):
        a = _make_upload_file(filename="a.csv")
        b = _make_upload_file(filename="b.csv", content=b"x,y\n1,2\n")
        da = client.get(_url(f"/{a['file_id']}")).get_json()["data"]
        db = client.get(_url(f"/{b['file_id']}")).get_json()["data"]
        assert da["file_id"] != db["file_id"]
        assert da["filename"] != db["filename"]


# ─────────────────────────────────────────────────────────────────────────────
# 3. GET /api/v1/files/<file_id>/download  — download a file
# ─────────────────────────────────────────────────────────────────────────────

class TestDownloadFile:

    def test_returns_200_for_valid_file(self, client):
        info = _make_upload_file()
        r = client.get(_url(f"/{info['file_id']}/download"))
        assert r.status_code == 200

    def test_response_is_not_json(self, client):
        info = _make_upload_file(content=b"name,age\nAlice,30\n")
        r = client.get(_url(f"/{info['file_id']}/download"))
        assert r.content_type != "application/json"

    def test_content_matches_file_bytes(self, client):
        content = b"name,age\nAlice,30\nBob,25\n"
        info = _make_upload_file(content=content)
        r = client.get(_url(f"/{info['file_id']}/download"))
        assert r.data == content

    def test_content_disposition_is_attachment(self, client):
        info = _make_upload_file()
        r = client.get(_url(f"/{info['file_id']}/download"))
        assert "attachment" in r.headers.get("Content-Disposition", "")

    def test_download_filename_is_original_name(self, client):
        info = _make_upload_file(filename="my_report.csv")
        r = client.get(_url(f"/{info['file_id']}/download"))
        cd = r.headers.get("Content-Disposition", "")
        assert "my_report.csv" in cd

    def test_can_download_json_file(self, client):
        content = b'[{"name":"Alice"}]'
        info = _make_upload_file(content=content, filename="data.json")
        r = client.get(_url(f"/{info['file_id']}/download"))
        assert r.status_code == 200
        assert r.data == content

    def test_can_download_txt_file(self, client):
        content = b"Hello, World!\nLine two.\n"
        info = _make_upload_file(content=content, filename="notes.txt")
        assert client.get(_url(f"/{info['file_id']}/download")).data == content

    def test_can_download_converted_output(self, client):
        content = b'{"result": true}'
        info = _make_output_file(content=content, filename="output.json")
        r = client.get(_url(f"/{info['file_id']}/download"))
        assert r.status_code == 200
        assert r.data == content

    def test_nonexistent_id_returns_404(self, client):
        r = client.get(_url(f"/{uuid.uuid4()}/download"))
        assert r.status_code == 404
        assert r.get_json()["error"] == "FILE_NOT_FOUND"

    def test_invalid_id_returns_400(self, client):
        r = client.get(_url("/bad-id/download"))
        assert r.status_code == 400
        assert r.get_json()["error"] == "INVALID_FILE_ID"

    def test_large_file_downloads_correctly(self, client):
        content = b"x" * (1024 * 50)   # 50 KB
        info = _make_upload_file(content=content, filename="big.txt")
        r = client.get(_url(f"/{info['file_id']}/download"))
        assert r.status_code == 200
        assert len(r.data) == len(content)

    def test_post_on_download_is_405(self, client):
        info = _make_upload_file()
        r = client.post(_url(f"/{info['file_id']}/download"))
        assert r.status_code == 405


# ─────────────────────────────────────────────────────────────────────────────
# 4. DELETE /api/v1/files/<file_id>  — delete a file
# ─────────────────────────────────────────────────────────────────────────────

class TestDeleteFile:

    def test_returns_200_on_success(self, client):
        info = _make_upload_file()
        r = client.delete(_url(f"/{info['file_id']}"))
        assert r.status_code == 200

    def test_success_true_on_delete(self, client):
        info = _make_upload_file()
        d = client.delete(_url(f"/{info['file_id']}")).get_json()
        assert d["success"] is True

    def test_message_says_deleted(self, client):
        info = _make_upload_file()
        msg = client.delete(_url(f"/{info['file_id']}")).get_json()["message"]
        assert "deleted" in msg.lower()

    def test_data_has_file_id(self, client):
        info = _make_upload_file()
        d = client.delete(_url(f"/{info['file_id']}")).get_json()["data"]
        assert d["file_id"] == info["file_id"]

    def test_data_deleted_is_true(self, client):
        info = _make_upload_file()
        d = client.delete(_url(f"/{info['file_id']}")).get_json()["data"]
        assert d["deleted"] is True

    def test_data_has_filename(self, client):
        info = _make_upload_file()
        d = client.delete(_url(f"/{info['file_id']}")).get_json()["data"]
        assert d["filename"] == info["filename"]

    def test_data_has_file_type(self, client):
        info = _make_upload_file()
        d = client.delete(_url(f"/{info['file_id']}")).get_json()["data"]
        assert d["file_type"] in ("uploaded", "converted")

    def test_file_is_actually_removed_from_disk(self, client):
        info = _make_upload_file()
        assert os.path.exists(info["path"])
        client.delete(_url(f"/{info['file_id']}"))
        assert not os.path.exists(info["path"])

    def test_file_absent_from_list_after_delete(self, client):
        info = _make_upload_file()
        client.delete(_url(f"/{info['file_id']}"))
        files = client.get(_url()).get_json()["data"]["files"]
        ids = [f["file_id"] for f in files]
        assert info["file_id"] not in ids

    def test_second_delete_returns_404(self, client):
        info = _make_upload_file()
        client.delete(_url(f"/{info['file_id']}"))
        r = client.delete(_url(f"/{info['file_id']}"))
        assert r.status_code == 404

    def test_nonexistent_id_returns_404(self, client):
        r = client.delete(_url(f"/{uuid.uuid4()}"))
        assert r.status_code == 404
        assert r.get_json()["error"] == "FILE_NOT_FOUND"

    def test_invalid_id_returns_400(self, client):
        r = client.delete(_url("/not-a-uuid"))
        assert r.status_code == 400
        assert r.get_json()["error"] == "INVALID_FILE_ID"

    def test_delete_converted_file(self, client):
        info = _make_output_file()
        r = client.delete(_url(f"/{info['file_id']}"))
        assert r.status_code == 200
        assert not os.path.exists(info["path"])

    def test_deleting_one_file_leaves_other_intact(self, client):
        a = _make_upload_file(filename="a.csv")
        b = _make_upload_file(filename="b.csv", content=b"x,y\n")
        client.delete(_url(f"/{a['file_id']}"))
        assert os.path.exists(b["path"])

    def test_get_method_on_delete_url_returns_file_metadata(self, client):
        info = _make_upload_file()
        r = client.get(_url(f"/{info['file_id']}"))
        assert r.status_code == 200           # GET still works


# ─────────────────────────────────────────────────────────────────────────────
# 5. Path traversal & security
# ─────────────────────────────────────────────────────────────────────────────

class TestSecurity:

    def test_traversal_in_file_id_segment_rejected(self, client):
        # UUID validation fires before any filesystem access
        r = client.get(_url("/../../etc/passwd"))
        assert r.status_code in (400, 404)

    def test_non_uuid_id_always_rejected(self, client):
        for bad_id in ("../secret", "'; DROP TABLE--", "<script>", "12345"):
            r = client.get(_url(f"/{bad_id}"))
            assert r.status_code in (400, 404), f"Expected 400/404 for: {bad_id}"

    def test_download_traversal_rejected(self, client):
        r = client.get(_url("/../../etc/passwd/download"))
        assert r.status_code in (400, 404)

    def test_delete_traversal_rejected(self, client):
        r = client.delete(_url("/../../etc/passwd"))
        assert r.status_code in (400, 404)

    def test_uuid_shaped_but_nonexistent_returns_404_not_500(self, client):
        r = client.get(_url(f"/{uuid.uuid4()}"))
        assert r.status_code == 404

    def test_error_responses_always_json(self, client):
        for bad_id in ("bad", str(uuid.uuid4())):
            r = client.get(_url(f"/{bad_id}"))
            # Must be parseable JSON regardless of error type
            assert r.get_json() is not None

    def test_error_envelope_has_success_false(self, client):
        r = client.get(_url(f"/{uuid.uuid4()}"))
        assert r.get_json()["success"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. Integration — upload then manage via files API
# ─────────────────────────────────────────────────────────────────────────────

class TestIntegrationWithUploadEndpoint:

    def _upload(self, client, content=b"name,age\nAlice,30\n", name="data.csv"):
        return client.post(
            f"{settings.API_PREFIX}/upload",
            data={"file": (io.BytesIO(content), name, "text/csv")},
            content_type="multipart/form-data",
        ).get_json()["data"]

    def test_uploaded_file_appears_in_list(self, client):
        up = self._upload(client)
        files = client.get(_url()).get_json()["data"]["files"]
        assert any(f["file_id"] == up["file_id"] for f in files)

    def test_uploaded_file_retrievable_by_id(self, client):
        up = self._upload(client)
        d = client.get(_url(f"/{up['file_id']}")).get_json()["data"]
        assert d["file_id"] == up["file_id"]

    def test_uploaded_file_downloadable(self, client):
        content = b"name,age\nAlice,30\n"
        up = self._upload(client, content=content)
        r = client.get(_url(f"/{up['file_id']}/download"))
        assert r.status_code == 200
        assert r.data == content

    def test_uploaded_file_deletable(self, client):
        up = self._upload(client)
        r = client.delete(_url(f"/{up['file_id']}"))
        assert r.status_code == 200
        assert r.get_json()["data"]["deleted"] is True

    def test_after_upload_and_delete_list_is_empty(self, client):
        up = self._upload(client)
        client.delete(_url(f"/{up['file_id']}"))
        assert client.get(_url()).get_json()["data"]["count"] == 0

    def test_converted_file_appears_in_list(self, client):
        up = self._upload(client)
        conv = client.post(
            f"{settings.API_PREFIX}/convert",
            data=json.dumps({
                "file_path":     up["upload_path"],
                "target_format": "json",
            }),
            content_type="application/json",
        ).get_json()["data"]
        files = client.get(_url()).get_json()["data"]["files"]
        assert any(f["file_id"] == conv["conversion_id"] for f in files)