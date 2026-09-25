"""Run with python -m tests.run_file_scope_gate0_postgres.

Owned loopback PostgreSQL only. No inherited database, no SQLite, no silent skips.
BEN_TEST_PG_BIN locates native binaries; BEN_TEST_PG_ROOT locates scratch space.
"""
import os

from tests.disposable_postgres import DisposablePostgres, ROOT


class NoSkippedTests:
    def __init__(self):
        self.skipped = []

    def pytest_runtest_logreport(self, report):
        if report.skipped and not getattr(report, "wasxfail", False):
            self.skipped.append(report.nodeid)

    def pytest_collectreport(self, report):
        if report.skipped:
            self.skipped.append(report.nodeid)


def main():
    cluster = DisposablePostgres()
    try:
        cluster.start()
        cluster.create_database("file_scope_gate0")
        cluster.alembic("file_scope_gate0", "upgrade", "head")
        cluster.create_database("media_v1_test_gate0")
        os.environ["DATABASE_URL"] = cluster.alembic_url("file_scope_gate0")
        os.environ["BEN_TEST_PG_DSN"] = os.environ["DATABASE_URL"].replace("postgresql+asyncpg:", "postgresql:", 1)
        os.environ["MEDIA_TEST_DATABASE_URL"] = cluster.alembic_url("media_v1_test_gate0").replace("postgresql+asyncpg:", "postgresql:", 1)
        os.chdir(ROOT)
        import pytest
        coverage = NoSkippedTests()
        result = pytest.main([
            "-q", "-p", "no:cacheprovider", "-rx",
            "--basetemp=" + str(cluster.run_dir / "pytest"),
            "tests/test_file_initial_read_jobs_postgres.py",
            "tests/test_document_processing_runner.py",
            "tests/test_media_repository.py",
            "tests/test_file_scope_v2_gate0.py",
            "tests/test_workspace_files_v1.py",
            "tests/test_project_auth.py",
            "tests/test_project_library.py",
            "tests/test_security_gate_a.py",
        ], plugins=[coverage])
        if coverage.skipped:
            print("Gate 0 invalid: unexpected skipped tests:", coverage.skipped)
            return 1
        return int(result)
    finally:
        cluster.cleanup()
        print("Owned cluster cleanup:", cluster.shutdown_confirmed, cluster.deleted)


if __name__ == "__main__":
    raise SystemExit(main())
