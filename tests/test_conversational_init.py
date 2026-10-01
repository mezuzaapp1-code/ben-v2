"""The retired onboarding endpoint cannot create projects or provision schemas."""
from fastapi.testclient import TestClient
from unittest.mock import patch
import main

def test_retired_onboarding_cannot_create_project():
    with patch("routers.projects.create_project") as create:
        response = TestClient(main.app).post("/api/projects/conversational-init", json={"name":"Video", "software_description":"Table: unwanted"})
    assert response.status_code in (404, 405)
    create.assert_not_called()

def test_project_contract_has_no_legacy_setup_fields():
    from routers.projects import ProjectCreateBody
    from pydantic import ValidationError
    import pytest
    assert ProjectCreateBody(name="Video").description is None
    for key in ("software_description", "location_base", "key_contacts", "initial_tactical_tasks", "schema_tables"):
        with pytest.raises(ValidationError):
            ProjectCreateBody(name="Video", **{key:"unwanted"})
