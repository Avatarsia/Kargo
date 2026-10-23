"""Compile and render the shipped macros with Klipper's exact delimiters."""
import configparser
from pathlib import Path

import jinja2
import pytest


CONFIG = configparser.RawConfigParser()
CONFIG.read(Path(__file__).resolve().parents[1] / "klipper_config" / "nas_offload.cfg")
ENV = jinja2.Environment("{%", "%}", "{", "}")


@pytest.mark.parametrize("name", CONFIG.sections())
def test_macro_compiles_with_klipper_delimiters(name):
    ENV.from_string(CONFIG[name]["gcode"])


@pytest.mark.parametrize("filename", ["part.gcode", "folder/my part.gcode", 'folder/part "quoted".gcode', r'part\quoted.gcode'])
def test_print_only_requests_fetch_and_delegates_start(filename):
    calls = []

    def remote(method, **kwargs):
        calls.append((method, kwargs))
        return ""

    rendered = ENV.from_string(CONFIG["gcode_macro SDCARD_PRINT_FILE"]["gcode"]).render(
        params={"FILENAME": filename}, action_call_remote_method=remote,
    )
    assert calls == [("nas_offload_fetch", {"filename": filename, "start_print": True})]
    commands = [line.strip() for line in rendered.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert commands == []


@pytest.mark.parametrize("filename", ["part.gcode", 'folder/my "quoted" part.gcode'])
def test_manual_fetch_does_not_start_or_claim_completion(filename):
    calls = []

    def remote(method, **kwargs):
        calls.append((method, kwargs))
        return ""

    rendered = ENV.from_string(CONFIG["gcode_macro NAS_FETCH"]["gcode"]).render(
        params={"FILE": filename}, action_call_remote_method=remote,
    )
    assert calls == [("nas_offload_fetch", {"filename": filename})]
    assert "complete" not in rendered.lower()
    assert "_SDCARD_PRINT_FILE_ORIGINAL" not in rendered
    # User filenames must not be interpolated into a quoted RESPOND command.
    assert filename not in rendered


def test_manual_fetch_without_filename_does_not_dispatch():
    def remote(*args, **kwargs):
        pytest.fail("Empty filename must not dispatch a fetch")

    rendered = ENV.from_string(CONFIG["gcode_macro NAS_FETCH"]["gcode"]).render(
        params={}, action_call_remote_method=remote,
    )
    assert "Usage: NAS_FETCH" in rendered


def test_offload_renders_with_quoted_filename_without_invalid_json():
    rendered = ENV.from_string(CONFIG["gcode_macro NAS_OFFLOAD"]["gcode"]).render(
        params={"FILE": 'folder/my "quoted" part.gcode'},
    )
    assert "POST http://localhost:7125/server/nas_offload/offload" in rendered
    assert '"quoted"' not in rendered
