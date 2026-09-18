"""Replacement races must never redirect an advisory read outside its root."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from flask import Flask

from app.api import research as api
from app.config import Config
from app.services import pipeline_inspection as inspection


def _replace_after_containment(monkeypatch, target, external, kind):
    contained = inspection._contained
    replaced = []

    def race(path, roots):
        result = contained(path, roots)
        if result == target and not replaced:
            replaced.append(True)
            if kind == "file":
                target.unlink()
                target.symlink_to(external)
            else:
                directory = target.parent
                directory.rename(directory.with_name(directory.name + "-retained"))
                directory.symlink_to(external.parent, target_is_directory=True)
        return result

    original_fdopen = os.fdopen
    external_identity = external.stat()
    outside_opens = []

    def checked_fdopen(fd, *args, **kwargs):
        actual = os.fstat(fd)
        if (actual.st_dev, actual.st_ino) == (external_identity.st_dev, external_identity.st_ino):
            outside_opens.append(True)
            pytest.fail("outside-root descriptor reached the content reader")
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(inspection, "_contained", race)
    monkeypatch.setattr(os, "fdopen", checked_fdopen)
    return replaced, outside_opens


@pytest.mark.parametrize("kind", ["file", "ancestor"])
def test_descriptor_reader_rejects_replacement_after_last_path_check(tmp_path, monkeypatch, kind):
    root = tmp_path / "allowed"
    target = root / "handoff" / "ontology.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"safe":true}')
    external = tmp_path / "outside" / target.name
    external.parent.mkdir()
    external.write_text('{"private":"must not read"}')
    replaced, outside_opens = _replace_after_containment(monkeypatch, target, external, kind)

    with pytest.raises((OSError, ValueError)):
        with inspection._open_regular(target, (root.resolve(),)) as handle:
            handle.read()

    assert replaced == [True]
    assert outside_opens == []


@pytest.mark.parametrize("part", ["state", "manifest", "artifact"])
@pytest.mark.parametrize("kind", ["file", "ancestor"])
def test_api_never_consumes_replaced_state_manifest_or_artifact(tmp_path, monkeypatch, part, kind):
    root = tmp_path / "pipelines"
    home = root / "pipe_replacement"
    handoff = home / "handoff"
    handoff.mkdir(parents=True)
    monkeypatch.setattr(Config, "PIPELINE_DATA_DIR", str(root))
    state_path = home / "pipeline_state.json"
    state_path.write_text(json.dumps({
        "pipeline_id": home.name, "schema_version": 2, "status": "completed",
        "handoff_dir": str(handoff), "mode": "full",
        "stages": {s: {"name": s, "status": "completed"} for s in inspection.STAGES},
    }))
    artifact = handoff / "ontology.json"
    artifact.write_text('{"entity_types":[],"edge_types":[]}')
    manifest = handoff / "manifest.json"
    manifest.write_text(json.dumps({"ontology": {"path": str(artifact)}}))
    target = {"state": state_path, "manifest": manifest, "artifact": artifact}[part]
    external = tmp_path / "outside" / target.name
    external.parent.mkdir()
    external.write_text('{"private":"must not read"}')
    replaced, outside_opens = _replace_after_containment(monkeypatch, target, external, kind)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(api.research_bp, url_prefix="/api/research")

    with app.test_client() as client:
        response = client.get(f"/api/research/{home.name}/reuse-plan")

    assert replaced == [True]
    assert outside_opens == []
    if part == "state":
        assert response.status_code == 409
    else:
        assert response.status_code == 200
        rows = {row["stage"]: row for row in response.get_json()["data"]["stages"]}
        assert rows["ontology"]["decision"] == rows["report"]["decision"] == "reject"


def test_in_root_alias_reads_the_pinned_target(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    target = root / "owner.json"
    target.write_text('{"owner":"base"}')
    alias = root / "alias.json"
    alias.symlink_to(target)
    assert inspection.read_observation_json(str(alias), [str(root)]) == {"owner": "base"}


def test_replacement_after_open_is_rejected_without_following_new_target(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    target = root / "artifact.json"
    target.write_bytes(b"original bytes")
    external = tmp_path / "outside.json"
    external.write_bytes(b"outside bytes")
    with pytest.raises(ValueError, match="changed_during_inspection"):
        with inspection._open_regular(target, (root.resolve(),)) as handle:
            target.unlink()
            target.symlink_to(external)
            assert handle.read() == b"original bytes"
