"""The Modal image pins its own web deps; they must match the versions uv.lock tests against."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = (ROOT / "modal-deployment" / "deploy.py").read_text()
LOCK = (ROOT / "uv.lock").read_text()


def locked_version(name):
    (version,) = re.findall(rf'^name = "{name}"\nversion = "([^"]+)"', LOCK, re.M)
    return version


@pytest.mark.parametrize("package, image_spec", [
    ("fastapi", r'"fastapi\[standard\]==([^"]+)"'),
    ("itsdangerous", r'"itsdangerous==([^"]+)"'),
])
def test_image_pins_match_lock(package, image_spec):
    (pinned,) = re.findall(image_spec, DEPLOY)
    assert pinned == locked_version(package)


def test_readme_rodney_matches_image():
    (rodney,) = re.findall(r'^RODNEY_VERSION = "([^"]+)"', DEPLOY, re.M)
    readme = (ROOT / "README.md").read_text()
    assert set(re.findall(r"simonw/rodney@(v[\d.]+)", readme)) == {rodney}
