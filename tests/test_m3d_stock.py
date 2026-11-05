"""Заготовка по габариту модели: разбор сортамента, длина проката, массы."""
from core import m3d_stock as ms


def test_round_length_is_the_odd_dimension():
    kind, label, need = ms._linear_section("Круг 80")
    assert (kind, label) == ("round", "Круг 80")
    assert ms._length_from(kind, need, (70.0, 16.0, 70.0)) == 16.0      # Ролик
    assert ms._length_from(kind, need, (50.0, 170.0, 50.0)) == 170.0    # Ось


def test_bar_alias_and_square():
    kind, label, need = ms._linear_section("Пруток ПКРНХ 70 БрАЖМц 10-3-1,5")
    assert (kind, label) == ("round", "Круг 70")
    kind, label, need = ms._linear_section("Квадрат 100")
    assert ms._length_from(kind, need, (90.0, 88.0, 90.0)) == 88.0


def test_profile_tube_channel_remove_section_sizes():
    kind, _, need = ms._linear_section("Профиль 100х50х5")      # кириллическая х
    assert ms._length_from(kind, need, (50.0, 935.0, 100.0)) == 935.0
    kind, _, need = ms._linear_section("Труба 159x7")
    assert ms._length_from(kind, need, (159.0, 1970.0, 159.0)) == 1970.0
    kind, _, need = ms._linear_section("Швеллер 12У")
    assert ms._length_from(kind, need, (120.0, 3100.0, 52.0)) == 3100.0
    # сечение в записи не найдено: длину не угадываем
    assert ms._length_from(kind, need, (40.0, 700.0, 40.0)) is None


def test_masses_follow_tables_and_density():
    assert abs(ms._lin_mass("channel", "Швеллер 12У", [120.0], 3100.0, 7.85) - 32.24) < 1e-6
    assert abs(ms._lin_mass("channel", "Швеллер 14У", [140.0], 1800.0, 7.85) - 22.14) < 1e-6
    assert abs(ms._lin_mass("square", "Квадрат 100", [100.0], 88.0, 7.85) - 6.908) < 1e-3
    bronze = ms._lin_mass("round", "Круг 70", [70.0], 40.0, 7.5)
    assert round(bronze, 2) == 1.15


def test_variant_keeps_length_changes_section():
    base = ms.Stock("lin", l_mm=1800.0, section="Швеллер 12У", method="габарит модели")
    v = ms.with_material(base, "Швеллер$d14У ГОСТ 8240-97;Ст3сп ГОСТ 535-2005$", 7850)
    assert v.l_mm == 1800.0 and v.section == "Швеллер 14У"
    assert abs(v.blank_mass_kg - 22.14) < 1e-6
    assert v.size_text == "Швеллер 14У, L=1800"


def test_sheet_size_text_and_head():
    st = ms.Stock("sheet", h_mm=300.0, l_mm=1980.0, thickness_mm=16.0)
    assert st.size_text == "HxL = 300x1980"
    assert ms._head("Лист$d16 ГОСТ 19903-2015;Ст3пс ГОСТ 14637-89$") == "Лист 16"
    assert ms._sheet_thickness("Жесть 25-ЧЖР") == 0.25


def test_sheet_sides_from_edge_lines():
    boxes = [((5.0, 120.0, 5.0), 6), ((5.0, 230.0, 5.0), 6), ((30.0, 63.0, 5.0), 4)]
    assert ms._sheet_by_edges(boxes, 5.0, lambda h, l, k=0.99: True) == (120.0, 230.0)
    assert ms._sheet_by_edges(boxes[:1], 5.0, lambda h, l, k=0.99: True) is None


def test_cdw_fallback_takes_size_from_drawing(tmp_path, monkeypatch):
    import sys
    from unittest.mock import MagicMock
    for m in ("customtkinter", "tkinter", "tkinter.filedialog", "tkinter.messagebox",
              "tkinter.ttk", "PIL", "PIL.Image", "PIL.ImageTk", "tkinterdnd2", "tkinter.font"):
        sys.modules.setdefault(m, MagicMock())
    from ui.screens import batch_tool as bt
    (tmp_path / "201647.03.00.002 Палец.cdw").write_bytes(b"x")
    (tmp_path / "201647.03.00.002 Палец.m3d").write_bytes(b"x")
    monkeypatch.setattr(bt, "_process_file", lambda p, ai=False, **k: (
        {"stock_size": "L=94", "stock_mass_kg": "0,83", "status": "ОК", "error": ""}, None))
    row = {"drawing_no": "201647.03.00.002", "stock_size": "", "status": "⚠ нет длины",
           "error": "не определён"}
    out = bt._cdw_fallback(str(tmp_path / "201647.03.00.002 Палец.m3d"), row)
    assert out["stock_size"] == "L=94" and out["status"] == "ОК" and out["error"] == ""
    assert bt._find_cdw_beside(str(tmp_path / "x.m3d"), "201647.03.00.002-01").endswith("Палец.cdw")


def test_beams_sections_and_mass():
    kind, label, need = ms._linear_section("Двутавр 40Б1")
    assert kind == "beam" and need == [400.0]
    assert abs(ms._lin_mass(kind, "Двутавр 40Б1", need, 7350.0, 7.85) - 416.01) < 0.01
    k2, _, n2 = ms._linear_section("Балка IPE 360 DIN 1025")
    assert k2 == "beam" and n2 == [360.0]
    assert abs(ms._lin_mass(k2, "Балка IPE 360 DIN 1025", n2, 3655.0, 7.85) - 208.7) < 0.1
    # круглая труба по формуле, без таблицы
    tk, _, tn = ms._linear_section("Труба 25х3,2")
    assert abs(ms._lin_mass(tk, "Труба 25х3,2", tn, 1000.0, 7.85) - 1.72) < 0.01


def test_export_keeps_size_for_kompas_cross_check_rows():
    from core import client_format as cf
    row = {"drawing_no": "1.2.3", "part_name": "A", "status": "⚠ проверить", "source": "КОМПАС",
           "new_sortament": "Лист 5 ГОСТ 19903-2015", "zagotovka_full": "Лист 5 ГОСТ 19903-2015",
           "stock_size": "HxL = 120x230", "stock_mass_kg": "1,08", "clean_mass_kg": "0,972",
           "error": "Габарит КОМПАС расходится"}
    out = cf.rows_for_client([row])[0]
    assert "120x230" in str(out)
