"""Additive local tables. History is immutable even for direct SQL writes."""

MAPPING_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS capability_mappings (
    mapping_id TEXT PRIMARY KEY NOT NULL,
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    finding_id TEXT NOT NULL REFERENCES findings(id),
    snapshot_hash TEXT NOT NULL REFERENCES capability_snapshots(content_hash),
    created_at TEXT NOT NULL,
    UNIQUE(analysis_id, finding_id, snapshot_hash)
);
CREATE TABLE IF NOT EXISTS capability_mapping_revisions (
    mapping_id TEXT NOT NULL REFERENCES capability_mappings(mapping_id),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    status TEXT NOT NULL CHECK(status IN ('DRAFT', 'APPROVED', 'REVOKED')),
    review_sequence INTEGER REFERENCES reviews(sequence),
    payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB)) BETWEEN 1 AND 65536),
    PRIMARY KEY(mapping_id, revision)
);
CREATE TRIGGER IF NOT EXISTS capability_mapping_scope
BEFORE INSERT ON capability_mappings
WHEN NOT EXISTS(SELECT 1 FROM findings
    WHERE id=NEW.finding_id AND analysis_run_id=NEW.analysis_id)
BEGIN SELECT RAISE(ABORT, 'mapping scope'); END;
CREATE TRIGGER IF NOT EXISTS capability_mapping_revision_order
BEFORE INSERT ON capability_mapping_revisions
WHEN NEW.revision != COALESCE((SELECT MAX(revision) + 1
    FROM capability_mapping_revisions WHERE mapping_id=NEW.mapping_id), 1)
BEGIN SELECT RAISE(ABORT, 'mapping revision order'); END;
CREATE TRIGGER IF NOT EXISTS capability_mapping_review_scope
BEFORE INSERT ON capability_mapping_revisions
WHEN NEW.review_sequence IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM reviews r JOIN capability_mappings m ON m.finding_id=r.finding_id
    WHERE r.sequence=NEW.review_sequence AND m.mapping_id=NEW.mapping_id)
BEGIN SELECT RAISE(ABORT, 'mapping review scope'); END;
CREATE TRIGGER IF NOT EXISTS capability_mappings_no_update
BEFORE UPDATE ON capability_mappings
BEGIN SELECT RAISE(ABORT, 'immutable mapping identity'); END;
CREATE TRIGGER IF NOT EXISTS capability_mappings_no_delete
BEFORE DELETE ON capability_mappings
BEGIN SELECT RAISE(ABORT, 'immutable mapping identity'); END;
CREATE TRIGGER IF NOT EXISTS capability_mapping_revisions_no_update
BEFORE UPDATE ON capability_mapping_revisions
BEGIN SELECT RAISE(ABORT, 'immutable mapping revision'); END;
CREATE TRIGGER IF NOT EXISTS capability_mapping_revisions_no_delete
BEFORE DELETE ON capability_mapping_revisions
BEGIN SELECT RAISE(ABORT, 'immutable mapping revision'); END;
"""
