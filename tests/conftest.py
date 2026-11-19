"""Bütün testlerin ortak düzeni."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _forget_loaded_ollama_models():
    """translate._loaded_models ("bu modeli bu uygulama belleğe yükletti" kaydı) modül düzeyindedir. Bir testin
    kaydı sonrakine taşmasın: taşarsa release_ollama_models o testin sahte adresine gerçek istek göndermeye çalışır."""
    yield
    from altyazitr import translate

    with translate._loaded_lock:
        translate._loaded_models.clear()
        translate._wanted = None


@pytest.fixture(autouse=True)
def _no_real_ollama(monkeypatch):
    """Testler bu bilgisayarda çalışan gerçek Ollama'ya (varsayılan port 11434) istek gönderemez: model listesi,
    modeli bellekten boşaltma ya da süre isteği gerçek kullanıcının (ve süre ölçümlerinin) modelini etkiler. Sürekli
    tümleştirmedeki gibi Ollama kapalıymış davranılır; sahte sunucular rastgele portta çalışır."""
    import requests

    real = requests.Session.request

    def guarded(self, method, url, *args, **kwargs):
        if ":11434" in str(url):
            raise requests.ConnectionError(f"testlerde gerçek Ollama'ya istek gönderilmez: {method} {url}")
        return real(self, method, url, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "request", guarded)
