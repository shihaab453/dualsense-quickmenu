# pytest configuration shared by everything under tests\.
#
# The only thing here is the hardware opt-in. A test marked `hardware` needs a
# DualSense physically plugged in, so it can't be part of a normal run: it
# would fail on any machine that doesn't have one, which is most of them, and
# a test that fails for a reason unrelated to the code teaches people to
# ignore failures.

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--hardware",
        action="store_true",
        default=False,
        help="Also run tests that need a DualSense controller plugged in.",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--hardware"):
        return
    skip_hardware = pytest.mark.skip(
        reason="needs a DualSense plugged in; pass --hardware to run it"
    )
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip_hardware)
