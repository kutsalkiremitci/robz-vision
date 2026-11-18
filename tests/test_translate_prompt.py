"""LLM istemi (Ollama ve Gemini): oyun profili, kullanıcı sözlüğü, bağlam ve istek gövdesi. Ağ yok: istekler
sahte oturumla yakalanır."""

from types import SimpleNamespace

import pytest

from altyazitr import translate as tr_mod
from altyazitr.translate import (LLM_BASE_RULES, OLLAMA_NUM_CTX, GeminiTranslator, Glossary, OllamaTranslator,
                                 _clean_llm_output, build_system_prompt, build_user_prompt)


def _profile(**kw):
    base = dict(id="test", name="Test Oyunu", description="", world="A crime city.",
                rules=("Use street Turkish.",), keep=("Vice City", "Marauder"),
                glossary=(("heist", "soygun"), ("cop", "polis")),
                examples=(("Cops on our six!", "Aynasızlar peşimizde!"),))
    base.update(kw)
    return SimpleNamespace(**base)


def test_system_prompt_contains_profile_rules_terms_and_examples():
    s = build_system_prompt(_profile(), Glossary(), "Lucia ile soygun planı")
    assert s.startswith(tr_mod.LLM_INTRO)
    assert "Game: Test Oyunu. A crime city." in s
    assert "- Use street Turkish." in s
    for rule in LLM_BASE_RULES:
        assert f"- {rule}" in s
    assert "Never translate these names: Vice City, Marauder." in s
    assert "Preferred terms: heist = soygun; cop = polis." in s
    assert "Extra context from the player: Lucia ile soygun planı" in s
    assert "EN: Cops on our six!\nTR: Aynasızlar peşimizde!" in s
    # örnekler en sonda: sabit önek uzun, satıra bağlı hiçbir şey sistem isteminde yok
    assert s.rstrip().endswith("TR: Aynasızlar peşimizde!")


def test_user_glossary_overrides_profile():
    g = Glossary([["Cop", "aynasız"], ["marauder", "yağmacı"], ["Andrew", ""], ["heist", "heist"]])
    s = build_system_prompt(_profile(), g)
    # kullanıcının karşılığı profilinkini ezer (büyük/küçük harf fark etmez)
    assert "cop = polis" not in s and "Cop = aynasız" in s
    # profilin çevrilmeyen adı kullanıcıda çevirili: artık ad listesinde değil, karşılıklarda
    assert "marauder = yağmacı" in s and "Marauder," not in s
    # kullanıcının boş ya da aynı karşılıklı terimi çevrilmeyecek ad olur, karşılıklardan çıkar
    assert "Never translate these names: Vice City, Andrew, heist." in s
    assert "heist = soygun" not in s


def test_system_prompt_without_profile_has_only_base_rules():
    s = build_system_prompt(None, Glossary())
    assert "Game:" not in s and "Never translate these names" not in s and "Examples:" not in s
    assert all(rule in s for rule in LLM_BASE_RULES)


def test_generic_profile_without_world_skips_game_line():
    s = build_system_prompt(_profile(world="", keep=(), glossary=(), examples=()), Glossary())
    assert "Game:" not in s and "Preferred terms" not in s


def test_user_prompt_marks_context_and_line():
    p = build_user_prompt("Not here.", [("You good?", "İyi misin?"), ("Cops on our six!", "")])
    assert p == ("Previous lines (context only):\nEN: You good?\nTR: İyi misin?\nEN: Cops on our six!\n"
                 "Line to translate:\nEN: Not here.\nTR:")
    assert build_user_prompt("Hey.") == "EN: Hey.\nTR:"


