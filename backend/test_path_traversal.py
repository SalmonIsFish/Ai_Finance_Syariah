from fastapi.testclient import TestClient
from local_api import app, get_owner_actor
import auth


def test_dashboard_path_traversal_blocked():
    # Override auth to allow access to the dashboard router
    app.dependency_overrides[get_owner_actor] = lambda: auth.Actor(
        username="project_owner", role="admin"
    )
    client = TestClient(app)

    # Attempt path traversal
    response = client.get("/dashboard/../../backend/.env")
    assert response.status_code == 404, "Path traversal allowed!"

    # Verify legitimate files are still served
    response_normal = client.get("/dashboard/index.html")
    assert response_normal.status_code == 200, "Normal file failed to serve"

    # Cleanup dependency overrides
    app.dependency_overrides.clear()


def test_knowledge_note_path_traversal_blocked():
    """`/api/knowledge/note/{note_path:path}` takes an arbitrary path.

    It is currently safe *by construction* rather than by a check:
    screening_api.knowledge_note delegates to vault_indexer.get_note, which is a
    dictionary lookup into an index built by walking the vault, so only paths the
    indexer discovered inside the vault are reachable at all. That is a stronger
    guarantee than the `is_relative_to` containment check used on /dashboard/ --
    but it is also incidental, and a refactor of get_note to touch the filesystem
    would reintroduce the risk silently. This test makes the property explicit.

    Calls screening_api directly rather than over HTTP so the assertion is about
    the resolver, not about whatever auth happens to sit in front of the route.
    """
    import config
    import screening_api

    vault_path = str(config.load_settings().shariah_wiki_path)
    payloads = [
        "../../../../Windows/win.ini",
        "../../CLAUDE.md",
        "..%2f..%2fCLAUDE.md",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "....//....//CLAUDE.md",
        "../../backend/.env",
    ]
    for payload in payloads:
        assert screening_api.knowledge_note(vault_path, payload) is None, (
            f"path traversal resolved a note outside the vault: {payload!r}"
        )
