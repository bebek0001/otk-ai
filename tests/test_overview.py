# -*- coding: utf-8 -*-
from core import overview


def test_base_rows_and_filter():
    rows = [
        {"drawing_no": "A.1", "part_name": "Нож", "stock_type": "Квадрат", "sortament": "x", "size": "",
         "mass_blank": 1, "mass_clean": 1, "origin": "PDF", "saved_at": ""},
        {"drawing_no": "B.2", "part_name": "Втулка", "stock_type": "Круг", "sortament": "y", "size": "",
         "mass_blank": 2, "mass_clean": 2, "origin": "Excel", "saved_at": ""},
    ]
    s = overview.base_summary(rows)
    assert s["total"] == 2
    assert len(overview.filter_rows(rows, "нож", "Все")) == 1
    assert len(overview.filter_rows(rows, "", "Круг")) == 1


def test_pipeline_and_tables():
    assert overview.pipeline_text()
    assert isinstance(overview.allowance_table(), list)
    assert isinstance(overview.agent_table(), list)
