"""quickmt indirme yardımcıları ve pythonw (konsolsuz) korumaları için ağsız birim testleri."""

import io
import logging
import os
import socket
import sys
import threading

import pytest

import altyazitr.translate as T
from altyazitr import logutil
from altyazitr.logutil import guard_stdio, has_console
from altyazitr.translate import (QUICKMT_TOTAL_MB, TranslatorError, download_error_message, download_fraction,
                                 is_network_error)

NET_HEAD = "İnternet bağlantınızı kontrol edin"


# --- stdio koruması (pythonw / PyInstaller --windowed) ------------------------------
def test_guard_stdio_replaces_none_streams(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    guard_stdio()
    out, err = sys.stdout, sys.stderr
    try:
        assert out is not None and err is not None
        assert out.name == os.devnull and err.name == os.devnull
        out.write("ğüşıöç\n")  # eskiden: 'NoneType' object has no attribute 'write'
        err.write("x")
        assert not has_console(out)
    finally:
        out.close()
        err.close()


def test_guard_stdio_keeps_existing_streams(monkeypatch):
    buf_out, buf_err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf_out)
    monkeypatch.setattr(sys, "stderr", buf_err)
    guard_stdio()
    assert sys.stdout is buf_out and sys.stderr is buf_err


def test_has_console():
    assert not has_console(None)
    with open(os.devnull, "w", encoding="utf-8") as nul:
        assert not has_console(nul)
    assert has_console(io.StringIO())


def test_setup_logging_skips_console_handler_on_devnull(monkeypatch, tmp_path):
    monkeypatch.setattr(logutil, "LOG_DIR", tmp_path)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # teardown'da geri yüklensin
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    with open(os.devnull, "w", encoding="utf-8") as nul:
        monkeypatch.setattr(sys, "stdout", nul)
        try:
            logutil.setup_logging()
            new = [h for h in root.handlers if h not in old_handlers]
            assert len(new) == 1 and isinstance(new[0], logging.FileHandler)
        finally:
            for h in root.handlers[:]:
                if h not in old_handlers:
                    root.removeHandler(h)
                    h.close()
            root.setLevel(old_level)


# --- hata mesajı sınıflandırması -------------------------------------------------
@pytest.mark.parametrize("exc", [
    ConnectionError("bağlantı reddedildi"),
    TimeoutError("timed out"),
    socket.gaierror(11001, "getaddrinfo failed"),
])
def test_builtin_network_errors(exc):
    assert is_network_error(exc)
    assert NET_HEAD in download_error_message(exc)


def test_requests_network_errors():
    requests = pytest.importorskip("requests")
    for exc in (requests.exceptions.ConnectionError("x"), requests.exceptions.ReadTimeout("x"),
                requests.exceptions.SSLError("x")):
        assert is_network_error(exc), exc


@pytest.mark.parametrize("mod", ["httpx2", "httpx"])
def test_httpx_network_errors(mod):
    httpx = pytest.importorskip(mod)
    assert is_network_error(httpx.ConnectError("x"))
    assert is_network_error(httpx.ReadTimeout("x"))
    assert not is_network_error(httpx.InvalidURL("x"))


def test_wrapped_hf_connection_error_is_network():
    """huggingface_hub bağlantı hatasını LocalEntryNotFoundError(...) from ConnectError olarak sarar."""
    errors = pytest.importorskip("huggingface_hub.errors")

    try:
        try:
            raise ConnectionError("getaddrinfo failed")
        except ConnectionError as inner:
            raise errors.LocalEntryNotFoundError("cannot find snapshot") from inner
    except Exception as e:  # noqa: BLE001
        wrapped = e
    assert is_network_error(wrapped)
    assert NET_HEAD in download_error_message(wrapped)


@pytest.mark.parametrize("exc", [
    AttributeError("'NoneType' object has no attribute 'write'"),  # asıl hata: pythonw + tqdm
    ValueError("bozuk yanıt"),
    PermissionError(13, "Erişim engellendi"),
])
def test_other_errors_are_not_network(exc):
    assert not is_network_error(exc)
    msg = download_error_message(exc)
    assert NET_HEAD not in msg
    assert f"({type(exc).__name__})" in msg.splitlines()[0]
    assert msg.splitlines()[1].startswith("Ayrıntı: ")


def test_suppressed_context_is_ignored():
    try:
        try:
            raise ConnectionError("x")
        except ConnectionError:
            raise ValueError("başka sorun") from None
    except ValueError as e:
        assert not is_network_error(e)


def test_disk_full_message():
    msg = download_error_message(OSError(28, "No space left on device"))
    assert "boş alan" in msg and NET_HEAD not in msg


