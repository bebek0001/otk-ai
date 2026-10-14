# -*- coding: utf-8 -*-
"""Хранилище помощника: решения технолога и список позиций для разбора."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List

from core import fsutil, logger
from core.paths import ASSISTANT_DIR


def _ensure() -> None:
    ASSISTANT_DIR.mkdir(parents=True, exist_ok=True)


def _load(name: str) -> List[Dict[str, Any]]:
    p = ASSISTANT_DIR / name
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:                                    # noqa: BLE001
        logger.warn(f"assistant: не прочитан {name}: {e}")
        return []


def _save(name: str, items: List[Dict[str, Any]]) -> None:
    _ensure()
    fsutil.write_json_atomic(ASSISTANT_DIR / name, items)


# ---------- решения технолога (обучающие примеры) ----------

def load_decisions() -> List[Dict[str, Any]]:
    return _load("decisions.json")


def add_decision(part_name: str, material: str, length_mm, stock_type: str,
                 drawing_no: str = "", source: str = "technologist") -> Dict[str, Any]:
    items = load_decisions()
    rec = {
        "id": uuid.uuid4().hex[:12],
        "part_name": part_name, "material_mark": material,
        "l_part_mm": length_mm, "stock_type": stock_type,
        "drawing_no": drawing_no, "source": source,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    # то же обозначение -> обновляем решение, а не плодим дубли
    if drawing_no:
        items = [r for r in items if r.get("drawing_no") != drawing_no]
    items.append(rec)
    _save("decisions.json", items)
    return rec


def revoke_decision(decision_id: str) -> bool:
    items = load_decisions()
    new = [r for r in items if r.get("id") != decision_id]
    if len(new) == len(items):
        return False
    _save("decisions.json", new)
    return True


# ---------- позиции для разбора (то, что программа не определила) ----------

def load_cases() -> List[Dict[str, Any]]:
    return _load("cases.json")


def add_case(source_file: str, part_name: str, material: str, length_mm,
             drawing_no: str, reason: str, error: str = "") -> Dict[str, Any]:
    """reason: 'error' — программа не определила заготовку."""
    items = load_cases()
    for r in items:                       # одна позиция — одна запись
        if r.get("source_file") == source_file and r.get("status") == "open":
            return r
    rec = {
        "id": uuid.uuid4().hex[:12], "status": "open",
        "source_file": source_file, "part_name": part_name,
        "material": material, "length_mm": length_mm,
        "drawing_no": drawing_no, "reason": reason, "error": error,
        "added_at": datetime.now().isoformat(timespec="seconds"),
    }
    items.append(rec)
    _save("cases.json", items)
    return rec


def close_case(case_id: str, stock_type: str) -> None:
    items = load_cases()
    for r in items:
        if r.get("id") == case_id:
            r["status"] = "done"
            r["stock_type"] = stock_type
            r["closed_at"] = datetime.now().isoformat(timespec="seconds")
    _save("cases.json", items)


def open_cases() -> List[Dict[str, Any]]:
    return [r for r in load_cases() if r.get("status") == "open"]
