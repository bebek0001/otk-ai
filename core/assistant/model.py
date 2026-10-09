# -*- coding: utf-8 -*-
"""
Модель помощника: подсказывает тип заготовки по похожим подтверждённым деталям.

- Обучающие примеры: база эталонов + решения технолога.
- Типы с числом примеров < MIN_PER_CLASS модель не предсказывает ("мало данных"),
  но может сослаться на одну очень похожую деталь с низкой уверенностью.
- Версии модели лежат в ASSISTANT_DIR/models; новая заменяет активную только
  если не хуже на отложенных примерах (holdout). Предыдущая версия сохраняется.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core import logger
from core.paths import ASSISTANT_DIR
from core.assistant import store
from core.assistant.features import sample_from_record, sample_text

try:  # sklearn может отсутствовать в урезанной сборке
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    import numpy as np
    SKLEARN_OK = True
except Exception:                                             # noqa: BLE001
    SKLEARN_OK = False

MIN_PER_CLASS = 10
MIN_TOTAL = 20
HOLDOUT_MOD = 5          # каждый 5-й пример (по хэшу ключа) — отложенный
MODELS_DIR = ASSISTANT_DIR / "models"
NEIGHBOR_SIM_RARE = 0.75


@dataclass
class Candidate:
    stock_type: str
    level: str                     # высокая / средняя / низкая
    prob: float
    reasons: List[Dict[str, Any]] = field(default_factory=list)
    note: str = ""


@dataclass
class Suggestion:
    candidates: List[Candidate]
    abstain: bool                  # модель не берётся подсказывать
    message: str = ""


def available() -> bool:
    return SKLEARN_OK


# ---------------- данные ----------------

def collect_samples() -> List[Dict[str, Any]]:
    from core import drawing_db
    out: List[Dict[str, Any]] = []
    seen = set()
    for r in store.load_decisions() + drawing_db.get_all_etalons():
        if not r.get("stock_type"):
            continue
        s = sample_from_record(r)
        if s["key"] in seen:
            continue
        seen.add(s["key"])
        out.append(s)
    return out


def _is_holdout(key: str) -> bool:
    return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % HOLDOUT_MOD == 0


def class_counts(samples: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    samples = samples if samples is not None else collect_samples()
    return dict(Counter(s["label"] for s in samples).most_common())


def _level(prob: float, margin: float) -> str:
    if prob >= 0.75 and margin >= 0.35:
        return "высокая"
    if prob >= 0.5:
        return "средняя"
    return "низкая"


# ---------------- обучение ----------------

class _Bundle:
    """Всё, что нужно для предсказания (кладётся в pickle)."""

    def __init__(self, vec, clf, classes, X_all, samples):
        self.vec, self.clf, self.classes = vec, clf, classes
        self.X_all = X_all
        self.samples = [{k: s[k] for k in ("name", "material", "length_mm", "label", "drawing_no")}
                        for s in samples]


def _fit(samples: List[Dict[str, Any]], class_pool: List[str]):
    texts = [sample_text(s["name"], s["material"], s["length_mm"]) for s in samples]
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)
    X = vec.fit_transform(texts)
    idx = [i for i, s in enumerate(samples) if s["label"] in class_pool]
    clf = LogisticRegression(C=10.0, class_weight="balanced", max_iter=2000)
    clf.fit(X[idx], [samples[i]["label"] for i in idx])
    return vec, clf, X


def _predict_raw(b: _Bundle, name, material, length_mm) -> Suggestion:
    x = b.vec.transform([sample_text(name, material, length_mm)])
    proba = b.clf.predict_proba(x)[0]
    order = np.argsort(proba)[::-1]
    sims = (b.X_all @ x.T).toarray().ravel()
    nn = np.argsort(sims)[::-1]

    def neighbors(label: str, k: int = 3):
        res = []
        for i in nn:
            if b.samples[i]["label"] == label and sims[i] > 0:
                res.append({"drawing_no": b.samples[i]["drawing_no"],
                            "part_name": b.samples[i]["name"],
                            "material": b.samples[i]["material"],
                            "stock_type": str(label), "similarity": float(sims[i])})
                if len(res) >= k:
                    break
        return res

    cands: List[Candidate] = []
    top = float(proba[order[0]])
    second = float(proba[order[1]]) if len(order) > 1 else 0.0
    for rank, j in enumerate(order[:3]):
        p = float(proba[j])
        if p < 0.05 and rank > 0:
            continue
        lvl = _level(p, top - second) if rank == 0 else ("средняя" if p >= 0.3 else "низкая")
        cands.append(Candidate(str(b.classes[j]), lvl, p, neighbors(str(b.classes[j]))))

    # редкий тип: очень похожая деталь в базе
    seen_types = {c.stock_type for c in cands}
    for i in nn[:3]:
        lab = b.samples[i]["label"]
        if lab not in b.classes and lab not in seen_types and sims[i] >= NEIGHBOR_SIM_RARE:
            cands.append(Candidate(lab, "низкая", float(sims[i]), neighbors(lab, 1),
                                   note="мало данных по этому типу, ссылка на одну похожую деталь"))
            break
    return Suggestion(cands, abstain=False)


def train(force: bool = False) -> Dict[str, Any]:
    """Обучает новую версию. Возвращает отчёт (в т.ч. причину отказа заменить)."""
    if not SKLEARN_OK:
        return {"ok": False, "reason": "scikit-learn недоступен"}
    samples = collect_samples()
    counts = class_counts(samples)
    pool = [c for c, n in counts.items() if n >= MIN_PER_CLASS]
    if len(samples) < MIN_TOTAL or len(pool) < 2:
        return {"ok": False, "reason": "мало данных для обучения", "counts": counts}

    train_s = [s for s in samples if not _is_holdout(s["key"])]
    hold_s = [s for s in samples if _is_holdout(s["key"]) and s["label"] in pool]

    pool_tr = [c for c in pool if sum(1 for s in train_s if s["label"] == c) >= 2]
    vec, clf, X = _fit(train_s, pool_tr)
    probe = _Bundle(vec, clf, [str(c) for c in clf.classes_], X, train_s)

    hold_acc, conf_acc, conf_n = None, None, 0
    if hold_s:
        ok = 0
        c_ok = 0
        for s in hold_s:
            sug = _predict_raw(probe, s["name"], s["material"], s["length_mm"])
            top = sug.candidates[0]
            hit = top.stock_type == s["label"]
            ok += hit
            if top.level == "высокая":
                conf_n += 1
                c_ok += hit
        hold_acc = ok / len(hold_s)
        conf_acc = (c_ok / conf_n) if conf_n else None

    prev = active_meta()
    report: Dict[str, Any] = {
        "ok": True, "n_samples": len(samples), "n_holdout": len(hold_s),
        "holdout_acc": hold_acc, "confident_acc": conf_acc, "confident_n": conf_n,
        "classes": pool_tr, "counts": counts, "replaced": True,
    }
    if (not force and prev and prev.get("holdout_acc") is not None
            and hold_acc is not None and len(hold_s) >= 10
            and hold_acc + 1e-9 < prev["holdout_acc"]):
        report["replaced"] = False
        report["reason"] = (f"новая версия хуже на проверочных примерах "
                            f"({hold_acc:.0%} против {prev['holdout_acc']:.0%}); оставлена прежняя")
        logger.info("assistant: " + report["reason"])
        return report

    # итоговая модель — на всех данных
    vec2, clf2, X2 = _fit(samples, pool)
    bundle = _Bundle(vec2, clf2, [str(c) for c in clf2.classes_], X2, samples)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    n = len(list(MODELS_DIR.glob("v*.pkl"))) + 1
    ver = f"v{n:04d}"
    with open(MODELS_DIR / f"{ver}.pkl", "wb") as f:
        pickle.dump(bundle, f)
    meta = {"version": ver, "trained_at": datetime.now().isoformat(timespec="seconds"),
            "n_samples": len(samples), "holdout_acc": hold_acc, "n_holdout": len(hold_s),
            "confident_acc": conf_acc, "confident_n": conf_n, "classes": [str(c) for c in bundle.classes]}
    (MODELS_DIR / f"{ver}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (MODELS_DIR / "active.txt").write_text(ver, encoding="utf-8")
    _cache.pop("bundle", None)
    report["version"] = ver
    logger.info(f"assistant: обучена {ver}, holdout={hold_acc}")
    return report


# ---------------- версии ----------------

def list_versions() -> List[Dict[str, Any]]:
    out = []
    for p in sorted(MODELS_DIR.glob("v*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:                                     # noqa: BLE001
            pass
    return out


def active_version() -> Optional[str]:
    p = MODELS_DIR / "active.txt"
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def active_meta() -> Optional[Dict[str, Any]]:
    v = active_version()
    for m in list_versions():
        if m.get("version") == v:
            return m
    return None


def rollback() -> Optional[str]:
    """Активной становится предыдущая версия."""
    vs = [m["version"] for m in list_versions()]
    cur = active_version()
    if cur not in vs or vs.index(cur) == 0:
        return None
    prev = vs[vs.index(cur) - 1]
    (MODELS_DIR / "active.txt").write_text(prev, encoding="utf-8")
    _cache.pop("bundle", None)
    return prev


# ---------------- предсказание ----------------

_cache: Dict[str, Any] = {}


def _load_bundle() -> Optional[_Bundle]:
    if "bundle" in _cache:
        return _cache["bundle"]
    v = active_version()
    if not v or not (MODELS_DIR / f"{v}.pkl").exists():
        return None
    try:
        with open(MODELS_DIR / f"{v}.pkl", "rb") as f:
            _cache["bundle"] = pickle.load(f)
    except Exception as e:                                    # noqa: BLE001
        logger.warn(f"assistant: модель {v} не загрузилась: {e}")
        return None
    return _cache["bundle"]


def suggest(name: str, material: str = "", length_mm=None) -> Suggestion:
    if not SKLEARN_OK:
        return Suggestion([], True, "Модуль подсказок недоступен на этом компьютере.")
    b = _load_bundle()
    if b is None:
        return Suggestion([], True, "Модель ещё не обучена. Откройте вкладку «Обучение».")
    s = _predict_raw(b, name, material, length_mm)
    if not s.candidates or (s.candidates[0].level == "низкая" and s.candidates[0].prob < 0.35):
        s.abstain = True
        s.message = "Уверенной подсказки нет. Выберите тип вручную: решение пополнит базу."
    return s
