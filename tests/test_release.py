from scripts.release_version import next_version


def test_initial_release():
    assert next_version(None, "initial import") == "v0.1.0"


def test_conventional_versions():
    assert next_version("v0.1.0", "fix: repair") == "v0.1.1"
    assert next_version("v0.1.0", "feat(api): add tool\nfix: repair") == "v0.2.0"
    assert next_version("v0.1.0", "feat!: change API") == "v1.0.0"
    assert next_version("v1.2.3", "fix: change\n\nBREAKING CHANGE: API") == "v2.0.0"
    assert next_version("v1.2.3", "docs: tutorial") == ""
