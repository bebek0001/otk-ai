# -*- coding: utf-8 -*-
"""
Агент-технолог: решает, какие ответы программы можно выдавать без проверки.

Каждый ответ получает правило-источник (rule) и уровень:
  авто     правило доказало точность на ответах технологов;
  проверка ответ предложен, технолог должен подтвердить;
  отказ    ответа нет.

Автономность не задаётся вручную, а зарабатывается: на каждое правило ведётся
статистика «технолог принял без правки / поправил». Правило становится «авто»,
только когда нижняя граница доверительного интервала Уилсона по точности не
ниже порога (по умолчанию 95%) при минимуме 30 проверенных ответов. Если
точность упала, правило снова возвращается в «проверку». Каждое решение
пишется в журнал агента.

Агент не меняет сам правила расчёта и не отправляет данные никуда: он только
выбирает уровень доверия по накопленной статистике.
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from core import fsutil, logger
from core.paths import AGENT_DIR

MIN_N = 30                 # минимум проверенных ответов на правило
MIN_LOWER_BOUND = 0.95     # нижняя граница точности для статуса «авто»

# Правило определяется по источнику результата (result["source"]) и статусу
RULES = {
    "CDW+база":      "etalon_exact",       # ответ технолога по номеру чертежа
    "M3D+база":      "etalon_exact",
    "CDW+расчёт":    "formula_stamp",      # сортамент из штампа + формулы по ГОСТ
    "расчёт":        "formula_pdf",
    "CDW+размеры":   "dims_length",        # длина по размерам чертежа и массе
    "ИИ":            "ai_guess",
    "CDW+ИИ":        "ai_guess",
}
NEVER_AUTO = {"ai_guess", "dims_length"}   # пока не доказано контрольным набором


def _stats_path() -> Path:
    return AGENT_DIR / "rule_stats.json"


def load_stats() -> Dict[str, Dict[str, int]]:
    try:
        return json.loads(_stats_path().read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {}


def wilson_lower(correct: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = correct / n
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / den)


def autonomy(rule: str, stats: Optional[Dict[str, Dict[str, int]]] = None) -> str:
    """'авто' или 'проверка' для правила по накопленной статистике."""
    if rule in NEVER_AUTO:
        return "проверка"
    s = (stats if stats is not None else load_stats()).get(rule) or {}
    n, c = int(s.get("n", 0)), int(s.get("correct", 0))
    if n >= MIN_N and wilson_lower(c, n) >= MIN_LOWER_BOUND:
        return "авто"
    return "проверка"


def rule_for(result: Dict[str, Any]) -> Optional[str]:
    return RULES.get(result.get("source", ""))


def decide(result: Dict[str, Any]) -> Dict[str, str]:
    """Уровень доверия для результата обработки одной детали."""
    status = result.get("status", "")
    if status == "Сборка (пропуск)":
        return {"level": "пропуск", "rule": "", "reason": "сборочный чертёж"}
    rule = rule_for(result)
    if status == "ОК" and rule:
        lvl = autonomy(rule)
        s = load_stats().get(rule) or {}
        if lvl == "авто":
            reason = f"правило {rule}: {s.get('correct', 0)} из {s.get('n', 0)} подтверждено технологами"
        else:
            reason = (f"правило {rule}: подтверждений {s.get('correct', 0)} из {s.get('n', 0)}, "
                      f"нужно ≥{MIN_N} и точность ≥{int(MIN_LOWER_BOUND * 100)}%")
        return {"level": lvl, "rule": rule, "reason": reason}
    if "⚠" in status and result.get("stock_size") and rule:
        return {"level": "проверка", "rule": rule, "reason": result.get("error", "")[:140]}
    return {"level": "отказ", "rule": rule or "", "reason": result.get("error", "") or status}


def record_outcome(rule: str, correct: bool) -> None:
    """Технолог принял ответ правила (correct) или исправил его."""
    if not rule:
        return
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    st = load_stats()
    s = st.setdefault(rule, {"n": 0, "correct": 0})
    s["n"] += 1
    s["correct"] += 1 if correct else 0
    fsutil.write_json_atomic(_stats_path(), st)


def log_decision(drawing_no: str, decision: Dict[str, str]) -> None:
    try:
        AGENT_DIR.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "drawing_no": drawing_no, **decision}
        with open(AGENT_DIR / "decisions.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:                                      # noqa: BLE001
        logger.warn(f"Агент: журнал решений не записан: {e}")


# ------------------- обучение на исправленных таблицах -------------------

def _norm(s: Any) -> str:
    return re.sub(r"[\s;,]+", " ", str(s or "")).strip().lower()


def same_answer(program: Dict[str, Any], final: Dict[str, Any]) -> bool:
    """Ответ программы и итоговый ответ технолога совпали (сортамент и размер)."""
    ps = _norm(program.get("new_sortament") or program.get("zagotovka_full"))
    fs = _norm(final.get("sortament"))
    # у технолога сортамент идёт «сортамент/ материал» — сравниваем по началу
    fs = fs.split("/")[0].strip()
    ps = ps.split("/")[0].strip()
    if not ps or ps != fs:
        return False
    return _norm(program.get("stock_size")) == _norm(final.get("size"))


def last_program_record(drawing_no: str, log_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Последняя запись журнала обработки по номеру чертежа."""
    from core.paths import PROCESSING_LOG_DIR
    d = Path(log_dir) if log_dir else Path(PROCESSING_LOG_DIR)
    key = re.sub(r"\s+", "", drawing_no or "").lower()
    if not key or not d.exists():
        return None
    for f in sorted(d.glob("*.jsonl"), reverse=True):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if re.sub(r"\s+", "", r.get("drawing_no", "")).lower() == key:
                return r
    return None


def learn_from_row(drawing_no: str, final: Dict[str, Any],
                   log_dir: Optional[Path] = None) -> Optional[bool]:
    """Сверка итогового ответа технолога с тем, что предложила программа.
    Обновляет статистику правила. Возвращает True/False или None, если
    в журнале нет ответа программы по этой детали."""
    prog = last_program_record(drawing_no, log_dir)
    if not prog:
        return None
    rule = rule_for(prog)
    if not rule or not (prog.get("stock_size") or prog.get("new_sortament")):
        return None
    ok = same_answer(prog, final)
    record_outcome(rule, ok)
    return ok