@pytest.mark.parametrize("raw, out", [
    ("TR: Selam kanka.", "Selam kanka."),
    ("  \"Bas gaza!\"  ", "Bas gaza!"),
    ("<think>hmm</think>Tamamdır.", "Tamamdır."),
    ("Türkçe: Yandık lan.\n", "Yandık lan."),
    ("Translate: Seçebileceğin bolca şey var", "Seçebileceğin bolca şey var"),
    ("EN: Bet.\nTR: Tamamdır.", "Tamamdır."),
])
def test_clean_llm_output(raw, out):
    assert _clean_llm_output(raw) == out


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status
        self.text = ""

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append((url, json))
        return _FakeResponse(self.payload)

    def close(self):
        pass


def test_ollama_request_body_uses_profile_and_context():
    t = OllamaTranslator("http://127.0.0.1:11434/", "gemma4:e4b", Glossary(), "not", profile=_profile())
    t._s = _FakeSession({"message": {"content": "TR: Burada olmaz."}})
    ctx = [("You good?", "İyi misin?")]
    assert t.translate("Not here.", ctx) == "Burada olmaz."
    url, body = t._s.calls[0]
    assert url == "http://127.0.0.1:11434/api/chat"
    assert body["messages"][0] == {"role": "system", "content": t.system}
    assert body["messages"][1] == {"role": "user", "content": build_user_prompt("Not here.", ctx)}
    assert body["think"] is False and body["stream"] is False
    assert body["keep_alive"] == tr_mod.OLLAMA_KEEP_ALIVE
    assert body["options"]["num_ctx"] == OLLAMA_NUM_CTX
    assert body["options"]["stop"] == ["\nEN:"]
    assert "Game: Test Oyunu." in t.system and "Extra context from the player: not" in t.system


def test_translategemma_gets_no_context_and_keeps_names():
    t = OllamaTranslator("http://x", "translategemma:12b", Glossary([["Vice City", ""]]), profile=_profile())
    t._s = _FakeSession({"message": {"content": "ZQX0'ye gidiyoruz."}})
    assert t.translate("We're going to Vice City.", [("Hey.", "Selam.")]) == "Vice City'ye gidiyoruz."
    body = t._s.calls[0][1]
    assert len(body["messages"]) == 1 and "think" not in body
    assert "Hey." not in body["messages"][0]["content"] and "ZQX0" in body["messages"][0]["content"]


def test_ollama_prepare_warms_up_with_real_prompt(monkeypatch):
    monkeypatch.setattr(tr_mod, "ollama_list", lambda url: ["gemma4:e4b"])
    t = OllamaTranslator("http://x", "gemma4:e4b", Glossary(), profile=_profile())
    t._s = _FakeSession({"message": {"content": "Selam."}})
    t.prepare(lambda _msg: None)
    assert len(t._s.calls) == len(tr_mod.WARMUP_LINES)
    # ısınma gerçek istemle yapılır: sistem istemi ve num_ctx çeviri istekleriyle aynı
    assert all(b["messages"][0]["content"] == t.system for _u, b in t._s.calls)
    assert all(b["options"]["num_ctx"] == OLLAMA_NUM_CTX for _u, b in t._s.calls)


def test_ollama_prepare_reports_missing_model(monkeypatch):
    monkeypatch.setattr(tr_mod, "ollama_list", lambda url: ["gemma4:12b"])
    t = OllamaTranslator("http://x", "gemma4:e4b", Glossary())
    with pytest.raises(tr_mod.TranslatorError, match="yüklü değil"):
        t.prepare(lambda _msg: None)


def test_gemini_body_uses_same_prompt():
    t = GeminiTranslator("anahtar", "gemini-3.5-flash-lite", Glossary(), profile=_profile())
    t._s = _FakeSession({"candidates": [{"content": {"parts": [{"text": "Bas gaza!"}]}}]})
    assert t.translate("Floor it!", [("Cops!", "")]) == "Bas gaza!"
    body = t._s.calls[0][1]
    assert body["systemInstruction"]["parts"][0]["text"] == t.system
    assert body["contents"] == [{"role": "user", "parts": [{"text": build_user_prompt("Floor it!", [("Cops!", "")])}]}]
    assert body["generationConfig"]["stopSequences"] == ["\nEN:"]
