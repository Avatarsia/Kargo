"""Path sanitization and validation utilities"""

import os
from contextlib import contextmanager
from pathlib import Path
from typing import List

# Allowed G-code file extensions
ALLOWED_EXTENSIONS = ['.gcode', '.gcode.gz', '.ufp', '.nc', '.gco']


class InvalidPathError(ValueError):
    """Raised when a path fails validation"""
    pass


def sanitize_path(rel_path: str, allowed_extensions: List[str] = None) -> str:
    """
    Validate and sanitize a relative file path.
    
    Prevents directory traversal attacks and validates file extensions.
    
    Args:
        rel_path: Relative path to validate
        allowed_extensions: List of allowed file extensions (default: ALLOWED_EXTENSIONS)
    
    Returns:
        The validated relative path
    
    Raises:
        InvalidPathError: If path is invalid or unsafe
    
    Examples:
        >>> sanitize_path("test.gcode")
        'test.gcode'
        >>> sanitize_path("/absolute/path.gcode")
        Traceback (most recent call last):
        ...
        InvalidPathError: Absolute paths not allowed
        >>> sanitize_path("../../../etc/passwd")
        Traceback (most recent call last):
        ...
        InvalidPathError: Parent directory traversal not allowed
    """
    if allowed_extensions is None:
        allowed_extensions = ALLOWED_EXTENSIONS
    
    # Check for empty path
    if not rel_path or not rel_path.strip():
        raise InvalidPathError("Path cannot be empty")
    
    # Check for absolute paths
    if rel_path.startswith('/'):
        raise InvalidPathError("Absolute paths not allowed")
    
    # Check for null bytes (security)
    if '\0' in rel_path:
        raise InvalidPathError("Null bytes not allowed in path")

    if any(char in rel_path for char in ('\r', '\n')):
        raise InvalidPathError("Control characters not allowed in path")
    
    # Parse as Path object
    path = Path(rel_path)
    
    # Check for parent directory references (..)
    if '..' in path.parts:
        raise InvalidPathError("Parent directory traversal not allowed")
    
    # Check for hidden files/directories (starting with .)
    # Allow .gcode.gz but not .hidden_folder/
    for part in path.parts[:-1]:  # All parts except filename
        if part.startswith('.'):
            raise InvalidPathError(f"Hidden directories not allowed: {part}")
    
    # Validate file extension
    has_valid_extension = any(rel_path.lower().endswith(ext) for ext in allowed_extensions)
    if not has_valid_extension:
        raise InvalidPathError(
            f"Invalid file extension. Allowed: {', '.join(allowed_extensions)}"
        )
    
    # Return normalized path (resolves ./ and extra slashes)
    return str(path)


def is_safe_filename(filename: str) -> bool:
    """
    Check if filename is safe (no special characters).
    
    Args:
        filename: Filename to check
    
    Returns:
        True if safe, False otherwise
    """
    # Allow alphanumeric, spaces, dashes, underscores, dots
    import re
    pattern = r'^[a-zA-Z0-9\s\-_.]+$'
    return bool(re.match(pattern, filename))


def normalize_path(rel_path: str) -> str:
    """
    Normalize path (remove redundant separators, etc).
    
    Args:
        rel_path: Path to normalize
    
    Returns:
        Normalized path
    """
    return str(Path(rel_path))


@contextmanager
def open_directory(path: Path, create: bool = False):
    """Pin a POSIX directory; never follow symlink components or reopen paths."""
    path = Path(os.path.abspath(str(path)))
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, dir_fd=descriptor)
                except FileExistsError:
                    pass
                os.fsync(descriptor)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def rooted_parent(root: Path, rel_path: str, create: bool = False):
    """Yield a pinned parent and basename beneath a trusted, non-symlink root."""
    path = Path(rel_path)
    if path.is_absolute() or '..' in path.parts or not path.name:
        raise InvalidPathError("Path must stay beneath its root")
    with open_directory(root, create=create) as root_fd:
        descriptor = os.dup(root_fd)
        try:
            for part in path.parts[:-1]:
                if create:
                    try:
                        os.mkdir(part, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    os.fsync(descriptor)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            yield descriptor, path.name
        finally:
            os.close(descriptor)


def relative_to_root(path: Path, root: Path) -> str:
    """Bound an untrusted absolute reference lexically; opening stays no-follow."""
    root = Path(os.path.abspath(str(root)))
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise InvalidPathError("Reference must be an absolute path beneath its root")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise InvalidPathError("Reference leaves its root") from exc
    if not relative.name:
        raise InvalidPathError("Reference must name a file")
    return str(relative)
