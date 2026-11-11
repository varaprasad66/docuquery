"""Rehearse migrations on a newly created local database, never an existing one."""
import os
from pathlib import Path
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql

backend = Path(__file__).resolve().parents[1] / "backend"
name = "docuquery_migration_test_" + uuid.uuid4().hex[:12]
server = "postgresql://docuquery:docuquery@localhost:5433/postgres"
with psycopg.connect(server, autocommit=True) as connection:
    connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        env = {**os.environ, "DATABASE_URL": f"postgresql+psycopg://docuquery:docuquery@localhost:5433/{name}", "MIGRATION_DATABASE_URL": "", "ENVIRONMENT": "local"}
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=backend, env=env, check=True)
        with psycopg.connect(server.rsplit("/", 1)[0] + "/" + name) as test_db:
            version = test_db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            assert version == "0008", version
            assert test_db.execute("SELECT to_regclass('storage_cleanup')").fetchone()[0]
            assert test_db.execute("SELECT to_regclass('ix_chunks_embedding')").fetchone()[0]
            assert test_db.execute("SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid='chunks'::regclass AND attname='embedding'").fetchone()[0] == "vector(768)"
        print("PASS: fresh Alembic migration reaches 0008; cleanup table and vector index verified")
    finally:
        connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
