"""Original synthetic profile declarations; no workforce or procurement input."""

from uuid import UUID

from tendercite.domain.profiles import ProfileCreate, ProfileUpdate

PROFILE_ID = UUID(int=800)


def create_body(**changes):
    return {
        "schema_version": "native-profile-create-1",
        "profile_id": str(PROFILE_ID),
        "profile": {
            "schema_version": "native-capability-profile-1",
            "name": "Synthetic freelancer",
            "profile_type": "FREELANCER",
            "headline": "Manually declared software skills",
            "location": "Synthetic city",
            "work_arrangement": "REMOTE",
            "skills": [{"skill_id": str(UUID(int=801)), "name": "Python"}],
            "opportunity_keywords": ["Software Development", "Consulting"],
        },
        **changes,
    }


def create_input(**changes):
    import json

    return ProfileCreate.model_validate_json(json.dumps(create_body(**changes)))


def update_input(profile, revision=1):
    return ProfileUpdate(expected_revision=revision, profile=profile)
