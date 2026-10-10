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


# ----------------- Deleting a symlink -----------------

def make_link(link, target, is_dir):
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except OSError:
        pytest.skip("this system can't create symlinks")


def test_deleting_a_link_to_a_folder_keeps_the_folder(shared):
    real = shared / "projects"
    real.mkdir()
    (real / "keep.txt").write_text("x")
    make_link(shared / "shortcut", real, True)
    fm.delete_path("shortcut")
    assert not (shared / "shortcut").exists() and not (shared / "shortcut").is_symlink()
    assert (real / "keep.txt").read_text() == "x"


def test_deleting_a_link_to_a_file_keeps_the_file(shared):
    real = shared / "report.txt"
    real.write_text("x")
    make_link(shared / "docs-link.txt", real, False)
    fm.delete_path("docs-link.txt")
    assert real.read_text() == "x"
    assert not (shared / "docs-link.txt").is_symlink()


def test_a_link_pointing_outside_can_still_be_deleted(shared, tmp_path):
    """Removing the link touches nothing outside the shared folder."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("x")
    make_link(shared / "escape", outside, True)
    fm.delete_path("escape")
    assert (outside / "secret").read_text() == "x"
    assert not (shared / "escape").is_symlink()


def test_a_link_inside_a_subfolder_is_deleted_itself(shared):
    sub = shared / "sub"
    sub.mkdir()
    real = shared / "data"
    real.mkdir()
    make_link(sub / "data-link", real, True)
    fm.delete_path("sub/data-link")
    assert real.is_dir() and not (sub / "data-link").is_symlink()


def test_parent_traversal_is_still_rejected_on_delete():
    with pytest.raises(HTTPException) as exc:
        fm.delete_path("../etc")
    assert exc.value.status_code == 403


# ----------------- Uploads -----------------

def upload(name, data=b"hello", path="", overwrite=False):
    import asyncio
    import io
    from fastapi import UploadFile
    return asyncio.run(fm.save_uploaded_file(UploadFile(io.BytesIO(data), filename=name), path, overwrite))


@pytest.mark.parametrize("name", [
    pytest.param("a" * 250 + ".txt", id="254-chars",  # a valid name on Linux and NTFS
                 marks=pytest.mark.skipif(os.name == "nt", reason="exceeds Windows' 260-character path limit here")),
    pytest.param("é" * 110 + ".txt", id="224-bytes"),  # over the old limit in UTF-8 bytes
])
def test_a_long_but_valid_name_can_be_uploaded(shared, name):
    result = upload(name)
    assert result["filename"] == name
    assert (shared / name).read_bytes() == b"hello"


def test_no_temp_file_is_left_behind(shared):
    upload("report.txt")
    assert sorted(p.name for p in shared.iterdir()) == ["report.txt"]


def test_an_existing_file_is_only_replaced_with_overwrite(shared):
    upload("report.txt", b"one")
    with pytest.raises(HTTPException) as exc:
        upload("report.txt", b"two")
    assert exc.value.status_code == 409
    upload("report.txt", b"two", overwrite=True)
    assert (shared / "report.txt").read_bytes() == b"two"


# ----------------- Upload temp files -----------------

def temp_name():
    import uuid
    return f".upload-{uuid.uuid4().hex}.part"


def test_upload_temp_files_are_not_listed(shared):
    (shared / temp_name()).write_bytes(b"partial")
    (shared / "report.txt").write_text("x")
    assert [f["name"] for f in fm.list_files("")] == ["report.txt"]


def test_a_stale_upload_temp_file_is_removed(shared):
    stale = shared / temp_name()
    stale.write_bytes(b"cut off")
    old = stale.stat().st_mtime - fm.STALE_UPLOAD_SECONDS - 60
    os.utime(stale, (old, old))
    fm.list_files("")
    assert not stale.exists()


def test_a_running_upload_temp_file_is_kept(shared):
    running = shared / temp_name()
    running.write_bytes(b"in progress")
    fm.list_files("")
    assert running.exists()


@pytest.mark.parametrize("name", [".upload-notes.part", "upload-" + "0" * 32 + ".part", ".hidden"])
def test_files_that_only_look_similar_are_listed(shared, name):
    (shared / name).write_text("x")
    assert [f["name"] for f in fm.list_files("")] == [name]


def test_a_finished_upload_leaves_no_temp_file(shared):
    upload("done.txt")
    assert [p.name for p in shared.iterdir()] == ["done.txt"]
