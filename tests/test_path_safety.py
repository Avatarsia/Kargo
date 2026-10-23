"""Untrusted API filenames must be safe POSIX relative names."""

import pytest

from scripts.utils.path_utils import InvalidPathError, sanitize_path


@pytest.mark.parametrize('name', ['part\n.gcode', 'part\r.gcode'])
def test_rejects_control_characters(name):
    with pytest.raises(InvalidPathError):
        sanitize_path(name)


def test_preserves_literal_posix_backslash():
    assert sanitize_path('part\\name.gcode') == 'part\\name.gcode'
