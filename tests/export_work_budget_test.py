"""Synthetic collisions and byte streams cannot amplify batch work forever."""

import os
import re
from pathlib import Path

import pytest

from kptncook import exporter_utils
from kptncook.exporter_utils import move_to_free_target
from kptncook.http_errors import UserFacingError


@pytest.mark.parametrize("fallback", [False, True])
def test_collision_work_is_bounded(tmp_path, monkeypatch, fallback):
    source = tmp_path / "source"
    source.write_bytes(b"synthetic archive")
    out = tmp_path / "out"
    out.mkdir()
    old = out / "existing.zip"
    old.write_bytes(b"keep")
    link = os.link
    attempts = []

    def collisions(src, dst):
        attempts.append(Path(dst))
        assert len(attempts) <= 20, "Publication exceeded its collision work budget"
        if fallback and re.search(r"-[0-9a-f]{16}\.zip$", str(dst)):
            link(src, dst)
        else:
            raise FileExistsError

    monkeypatch.setattr(os, "link", collisions)
    if fallback:
        target = move_to_free_target(source, out, "食" * 240, ".zip", "synthetic-id")
        assert target.read_bytes() == b"synthetic archive"
        assert len(target.name.encode()) <= 240
        assert len(attempts) == 17
        assert not source.exists()
        assert sorted(out.iterdir()) == sorted([old, target])
    else:
        with pytest.raises(UserFacingError, match="collision"):
            move_to_free_target(source, out, "name", ".zip", "synthetic-id")
        assert len(attempts) == 20
        assert source.exists()
        assert list(out.iterdir()) == [old]
    assert old.read_bytes() == b"keep"


def test_random_fallback_retains_uncertain_publication(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_bytes(b"complete")
    link = os.link
    attempts = []

    def collisions(src, dst):
        attempts.append(Path(dst))
        assert len(attempts) <= 20
        if len(attempts) <= 16:
            raise FileExistsError
        link(src, dst)

    def refuse_sync(directory):
        raise OSError("synthetic sync failure")

    monkeypatch.setattr(os, "link", collisions)
    monkeypatch.setattr(exporter_utils, "fsync_directory", refuse_sync)
    with pytest.raises(exporter_utils.ExportPublicationUncertain) as caught:
        move_to_free_target(source, tmp_path, "name", ".zip")
    assert caught.value.target == attempts[-1]
    assert caught.value.target.read_bytes() == b"complete"
    assert not list(tmp_path.glob(".kptncook-*"))


@pytest.mark.parametrize("allowance", [5, 6])
def test_publication_copy_consumes_output_allowance(tmp_path, monkeypatch, allowance):
    source = tmp_path / "source"
    source.write_bytes(b"abcdef")
    out = tmp_path / "out"
    out.mkdir()
    sentinel = out / "previous.zip"
    sentinel.write_bytes(b"keep")
    monkeypatch.setattr(exporter_utils, "MAX_BATCH_OUTPUT_BYTES", allowance)
    budget = exporter_utils.ExportBudget()
    if allowance == 5:
        with pytest.raises(UserFacingError, match="output.*allowance"):
            move_to_free_target(source, out, "name", ".zip", budget=budget)
        assert source.exists()
        assert list(out.iterdir()) == [sentinel]
    else:
        target = move_to_free_target(source, out, "name", ".zip", budget=budget)
        assert target.read_bytes() == b"abcdef"
        assert budget.output_bytes == 6
        assert sorted(out.iterdir()) == sorted([sentinel, target])
    assert sentinel.read_bytes() == b"keep"
