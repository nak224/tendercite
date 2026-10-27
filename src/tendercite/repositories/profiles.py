"""Local profile CRUD with serialized, revision-checked saves and no snapshot coupling."""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError

from tendercite.domain.profiles import (
    MAX_PROFILE_BYTES,
    ProfileCreate,
    ProfileError,
    ProfileErrorCode,
    ProfileListing,
    ProfileMetadata,
    ProfileRecord,
    ProfileSaveResult,
    ProfileUpdate,
)

PROFILE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS native_profiles (
    profile_id TEXT PRIMARY KEY NOT NULL,
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 2147483647),
    payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB)) BETWEEN 1 AND 65536)
);
"""


class ProfileRepositoryMixin:
    @contextmanager
    def _profile_transaction(self, *, write=False):
        if write and self.read_only:
            raise ProfileError(ProfileErrorCode.DATABASE_FAILED)
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield conn
        except sqlite3.Error:
            raise ProfileError(ProfileErrorCode.DATABASE_FAILED) from None

    @staticmethod
    def _profile_id(profile_id):
        if not isinstance(profile_id, UUID):
            raise ProfileError(ProfileErrorCode.INVALID_INPUT)
        return str(profile_id)

    @staticmethod
    def _profile_input(value, contract):
        try:
            return contract.model_validate(value)
        except ValidationError:
            raise ProfileError(ProfileErrorCode.INVALID_INPUT) from None

    @staticmethod
    def _stored_profile(row):
        if row is None:
            raise ProfileError(ProfileErrorCode.NOT_FOUND)
        try:
            record = ProfileRecord.model_validate_json(row["payload"])
        except (ValidationError, ValueError):
            raise ProfileError(ProfileErrorCode.STORAGE_INTEGRITY) from None
        if str(record.profile_id) != row["profile_id"] or record.revision != row["revision"]:
            raise ProfileError(ProfileErrorCode.STORAGE_INTEGRITY)
        return record

    @staticmethod
    def _profile_payload(record):
        payload = record.model_dump_json()
        if len(payload.encode("utf-8")) > MAX_PROFILE_BYTES:
            raise ProfileError(ProfileErrorCode.INVALID_INPUT)
        return payload

    def get_profile(self, profile_id: UUID) -> ProfileRecord:
        identity = self._profile_id(profile_id)
        with self._profile_transaction() as conn:
            return self._stored_profile(
                conn.execute(
                    "SELECT * FROM native_profiles WHERE profile_id=?", (identity,)
                ).fetchone()
            )

    def list_profiles(self, *, limit=50, offset=0) -> ProfileListing:
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset <= 1000000
        ):
            raise ProfileError(ProfileErrorCode.INVALID_INPUT)
        with self._profile_transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM native_profiles ORDER BY profile_id LIMIT ? OFFSET ?",
                (limit + 1, offset),
            ).fetchall()
            records = [self._stored_profile(row) for row in rows[:limit]]
            return ProfileListing(
                profiles=tuple(
                    ProfileMetadata(
                        profile_id=r.profile_id,
                        revision=r.revision,
                        name=r.profile.name,
                        profile_type=r.profile.profile_type,
                        updated_at=r.updated_at,
                    )
                    for r in records
                ),
                next_offset=offset + limit if len(rows) > limit else None,
            )

    def create_profile(self, request: ProfileCreate) -> ProfileSaveResult:
        request = self._profile_input(request, ProfileCreate)
        identity = str(request.profile_id)
        with self._profile_transaction(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM native_profiles WHERE profile_id=?", (identity,)
            ).fetchone()
            if row:
                record = self._stored_profile(row)
                if record.revision != 1 or record.profile != request.profile:
                    raise ProfileError(ProfileErrorCode.ID_CONFLICT)
                return ProfileSaveResult(status="ALREADY_PRESENT", record=record)
            now = datetime.now(UTC)
            record = ProfileRecord(
                profile_id=request.profile_id,
                revision=1,
                created_at=now,
                updated_at=now,
                profile=request.profile,
            )
            conn.execute(
                "INSERT INTO native_profiles VALUES (?, ?, ?)",
                (identity, 1, self._profile_payload(record)),
            )
            return ProfileSaveResult(status="CREATED", record=record)

    def update_profile(self, profile_id: UUID, request: ProfileUpdate) -> ProfileSaveResult:
        identity = self._profile_id(profile_id)
        request = self._profile_input(request, ProfileUpdate)
        with self._profile_transaction(write=True) as conn:
            current = self._stored_profile(
                conn.execute(
                    "SELECT * FROM native_profiles WHERE profile_id=?", (identity,)
                ).fetchone()
            )
            if current.revision != request.expected_revision or current.revision == 2147483647:
                raise ProfileError(ProfileErrorCode.REVISION_CONFLICT)
            record = ProfileRecord(
                profile_id=profile_id,
                revision=current.revision + 1,
                created_at=current.created_at,
                updated_at=max(datetime.now(UTC), current.updated_at),
                profile=request.profile,
            )
            cursor = conn.execute(
                "UPDATE native_profiles SET revision=?, payload=? "
                "WHERE profile_id=? AND revision=?",
                (
                    record.revision,
                    self._profile_payload(record),
                    identity,
                    request.expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ProfileError(ProfileErrorCode.REVISION_CONFLICT)
            return ProfileSaveResult(status="UPDATED", record=record)
