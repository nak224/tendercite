"""Offline label previews over original synthetic persisted records."""

import json
from contextlib import contextmanager
from uuid import UUID

import pytest
from opportunity_helpers import create_input as opportunity_input
from opportunity_helpers import update_input as opportunity_update
from profile_helpers import create_input as profile_input
from profile_helpers import update_input as profile_update

from tendercite.domain.native_matching import NativeMatchError, NativeMatchSelection
from tendercite.domain.profiles import ProfileSkill
from tendercite.repositories.sqlite import SqliteRepository
from tendercite.services.native_matching import preview_native_match


def records(repo, skills=("Python",), required=("python",)):
    p = profile_input()
    profile = p.profile.model_copy(
        update={
            "skills": tuple(
                ProfileSkill(skill_id=UUID(int=900 + i), name=name) for i, name in enumerate(skills)
            )
        }
    )
    saved = repo.create_profile(p.model_copy(update={"profile": profile})).record
    o = opportunity_input()
    opportunity = o.opportunity.model_copy(update={"required_skills": tuple(required)})
    offered = repo.create_opportunity(o.model_copy(update={"opportunity": opportunity})).record
    selection = NativeMatchSelection(
        profile_id=saved.profile_id,
        opportunity_id=offered.opportunity_id,
        expected_profile_revision=1,
        expected_opportunity_revision=1,
    )
    return saved, offered, selection


@pytest.fixture
def repo(tmp_path):
    return SqliteRepository(tmp_path / "synthetic.db")


@pytest.mark.parametrize(
    "original,required,normalized",
    [
        ("Python", "python", "python"),
        ("Ｐｙｔｈｏｎ", "Python", "python"),
        ("Machine  Learning", "machine learning", "machine learning"),
        ("Machine\u2003Learning", "MACHINE learning", "machine learning"),
        ("Straße", "STRASSE", "strasse"),
        ("FastAPI", "fastapi", "fastapi"),
    ],
)
def test_normalized_exact_equality_preserves_originals(repo, original, required, normalized):
    _, _, selection = records(repo, (original,), (required,))
    before = repo.database_path.read_bytes()
    result = preview_native_match(selection, repository=repo)
    assert result.profile.skills[0].original_label == original
    assert result.profile.skills[0].normalized_label == normalized
    row = result.opportunity.required_skills[0]
    assert row.original_label == required and row.normalized_label == normalized
    assert row.status == "MATCHED"
    assert row.matched_profile_skill_ids == (result.profile.skills[0].skill_id,)
    assert result.counts.matched_skills == 1
    assert repo.database_path.read_bytes() == before


@pytest.mark.parametrize(
    "skills,required,matched",
    [
        (("Java",), ("JavaScript",), 0),
        (("ML",), ("Machine Learning",), 0),
        (("React",), ("React Native",), 0),
        (("cafe",), ("café",), 0),
        ((), ("Python",), 0),
        (("Python",), (), 0),
        (("Python", "FastAPI"), ("python", "fastapi", "Java"), 2),
    ],
)
def test_no_semantics_and_explicit_empty_counts(repo, skills, required, matched):
    _, _, selection = records(repo, skills, required)
    result = preview_native_match(selection, repository=repo)
    assert result.counts.model_dump() == {
        "total_opportunity_skills": len(required),
        "matched_skills": matched,
        "not_recorded_skills": len(required) - matched,
    }
    assert result.status == ("COMPARED" if required else "NO_REQUIREMENTS")
    assert result.qualification == result.eligibility == "NOT_VERIFIED"
    assert result.availability == "NOT_EVALUATED"
    assert all(
        not r.matched_profile_skill_ids
        for r in result.opportunity.required_skills
        if r.status == "NOT_RECORDED"
    )


def test_ignores_all_other_fields_and_does_not_modify_records(repo):
    p, o, selection = records(repo, ("Java",), ("FastAPI",))
    repo.update_profile(
        p.profile_id,
        profile_update(
            p.profile.model_copy(
                update={"headline": "FastAPI", "opportunity_keywords": ("FastAPI",)}
            )
        ),
    )
    result = preview_native_match(
        selection.model_copy(update={"expected_profile_revision": 2}), repository=repo
    )
    assert result.counts.matched_skills == 0
    assert repo.get_opportunity(o.opportunity_id) == o
    assert repo.get_profile(p.profile_id).profile.skills == p.profile.skills
    assert repo.list_findings() == [] and repo.list_capability_snapshots() == []


@pytest.mark.parametrize("kind", ["profile", "opportunity"])
def test_identical_content_new_revision_still_conflicts(repo, kind):
    p, o, selection = records(repo)
    if kind == "profile":
        repo.update_profile(p.profile_id, profile_update(p.profile))
    else:
        repo.update_opportunity(o.opportunity_id, opportunity_update(o.opportunity))
    with pytest.raises(NativeMatchError, match=f"{kind.upper()}_REVISION_CONFLICT"):
        preview_native_match(selection, repository=repo)


def test_one_read_snapshot_survives_interleaved_writer(repo, monkeypatch):
    _, o, selection = records(repo)
    with repo._connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    read = SqliteRepository(repo.database_path, read_only=True)
    connect = read._connect
    statements = []
    changed = False

    class Interleaved:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, *args):
            nonlocal changed
            statements.append(sql)
            result = self.conn.execute(sql, *args)
            if "FROM native_profiles" in sql and not changed:
                changed = True
                repo.update_opportunity(o.opportunity_id, opportunity_update(o.opportunity))
            return result

    @contextmanager
    def wrapped():
        with connect() as conn:
            yield Interleaved(conn)

    monkeypatch.setattr(read, "_connect", wrapped)
    result = preview_native_match(selection, repository=read)
    assert result.profile.revision == result.opportunity.revision == 1
    assert repo.get_opportunity(o.opportunity_id).revision == 2
    assert statements[0] == "BEGIN" and len(statements) == 3
    with pytest.raises(NativeMatchError, match="OPPORTUNITY_REVISION_CONFLICT"):
        preview_native_match(selection, repository=read)


@pytest.mark.parametrize("kind", ["profile", "opportunity"])
def test_missing_and_corrupt_identity_are_fixed_errors(repo, kind):
    _, _, selection = records(repo)
    with pytest.raises(NativeMatchError, match=f"{kind.upper()}_NOT_FOUND"):
        preview_native_match(
            selection.model_copy(update={f"{kind}_id": UUID(int=1)}), repository=repo
        )
    with repo._connect() as conn:
        table = "native_profiles" if kind == "profile" else "native_opportunities"
        conn.execute(f"UPDATE {table} SET payload=?", (json.dumps({"PRIVATE": "PRIVATE"}),))
    with pytest.raises(NativeMatchError, match=f"{kind.upper()}_STORAGE_INTEGRITY"):
        preview_native_match(selection, repository=repo)


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "absent.db"
    with pytest.raises(NativeMatchError, match="DATABASE_FAILED"):
        preview_native_match(
            NativeMatchSelection(
                profile_id=UUID(int=1),
                opportunity_id=UUID(int=2),
                expected_profile_revision=1,
                expected_opportunity_revision=1,
            ),
            repository=SqliteRepository(path, read_only=True),
        )
    assert not path.exists()
