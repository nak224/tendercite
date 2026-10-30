"""Original synthetic pipeline input. No external opportunity or personnel records."""

import json
from uuid import UUID

from tendercite.domain.pipeline import PipelineCreate, PipelineUpdate

PIPELINE_ID = UUID(int=1600)
OPPORTUNITY_ID = UUID(int=1200)


def create_body(**changes):
    return {
        "schema_version": "native-pipeline-create-1",
        "pipeline_id": str(PIPELINE_ID),
        "opportunity_id": str(OPPORTUNITY_ID),
        **changes,
    }


def create_input(**changes):
    return PipelineCreate.model_validate_json(json.dumps(create_body(**changes)))


def update_body(revision=1, **changes):
    return {
        "schema_version": "native-pipeline-update-1",
        "expected_revision": revision,
        "values": {
            "stage": "PREPARING",
            "priority": "HIGH",
            "notes": "Synthetic manual note",
            "next_action": "Draft a synthetic proposal",
            "next_action_due_date": "2027-02-15",
            **changes,
        },
    }


def update_input(revision=1, **changes):
    return PipelineUpdate.model_validate_json(json.dumps(update_body(revision, **changes)))
