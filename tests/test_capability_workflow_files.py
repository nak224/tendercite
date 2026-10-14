"""Guarded exports, metadata-only console output and mocked Windows primitives."""

import ctypes
import json
import ntpath
import os
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest
from matching_helpers import snapshot
from persistent_mapping_helpers import seeded_repository
from test_capability_files import NativeCall
from test_capability_workflow import assessment, selection

from tendercite.cli.capability_workflow import main, private_workflow_path
from tendercite.services import capabilities
from tendercite.services.capabilities import CapabilityError, write_private_json


@pytest.fixture
def repo(tmp_path):
    return seeded_repository(tmp_path / "workflow.db")


def invoke(repo, command, *args):
    return main(["--database", str(repo.database_path), command, *map(str, args)])


def test_cli_overview_and_prepare_are_private_and_read_only(repo, tmp_path, capsys):
    before = repo.database_path.read_bytes()
    output = tmp_path / "overview.json"
    assert (
        invoke(
            repo,
            "overview",
            "--analysis-id",
            "analysis-1",
            "--snapshot-hash",
            snapshot().content_hash,
            "--output",
            output,
        )
        == 0
    )
    console = capsys.readouterr()
    assert "PostgreSQL" not in console.out and "person_id" not in console.out
    overview = json.loads(output.read_text())
    assert overview["snapshot"]["skill_catalog"][1]["name"] == "PostgreSQL"
    source = tmp_path / "selection.json"
    write_private_json(source, selection())
    draft = tmp_path / "draft.json"
    assert invoke(repo, "prepare", "--input", source, "--output", draft) == 0
    console = capsys.readouterr()
    assert "PostgreSQL" not in console.out and "rationale" not in console.out
    assert json.loads(draft.read_text())["schema_version"] == "capability-mapping-draft-1"
    assert repo.database_path.read_bytes() == before
    assert repo.list_capability_mappings() == []
    if os.name == "posix":
        assert output.stat().st_mode & 0o777 == 0o600
        assert draft.stat().st_mode & 0o777 == 0o600


def test_cli_report_uses_existing_database_configuration(repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TENDERCITE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TENDERCITE_LLM_BASE_URL", "not-a-provider-config")
    # The existing DB default is tendercite.db, never initialize another database.
    repo.database_path.rename(tmp_path / "tendercite.db")
    source = tmp_path / "assessment-selection.json"
    write_private_json(source, assessment())
    output = tmp_path / "assessment.json"
    assert main(["report", "--input", str(source), "--output", str(output)]) == 0
    console = capsys.readouterr()
    assert "PostgreSQL" not in console.out and "person_id" not in console.out
    assert json.loads(console.out)["summary"]["without_mapping"] == 1
    body = json.loads(output.read_text())
    assert body["findings"][0]["coverage"] == "NOT_EVALUATED"
    assert body["qualification"] == "NOT_VERIFIED"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"analysis_id":"private-synthetic-marker","analysis_id":"x"}',
        b'{"rationale":NaN}',
        b"{",
        b"\xff",
        b'{"unsupported":"private-synthetic-marker"}',
    ],
)
def test_input_errors_are_fixed_and_never_echo_content(repo, tmp_path, capsys, raw):
    source = tmp_path / "selection.json"
    source.write_bytes(raw)
    output = tmp_path / "draft.json"
    assert invoke(repo, "prepare", "--input", source, "--output", output) == 2
    console = capsys.readouterr()
    assert "private-synthetic-marker" not in console.err
    assert console.out == "" and not output.exists()
    assert json.loads(console.err)["status"] == "ERROR"


def test_oversized_input_never_parsed(repo, tmp_path, capsys):
    source = tmp_path / "too-large.json"
    source.write_bytes(b" " * 65537)
    assert invoke(repo, "prepare", "--input", source, "--output", tmp_path / "draft.json") == 2
    assert json.loads(capsys.readouterr().err)["error"] == "INPUT_TOO_LARGE"


def test_missing_database_is_not_initialized(tmp_path, capsys):
    database = tmp_path / "missing.db"
    status = main(
        [
            "--database",
            str(database),
            "overview",
            "--analysis-id",
            "missing",
            "--snapshot-hash",
            snapshot().content_hash,
            "--output",
            str(tmp_path / "out.json"),
        ]
    )
    assert status == 2 and not database.exists()
    assert "missing" not in capsys.readouterr().err


def test_invalid_argument_is_not_echoed(capsys):
    assert main(["--private-synthetic-marker"]) == 2
    assert "private-synthetic-marker" not in capsys.readouterr().err


