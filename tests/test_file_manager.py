"""Path containment for the shared-folder API. A regression here is arbitrary FS access."""
import os

import pytest
from fastapi import HTTPException

import file_manager as fm


@pytest.fixture(autouse=True)
def shared(tmp_path, monkeypatch):
    """Point SHARED_DIR at a fresh directory for each test."""
    root = tmp_path / "shared"
    root.mkdir()
    monkeypatch.setattr(fm, "SHARED_DIR", root.resolve())
    return root.resolve()


# ----------------- safe_path -----------------

def test_empty_path_is_the_root(shared):
    assert fm.safe_path("") == shared


def test_nested_path_stays_inside(shared):
    assert fm.safe_path("docs/reports") == shared / "docs" / "reports"


@pytest.mark.parametrize("attack", [
    "../etc/passwd",
    "../../../../etc/passwd",
    "docs/../../etc/passwd",
    "..",
    "docs/../..",
])
def test_traversal_is_rejected(attack):
    with pytest.raises(HTTPException) as exc:
        fm.safe_path(attack)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("path", ["/etc/passwd", "\\etc\\passwd", "//etc/passwd"])
def test_absolute_paths_are_treated_as_relative(shared, path):
    """A leading separator is stripped rather than escaping to the real filesystem root."""
    assert fm.safe_path(path).is_relative_to(shared)


def test_prefix_lookalike_sibling_is_rejected(shared, monkeypatch):
    """Containment is component-wise: /shared-evil must not pass as /shared."""
    sibling = shared.parent / (shared.name + "-evil")
    sibling.mkdir()
    with pytest.raises(HTTPException):
        fm.safe_path(f"../{sibling.name}/secret.txt")


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_symlink_out_of_the_shared_dir_is_rejected(shared, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (shared / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(HTTPException):
        fm.safe_path("escape/loot.txt")


# ----------------- safe_filename -----------------

@pytest.mark.parametrize("given, expected", [
    ("report.pdf", "report.pdf"),
    ("../../evil.sh", "evil.sh"),
    ("C:\\Users\\kodi\\notes.txt", "notes.txt"),
    ("/tmp/payload.bin", "payload.bin"),
    ("  spaced.txt  ", "spaced.txt"),
])
def test_safe_filename_keeps_only_the_final_component(given, expected):
    assert fm.safe_filename(given) == expected


@pytest.mark.parametrize("bad", ["", "   ", ".", "..", "../", "with\x00null", None])
def test_safe_filename_rejects_dangerous_names(bad):
    with pytest.raises(HTTPException):
        fm.safe_filename(bad)


# ----------------- format_size -----------------

@pytest.mark.parametrize("size, expected", [
    (0, "0 B"),
    (1023, "1023 B"),
    (1024, "1.0 KB"),
    (1024 * 1024, "1.0 MB"),
    (1024 * 1024 * 1024, "1.00 GB"),
])
def test_format_size(size, expected):
    assert fm.format_size(size) == expected


# ----------------- listing and deletion -----------------

def test_list_files_sorts_folders_first(shared):
    (shared / "zebra").mkdir()
    (shared / "apple.txt").write_text("x")
    (shared / "beta.txt").write_text("y")
    assert [i["name"] for i in fm.list_files("")] == ["zebra", "apple.txt", "beta.txt"]


def test_list_files_on_a_file_is_rejected(shared):
    (shared / "a.txt").write_text("x")
    with pytest.raises(HTTPException) as exc:
        fm.list_files("a.txt")
    assert exc.value.status_code == 400


def test_delete_refuses_the_root(shared):
    with pytest.raises(HTTPException) as exc:
        fm.delete_path("")
    assert exc.value.status_code == 400


def test_delete_removes_a_file(shared):
    (shared / "a.txt").write_text("x")
    fm.delete_path("a.txt")
    assert not (shared / "a.txt").exists()


def test_delete_missing_path_is_404(shared):
    with pytest.raises(HTTPException) as exc:
        fm.delete_path("nope.txt")
    assert exc.value.status_code == 404


def test_create_folder_strips_traversal_from_the_name(shared):
    """The name is reduced to its final component, so ../ cannot walk up."""
    fm.create_folder("", "../escaped")
    assert (shared / "escaped").is_dir()
    assert not (shared.parent / "escaped").exists()


def test_create_folder_rejects_an_unusable_name(shared):
    with pytest.raises(HTTPException):
        fm.create_folder("", "..")


def test_create_folder_rejects_a_duplicate(shared):
    fm.create_folder("", "docs")
    with pytest.raises(HTTPException) as exc:
        fm.create_folder("", "docs")
    assert exc.value.status_code == 409
