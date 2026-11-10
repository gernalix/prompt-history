"""Confined reads and private file creation for the local warehouse."""
from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def open_member(root: Path, path: Path):
    relative = path.relative_to(root)
    if not relative.parts or ".." in relative.parts:
        raise ValueError("invalid archive member path")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = os.open(root, directory_flags)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=directory)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("archive member is not a regular file")
            yield handle
    finally:
        os.close(directory)


def file_stamp(handle) -> str:
    info = os.fstat(handle.fileno())
    return json.dumps([[info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                        info.st_ctime_ns]], separators=(",", ":"))


def member_stamp(root: Path, path: Path) -> str:
    try:
        with open_member(root, path) as handle:
            return file_stamp(handle)
    except FileNotFoundError:
        return "[null]"


@contextmanager
def private_directory(path: Path):
    path = path.absolute()
    if ".." in path.parts:
        raise ValueError("parent traversal is not allowed")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = os.open("/", flags)
    try:
        for depth, part in enumerate(path.parts[1:]):
            info = os.fstat(directory)
            if ((depth and info.st_uid not in (0, os.geteuid()))
                    or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)):
                raise PermissionError("untrusted ancestor of warehouse directory")
            try:
                os.mkdir(part, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise PermissionError("warehouse directory must be owned by the user and protected against other writers")
        yield directory
    finally:
        os.close(directory)


def ensure_private_database(path: Path) -> None:
    with private_directory(path.parent) as directory:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        for suffix in ("-wal", "-shm", "-journal", ""):
            try:
                fd = os.open(path.name + suffix, flags | (0 if suffix else os.O_CREAT),
                             0o600, dir_fd=directory)
            except FileNotFoundError:
                if not suffix:
                    raise
                continue
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
                    raise PermissionError("warehouse file must be an owned regular file")
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
