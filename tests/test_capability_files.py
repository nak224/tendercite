import ctypes
import json
import ntpath
import os
import socket
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import httpx
import pytest
from capability_helpers import CONTENT_HASH, FIXTURE, encode, example

from tendercite.services import capabilities
from tendercite.services.capabilities import (
    MAX_SNAPSHOT_BYTES,
    CapabilityError,
    CapabilityErrorCode,
    read_capability_snapshot,
    validate_capability_json,
)


@pytest.mark.parametrize("level", ["root", "period", "member", "capacity", "skill", "catalog"])
def test_duplicate_json_keys_are_detected_before_dictionary_construction(level):
    raw = FIXTURE.read_text()
    key = {
        "root": "schema_version",
        "period": "start_date",
        "member": "person_id",
        "capacity": "scheduled_hours",
        "skill": "proficiency",
        "catalog": "name",
    }[level]
    body = json.loads(raw)
    value = {
        "root": body["schema_version"],
        "period": body["assessment_period"]["start_date"],
        "member": body["workforce_members"][0]["person_id"],
        "capacity": body["workforce_members"][0]["capacity"]["scheduled_hours"],
        "skill": body["workforce_members"][0]["skills"][0]["proficiency"],
        "catalog": body["skill_catalog"][0]["name"],
    }[level]
    token = f'"{key}": {json.dumps(value)}'
    duplicate = raw.replace(token, token + ", " + token, 1).encode()
    with pytest.raises(CapabilityError) as error:
        validate_capability_json(duplicate)
    assert error.value.code == CapabilityErrorCode.INVALID_JSON


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"{} trailing",
        b"\xff",
        b'{"x": NaN}',
        b'{"x": Infinity}',
        b'{"x": -Infinity}',
        b'{"x": 1e999}',
    ],
)
def test_malformed_nonfinite_or_deep_json_fails_safely(raw):
    with pytest.raises(CapabilityError) as error:
        validate_capability_json(raw)
    assert error.value.code == CapabilityErrorCode.INVALID_JSON


def test_parser_recursion_errors_have_a_fixed_safe_category(monkeypatch):
    def depth_error(*_args, **_kwargs):
        raise RecursionError("synthetic-private-marker")

    monkeypatch.setattr(capabilities.json, "loads", depth_error)
    with pytest.raises(CapabilityError) as error:
        validate_capability_json(b"[]")
    assert error.value.code == CapabilityErrorCode.INVALID_JSON


@pytest.mark.parametrize(
    "path",
    [
        "https://example.org/snapshot.json",
        "file:///tmp/snapshot.json",
        "file:input.json",
        "\\\\server\\share\\input.json",
        "//server/input.json",
        "../input.json",
        "input.exe",
        "",
    ],
)
def test_only_local_json_file_paths_are_allowed(path):
    with pytest.raises(CapabilityError) as error:
        read_capability_snapshot(path)
    assert error.value.code == CapabilityErrorCode.UNSAFE_INPUT


def test_input_is_preserved_and_no_network_calls_occur(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Snapshot processing attempted network communication")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    path = tmp_path / "input.JSON"
    path.write_bytes(FIXTURE.read_bytes())
    before = path.stat()
    assert read_capability_snapshot(path).content_hash == CONTENT_HASH
    assert path.read_bytes() == FIXTURE.read_bytes()
    assert path.stat().st_mtime_ns == before.st_mtime_ns


def test_oversized_regular_file_rejected_before_parsing(tmp_path, monkeypatch):
    path = tmp_path / "oversized.json"
    with path.open("wb") as file:
        file.seek(MAX_SNAPSHOT_BYTES)
        file.write(b"x")
    monkeypatch.setattr(
        capabilities, "validate_capability_json", lambda _: pytest.fail("Parsed oversized file")
    )
    with pytest.raises(CapabilityError) as error:
        read_capability_snapshot(path)
    assert error.value.code == CapabilityErrorCode.INPUT_TOO_LARGE


def test_directory_and_missing_file_have_fixed_errors(tmp_path):
    path = tmp_path / "directory.json"
    path.mkdir()
    for candidate, code in [
        (path, "UNSAFE_INPUT"),
        (tmp_path / "missing.json", "FILE_READ_FAILED"),
    ]:
        with pytest.raises(CapabilityError) as error:
            read_capability_snapshot(candidate)
        assert str(error.value) == code


@pytest.mark.skipif(os.name != "posix", reason="POSIX file descriptor protections")
def test_symlink_and_symlink_parent_are_rejected(tmp_path):
    target = tmp_path / "target.json"
    target.symlink_to(FIXTURE)
    directory = tmp_path / "linked-directory"
    directory.symlink_to(FIXTURE.parent, target_is_directory=True)
    for path in (target, directory / FIXTURE.name):
        with pytest.raises(CapabilityError) as error:
            read_capability_snapshot(path)
        assert error.value.code == CapabilityErrorCode.UNSAFE_INPUT


@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO")
def test_nonregular_fifo_is_rejected_without_waiting_for_a_writer(tmp_path):
    path = tmp_path / "pipe.json"
    os.mkfifo(path)
    with pytest.raises(CapabilityError) as error:
        read_capability_snapshot(path)
    assert error.value.code == CapabilityErrorCode.UNSAFE_INPUT


@pytest.mark.skipif(os.name != "posix", reason="POSIX inode/time checks")
def test_mutation_during_read_is_detected(tmp_path, monkeypatch):
    path = tmp_path / "input.json"
    path.write_bytes(FIXTURE.read_bytes())
    original = os.fstat
    calls = 0

    def changed(fd):
        nonlocal calls
        result = original(fd)
        calls += 1
        if calls == 1:
            with path.open("ab") as file:
                file.write(b" ")
        return result

    monkeypatch.setattr(os, "fstat", changed)
    with pytest.raises(CapabilityError) as error:
        read_capability_snapshot(path)
    assert error.value.code == CapabilityErrorCode.INPUT_CHANGED


@pytest.mark.skipif(os.name != "posix", reason="POSIX O_NOFOLLOW")
def test_symlink_swap_at_final_open_is_rejected(tmp_path, monkeypatch):
    target = tmp_path / "race.json"
    target.write_bytes(FIXTURE.read_bytes())
    original = os.open

    def swapped(path, flags, *args, **kwargs):
        if path == target.name:
            target.unlink()
            target.symlink_to(FIXTURE)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapped)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {swapped})
    with pytest.raises(CapabilityError) as error:
        read_capability_snapshot(target)
    assert error.value.code == CapabilityErrorCode.UNSAFE_INPUT


