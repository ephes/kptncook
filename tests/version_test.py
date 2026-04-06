from importlib.metadata import version

import kptncook


def test_package_version_matches_project_metadata():
    assert kptncook.__version__ == version("kptncook")
