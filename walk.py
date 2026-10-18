#!/usr/bin/env python3
"""
media-index — metadata-only filesystem walk into SQLite.

STRICTLY READ-ONLY. No hashing, no file content is opened, nothing is moved,
renamed or deleted. Only directory metadata (name, size, mtime) is read.

Usage:
    ./walk.py <db> [--config=<file>] [--exclude=<path> ...] <label>:<path> [<label>:<path> ...]

--config names a JSON file whose "skip_paths" list is added to SKIP_PATHS. With
no --config, media-index.local.json next to this script is used if it exists.
See media-index.example.json.

--exclude marks a subtree as DELIBERATELY NOT MEASURED. Unlike the SKIP_* lists
below, it is not even summarised — summarising still stat()s every file, which
is the expensive part on a spinning disk. Excluded trees get a `skipped` row
with files/bytes of -1 meaning "unknown", so a later query can tell
"not measured" apart from "not present". Use it for things like an ebook
backup: ~500k tiny files whose count, not size, is the cost.

Each root is walked depth-first with os.scandir (no symlink following, no
crossing into other filesystems). Rows are batched into SQLite.

Directories in SKIP_* are not indexed file-by-file; instead one row is written
to `skipped` recording their existence, recursive file count and total size, so
"what's in there" is still answerable without doubling the index.
"""

import json
import os
import sqlite3
import sys
import time

BATCH = 20000

# Absolute paths whose contents are summarised, not indexed. Filled from the
# config file's "skip_paths" (e.g. ~/.cache, a games library, an old backup).
SKIP_PATHS = set()

LOCAL_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media-index.local.json")


def load_config(path=None):
    """Read the JSON config: the --config file, else media-index.local.json beside
    this script if present, else nothing."""
    path = path or (LOCAL_CONFIG if os.path.exists(LOCAL_CONFIG) else None)
    if not path:
        return {}
    with open(os.path.expanduser(path)) as f:
        return json.load(f)

# Directory basenames whose contents are summarised, not indexed, anywhere.
SKIP_NAMES = {".git", "node_modules", "__pycache__", ".venv", ".npm", ".cargo"}

# Populated from --exclude. Not descended into, not even counted.
EXCLUDE_PATHS = set()

SCHEMA = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;
PRAGMA temp_store = MEMORY;
PRAGMA cache_size = -200000;

CREATE TABLE IF NOT EXISTS files (
    drive  TEXT NOT NULL,
    path   TEXT NOT NULL,   -- full absolute path
    parent TEXT NOT NULL,   -- containing directory
    name   TEXT NOT NULL,
    ext    TEXT,            -- lowercase, no dot; '' if none
    size   INTEGER NOT NULL,
    mtime  INTEGER NOT NULL,
    depth  INTEGER NOT NULL -- depth below the walk root
);

