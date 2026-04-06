import pytest


def pytest_addoption(parser):
    parser.addoption("--run-smoke", action="store_true", default=False, help="Run smoke tests (needs internet + VPN)")


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: marks tests that hit real Instagram (deselect with '-m \"not smoke\"')")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-smoke"):
        skip = pytest.mark.skip(reason="Need --run-smoke to run")
        for item in items:
            if "smoke" in item.keywords:
                item.add_marker(skip)
