"""Test-only native PostgreSQL 16 lifecycle. Not imported by runtime paths."""
from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

BIN = Path("/usr/lib/postgresql/16/bin")
INITDB = BIN / "initdb"
PG_CTL = BIN / "pg_ctl"
PSQL = BIN / "psql"
ALEMBIC_INI = Path("/workspace/database/migrations/alembic.ini")
START_WAIT = 30
STOP_WAIT = 30

_INHERITED = (
    "DATABASE_URL",
    "PGHOST",
    "PGPORT",
    "PGUSER",
    "PGPASSWORD",
    "PGDATABASE",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGPASSFILE",
)


class ClusterError(RuntimeError):
    pass


def _redact(text: str, secrets_list: list[str]) -> str:
    out = text
    for s in secrets_list:
        if s:
            out = out.replace(s, "<redacted>")
    return out


def _pick_port() -> int:
    for port in range(55450, 55650):
        s = socket.socket()
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            s.close()
    raise ClusterError("no available non-default port")


def _run(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 120,
    check: bool = True,
    secrets_list: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(
        args,
        env=env,
        timeout=timeout,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and cp.returncode != 0:
        err = _redact((cp.stderr or "") + "\n" + (cp.stdout or ""), secrets_list or [])
        raise ClusterError(f"command failed rc={cp.returncode} argv0={args[0]}: {err[:2000]}")
    return cp


class DisposablePostgres:
    """One owned cluster; multiple databases. Credentials never printed."""

    def __init__(self) -> None:
        if os.geteuid() == 0:
            raise ClusterError("refusing to run PostgreSQL as root")
        self.run_id = secrets.token_hex(8)
        self.run_dir = Path(f"/tmp/mig-baseline-{self.run_id}")
        self.pgdata = self.run_dir / "pgdata"
        self.sockdir = self.run_dir / "sock"
        self.log_path = self.run_dir / "pg.log"
        self.pwfile = self.run_dir / "pwfile"
        self.pgpass = self.run_dir / "pgpass"
        self.role = f"mbr_{self.run_id[:8]}"
        self._password = ""
        self.port: int | None = None
        self.started = False
        self.server_pid: int | None = None
        self.shutdown_confirmed = False
        self.deleted = False
        self._cleanup_registered = False

    def _secrets(self) -> list[str]:
        return [self._password] if self._password else []

    def _psql_env(self) -> dict[str, str]:
        env = os.environ.copy()
        for k in _INHERITED:
            env.pop(k, None)
        env["PGPASSFILE"] = str(self.pgpass)
        env["PGSSLMODE"] = "disable"
        return env

    def alembic_url(self, dbname: str) -> str:
        assert self.port is not None
        return (
            f"postgresql+asyncpg://{quote_plus(self.role)}:{quote_plus(self._password)}"
            f"@127.0.0.1:{self.port}/{dbname}"
        )

    def asyncpg_kwargs(self, dbname: str) -> dict[str, Any]:
        assert self.port is not None
        return {
            "host": "127.0.0.1",
            "port": self.port,
            "user": self.role,
            "password": self._password,
            "database": dbname,
            "ssl": False,
            "timeout": 10,
        }

    def start(self) -> None:
        self.run_dir.mkdir(mode=0o700)
        os.chmod(self.run_dir, 0o700)
        self.sockdir.mkdir(mode=0o700)
        self._cleanup_registered = True
        self.port = _pick_port()
        self._password = secrets.token_urlsafe(32)
        self.pwfile.write_text(self._password + "\n", encoding="utf-8")
        os.chmod(self.pwfile, 0o600)
        self.pgpass.write_text(
            f"127.0.0.1:{self.port}:*:{self.role}:{self._password}\n",
            encoding="utf-8",
        )
        os.chmod(self.pgpass, 0o600)
        _run(
            [
                str(INITDB),
                "--pgdata",
                str(self.pgdata),
                "--username",
                self.role,
                "--pwfile",
                str(self.pwfile),
                "--auth-local=scram-sha-256",
                "--auth-host=scram-sha-256",
                "--encoding=UTF8",
                "--locale=C.utf8",
                "--no-instructions",
            ],
            secrets_list=self._secrets(),
        )
        with (self.pgdata / "postgresql.conf").open("a", encoding="utf-8") as f:
            f.write(
                "\nlisten_addresses = '127.0.0.1'\n"
                f"port = {self.port}\n"
                f"unix_socket_directories = '{self.sockdir}'\n"
                "unix_socket_permissions = 0700\n"
                "password_encryption = scram-sha-256\n"
                "ssl = off\n"
            )
        _run(
            [
                str(PG_CTL),
                "-D",
                str(self.pgdata),
                "-l",
                str(self.log_path),
                "-w",
                "-t",
                str(START_WAIT),
                "start",
                "-o",
                f"-h 127.0.0.1 -p {self.port} -k {self.sockdir}",
            ],
            secrets_list=self._secrets(),
            timeout=START_WAIT + 15,
        )
        self.started = True
        self.server_pid = int((self.pgdata / "postmaster.pid").read_text().splitlines()[0])
        self.verify_identity("postgres")

    def psql(self, dbname: str, sql: str) -> str:
        assert self.port is not None
        cp = _run(
            [
                str(PSQL),
                "-h",
                "127.0.0.1",
                "-p",
                str(self.port),
                "-U",
                self.role,
                "-d",
                dbname,
                "-v",
                "ON_ERROR_STOP=1",
                "-A",
                "-t",
                "-c",
                sql,
            ],
            env=self._psql_env(),
            secrets_list=self._secrets(),
        )
        return cp.stdout

    def verify_identity(self, dbname: str) -> None:
        db = self.psql(dbname, "SELECT current_database();").strip()
        user = self.psql(dbname, "SELECT current_user;").strip()
        data_dir = self.psql(dbname, "SHOW data_directory;").strip()
        if db != dbname:
            raise ClusterError(f"current_database mismatch: {db!r} != {dbname!r}")
        if user != self.role:
            raise ClusterError(f"current_user mismatch: {user!r}")
        if Path(data_dir).resolve() != self.pgdata.resolve():
            raise ClusterError("data_directory is not this run's owned cluster")

    def create_database(self, dbname: str) -> None:
        self.verify_identity("postgres")
        self.psql("postgres", f"CREATE DATABASE {dbname};")
        self.verify_identity(dbname)

    def alembic(self, dbname: str, *args: str) -> None:
        self.verify_identity(dbname)
        env = os.environ.copy()
        for k in _INHERITED:
            env.pop(k, None)
        env["DATABASE_URL"] = self.alembic_url(dbname)
        env["PYTHONPATH"] = "/workspace"
        env["PGSSLMODE"] = "disable"
        _run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(ALEMBIC_INI),
                *args,
            ],
            env=env,
            timeout=180,
            secrets_list=self._secrets(),
        )

    def cleanup(self) -> None:
        if not self._cleanup_registered:
            return
        if self.started and self.pgdata.exists():
            _run(
                [
                    str(PG_CTL),
                    "-D",
                    str(self.pgdata),
                    "-w",
                    "-t",
                    str(STOP_WAIT),
                    "stop",
                    "-m",
                    "fast",
                ],
                check=False,
                timeout=STOP_WAIT + 15,
                secrets_list=self._secrets(),
            )
            deadline = time.time() + STOP_WAIT
            while time.time() < deadline:
                if not (self.pgdata / "postmaster.pid").exists():
                    self.shutdown_confirmed = True
                    break
                time.sleep(0.2)
        else:
            self.shutdown_confirmed = True
        if not self.shutdown_confirmed:
            raise ClusterError(
                f"owned server shutdown not confirmed; retaining {self.run_dir}"
            )
        expected = Path(f"/tmp/mig-baseline-{self.run_id}")
        if self.run_dir != expected or not str(self.run_dir).startswith("/tmp/mig-baseline-"):
            raise ClusterError("refusing to delete unverified path")
        if self.run_dir.exists():
            shutil.rmtree(self.run_dir)
        if self.run_dir.exists():
            raise ClusterError("run directory still present after delete")
        self.deleted = True
