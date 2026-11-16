"""Eski 'AltyaziTR' klasörlerinden 'RobzVision' klasörlerine taşıma testleri (gerçek klasörlere dokunmaz)."""

from altyazitr import APP_ID, APP_NAME, LEGACY_APP_ID
from altyazitr.paths import migrate_legacy_dirs


def test_names():
    assert APP_NAME == "Robz Vision"
    assert APP_ID == "RobzVision" and LEGACY_APP_ID == "AltyaziTR"


def test_moves_settings_and_model(tmp_path):
    old_cfg, new_cfg = tmp_path / "Roaming" / "AltyaziTR", tmp_path / "Roaming" / "RobzVision"
    old_data, new_data = tmp_path / "Local" / "AltyaziTR", tmp_path / "Local" / "RobzVision"
    (old_cfg).mkdir(parents=True)
    (old_cfg / "ayarlar.json").write_text('{"first_run": false}', encoding="utf-8")
    model = old_data / "modeller" / "quickmt-en-tr"
    model.mkdir(parents=True)
    (model / "model.bin").write_bytes(b"x" * 1000)
    # yeni sürüm taşımadan önce bazı klasörleri zaten oluşturmuş olabilir: boş "modeller" (ensure_dirs),
    # dolu "Logs" (yeni log dosyası)
    (new_data / "modeller").mkdir(parents=True)
    (new_data / "Logs").mkdir(parents=True)
    (new_data / "Logs" / "robzvision.log").write_text("yeni", encoding="utf-8")
    (old_data / "Logs").mkdir()
    (old_data / "Logs" / "eski.log").write_text("eski", encoding="utf-8")

    moved = migrate_legacy_dirs([(old_cfg, new_cfg), (old_data, new_data)])

    assert (new_cfg / "ayarlar.json").read_text(encoding="utf-8") == '{"first_run": false}'
    assert (new_data / "modeller" / "quickmt-en-tr" / "model.bin").stat().st_size == 1000
    assert "ayarlar.json" in moved and "modeller" in moved
    assert not old_cfg.exists()  # boşalan eski klasör silindi
    # hedefte zaten olan klasörün üzerine yazılmaz; eski içerik yerinde kalır
    assert not (new_data / "Logs" / "eski.log").exists()
    assert (old_data / "Logs" / "eski.log").exists()


def test_noop_when_no_legacy(tmp_path):
    assert migrate_legacy_dirs([(tmp_path / "yok", tmp_path / "yeni")]) == []
    assert not (tmp_path / "yeni").exists()


def test_removes_identical_duplicate_model(tmp_path):
    old, new = tmp_path / "eski", tmp_path / "yeni"
    for i, root in enumerate((old, new)):
        m = root / "modeller" / "quickmt-en-tr"
        (m / ".cache").mkdir(parents=True)
        (m / "model.bin").write_bytes(b"x" * 500)
        (m / "config.json").write_text("{}", encoding="utf-8")
        (m / ".cache" / "model.bin.metadata").write_text("x" * (127 + i), encoding="utf-8")  # farklı olabilir
    moved = migrate_legacy_dirs([(old, new)])
    assert any("yinelenen" in m for m in moved)
    assert not (old / "modeller").exists()  # yinelenen eski kopya silindi
    assert (new / "modeller" / "quickmt-en-tr" / "model.bin").stat().st_size == 500


def test_keeps_different_existing_tree(tmp_path):
    old, new = tmp_path / "eski", tmp_path / "yeni"
    (old / "modeller").mkdir(parents=True)
    (old / "modeller" / "a.bin").write_bytes(b"1")
    (new / "modeller").mkdir(parents=True)
    (new / "modeller" / "b.bin").write_bytes(b"2")
    migrate_legacy_dirs([(old, new)])
    assert (old / "modeller" / "a.bin").exists()  # farklı içerik: dokunulmaz


def test_does_not_overwrite_existing(tmp_path):
    old, new = tmp_path / "eski", tmp_path / "yeni"
    old.mkdir()
    new.mkdir()
    (old / "ayarlar.json").write_text("eski", encoding="utf-8")
    (new / "ayarlar.json").write_text("yeni", encoding="utf-8")
    assert migrate_legacy_dirs([(old, new)]) == []
    assert (new / "ayarlar.json").read_text(encoding="utf-8") == "yeni"
