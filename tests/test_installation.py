"""Run extracted installer functions only, in a temporary Linux filesystem."""
import configparser
import os
from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLERS = ["install.sh", "install_de.sh"]


def run_functions(installer, commands, env, prelude=""):
    source = (ROOT / installer).read_text(encoding="utf-8")
    functions = re.findall(r"^\w+\(\) \{\n.*?^\}", source, re.M | re.S)
    # Never source an installer or allow sudo/service/mount side effects.
    guards = """
sudo() { echo 'unexpected sudo' >&2; return 99; }
systemctl() { return 99; }
mount() { return 99; }
umount() { return 99; }
mountpoint() { return 0; }
"""
    return subprocess.run(
        ["bash", "-c", "set -e\n" + "\n".join(functions) + guards + prelude + commands],
        env=dict(os.environ, **env), capture_output=True, text=True,
    )


@pytest.mark.parametrize("installer", INSTALLERS)
def test_connection_probe_preserves_predictable_symlink_targets(installer, tmp_path):
    nas = tmp_path / "nas with spaces"
    nas.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("ORIGINAL")
    for name in (".kargo_test", ".nas_offload_install_test"):
        (nas / name).symlink_to(outside)
    result = run_functions(installer, 'test_nas "$NAS"', {"NAS": str(nas)})
    assert result.returncode == 0, result.stderr
    assert outside.read_text() == "ORIGINAL"
    assert sorted(p.name for p in nas.iterdir()) == [".kargo_test", ".nas_offload_install_test"]
    assert all(p.is_symlink() for p in nas.iterdir())


@pytest.mark.parametrize("installer", INSTALLERS)
def test_write_probe_reports_service_user_permission_failure(installer, tmp_path):
    nas = tmp_path / "readonly"
    nas.mkdir(mode=0o500)
    try:
        result = run_functions(installer, 'test_nas "$NAS"', {"NAS": str(nas)})
        assert result.returncode != 0
        assert list(nas.iterdir()) == []
        assert "unexpected sudo" not in result.stderr
    finally:
        nas.chmod(0o700)


@pytest.mark.parametrize("installer", INSTALLERS)
def test_mount_probe_uses_same_safe_user_write_path(installer, tmp_path):
    source = (ROOT / installer).read_text(encoding="utf-8")
    # Execute only the mount wizard's write-permission condition, never its mount/fstab code.
    condition = re.search(r'^        if (.*\$NAS_MOUNT_LOCAL.*); then\n            print_success', source, re.M)
    assert condition is not None
    outside = tmp_path / "outside"
    outside.write_text("ORIGINAL")
    nas = tmp_path / "nas"
    nas.mkdir()
    (nas / ".kargo_test").symlink_to(outside)
    result = run_functions(installer, condition.group(1), {"NAS_MOUNT_LOCAL": str(nas)})
    assert result.returncode == 0, result.stderr
    assert outside.read_text() == "ORIGINAL"
    assert (nas / ".kargo_test").is_symlink()
    assert len(list(nas.iterdir())) == 1


@pytest.mark.parametrize("installer", INSTALLERS)
def test_random_probe_never_follows_a_colliding_symlink(installer, tmp_path):
    nas = tmp_path / "nas"
    nas.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("ORIGINAL")
    (nas / ".kargo-test-collision").symlink_to(outside)
    result = run_functions(
        installer, 'test_nas_write "$NAS"', {"NAS": str(nas)}, prelude="""
python3() {
    local program
    program=$(cat)
    shift
    command python3 -c 'import secrets, sys; secrets.token_hex = lambda *args: "collision"; program = sys.argv.pop(1); exec(program)' "$program" "$@"
}
""",
    )
    assert result.returncode != 0
    assert "command not found" not in result.stderr
    assert outside.read_text() == "ORIGINAL"
    assert (nas / ".kargo-test-collision").is_symlink()
    assert len(list(nas.iterdir())) == 1


@pytest.mark.parametrize("installer", INSTALLERS)
def test_install_uses_detected_data_root_and_preserves_moonraker_init(installer, tmp_path):
    data = tmp_path / "custom printer data"
    config = data / "config"
    config.mkdir(parents=True)
    components = tmp_path / "moonraker" / "components"
    components.mkdir(parents=True)
    (components / "__init__.py").write_text("MOONRAKER ORIGINAL")
    checkout = tmp_path / "checkout"
    (checkout / "scripts" / "utils").mkdir(parents=True)
    (checkout / "scripts" / "__init__.py").write_text("DO NOT COPY")
    (checkout / "scripts" / "nas_offload.py").write_text("COMPONENT")
    (checkout / "scripts" / "utils" / "__init__.py").write_text("UTILS")
    (checkout / "klipper_config").mkdir()
    (checkout / "klipper_config" / "nas_offload.cfg").write_text("MACROS")
    result = run_functions(
        installer, "do_install", {
            "PRINTER_DATA_DIR": str(data), "KLIPPER_CONFIG_DIR": str(config),
            "MOONRAKER_DIR": str(components.parent), "SCRIPT_DIR": str(checkout),
            "NAS_MOUNT": str(tmp_path / "nas"), "NAS_GCODES": str(tmp_path / "nas" / "gcodes"),
            "OFFLOAD_MODE": "hybrid", "OFFLOAD_COMPLETE": "True",
            "OFFLOAD_CANCELLED": "False", "OFFLOAD_ERROR": "False",
        }, prelude="""
detect_paths() { :; }
check_prerequisites() { :; }
configure_nas() { :; }
test_nas() { :; }
read() { :; }
""",
    )
    assert result.returncode == 0, result.stderr
    parsed = configparser.ConfigParser()
    parsed.read(config / "moonraker.conf")
    section = parsed["nas_offload"]
    assert section.get("local_gcodes_dir") == str(data / "gcodes")
    assert section.get("cache_db") == str(data / "database" / "nas_offload.db")
    assert section.get("lock_dir") == str(data / "systemd" / "nas-offload-locks")
    for directory in (data / "systemd" / "nas-offload-locks", data / "database"):
        assert directory.is_dir()
        assert directory.stat().st_uid == os.geteuid()
    assert (components / "__init__.py").read_text() == "MOONRAKER ORIGINAL"
    assert (components / "nas_offload.py").read_text() == "COMPONENT"
    assert (components / "utils" / "__init__.py").read_text() == "UTILS"
