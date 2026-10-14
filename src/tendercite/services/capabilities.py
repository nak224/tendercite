"""Bounded local JSON processing with fixed, non-personnel error categories."""

import errno
import json
import os
import re
import stat
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from tendercite.domain.capabilities_v1 import CapabilitySnapshot, canonical_json

MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024


class CapabilityErrorCode(StrEnum):
    UNSAFE_INPUT = "UNSAFE_INPUT"
    UNSUPPORTED_PLATFORM = "UNSUPPORTED_PLATFORM"
    FILE_READ_FAILED = "FILE_READ_FAILED"
    INPUT_TOO_LARGE = "INPUT_TOO_LARGE"
    INPUT_CHANGED = "INPUT_CHANGED"
    INVALID_JSON = "INVALID_JSON"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    INVALID_CONTRACT = "INVALID_CONTRACT"
    HASH_MISMATCH = "HASH_MISMATCH"
    STORAGE_INTEGRITY = "STORAGE_INTEGRITY"
    DATABASE_FAILED = "DATABASE_FAILED"
    CONFIGURATION_FAILED = "CONFIGURATION_FAILED"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    ACKNOWLEDGEMENT_REQUIRED = "ACKNOWLEDGEMENT_REQUIRED"
    NOT_FOUND = "NOT_FOUND"
    OUTPUT_FAILED = "OUTPUT_FAILED"


class CapabilityError(ValueError):
    def __init__(self, code: CapabilityErrorCode):
        self.code = code
        super().__init__(code.value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CapabilityError(CapabilityErrorCode.INVALID_JSON)
        result[key] = value
    return result


def _nonfinite(_value):
    raise CapabilityError(CapabilityErrorCode.INVALID_JSON)


def validate_capability_json(raw: bytes) -> CapabilitySnapshot:
    """Detect duplicates before dictionary construction; then use strict JSON-mode typing."""
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise CapabilityError(CapabilityErrorCode.INPUT_TOO_LARGE)
    try:
        data = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_nonfinite
        )
        if not isinstance(data, dict):
            raise CapabilityError(CapabilityErrorCode.INVALID_CONTRACT)
        if "schema_version" in data and data["schema_version"] != "1.0":
            raise CapabilityError(CapabilityErrorCode.UNSUPPORTED_SCHEMA)
        # JSON-mode permits the producer's UUID strings/arrays while retaining strict
        # booleans, integers and the independent decimal-string validators.
        return CapabilitySnapshot.model_validate_json(
            json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        )
    except CapabilityError:
        raise
    except ValidationError as exc:
        hash_error = any(
            e["type"] == "capability_hash_mismatch"
            for e in exc.errors(include_input=False, include_context=False)
        )
        raise CapabilityError(
            CapabilityErrorCode.HASH_MISMATCH
            if hash_error
            else CapabilityErrorCode.INVALID_CONTRACT
        ) from None
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise CapabilityError(CapabilityErrorCode.INVALID_JSON) from None


def local_json_path(value: str | Path) -> Path:
    text = os.fspath(value)
    if (
        not text
        or "\x00" in text
        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]+:", text)
        or text.startswith(("//", "\\\\"))
    ):
        raise CapabilityError(CapabilityErrorCode.UNSAFE_INPUT)
    path = Path(text)
    if path.suffix.casefold() != ".json" or ".." in path.parts:
        raise CapabilityError(CapabilityErrorCode.UNSAFE_INPUT)
    return Path(os.path.abspath(path))  # Lexical, never resolve through a symlink.


