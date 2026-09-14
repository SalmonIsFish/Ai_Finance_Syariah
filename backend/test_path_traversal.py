import pytest
from fastapi.testclient import TestClient
from local_api import app, get_owner_actor
import auth

def test_dashboard_path_traversal_blocked():
    # Override auth to allow access to the dashboard router
    app.dependency_overrides[get_owner_actor] = lambda: auth.Actor(username="project_owner", role="admin")
    client = TestClient(app)

    # Attempt path traversal
    response = client.get("/dashboard/../../backend/.env")
    assert response.status_code == 404, "Path traversal allowed!"

    # Verify legitimate files are still served
    response_normal = client.get("/dashboard/index.html")
    assert response_normal.status_code == 200, "Normal file failed to serve"
    
    # Cleanup dependency overrides
    app.dependency_overrides.clear()
