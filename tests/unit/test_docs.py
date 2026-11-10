"""The README and the reference documents must not point at things that do not exist: every task ID they
name is a tracked task, and every relative link or image resolves. Task IDs change when the catalog is
renamed (opaque IDs), so a copied command goes stale without anyone noticing."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]

TASK_ID = re.compile(r"\b(?:deliver_bid|deliver|impossible|simops|tycoon|arena)-(?:train|dev|ood)-[0-9a-f]{4,6}\b")
# Generator output, before `railhead harden` renames seed IDs to opaque ones: "deliver/train/3: ok deliver-train-0003"
GEN_OUTPUT = re.compile(r"^\s*[a-z_]+/(?:train|dev|ood)/\d+: ok [a-z_]+-(?:train|dev|ood)-\d{4}\s*$")
LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|\b(?:src|srcset|href)=\"([^\"]+)\"")


def _tracked_task_ids() -> set[str]:
    out = subprocess.run(["git", "ls-files", "tasks"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return {line.split("/")[1] for line in out.splitlines() if line.count("/") >= 2}


@pytest.fixture(scope="module")
def tracked() -> set[str]:
    ids = _tracked_task_ids()
    if not ids:
        pytest.skip("not a git checkout with a task catalog")
    return ids


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_task_id_in_the_docs_is_tracked(doc, tracked):
    bad = [f"{doc.name}:{n}: {tid}" for n, line in enumerate(doc.read_text().splitlines(), 1)
           if not GEN_OUTPUT.match(line) for tid in TASK_ID.findall(line) if tid not in tracked]
    assert bad == []


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_relative_link_and_image_resolves(doc):
    bad = []
    for n, line in enumerate(doc.read_text().splitlines(), 1):
        for m in LINK.finditer(line):
            target = (m.group(1) or m.group(2)).split("#", 1)[0]
            if not target or re.match(r"^[a-z]+:", target):  # anchors in the same file, URLs, mailto
                continue
            if not (doc.parent / target).exists():
                bad.append(f"{doc.name}:{n}: {target}")
    assert bad == []


def test_the_patterns_catch_stale_ids_and_skip_generator_output():
    assert TASK_ID.findall("harbor run -p harbor/deliver-train-0000 -a oracle") == ["deliver-train-0000"]
    assert TASK_ID.findall("tasks/deliver_bid-dev-05536d") == ["deliver_bid-dev-05536d"]
    assert GEN_OUTPUT.match("   deliver/train/3: ok deliver-train-0003")
    assert not GEN_OUTPUT.match("railhead up deliver-train-0003")
