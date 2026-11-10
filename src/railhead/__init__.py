"""Railhead — models play OpenTTD directly."""

# The single source of the version: pyproject.toml reads it (hatch), the default image tag and the Harbor
# task version derive from it, and the image build passes it in as a build-arg (tests/unit/test_version.py).
__version__ = "0.1.2"
