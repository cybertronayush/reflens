"""Regression: stale temp-build cleanup must not delete a concurrent ingest's
live build dir. Only dead-pid leftovers from interrupted ingests are reaped.
"""

from __future__ import annotations

import os

from reflens.ingest.base import _reap_stale_workdirs


def test_reap_skips_live_pid_and_unparseable(tmp_path):
    repo = "demo"
    prefix = f".reflens-tmp-{repo}-"
    live = tmp_path / f"{prefix}{os.getpid()}"     # our own / a concurrent ingest
    dead = tmp_path / f"{prefix}999999"            # almost certainly not running
    weird = tmp_path / f"{prefix}notapid"          # unrecognized suffix
    other = tmp_path / ".reflens-tmp-otherrepo-1"  # different repo
    for d in (live, dead, weird, other):
        d.mkdir()

    _reap_stale_workdirs(tmp_path, repo)

    assert live.exists(), "must never delete a live pid's build dir"
    assert weird.exists(), "unparseable suffix must be left untouched"
    assert other.exists(), "a different repo's dir must be untouched"
    assert not dead.exists(), "a dead pid's leftover should be reaped"
