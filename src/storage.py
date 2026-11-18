"""Versioned local state; replace files atomically after flushing their contents."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID


def normalize_database_id(database_id: str) -> str:
    value = database_id.strip()
    try:
        return str(UUID(value))
    except ValueError:
        return value


def identity(database_id: str, name: str) -> str:
    return json.dumps([normalize_database_id(database_id), name], ensure_ascii=False)


def read_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return None


def fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _mkdir_durable(directory: Path) -> None:
    try:
        directory.mkdir()
    except FileNotFoundError:
        _mkdir_durable(directory.parent)
        _mkdir_durable(directory)
    except FileExistsError:
        if not directory.is_dir():
            raise
    else:
        fsync_directory(directory.parent)


def write_json(path: str, value: Any, *, durable: bool = False) -> None:
    destination = Path(path)
    if durable:
        _mkdir_durable(destination.parent)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False
        ) as file:
            temporary = file.name
            json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, destination)
        temporary = None
        if durable:
            fsync_directory(destination.parent)
    finally:
        if temporary is not None:
            os.unlink(temporary)
