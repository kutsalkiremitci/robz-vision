"""Ayarlar: dataclass tabanlı, JSON olarak %APPDATA%\\RobzVision\\ayarlar.json dosyasına kaydedilir."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
from typing import Any

from altyazitr.paths import SETTINGS_FILE

log = logging.getLogger(__name__)


@dataclass
class CaptureCfg:
    mode: str = "screen"  # "screen" (ekran bölgesi) | "window" (uygulama penceresi)
    # Ekran modu: çevrilecek bölge [x, y, genişlik, yükseklik], fiziksel piksel. Boşsa henüz seçilmedi.
    rect: list[int] = field(default_factory=list)
    # Pencere modu: hedef pencere + pencerenin istemci alanına göre oransal bölge (x, y, w, h), 0..1
    window_exe: str = ""
    window_title: str = ""
    region: list[float] = field(default_factory=lambda: [0.12, 0.74, 0.76, 0.22])
    fps: int = 10

    def is_ready(self) -> bool:
        if self.mode == "window":
            return bool(self.window_exe or self.window_title)
        return len(self.rect) == 4 and self.rect[2] > 4 and self.rect[3] > 4


@dataclass
class OcrCfg:
    engine: str = "rapidocr"  # "rapidocr" (önerilen) | "windows"
    detect_mode: str = "smart"  # "smart" (değişince oku) | "interval" (sabit aralık)
    interval_ms: int = 500
    stable_frames: int = 2  # metin bu kadar kare sabit kalınca okunur
    bright_threshold: int = 160  # metin maskesi parlaklık eşiği (0-255)
    upscale: str = "auto"  # "auto" | "1" | "1.5" | "2"
    # "auto" (kenarlı + parlak [+ ham], en çok anlamlı okuyanı seç) | "outline" (her renkte kenarlı yazı) |
    # "bright" (beyaz/sarı parlak metin) | "none"
    preprocess: str = "auto"
    min_letters: int = 3
    keep_lowest_block: bool = True
    # Yapay zekâ metin bulucu (PP-OCRv6 det): açık zeminde beyaz, kenarsız altyazıları da bulur; saniyede 5 kez
    text_detector: bool = True
    detect_speaker: bool = True
    hide_delay_ms: int = 700  # altyazı kaybolduktan sonra çeviriyi gizlemeden önce bekleme


@dataclass
class TranslateCfg:
    engine: str = "quickmt"  # "quickmt" | "ollama" | "gemini" | "deepl" | "none"
    # LLM/çevrimiçi motor seçiliyken yerel hızlı çeviri (quickmt) yedekte hazır bekler. Asıl motorun çevirisi
    # hybrid_wait_ms içinde gelirse yalnız o gösterilir; gecikirse önce hızlı çeviri görünür, asıl çeviri
    # gelince aynı satırın üstüne yazılır. hybrid_wait_ms = 0: hızlı çeviri hemen gösterilir.
    hybrid: bool = True
    hybrid_wait_ms: int = 1200
    gemini_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    quickmt_device: str = "cpu"  # "cpu" | "cuda"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "gemma4:12b"
    context_lines: int = 8  # LLM'e bağlam olarak giden önceki satır sayısı
    # Oyun profili (altyazitr.game_profiles): dünya, ton, çevrilmeyecek adlar, argo sözlüğü ve üslup örnekleri
    profile: str = "gta6"
    game_context: str = ""  # kullanıcının ek bağlam notu; profilin üstüne eklenir
    deepl_key: str = ""
    timeout_s: float = 6.0
    # [ingilizce, türkçe]; türkçe boşsa terim çevrilmeden korunur
    glossary: list[list[str]] = field(default_factory=lambda: [
        ["Vice City", ""], ["Leonida", ""], ["Lucia", ""], ["Jason", ""],
    ])


@dataclass
class StyleCfg:
    font_family: str = "Segoe UI"
    font_size: int = 44  # 1080p tuval için piksel
    font_weight: int = 700
    text_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_width: float = 3.0  # 1080p için piksel
    shadow: bool = True
    bg_color: str = "#000000"
    bg_opacity: float = 0.45
    speaker_color: str = "#FFD54A"
    # Vurgulu kelimeler: oyunun renkli yazdığı yer, kişi ve eşya adlarının çevirideki karşılığı bu renkte çizilir
    # (varsayılan GTA 6'nın görev yazısındaki pembe). İki görünümde de kapalıysa vurgu hiç aranmaz.
    highlight: bool = True
    highlight_color: str = "#EC8DC0"
    bottom_percent: float = 8.0
    max_width_percent: float = 80.0
    fade_ms: int = 180
    show_original: bool = False
    original_size_percent: int = 55
    mask_original: bool = False
    mask_color: str = "#101010"
    mask_opacity: float = 0.92
    mask_pad_percent: float = 0.6


@dataclass
class FlowCfg:
    """Altyazı akışı: hızlı konuşmada hiçbir çeviri kaybolmasın diye son satırlar alt alta durur."""
    mode: str = "flow"  # "flow" (son satırlar alt alta, en yenisi altta) | "single" (tek satır, eski davranış)
    max_lines: int = 3  # akışta aynı anda en fazla bu kadar satır (2-4)
    min_show_s: float = 3.0  # her satır, oyun sonrakine geçse de en az bu kadar ekranda kalır
    # Her satır, oyundaki yazı ekranda dursa bile ilk göründüğü andan en fazla bu kadar saniye sonra kalkar
    # (uzun satırlara okuma süresi kadar pay tanınır). Ekranda sabit kalan yazılar (tuş ipuçları, menüler)
    # çeviriyi sonsuza dek ekranda tutmasın diye.
    max_show_s: float = 7.0


@dataclass
class OverlayCfg:
    enabled: bool = True  # çeviriyi kendi ekranımda göster
    visible_in_stream: bool = False  # True: OBS Ekran Yakalama'da da görünür (tarayıcı kaynağına gerek kalmaz)
    box: list[int] = field(default_factory=list)  # [x, y, w, h] Qt (mantıksal) koordinat; boşsa varsayılan
    scale: float = 1.0


@dataclass
class ServerCfg:
    port: int = 8765


@dataclass
class HotkeyCfg:
    toggle: str = "Ctrl+Alt+T"
    visibility: str = "Ctrl+Alt+H"
    clear: str = "Ctrl+Alt+C"


@dataclass
class Settings:
    capture: CaptureCfg = field(default_factory=CaptureCfg)
    ocr: OcrCfg = field(default_factory=OcrCfg)
    translate: TranslateCfg = field(default_factory=TranslateCfg)
    # İki ayrı görünüm: "Yayınımda" (OBS tarayıcı kaynağı + OBS maskesi) ve "Ekranımda" (oyunun üstündeki
    # kutu). Ayar dosyasındaki eski tek "style" anahtarı yayın görünümü olarak kalır; ekran görünümü eski
    # dosyalarda yoksa yayınınkinin kopyasıyla başlar (bkz. load_settings).
    style: StyleCfg = field(default_factory=StyleCfg)  # yayınımda
    screen_style: StyleCfg = field(default_factory=StyleCfg)  # ekranımda
    flow: FlowCfg = field(default_factory=FlowCfg)  # akış: son çeviriler alt alta (iki çıktıda da)
    overlay: OverlayCfg = field(default_factory=OverlayCfg)
    server: ServerCfg = field(default_factory=ServerCfg)
    hotkeys: HotkeyCfg = field(default_factory=HotkeyCfg)
    first_run: bool = True
    tour_seen: bool = False  # tanıtım turu tamamlandı mı ("Daha sonra" denirse bir sonraki açılışta yine gösterilir)
    minimize_to_tray: bool = True
    # Ayar dosyası biçimi. 2: varsayılan OCR motoru RapidOCR oldu (eski dosyalardaki "windows" bir kez taşınır).
    # 3: yerel LLM'in varsayılanı TranslateGemma'dan Gemma 4'e geçti, bağlam 3 satırdan 8 satıra çıktı.
    settings_version: int = 3


def _from_dict(cls, data: Any):
    """Bilinmeyen anahtarları yok sayar, eksikleri varsayılanla doldurur, tipleri korur."""
    obj = cls()
    if not isinstance(data, dict):
        return obj
    for f in fields(cls):
        if f.name not in data:
            continue
        cur = getattr(obj, f.name)
        val = data[f.name]
        if is_dataclass(cur):
            setattr(obj, f.name, _from_dict(type(cur), val))
            continue
        try:
            if isinstance(cur, bool):
                val = bool(val)
            elif isinstance(cur, int) and not isinstance(cur, bool):
                val = int(val)
            elif isinstance(cur, float):
                val = float(val)
            elif isinstance(cur, str):
                val = str(val)
            elif isinstance(cur, list) and not isinstance(val, list):
                continue
        except (TypeError, ValueError):
            continue
        setattr(obj, f.name, val)
    return obj


def load_settings() -> Settings:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fp:
            data = json.load(fp)
        s = _from_dict(Settings, data)
        if isinstance(data, dict) and "screen_style" not in data:
            # tek görünümlü eski sürüm: ekrandaki kutu bugüne kadar yayınla aynı görünüyordu
            s.screen_style = replace(s.style)
        version = int(data.get("settings_version", 1) or 1) if isinstance(data, dict) else 1
        if version < 2:
            # 1.0.2 ve öncesinde Windows OCR varsayılandı (çoğu kullanıcı bilerek seçmedi): RapidOCR'a geç
            if s.ocr.engine == "windows":
                s.ocr.engine = "rapidocr"
        if version < 3:
            # 1.0.x varsayılanları: TranslateGemma bağlamı hiç kullanmıyordu, 3 satır bağlam azdı. Kullanıcı
            # bunları değiştirmediyse yenilerine geç; kendi seçtiği model ve sayı korunur. Ollama'yı TranslateGemma
            # ile zaten kullanan birinin çalışan kurulumu bozulmaz: yeni model inene kadar hata alırdı.
            if s.translate.engine != "ollama" and s.translate.ollama_model.strip() in (
                    "translategemma:4b", "translategemma"):
                s.translate.ollama_model = TranslateCfg.ollama_model
            if s.translate.context_lines == 3:
                s.translate.context_lines = TranslateCfg.context_lines
        s.settings_version = Settings.settings_version
        log.info("Ayarlar yüklendi: %s", SETTINGS_FILE)
        return s
    except FileNotFoundError:
        return Settings()
    except Exception:  # bozuk dosya: yedekle, varsayılanla devam et
        log.exception("Ayar dosyası okunamadı, varsayılanlar kullanılacak")
        try:
            os.replace(SETTINGS_FILE, SETTINGS_FILE.with_suffix(".bozuk.json"))
        except OSError:
            pass
        return Settings()


def save_settings(s: Settings) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(asdict(s), fp, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_FILE)
