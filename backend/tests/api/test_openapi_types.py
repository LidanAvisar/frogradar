"""The committed OpenAPI file (frontend types are generated from it) must match the code."""

from pathlib import Path

import pytest

from app.api.openapi_export import openapi_json

COMMITTED = Path(__file__).resolve().parents[3] / "frontend/src/app/api/openapi.json"


def test_committed_openapi_is_current():
    if not COMMITTED.exists():
        pytest.skip("frontend not in this checkout")
    assert COMMITTED.read_text(encoding="utf-8") == openapi_json(), (
        "API changed: run `make api-types` and commit the regenerated files"
    )
