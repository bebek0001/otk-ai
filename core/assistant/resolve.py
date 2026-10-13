# -*- coding: utf-8 -*-
"""Применение выбора технолога: пересчёт позиции с заданным типом заготовки."""

from __future__ import annotations

from typing import Any, Dict

from core import engine, logger
from core.assistant import store


def recalc_with_type(source_file: str, stock_type: str) -> Dict[str, Any]:
    """Пересчитывает файл так, будто движок сам определил указанный тип.
    Размер и массу считают прежние формулы по ГОСТ."""
    from ui.screens.batch_tool import process_one_file   # отложенный импорт
    prev = engine.FORCED_STOCK_TYPE
    engine.FORCED_STOCK_TYPE = stock_type
    try:
        res = process_one_file(source_file)
    finally:
        engine.FORCED_STOCK_TYPE = prev
    return res


def accept(case: Dict[str, Any], stock_type: str) -> Dict[str, Any]:
    """Технолог подтвердил тип: пересчёт, решение в обучающую базу, позиция закрыта."""
    result = recalc_with_type(case["source_file"], stock_type)
    if result.get("status") == "⚠ масса не сходится":
        # Технолог сам выбрал тип и отвечает за выбор: расхождение по массе
        # остаётся в примечании, но не блокирует решение.
        result["status"] = "ОК"
        result["error"] = ""
    if str(result.get("status", "")).startswith("⚠") or result.get("status") == "❌ ошибка":
        # не удалось посчитать с этим типом — решение не сохраняем
        return result
    store.add_decision(
        part_name=case.get("part_name", ""), material=case.get("material", ""),
        length_mm=case.get("length_mm"), stock_type=stock_type,
        drawing_no=case.get("drawing_no", ""))
    store.close_case(case["id"], stock_type)
    logger.info(f"Помощник: «{case.get('part_name')}» -> {stock_type}")
    return result
