"""Unit tests for the lossless fallback source (fully mocked, no network)."""

from __future__ import annotations

import json
import os
import types

import pytest
import requests

from spotiseek.config import Config
from spotiseek.fallback import FallbackSource, odesli, providers
from spotiseek.fallback.odesli import OdesliResult
from spotiseek.models import Track

from ..conftest import FIXTURES


# --------------------------------------------------------------------------- #
# Fake HTTP plumbing
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, *, json_data=None, content=b"", headers=None, ok=True):
        self._json = json_data
        self._content = content
        self.headers = headers or {}
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            raise requests.HTTPError("simulated HTTP error")

    def json(self):
        if self._json is None:
            raise ValueError("no JSON body")
        return self._json

    def iter_content(self, chunk_size):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i : i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Session:
    """A requests-like session whose responses come from a handler callable."""

    def __init__(self, handler):
        self.handler = handler

    def get(self, url, params=None, timeout=None, headers=None, stream=False,
            allow_redirects=True):
        return self.handler(url, params, stream)


# --------------------------------------------------------------------------- #
# Odesli
# --------------------------------------------------------------------------- #
def _fixture_data() -> dict:
    return json.loads((FIXTURES / "odesli_track.json").read_text(encoding="utf-8"))


def test_odesli_parse_extracts_native_ids() -> None:
    result = odesli._parse(_fixture_data())
    assert result.provider_ids["tidal"] == "491206012"
    assert result.provider_ids["deezer"] == "781592622"
    assert result.provider_ids["amazon"] == "B0DZTSF3YS"
    # Odesli does not cover Qobuz.
    assert "qobuz" not in result.provider_ids


def test_odesli_resolve_success() -> None:
    session = _Session(lambda url, params, stream: _Resp(json_data=_fixture_data()))
    result = odesli.resolve("4cOdK2wGLETKBW3PvgPWqT", session=session)
    assert result is not None
    assert result.id_for("tidal") == "491206012"


def test_odesli_resolve_no_spotify_id_but_isrc() -> None:
    result = odesli.resolve(None, isrc="USRC12345678")
    assert result is not None
    assert result.isrc == "USRC12345678"
    assert result.provider_ids == {}


def test_odesli_resolve_nothing_to_go_on() -> None:
    assert odesli.resolve(None) is None


def test_odesli_resolve_network_error_returns_none() -> None:
    def boom(*_a, **_k):
        raise requests.ConnectionError("down")

    session = types.SimpleNamespace(get=boom)
    assert odesli.resolve("abc", session=session) is None
    # ...but keeps the ISRC we already knew.
    assert odesli.resolve("abc", isrc="X", session=session).isrc == "X"


# --------------------------------------------------------------------------- #
# Provider helpers
# --------------------------------------------------------------------------- #
def test_find_stream_url_prefers_known_keys() -> None:
    payload = [{"foo": 1}, {"OriginalTrackUrl": "https://cdn.example/a.flac"}]
    assert providers._find_stream_url(payload) == "https://cdn.example/a.flac"


def test_find_stream_url_falls_back_to_media_url() -> None:
    payload = {"meta": {"nested": ["https://cdn.example/track.m4a"]}}
    assert providers._find_stream_url(payload) == "https://cdn.example/track.m4a"


def test_find_stream_url_none_when_absent() -> None:
    assert providers._find_stream_url({"status": "error"}) is None


def test_ext_from_url_then_content_type() -> None:
    assert providers._ext_from("https://x/y.flac?token=1", None) == "flac"
    assert providers._ext_from("https://x/stream", "audio/mpeg") == "mp3"
    assert providers._ext_from("https://x/stream", None) is None


# --------------------------------------------------------------------------- #
# A concrete provider end-to-end (mocked HTTP)
# --------------------------------------------------------------------------- #
def test_tidal_provider_fetch(tmp_path) -> None:
    audio = b"\x00" * (128 * 1024)

    def handler(url, params, stream):
        if stream:
            return _Resp(content=audio, headers={"Content-Type": "audio/flac"})
        assert url.endswith("/track/")
        assert params["quality"] == "LOSSLESS"
        return _Resp(json_data=[{"OriginalTrackUrl": "https://cdn.example/x.flac"}])

    provider = providers.TidalProvider("https://tidal.example", session=_Session(handler))
    out = provider.fetch("491206012", str(tmp_path))
    assert out is not None
    path, ext = out
    assert ext == "flac"
    assert os.path.getsize(path) == len(audio)


def test_provider_without_base_url_is_skipped(tmp_path) -> None:
    provider = providers.TidalProvider("", session=_Session(lambda *a: None))
    assert provider.fetch("1", str(tmp_path)) is None