def test_export_refuses_overwrite_and_preserves_existing_bytes(tmp_path):
    output = tmp_path / "private.json"
    output.write_bytes(b"original private contents")
    with pytest.raises(CapabilityError, match="OUTPUT_FAILED"):
        write_private_json(output, assessment())
    assert output.read_bytes() == b"original private contents"
    assert list(tmp_path.glob(".tendercite-*.json")) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX anchored publication")
def test_interrupted_export_never_publishes_partial_final_file(tmp_path, monkeypatch):
    def fail(_fd):
        raise OSError("private-synthetic-marker")

    monkeypatch.setattr(capabilities.os, "fsync", fail)
    output = tmp_path / "report.json"
    with pytest.raises(CapabilityError, match="OUTPUT_FAILED"):
        write_private_json(output, assessment())
    assert not output.exists() and list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="Symlink and descriptor tests")
@pytest.mark.parametrize("target", ["input", "output", "parent", "database"])
def test_symlinks_fail_closed(repo, tmp_path, capsys, target):
    source = tmp_path / "selection.json"
    write_private_json(source, selection())
    output = tmp_path / "draft.json"
    if target == "input":
        link = tmp_path / "linked.json"
        link.symlink_to(source)
        source = link
    elif target == "output":
        output.symlink_to(source)
    elif target == "parent":
        link = tmp_path / "linked-directory"
        link.symlink_to(tmp_path, target_is_directory=True)
        output = link / "draft.json"
    else:
        link = tmp_path / "linked.db"
        link.symlink_to(repo.database_path)
        repo.database_path = link
    assert invoke(repo, "prepare", "--input", source, "--output", output) == 2
    assert capsys.readouterr().out == ""
    assert (
        json.loads((tmp_path / "selection.json").read_text())["rationale"] == selection().rationale
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory-FD publication")
def test_parent_swap_cannot_redirect_output(tmp_path, monkeypatch):
    parent = tmp_path / "private"
    parent.mkdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    original = capabilities.os.link

    def swapped(*args, **kwargs):
        parent.rename(tmp_path / "retained")
        parent.symlink_to(victim, target_is_directory=True)
        return original(*args, **kwargs)

    monkeypatch.setattr(capabilities.os, "link", swapped)
    write_private_json(parent / "output.json", assessment())
    assert list(victim.iterdir()) == []
    assert json.loads((tmp_path / "retained/output.json").read_text())["finding_ids"] == [
        "finding-1"
    ]


def test_checkout_artifacts_are_restricted_and_excluded_from_git_and_docker(tmp_path):
    root = Path(__file__).absolute().parents[1]
    for candidate in (root / "assessment.json", root / "examples/selection.json"):
        with pytest.raises(CapabilityError, match="UNSAFE_INPUT"):
            private_workflow_path(candidate)
    allowed = root / "data/capabilities/workflow/selection.json"
    assert private_workflow_path(allowed) == allowed
    result = subprocess.run(
        ["git", "check-ignore", str(allowed)], cwd=root, capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == str(allowed)
    assert "data" in (root / ".dockerignore").read_text().splitlines()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / ".git").mkdir()
    (foreign / ".git/HEAD").write_text("ref: refs/heads/main\n")
    with pytest.raises(CapabilityError, match="UNSAFE_INPUT"):
        private_workflow_path(foreign / "selection.json")


def test_windows_directory_handles_deny_rename_and_reparse(monkeypatch):
    from ctypes import wintypes

    opened = []

    def attributes(_handle, _kind, pointer, _size):
        ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))[0] = 0x10
        return True

    def final(_handle, buffer, _size, _flags):
        buffer.value = "\\\\?\\C:\\private"
        return len(buffer.value)

    kernel = SimpleNamespace(
        CreateFileW=NativeCall(lambda *args: opened.append(args) or 42),
        CloseHandle=NativeCall(lambda _: True),
        GetFileInformationByHandleEx=NativeCall(attributes),
        GetFinalPathNameByHandleW=NativeCall(final),
        GetFileType=NativeCall(lambda _: 1),
        GetDriveTypeW=NativeCall(lambda _: 3),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: kernel, raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(open_osfhandle=lambda *_: 77))
    monkeypatch.setattr(
        capabilities, "os", SimpleNamespace(path=ntpath, O_RDONLY=0, O_BINARY=0x8000)
    )
    assert capabilities._open_windows(PureWindowsPath("C:/private"), directory=True) == 77
    assert opened[0][2] == 3  # Share read/write, never FILE_SHARE_DELETE.
    assert opened[0][4] == 3  # OPEN_EXISTING: never create a parent.
    assert opened[0][5] & 0x00200000