CREATE TABLE IF NOT EXISTS dirs (
    drive  TEXT NOT NULL,
    path   TEXT NOT NULL,
    name   TEXT NOT NULL,
    depth  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS skipped (
    drive  TEXT NOT NULL,
    path   TEXT NOT NULL,
    reason TEXT NOT NULL,
    files  INTEGER NOT NULL,
    bytes  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS errors (
    drive TEXT NOT NULL,
    path  TEXT NOT NULL,
    error TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    drive    TEXT NOT NULL,
    root     TEXT NOT NULL,
    started  INTEGER NOT NULL,
    finished INTEGER,
    files    INTEGER,
    dirs     INTEGER,
    bytes    INTEGER,
    errors   INTEGER
);
"""


def summarise(path, dev):
    """Recursive file count + byte total for a skipped subtree. Metadata only."""
    files = total = 0
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        st = e.stat(follow_symlinks=False)
                        if st.st_dev != dev:
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            files += 1
                            total += st.st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return files, total


def walk(conn, drive, root):
    root = os.path.abspath(root)
    try:
        dev = os.stat(root).st_dev
    except OSError as exc:
        print(f"!! {drive}: cannot stat {root}: {exc}", flush=True)
        return

    started = int(time.time())
    conn.execute(
        "INSERT INTO runs (drive, root, started) VALUES (?,?,?)", (drive, root, started)
    )
    conn.commit()

    fbuf, dbuf = [], []
    nfiles = ndirs = nbytes = nerr = 0
    rootdepth = root.rstrip("/").count("/")
    stack = [root]

    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        # Never follow symlinks: ~/media -> /mnt/disk1/media
                        # would otherwise be walked twice.
                        if e.is_symlink():
                            continue
                        st = e.stat(follow_symlinks=False)
                        if st.st_dev != dev:
                            continue  # don't cross filesystem boundaries

                        depth = e.path.rstrip("/").count("/") - rootdepth

                        if e.is_dir(follow_symlinks=False):
                            if e.path in EXCLUDE_PATHS:
                                # -1/-1 = deliberately not measured, not empty.
                                conn.execute(
                                    "INSERT INTO skipped VALUES (?,?,?,?,?)",
                                    (drive, e.path, "excluded-not-measured", -1, -1),
                                )
                                print(f"   {drive}: EXCLUDED (not measured): {e.path}", flush=True)
                                continue
                            if e.path in SKIP_PATHS or e.name in SKIP_NAMES:
                                sf, sb = summarise(e.path, dev)
                                reason = (
                                    "skip-path" if e.path in SKIP_PATHS else "skip-name"
                                )
                                conn.execute(
                                    "INSERT INTO skipped VALUES (?,?,?,?,?)",
                                    (drive, e.path, reason, sf, sb),
                                )
                                continue
                            ndirs += 1
                            dbuf.append((drive, e.path, e.name, depth))
                            stack.append(e.path)
                        else:
                            ext = os.path.splitext(e.name)[1].lstrip(".").lower()
                            nfiles += 1
                            nbytes += st.st_size
                            fbuf.append(
                                (
                                    drive,
                                    e.path,
                                    cur,
                                    e.name,
                                    ext,
                                    st.st_size,
                                    int(st.st_mtime),
                                    depth,
                                )
                            )
                    except OSError as exc:
                        nerr += 1
                        conn.execute(
                            "INSERT INTO errors VALUES (?,?,?)",
                            (drive, getattr(e, "path", cur), str(exc)),
                        )

            if len(fbuf) >= BATCH:
                conn.executemany(
                    "INSERT INTO files VALUES (?,?,?,?,?,?,?,?)", fbuf
                )
                conn.executemany("INSERT INTO dirs VALUES (?,?,?,?)", dbuf)
                conn.commit()
                fbuf, dbuf = [], []
                print(
                    f"   {drive}: {nfiles:,} files  {nbytes / 1e12:.2f} TB  ({cur[:70]})",
                    flush=True,
                )
        except OSError as exc:
            nerr += 1
            conn.execute("INSERT INTO errors VALUES (?,?,?)", (drive, cur, str(exc)))

    if fbuf:
        conn.executemany("INSERT INTO files VALUES (?,?,?,?,?,?,?,?)", fbuf)
    if dbuf:
        conn.executemany("INSERT INTO dirs VALUES (?,?,?,?)", dbuf)

    finished = int(time.time())
    conn.execute(
        "UPDATE runs SET finished=?, files=?, dirs=?, bytes=?, errors=? "
        "WHERE drive=? AND root=? AND started=?",
        (finished, nfiles, ndirs, nbytes, nerr, drive, root, started),
    )
    conn.commit()
    print(
        f"== {drive}: {nfiles:,} files, {ndirs:,} dirs, "
        f"{nbytes / 1e12:.3f} TB, {nerr} errors, {finished - started}s",
        flush=True,
    )


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    db = sys.argv[1]
    specs = []
    config_path = None
    for a in sys.argv[2:]:
        if a.startswith("--exclude="):
            EXCLUDE_PATHS.add(os.path.abspath(a.split("=", 1)[1].rstrip("/")))
        elif a.startswith("--config="):
            config_path = a.split("=", 1)[1]
        else:
            specs.append(a)
    config = load_config(config_path)
    SKIP_PATHS.update(os.path.abspath(os.path.expanduser(p).rstrip("/")) for p in config.get("skip_paths", []))
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    for spec in specs:
        drive, _, path = spec.partition(":")
        print(f"-> {drive}  {path}", flush=True)
        walk(conn, drive, path)
    print("indexing...", flush=True)
    conn.executescript(
        "CREATE INDEX IF NOT EXISTS i_files_drive ON files(drive);"
        "CREATE INDEX IF NOT EXISTS i_files_ext   ON files(ext);"
        "CREATE INDEX IF NOT EXISTS i_files_size  ON files(size);"
        "CREATE INDEX IF NOT EXISTS i_files_name  ON files(name);"
        "CREATE INDEX IF NOT EXISTS i_files_par   ON files(parent);"
        "CREATE INDEX IF NOT EXISTS i_dirs_path   ON dirs(path);"
    )
    conn.commit()
    conn.close()
    print("done", flush=True)


if __name__ == "__main__":
    main()
