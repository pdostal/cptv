from __future__ import annotations

from fastapi.testclient import TestClient

from cptv import __version__


def test_footer_renders_app_version(client: TestClient):
    """Footer shows v<X.Y.Z> right before the GitHub link."""
    r = client.get("/", headers={"Accept": "text/html"})
    assert r.status_code == 200
    body = r.text
    expected = f"v{__version__}"
    assert expected in body, f"missing {expected!r} in footer"
    # Order check: version appears before the GitHub link.
    assert body.index(expected) < body.index("github.com/pdostal/cptv")


def test_app_version_matches_pyproject():
    """Single source of truth: cptv.__version__ must equal pyproject.toml."""
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == __version__
