"""One version: railhead.__version__ is the single source. Every place that names a version or a default
image tag must follow it, so a release cannot ship an image that reports an older version."""

import re
import tomllib
from pathlib import Path

import railhead
from railhead import harbor_export, hub

ROOT = Path(__file__).resolve().parents[2]
V = railhead.__version__


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", V)


def test_pyproject_reads_the_version_from_the_package():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "version" not in meta["project"]
    assert "version" in meta["project"]["dynamic"]
    assert meta["tool"]["hatch"]["version"]["path"] == "src/railhead/__init__.py"


def test_default_image_and_harbor_version_follow_the_package():
    assert harbor_export.DEFAULT_VERSION == V
    assert harbor_export.DEFAULT_IMAGE == f"ghcr.io/rachittshah/railhead:{V}"
    assert harbor_export.export_task.__defaults__[-1] == V
    assert harbor_export.export_all.__defaults__[-1] == V
    assert hub.write_card.__defaults__[-1] == harbor_export.DEFAULT_IMAGE


def test_dockerfile_default_build_arg_matches():
    text = (ROOT / "docker" / "Dockerfile").read_text()
    assert re.findall(r"^ARG RAILHEAD_VERSION=(\S+)$", text, re.MULTILINE) == [V]


def test_image_workflow_passes_the_package_version_and_checks_the_tag():
    text = (ROOT / ".github" / "workflows" / "image.yml").read_text()
    assert "src/railhead/__init__.py" in text
    assert "RAILHEAD_VERSION=${{ needs.version.outputs.version }}" in text
    assert 'GITHUB_REF_NAME" != "v$v"' in text  # a tag v0.1.3 must not build a package that says 0.1.2


def test_usage_tables_show_the_package_version():
    text = (ROOT / "docs" / "USAGE.md").read_text()
    assert f"| `RAILHEAD_VERSION` | `{V}` |" in text
    assert f"| `--image <ref>` | `ghcr.io/rachittshah/railhead:{V}` |" in text
    assert f"| `--version <v>` | `{V}` |" in text


def test_changelog_has_an_entry_for_this_version():
    assert re.search(rf"^## {re.escape(V)}\b", (ROOT / "CHANGELOG.md").read_text(), re.MULTILINE)


def test_no_stale_version_literal_in_code_or_build_files():
    stale = re.compile(r"\b0\.1\.[0-9]+\b")
    paths = [*sorted((ROOT / "src").rglob("*.py")), ROOT / "docker" / "Dockerfile",
             *sorted((ROOT / ".github" / "workflows").glob("*.yml"))]
    allowed = {("src/railhead/__init__.py", V), ("docker/Dockerfile", V)}  # the source, and the checked ARG
    hits = [(str(p.relative_to(ROOT)), m.group()) for p in paths for m in stale.finditer(p.read_text())]
    assert [h for h in hits if h not in allowed] == []