# --- ilerleme oranı -----------------------------------------------------------------
@pytest.mark.parametrize("mb,expected", [
    (0, 0.0), (-5, 0.0), (QUICKMT_TOTAL_MB / 2, 0.5), (QUICKMT_TOTAL_MB, 0.99), (10_000, 0.99),
])
def test_download_fraction_caps(mb, expected):
    assert download_fraction(mb) == pytest.approx(expected)


def test_download_fraction_zero_total():
    assert download_fraction(10, 0) == 0.0


# --- quickmt_downloaded / download_quickmt (sahte indirici, ağ yok) ---------------------
def _write_model(d, model_bytes=2000):
    d.mkdir(parents=True, exist_ok=True)
    for f in T.QUICKMT_FILES:
        (d / f).write_bytes(b"x" * (model_bytes if f == "model.bin" else 10))


def test_quickmt_downloaded_checks_files_and_size(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "QUICKMT_MODEL_MIN_BYTES", 1000)
    d = tmp_path / "m"
    assert not T.quickmt_downloaded(d)
    _write_model(d, model_bytes=500)  # yarım model.bin
    assert not T.quickmt_downloaded(d)
    _write_model(d, model_bytes=2000)
    assert T.quickmt_downloaded(d)
    (d / "tgt.spm.model").unlink()
    assert not T.quickmt_downloaded(d)
    monkeypatch.setattr(T, "QUICKMT_DIR", d)
    assert not T.quickmt_downloaded()


def _fake_snapshot(expected_dir, behaviour):
    def snapshot_download(repo, local_dir, allow_patterns):
        from pathlib import Path

        assert Path(local_dir) == expected_dir  # asla gerçek model klasörüne yazma
        assert repo == T.QUICKMT_REPO and "model.bin" in allow_patterns
        behaviour(Path(local_dir))
    return lambda: snapshot_download


def test_download_quickmt_success_reports_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "QUICKMT_MODEL_MIN_BYTES", 1000)
    monkeypatch.setattr(T, "QUICKMT_DIR", tmp_path)  # varsayılan hedef çağrı anında okunur

    def slow_write(d):
        import time
        time.sleep(0.7)  # izleyici en az bir kez çalışsın
        _write_model(d)
    monkeypatch.setattr(T, "_snapshot_download", _fake_snapshot(tmp_path, slow_write))
    msgs, fracs = [], []
    T.download_quickmt(msgs.append, progress=fracs.append)
    assert fracs and fracs[-1] == 1.0 and all(0.0 <= f <= 0.99 for f in fracs[:-1])
    assert msgs[-1] == "Çeviri modeli indirildi."
    assert any("/ 404 MB" in m for m in msgs[:-1])


def test_download_quickmt_legacy_signature(monkeypatch, tmp_path):
    """gui/pages.py: download_quickmt(lambda m: ...) — tek konumsal argümanla çalışmalı."""
    monkeypatch.setattr(T, "QUICKMT_MODEL_MIN_BYTES", 1000)
    monkeypatch.setattr(T, "QUICKMT_DIR", tmp_path)
    monkeypatch.setattr(T, "_snapshot_download", _fake_snapshot(tmp_path, _write_model))
    msgs = []
    T.download_quickmt(lambda m: msgs.append(m))
    assert msgs[-1] == "Çeviri modeli indirildi."


@pytest.mark.parametrize("exc,network", [
    (ConnectionError("getaddrinfo failed"), True),
    (AttributeError("'NoneType' object has no attribute 'write'"), False),
])
def test_download_quickmt_error_messages(monkeypatch, tmp_path, exc, network):
    def boom(_d):
        raise exc
    monkeypatch.setattr(T, "_snapshot_download", _fake_snapshot(tmp_path, boom))
    with pytest.raises(TranslatorError) as ei:
        T.download_quickmt(lambda m: None, target_dir=tmp_path)
    assert (NET_HEAD in str(ei.value)) is network
    assert ei.value.__cause__ is exc


def test_download_quickmt_incomplete(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "_snapshot_download", _fake_snapshot(tmp_path, lambda d: None))
    with pytest.raises(TranslatorError, match="eksik"):
        T.download_quickmt(lambda m: None, target_dir=tmp_path)


def test_snapshot_download_has_progress_bars_disabled(monkeypatch):
    pytest.importorskip("huggingface_hub")
    monkeypatch.setenv("HF_HUB_DISABLE_PROGRESS_BARS", "0")
    T._snapshot_download()
    assert os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] == "1"
    from huggingface_hub.utils import are_progress_bars_disabled, tqdm

    assert are_progress_bars_disabled()
    # konsolsuz (stderr None) ortamda ilerleme çubuğu artık yazmaya çalışmamalı
    monkeypatch.setattr(sys, "stderr", None)
    bar = tqdm(total=10, name="huggingface_hub.snapshot_download")
    bar.update(5)
    bar.close()
