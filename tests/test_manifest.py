"""Test the Weheat Custom integration manifest."""

from __future__ import annotations

import json
import re
from pathlib import Path


def test_manifest_requirements() -> None:
    """Verify manifest.json structure and zero unvetted requirements constraint."""
    manifest_path = Path("custom_components/weheat/manifest.json")
    assert manifest_path.is_file(), (
        "manifest.json must exist in custom_components/weheat/"
    )

    with manifest_path.open(encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["domain"] == "weheat"
    assert manifest["name"] == "Weheat"
    assert manifest["version"] == "0.1.4"
    assert re.match(r"^\d+\.\d+\.\d+$", manifest["version"]), (
        f"Version {manifest['version']} must follow Semantic Versioning (X.Y.Z)"
    )
    assert manifest["requirements"] == [], (
        "Strictly zero unvetted external dependencies permitted!"
    )
    assert "application_credentials" in manifest["dependencies"]
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["integration_type"] == "hub"
    assert "climate.heat_pumps_air_conditioning" in manifest["categories"]
    assert "@JayXD-2K" in manifest["codeowners"]
    assert manifest["documentation"].startswith("https://")