def test_provider_discards_tiny_file(tmp_path) -> None:
    def handler(url, params, stream):
        if stream:
            return _Resp(content=b"nope", headers={"Content-Type": "application/json"})
        return _Resp(json_data={"url": "https://cdn.example/x.flac"})

    provider = providers.DeezerProvider("https://dz.example", session=_Session(handler))
    assert provider.fetch("781592622", str(tmp_path)) is None
    # No leftover temp files.
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------- #
# FallbackSource orchestration
# --------------------------------------------------------------------------- #
class _FakeProvider:
    def __init__(self, name, result):
        self.name = name
        self._result = result
        self.calls: list[str] = []

    def fetch(self, identifier, dest_dir):
        self.calls.append(identifier)
        return self._result


def _track() -> Track:
    return Track(title="One More Time", artists=["Daft Punk"], spotify_id="abc")


def test_source_tries_providers_in_order(monkeypatch, tmp_path) -> None:
    cfg = Config(fallback_providers=["tidal", "deezer", "amazon"])
    src = FallbackSource(cfg)
    monkeypatch.setattr(
        src, "resolve",
        lambda track: OdesliResult(
            provider_ids={"tidal": "1", "deezer": "2", "amazon": "3"}
        ),
    )
    fakes = {
        "tidal": _FakeProvider("tidal", None),  # resolves an id but fails to fetch
        "deezer": _FakeProvider("deezer", (str(tmp_path / "f.flac"), "flac")),
        "amazon": _FakeProvider("amazon", ("unused", "flac")),
    }
    monkeypatch.setattr(src, "_build_provider", lambda name: fakes[name])

    outcome = src.download(_track(), str(tmp_path))
    assert outcome is not None
    assert outcome.provider == "deezer"
    assert fakes["tidal"].calls == ["1"]  # tidal tried first, with its native id
    assert fakes["amazon"].calls == []  # never reached after deezer succeeded


def test_source_returns_none_when_all_fail(monkeypatch, tmp_path) -> None:
    cfg = Config(fallback_providers=["tidal", "deezer"])
    src = FallbackSource(cfg)
    monkeypatch.setattr(
        src, "resolve",
        lambda track: OdesliResult(provider_ids={"tidal": "1", "deezer": "2"}),
    )
    monkeypatch.setattr(src, "_build_provider", lambda name: _FakeProvider(name, None))
    assert src.download(_track(), str(tmp_path)) is None


def test_source_skips_qobuz_without_isrc(monkeypatch, tmp_path) -> None:
    cfg = Config(fallback_providers=["qobuz"])
    src = FallbackSource(cfg)
    monkeypatch.setattr(src, "resolve", lambda track: OdesliResult(isrc=None))
    fake = _FakeProvider("qobuz", ("x", "flac"))
    monkeypatch.setattr(src, "_build_provider", lambda name: fake)

    assert src.download(_track(), str(tmp_path)) is None
    assert fake.calls == []  # never attempted — no ISRC to key on


def test_source_uses_isrc_for_qobuz(monkeypatch, tmp_path) -> None:
    cfg = Config(fallback_providers=["qobuz"])
    src = FallbackSource(cfg)
    monkeypatch.setattr(src, "resolve", lambda track: OdesliResult(isrc="USRC17600001"))
    fake = _FakeProvider("qobuz", (str(tmp_path / "q.flac"), "flac"))
    monkeypatch.setattr(src, "_build_provider", lambda name: fake)

    outcome = src.download(_track(), str(tmp_path))
    assert outcome is not None and outcome.provider == "qobuz"
    assert fake.calls == ["USRC17600001"]  # keyed by ISRC


def test_source_returns_none_when_unresolvable(monkeypatch, tmp_path) -> None:
    src = FallbackSource(Config(fallback_providers=["tidal"]))
    monkeypatch.setattr(src, "resolve", lambda track: None)
    assert src.download(_track(), str(tmp_path)) is None


# --------------------------------------------------------------------------- #
# SSRF / transport hardening
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url", [
    "https://169.254.169.254/latest/meta-data/",  # cloud metadata
    "https://127.0.0.1/x.flac",
    "https://10.0.0.5/x.flac",
    "https://192.168.1.9/x.flac",
    "https://localhost/x.flac",
    "http://cdn.example/x.flac",  # not https
])
def test_stream_url_rejected(url) -> None:
    assert providers._is_safe_stream_url(url) is False


def test_stream_url_allowed() -> None:
    assert providers._is_safe_stream_url("https://cdn.example/x.flac") is True


def test_download_refuses_unsafe_url(tmp_path) -> None:
    def handler(url, params, stream):
        if stream:
            return _Resp(content=b"\x00" * (128 * 1024))
        return _Resp(json_data={"url": "https://127.0.0.1/x.flac"})

    provider = providers.TidalProvider("https://tidal.example", session=_Session(handler))
    # The resolved stream URL points at loopback -> refused, nothing written.
    assert provider.fetch("1", str(tmp_path)) is None
    assert list(tmp_path.iterdir()) == []


def test_find_stream_url_ignores_non_media_urls() -> None:
    # A bare non-media URL (potential SSRF target) is no longer returned.
    assert providers._find_stream_url({"redirect": "https://evil.example/ping"}) is None
