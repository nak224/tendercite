"""Self-authored manual opportunities; no external listings or source documents."""

import json
from uuid import UUID

from tendercite.domain.opportunities import OpportunityCreate, OpportunityUpdate

OPPORTUNITY_ID = UUID(int=1200)


def create_body(kind="FREELANCE_PROJECT", **changes):
    return {
        "schema_version": "native-opportunity-create-1",
        "opportunity_id": str(OPPORTUNITY_ID),
        "opportunity": {
            "schema_version": "native-opportunity-1",
            "title": "Synthetic Python project",
            "opportunity_type": kind,
            "organization": "Synthetic organization",
            "description": "Self-authored testing opportunity",
            "location": "Synthetic city",
            "work_arrangement": "REMOTE",
            "deadline": "2027-03-01",
            "source_platform": "Synthetic external platform",
            "source_url": "https://example.invalid/projects/synthetic?ref=manual#details",
            "required_skills": ["Python", "FastAPI"],
            "requirements_description": "Manually declared skills, not a legal interpretation",
        },
        **changes,
    }


def create_input(**changes):
    return OpportunityCreate.model_validate_json(json.dumps(create_body(**changes)))


def update_input(opportunity, revision=1):
    return OpportunityUpdate(expected_revision=revision, opportunity=opportunity)
