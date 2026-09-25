"""Failure containment of the test helper; not substitutes for live DB tests."""
import os
from types import SimpleNamespace

import pytest

from tests import disposable_postgres as pg
from tests.run_file_scope_gate0_postgres import NoSkippedTests


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    monkeypatch.setenv("BEN_TEST_PG_ROOT", str(tmp_path))
    if hasattr(os, "geteuid"):
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
    instance = pg.DisposablePostgres()
    instance.run_dir.mkdir()
    instance.pgdata.mkdir()
    instance._cleanup_registered = True
    return instance


def test_partial_start_stops_server_before_cleanup(cluster, monkeypatch):
    pid = cluster.pgdata / "postmaster.pid"
    pid.write_text("123")
    def stop(args, **kwargs):
        assert "stop" in args
        assert cluster.pgdata.exists()
        pid.unlink()
    monkeypatch.setattr(pg, "_run", stop)
    cluster.cleanup()
    assert cluster.shutdown_confirmed and cluster.deleted


def test_failed_stop_retains_data(cluster, monkeypatch):
    (cluster.pgdata / "postmaster.pid").write_text("123")
    monkeypatch.setattr(pg, "_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(pg, "STOP_WAIT", 0)
    with pytest.raises(pg.ClusterError, match="shutdown not confirmed"):
        cluster.cleanup()
    assert cluster.pgdata.exists()


def test_cleanup_rejects_changed_target(cluster, tmp_path):
    unexpected = tmp_path / "unrelated"
    unexpected.mkdir()
    cluster.run_dir = unexpected
    with pytest.raises(pg.ClusterError, match="unverified path"):
        cluster.cleanup()
    assert unexpected.exists()


def test_skip_guard_keeps_security_xfails_distinct():
    guard = NoSkippedTests()
    guard.pytest_runtest_logreport(SimpleNamespace(skipped=True, wasxfail="security RED", nodeid="red"))
    guard.pytest_runtest_logreport(SimpleNamespace(skipped=True, nodeid="missing database"))
    guard.pytest_collectreport(SimpleNamespace(skipped=True, nodeid="missing driver"))
    assert guard.skipped == ["missing database", "missing driver"]
