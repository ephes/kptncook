from pathlib import Path

from kptncook.config import Settings


def test_root_must_exist_creates_dir(tmp_path, monkeypatch):
    root = tmp_path / "kptncook-home"
    monkeypatch.setenv("KPTNCOOK_API_KEY", "test-key")
    monkeypatch.setenv("KPTNCOOK_HOME", str(root))

    settings = Settings()

    assert isinstance(settings.root, Path)
    assert settings.root == root
    assert root.is_dir()


def test_root_must_exist_expands_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KPTNCOOK_API_KEY", "test-key")
    monkeypatch.setenv("KPTNCOOK_HOME", "~/kptncook-home")

    settings = Settings()

    expected = tmp_path / "kptncook-home"
    assert settings.root == expected
    assert expected.is_dir()
