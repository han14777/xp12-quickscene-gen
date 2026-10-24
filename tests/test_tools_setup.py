"""DSFTool auto-download (network replaced by an in-memory zip)."""

import io
import zipfile

import pytest

from quickscene import config, tools_setup


class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def make_zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def bundled(tmp_path, monkeypatch):
    target = tmp_path / "tools" / "DSFTool.exe"
    monkeypatch.setattr(config, "BUNDLED_DSFTOOL", target)
    return target


def settings_with(dsftool="", url="https://example.invalid/xptools.zip", sha256=""):
    return {"tools": {"dsftool": dsftool, "dsftool_url": url, "dsftool_sha256": sha256}}


def test_downloads_and_extracts_dsftool(bundled, monkeypatch):
    payload = make_zip({"xptools_win_24-5/tools/DSFTool.exe": b"exe", "xptools_win_24-5/XGrinder.exe": b"x"})
    calls = []
    monkeypatch.setattr(tools_setup.requests, "get", lambda url, timeout: calls.append(url) or FakeResponse(payload))
    assert tools_setup.ensure_dsftool(settings_with()) == bundled
    assert bundled.read_bytes() == b"exe"
    # Second call finds the file and does not download again
    assert tools_setup.ensure_dsftool(settings_with()) == bundled
    assert len(calls) == 1


def test_configured_path_wins_and_is_never_overridden(bundled, tmp_path, monkeypatch):
    monkeypatch.setattr(tools_setup.requests, "get", lambda *a, **k: pytest.fail("must not download"))
    own = tmp_path / "own" / "DSFTool.exe"
    own.parent.mkdir()
    own.write_bytes(b"own")
    assert tools_setup.ensure_dsftool(settings_with(str(own))) == own
    with pytest.raises(RuntimeError, match="fix tools.dsftool"):
        tools_setup.ensure_dsftool(settings_with(str(tmp_path / "missing.exe")))


def test_zip_without_dsftool_fails_cleanly(bundled, monkeypatch):
    monkeypatch.setattr(tools_setup.requests, "get", lambda url, timeout: FakeResponse(make_zip({"a.txt": b""})))
    with pytest.raises(RuntimeError, match="DSFTool.exe not found"):
        tools_setup.ensure_dsftool(settings_with())
    assert not bundled.exists()


def test_network_error_explains_manual_setup(bundled, monkeypatch):
    def fail(*a, **k):
        raise tools_setup.requests.ConnectionError("offline")
    monkeypatch.setattr(tools_setup.requests, "get", fail)
    with pytest.raises(RuntimeError, match="Could not download DSFTool"):
        tools_setup.ensure_dsftool(settings_with())


def test_checksum_mismatch_is_rejected(bundled, monkeypatch):
    payload = make_zip({"tools/DSFTool.exe": b"tampered"})
    monkeypatch.setattr(tools_setup.requests, "get", lambda url, timeout: FakeResponse(payload))
    with pytest.raises(RuntimeError, match="does not match"):
        tools_setup.ensure_dsftool(settings_with(sha256="00" * 32))
    assert not bundled.exists()
