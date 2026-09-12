#!/usr/bin/env python3
"""Phoenix lab backup (method A) — runs INSIDE the cmod-phoenix container.

Produces in /tmp:
- phoenix-sqlite-<ver>.db       consistent single-file dump of the live SQLite
                                DB via the sqlite3 .backup API (handles WAL)
- cmod-phoenix-backup-<ver>.tgz full archive of the /phoenix/data volume
                                (db + wal/shm + inferences/ + trace_datasets/ + wasm/)

docker cp both out to ./sidecar afterwards. Hot backup: safe for the idle
lab; stop cmod-phoenix first for a guaranteed-cold tar.
"""

import os
import sqlite3
import sys
import tarfile

DATA = "/phoenix/data"
VER = "20.7"
DUMP = f"/tmp/phoenix-sqlite-{VER}.db"
TGZ = f"/tmp/cmod-phoenix-backup-{VER}.tgz"

src = sqlite3.connect(os.path.join(DATA, "phoenix.db"))
dst = sqlite3.connect(DUMP)
with dst:
    src.backup(dst)
dst.close()
src.close()
print(f"dump {DUMP} {os.path.getsize(DUMP)}")

with tarfile.open(TGZ, "w:gz") as tf:
    tf.add(DATA, arcname=".")
print(f"tgz {TGZ} {os.path.getsize(TGZ)}")