def _open_posix_directory(path: Path) -> int:
    if (
        not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
        or os.open not in os.supports_dir_fd
    ):
        raise CapabilityError(CapabilityErrorCode.UNSUPPORTED_PLATFORM)
    guards = os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    directory_flags = os.O_RDONLY | guards | os.O_DIRECTORY
    directory = os.open(path.anchor, directory_flags)
    try:
        for component in path.parts[1:]:
            next_directory = os.open(component, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        return directory
    except BaseException:
        os.close(directory)
        raise


def _open_posix(path: Path, *, create: bool = False) -> int:
    directory = _open_posix_directory(path.parent)
    try:
        guards = os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY | os.O_NONBLOCK
        return os.open(path.name, guards | flags, 0o600, dir_fd=directory)
    finally:
        os.close(directory)


def _open_windows(path: Path, *, create: bool = False, directory: bool = False) -> int:
    """Open without following the final reparse point; reject redirected ancestors.

    Read-only sharing prevents writes/deletion while this handle is held. Native
    resolved-path/attribute checks reject junctions, symlinks and remote shares.
    """
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    attributes = kernel.GetFileInformationByHandleEx
    attributes.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    attributes.restype = wintypes.BOOL
    final_path = kernel.GetFinalPathNameByHandleW
    final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    final_path.restype = wintypes.DWORD
    file_type = kernel.GetFileType
    file_type.argtypes, file_type.restype = [wintypes.HANDLE], wintypes.DWORD
    drive_type = kernel.GetDriveTypeW
    drive_type.argtypes, drive_type.restype = [wintypes.LPCWSTR], wintypes.UINT

    class AttributeTag(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    if drive_type(path.anchor) == 4:  # DRIVE_REMOTE, including mapped network drives.
        raise CapabilityError(CapabilityErrorCode.UNSAFE_INPUT)
    handle = create_file(
        str(path),
        0x80 if directory else 0x40000000 if create else 0x80000000,
        3 if directory else 0 if create else 1,
        None,
        1 if create else 3,
        0x00200000 | 0x02000000,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise CapabilityError(CapabilityErrorCode.FILE_READ_FAILED)
    try:
        tag, buffer = AttributeTag(), ctypes.create_unicode_buffer(32768)
        size = final_path(handle, buffer, len(buffer), 0)
        actual = buffer.value.removeprefix("\\\\?\\")
        if (
            not attributes(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag))
            or tag.attributes & 0x400  # REPARSE_POINT.
            or bool(tag.attributes & 0x10) != directory
            or file_type(handle) != 1  # FILE_TYPE_DISK.
            or not 0 < size < len(buffer)
            or os.path.normcase(actual) != os.path.normcase(str(path))
        ):
            raise CapabilityError(CapabilityErrorCode.UNSAFE_INPUT)
        fd = msvcrt.open_osfhandle(handle, (os.O_WRONLY if create else os.O_RDONLY) | os.O_BINARY)
        handle = None  # Ownership transferred to the Python file descriptor.
        return fd
    finally:
        if handle is not None:
            close(handle)


def read_local_json_bytes(value: str | Path, *, max_bytes: int = MAX_SNAPSHOT_BYTES) -> bytes:
    """Bounded regular-file read; retain the importer's local-path and race safeguards."""
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_SNAPSHOT_BYTES:
        raise CapabilityError(CapabilityErrorCode.INVALID_ARGUMENTS)
    path = local_json_path(value)
    fd = None
    try:
        fd = _open_local(path)
        with os.fdopen(fd, "rb") as stream:
            fd = None
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise CapabilityError(CapabilityErrorCode.UNSAFE_INPUT)
            if before.st_size > max_bytes:
                raise CapabilityError(CapabilityErrorCode.INPUT_TOO_LARGE)
            raw = stream.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise CapabilityError(CapabilityErrorCode.INPUT_TOO_LARGE)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) or len(raw) != after.st_size:
                raise CapabilityError(CapabilityErrorCode.INPUT_CHANGED)
        return raw
    except CapabilityError:
        raise
    except OSError as exc:
        code = (
            CapabilityErrorCode.UNSAFE_INPUT
            if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR, errno.ENXIO)
            else CapabilityErrorCode.FILE_READ_FAILED
        )
        raise CapabilityError(code) from None
    finally:
        if fd is not None:
            os.close(fd)


def read_capability_snapshot(value: str | Path) -> CapabilitySnapshot:
    return validate_capability_json(read_local_json_bytes(value))


def _open_local(path: Path, *, create: bool = False) -> int:
    try:
        if os.name == "posix":
            return _open_posix(path, create=create)
        if os.name == "nt":
            return _open_windows(path, create=create)
        raise CapabilityError(CapabilityErrorCode.UNSUPPORTED_PLATFORM)
    except (ImportError, AttributeError, NotImplementedError):
        raise CapabilityError(CapabilityErrorCode.UNSUPPORTED_PLATFORM) from None


def write_capability_snapshot(path: str | Path, snapshot: CapabilitySnapshot) -> None:
    """Explicit private export to a new local file; never overwrite an existing path."""
    path = local_json_path(path)
    raw = canonical_json(snapshot)
    validate_capability_json(raw)
    fd = None
    try:
        fd = _open_local(path, create=True)
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except CapabilityError:
        raise
    except OSError:
        raise CapabilityError(CapabilityErrorCode.OUTPUT_FAILED) from None
    finally:
        if fd is not None:
            os.close(fd)


@contextmanager
def _windows_locked_parents(path):
    """Hold all ancestors against deletion/rename while publishing on Windows."""
    handles = []
    try:
        for parent in reversed(path.parents):
            handles.append(_open_windows(parent, directory=True))
        yield
    finally:
        for handle in reversed(handles):
            os.close(handle)


def write_private_json(path: str | Path, model) -> None:
    """Publish complete JSON exclusively; partial writes never occupy the final path.

    Reuse guarded directory/file opens. POSIX publication uses an anchored exclusive
    hard link; Windows rename refuses replacement while ancestor handles deny rename.
    No permissive fallback when the filesystem cannot publish safely.
    """
    path = local_json_path(path)
    raw = (model.model_dump_json(indent=2) + "\n").encode("utf-8")
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise CapabilityError(CapabilityErrorCode.INPUT_TOO_LARGE)
    temporary = path.with_name(f".tendercite-{uuid4().hex}.json")
    directory = None
    created = False
    try:
        if os.name == "posix":
            directory = _open_posix_directory(path.parent)
            fd = os.open(
                temporary.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            created = True
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(
                temporary.name,
                path.name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
            os.fsync(directory)
        elif os.name == "nt":
            with _windows_locked_parents(path):
                try:
                    fd = _open_local(temporary, create=True)
                    created = True
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.rename(temporary, path)  # Windows: fails if destination exists.
                finally:
                    if created and temporary.exists():
                        temporary.unlink()
        else:
            raise CapabilityError(CapabilityErrorCode.UNSUPPORTED_PLATFORM)
    except CapabilityError:
        raise
    except OSError:
        raise CapabilityError(CapabilityErrorCode.OUTPUT_FAILED) from None
    finally:
        if directory is not None:
            try:
                if created:
                    os.unlink(temporary.name, dir_fd=directory)
            finally:
                os.close(directory)
