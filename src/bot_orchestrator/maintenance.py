"""Housekeeping: the local Bot API file cache and disk space."""

from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

# The Bot API server keeps one folder per bot token: <data dir>/<token>/.
# Its own state (td.binlog, databases) sits directly in that folder; downloaded files live
# in subfolders (documents, photos, videos, ...). Only files in subfolders are removed.
_TOKEN_DIR = re.compile(r"^\d+:[A-Za-z0-9_-]+$")


@dataclass
class CacheStats:
    files: int = 0
    bytes: int = 0


def _cached_files(data_dir: Path):
    if not data_dir.is_dir():
        return
    for token_dir in data_dir.iterdir():
        if not token_dir.is_dir() or not _TOKEN_DIR.match(token_dir.name):
            continue
        for sub in token_dir.iterdir():
            if not sub.is_dir():
                continue
            for root, _dirs, files in os.walk(sub):
                for name in files:
                    yield Path(root) / name


def cache_usage(data_dir: str | Path) -> CacheStats:
    stats = CacheStats()
    for path in _cached_files(Path(data_dir)):
        try:
            stats.bytes += path.stat().st_size
            stats.files += 1
        except OSError:
            pass
    return stats


def clean_cache(data_dir: str | Path, older_than_hours: float) -> CacheStats:
    """Delete downloaded files older than the given age. Returns what was removed."""
    removed = CacheStats()
    cutoff = time.time() - older_than_hours * 3600
    for path in _cached_files(Path(data_dir)):
        try:
            stat = path.stat()
            if stat.st_mtime < cutoff:
                path.unlink()
                removed.files += 1
                removed.bytes += stat.st_size
        except OSError:
            pass
    return removed


def disk_percent_used(path: str | Path) -> float | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return usage.used / usage.total * 100 if usage.total else None


def folder_size(path: str | Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total