def test_invalid_personnel_values_never_appear_in_public_errors():
    body = example()
    body["workforce_members"][0]["email"] = "synthetic-person@example.invalid"
    with pytest.raises(CapabilityError) as error:
        validate_capability_json(encode(body))
    assert str(error.value) == "INVALID_CONTRACT"
    assert "synthetic-person" not in repr(error.value)


def test_unknown_platform_fails_closed(monkeypatch):
    monkeypatch.setattr(capabilities, "os", SimpleNamespace(name="unsupported"))
    with pytest.raises(CapabilityError) as error:
        capabilities._open_local(Path("input.json"))
    assert error.value.code == CapabilityErrorCode.UNSUPPORTED_PLATFORM


def test_default_workforce_artifact_directory_is_git_ignored():
    root = Path(__file__).parents[1]
    result = subprocess.run(
        ["git", "check-ignore", "data/capabilities/input.json", "data/capabilities/export.json"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == [
        "data/capabilities/input.json",
        "data/capabilities/export.json",
    ]


class NativeCall:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


@pytest.mark.parametrize(
    "failure", [None, "reparse", "directory", "redirected", "remote", "not_disk", "open_failure"]
)
def test_windows_native_handle_checks_without_posix_assumptions(monkeypatch, failure):
    from ctypes import wintypes

    closed, converted, opened = [], [], []

    def create(*args):
        opened.append(args)
        return ctypes.c_void_p(-1).value if failure == "open_failure" else 42

    def attributes(_handle, _kind, pointer, _size):
        flags = 0x400 if failure == "reparse" else 0x10 if failure == "directory" else 0
        ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))[0] = flags
        return True

    def final(_handle, buffer, _size, _flags):
        buffer.value = (
            "\\\\?\\C:\\elsewhere\\input.json"
            if failure == "redirected"
            else "\\\\?\\C:\\local\\input.json"
        )
        return len(buffer.value)

    kernel = SimpleNamespace(
        CreateFileW=NativeCall(create),
        CloseHandle=NativeCall(lambda handle: closed.append(handle)),
        GetFileInformationByHandleEx=NativeCall(attributes),
        GetFinalPathNameByHandleW=NativeCall(final),
        GetFileType=NativeCall(lambda _handle: 3 if failure == "not_disk" else 1),
        GetDriveTypeW=NativeCall(lambda _anchor: 4 if failure == "remote" else 3),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: kernel, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "msvcrt",
        SimpleNamespace(
            open_osfhandle=lambda handle, flags: converted.append((handle, flags)) or 77
        ),
    )
    monkeypatch.setattr(
        capabilities, "os", SimpleNamespace(path=ntpath, O_RDONLY=0, O_BINARY=0x8000, O_WRONLY=1)
    )
    if failure:
        with pytest.raises(CapabilityError):
            capabilities._open_windows(PureWindowsPath("C:/local/input.json"))
        assert not converted
        assert closed == ([] if failure in ("remote", "open_failure") else [42])
    else:
        assert capabilities._open_windows(PureWindowsPath("C:/local/input.json")) == 77
        assert opened[0][2] == 1 and opened[0][4] == 3  # share-read only / OPEN_EXISTING
        assert opened[0][5] & 0x00200000  # OPEN_REPARSE_POINT
        assert converted == [(42, 0x8000)] and closed == []
