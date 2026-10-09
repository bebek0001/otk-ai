# -*- coding: utf-8 -*-
"""
ui/screens/assistant_tool.py — «Помощник технолога».

Вкладка «Разбор»: позиции, где программа не определила заготовку или
угадала её (очередь подтверждения). Помощник предлагает тип заготовки по
похожим подтверждённым деталям; размер и массу считают прежние формулы.
Ничего не сохраняется без нажатия «Принять».

Вкладка «Обучение»: состав базы, обучение модели на этом компьютере
(без интернета), откат версии, загрузка исправленных Excel.
"""

from __future__ import annotations

import queue
import threading
from tkinter import filedialog, messagebox
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from core import confirm_queue, engine, logger
from core.assistant import excel_import, model, resolve, store
from core.stock_ext import NEW_TYPES

_BG_CARD = ("gray92", "#141416")
_BORDER = ("gray80", "#26262b")
_MUTED = ("gray40", "#9a9aa3")
_LEVEL_COLOR = {"высокая": "#4CAF50", "средняя": "#E6B95E", "низкая": "#EF5350"}


def _all_types() -> List[str]:
    return sorted(set(engine.STOCKTYPE_TO_GOST) | set(NEW_TYPES))


class AssistantScreen(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._items: List[Dict[str, Any]] = []
        self._sel: Optional[Dict[str, Any]] = None
        self._choice = ctk.StringVar(value="")
        self._results: List[dict] = []      # результаты принятых за сессию
        self._total = 0
        self._busy = False
        # Фоновые потоки не трогают Tk: результат кладётся в очередь,
        # а окно обновляет главный поток (на macOS иначе возможен вылет).
        self._ui_q: "queue.Queue" = queue.Queue()
        self._poll_job = None

        self._build_header()
        self._build_tabs()
        self.refresh()
        self._poll()

    def _poll(self):
        try:
            while True:
                fn = self._ui_q.get_nowait()
                fn()
        except queue.Empty:
            pass
        except Exception as e:                                # noqa: BLE001
            logger.error(f"Помощник: ошибка обновления окна: {e}")
        try:
            self._poll_job = self.after(120, self._poll)
        except Exception:                                     # noqa: BLE001
            self._poll_job = None

    def destroy(self):
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:                                 # noqa: BLE001
                pass
        super().destroy()

    # ── каркас ──────────────────────────────────────────────

    def _build_header(self):
        top = ctk.CTkFrame(self, corner_radius=12, fg_color=_BG_CARD,
                           border_width=1, border_color=_BORDER)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="Помощник технолога",
                     font=ctk.CTkFont(size=18, weight="bold")
                     ).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 0))
        ctk.CTkLabel(top, text="Подсказки строятся по базе эталонов и работают без интернета.",
                     text_color=_MUTED).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 6))
        self.lbl_model = ctk.CTkLabel(top, text="", text_color=_MUTED)
        self.lbl_model.grid(row=0, column=1, rowspan=2, padx=16)
        self.tabs_btn = ctk.CTkSegmentedButton(
            top, values=["Разбор", "Обучение"], command=self._switch_tab)
        self.tabs_btn.set("Разбор")
        self.tabs_btn.grid(row=2, column=0, sticky="w", padx=16, pady=(0, 12))

    def _build_tabs(self):
        self.review = ctk.CTkFrame(self, fg_color="transparent")
        self.training = ctk.CTkFrame(self, fg_color="transparent")
        for f in (self.review, self.training):
            f.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self._build_review()
        self._build_training()
        self.training.grid_remove()

    def _switch_tab(self, name: str):
        if name == "Разбор":
            self.training.grid_remove()
            self.review.grid()
        else:
            self.review.grid_remove()
            self.training.grid()
            self._refresh_training()

    # ── Разбор ──────────────────────────────────────────────

    def _build_review(self):
        r = self.review
        r.grid_columnconfigure(0, weight=1, uniform="c")
        r.grid_columnconfigure(1, weight=2, uniform="c")
        r.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(r, corner_radius=14, fg_color=_BG_CARD,
                            border_width=1, border_color=_BORDER)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        self.lbl_count = ctk.CTkLabel(left, text="", font=ctk.CTkFont(size=14, weight="bold"))
        self.lbl_count.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        self.list_frame = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        self.list_frame.grid_columnconfigure(0, weight=1)

        bottom = ctk.CTkFrame(left, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))
        bottom.grid_columnconfigure(0, weight=1)
        self.lbl_progress = ctk.CTkLabel(bottom, text="", text_color=_MUTED)
        self.lbl_progress.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(bottom, text="Выгрузить в формате заказчика", height=32,
                      corner_radius=10, command=self._export_client
                      ).grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ctk.CTkButton(bottom, text="Выгрузить решения в Excel", height=32,
                      corner_radius=10, fg_color=("gray88", "gray25"),
                      hover_color=("gray80", "gray30"), text_color=("gray10", "gray90"),
                      command=self._export).grid(row=2, column=0, sticky="ew", pady=(6, 0))

        self.detail = ctk.CTkScrollableFrame(r, corner_radius=14, fg_color=_BG_CARD,
                                             border_width=1, border_color=_BORDER)
        self.detail.grid(row=0, column=1, sticky="nsew")
        self.detail.grid_columnconfigure(0, weight=1)

    def refresh(self):
        items: List[Dict[str, Any]] = []
        for c in store.open_cases():
            items.append({"kind": "case", "id": c["id"], "case": c,
                          "part_name": c.get("part_name", ""), "material": c.get("material", ""),
                          "length_mm": c.get("length_mm"), "drawing_no": c.get("drawing_no", ""),
                          "source_file": c.get("source_file", ""),
                          "status": "Ошибка программы", "why": c.get("error", "")})
        for q in confirm_queue.list_pending():
            items.append({"kind": "guess", "id": q["id"], "queue": q,
                          "part_name": q.get("part_name", ""), "material": q.get("material_mark", ""),
                          "length_mm": q.get("l_part_mm"), "drawing_no": q.get("drawing_no", ""),
                          "source_file": q.get("source_file", ""), "guessed": q.get("stock_type", ""),
                          "status": "Ждёт подтверждения",
                          "why": "Тип заготовки угадан по картинке или похожему чертежу."})
        self._items = items
        self._total = max(self._total, len(items))
        self._render_list()
        self._render_detail()
        self._update_model_label()

    def _update_model_label(self):
        m = model.active_meta()
        if not model.available():
            txt = "Модуль подсказок недоступен"
        elif m:
            acc = m.get("holdout_acc")
            txt = f"Модель {m['version']} · {m['n_samples']} примеров" + \
                  (f" · проверка {acc:.0%}" if acc is not None else "")
        else:
            txt = f"Модель не обучена · {len(model.collect_samples())} примеров в базе"
        self.lbl_model.configure(text=txt)

    def _render_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.lbl_count.configure(text=f"Требуют решения: {len(self._items)}")
        done = self._total - len(self._items)
        self.lbl_progress.configure(text=f"Решено {done} из {self._total}")
        if not self._items:
            ctk.CTkLabel(self.list_frame, text="Нет позиций для разбора.",
                         text_color=_MUTED).grid(row=0, column=0, padx=10, pady=10, sticky="w")
        for i, it in enumerate(self._items):
            active = self._sel is not None and self._sel["id"] == it["id"]
            b = ctk.CTkButton(
                self.list_frame, anchor="w", height=54, corner_radius=12,
                fg_color=("gray85", "#1a2433") if active else ("gray95", "#18181c"),
                hover_color=("gray80", "#1f2a3a"), text_color=("gray10", "gray90"),
                text=f"{it['drawing_no'] or '—'}   [{it['status']}]\n{it['part_name'] or '(без названия)'} · {it['material']}",
                command=lambda x=it: self._select(x))
            b.grid(row=i, column=0, sticky="ew", padx=4, pady=3)

    def _select(self, it):
        self._sel = it
        self._choice.set("")
        self._render_list()
        self._render_detail()

    def _render_detail(self):
        for w in self.detail.winfo_children():
            w.destroy()
        it = self._sel
        if it is None or all(x["id"] != it["id"] for x in self._items):
            self._sel = None
            ctk.CTkLabel(self.detail, text="Выберите позицию слева.",
                         text_color=_MUTED).grid(row=0, column=0, padx=16, pady=16, sticky="w")
            return
        row = 0

        def label(text, size=13, bold=False, muted=False, pady=(0, 2)):
            nonlocal row
            ctk.CTkLabel(self.detail, text=text, anchor="w", justify="left", wraplength=560,
                         font=ctk.CTkFont(size=size, weight="bold" if bold else "normal"),
                         text_color=_MUTED if muted else ("gray10", "gray90")
                         ).grid(row=row, column=0, sticky="w", padx=16, pady=pady)
            row += 1

        label(f"{it['drawing_no'] or '—'} · {it['part_name'] or '(без названия)'}", 16, True, pady=(14, 2))
        length = f"{it['length_mm']:g} мм" if isinstance(it["length_mm"], (int, float)) else "не определена"
        label(f"Материал: {it['material'] or 'не определён'}   ·   Длина: {length}", muted=True)
        label(f"Почему здесь: {it['why']}", muted=True, pady=(0, 10))

        sug = model.suggest(it["part_name"], it["material"], it["length_mm"])
        label("Предлагаемый тип заготовки", 14, True, pady=(6, 4))
        if sug.message:
            label(sug.message, muted=True)
        first = True
        for c in sug.candidates:
            card = ctk.CTkFrame(self.detail, corner_radius=10, fg_color=("gray95", "#18181c"),
                                border_width=1, border_color=_BORDER)
            card.grid(row=row, column=0, sticky="ew", padx=16, pady=4)
            row += 1
            card.grid_columnconfigure(0, weight=1)
            ctk.CTkRadioButton(card, text=c.stock_type, variable=self._choice, value=c.stock_type,
                               font=ctk.CTkFont(size=14, weight="bold")
                               ).grid(row=0, column=0, sticky="w", padx=12, pady=(8, 0))
            ctk.CTkLabel(card, text=f"уверенность: {c.level}", text_color=_LEVEL_COLOR[c.level]
                         ).grid(row=0, column=1, padx=12, pady=(8, 0))
            why = "; ".join(f"{n['part_name']} {n['drawing_no']} ({n['material']})"
                            for n in c.reasons[:2])
            txt = ("Похожие детали: " + why) if why else ""
            if c.note:
                txt = (c.note + ". " + txt).strip()
            if txt:
                ctk.CTkLabel(card, text=txt, anchor="w", justify="left", wraplength=520,
                             text_color=_MUTED, font=ctk.CTkFont(size=12)
                             ).grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))
            if first and not sug.abstain:
                self._choice.set(c.stock_type)
            first = False

        if it["kind"] == "guess" and it.get("guessed"):
            label(f"Программа угадала: {it['guessed']}", muted=True, pady=(8, 0))

        label("Свой вариант", 13, True, pady=(10, 2))
        self.custom = ctk.CTkOptionMenu(self.detail, values=["—"] + _all_types(),
                                        command=lambda v: self._choice.set("" if v == "—" else v))
        self.custom.grid(row=row, column=0, sticky="w", padx=16, pady=(0, 8))
        row += 1

        btns = ctk.CTkFrame(self.detail, fg_color="transparent")
        btns.grid(row=row, column=0, sticky="w", padx=16, pady=(6, 4))
        row += 1
        self.btn_accept = ctk.CTkButton(btns, text="Принять выбранное", height=36, corner_radius=12,
                                        command=self._on_accept)
        self.btn_accept.pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="Пропустить", height=36, corner_radius=12,
                      fg_color=("gray88", "gray25"), hover_color=("gray80", "gray30"),
                      text_color=("gray10", "gray90"), command=self._on_skip).pack(side="left")
        label("Ничего не сохраняется, пока вы не нажали «Принять».", muted=True, pady=(4, 12))

    def _on_skip(self):
        idx = next((i for i, x in enumerate(self._items) if self._sel and x["id"] == self._sel["id"]), -1)
        if self._items:
            self._select(self._items[(idx + 1) % len(self._items)])

    def _on_accept(self):
        if self._busy or self._sel is None:
            return
        st = self._choice.get()
        if not st:
            messagebox.showinfo("Помощник", "Выберите тип заготовки.")
            return
        it = self._sel
        if not it.get("source_file"):
            messagebox.showwarning("Помощник", "Нет исходного файла для пересчёта.")
            return
        self._busy = True
        self.btn_accept.configure(state="disabled", text="Считаю...")
        threading.Thread(target=self._accept_worker, args=(it, st), daemon=True).start()

    def _accept_worker(self, it, st):
        try:
            if it["kind"] == "case":
                res = resolve.accept(it["case"], st)
            else:
                res = self._accept_guess(it, st)
            err = None
        except Exception as e:                                # noqa: BLE001
            res, err = None, str(e)
            logger.error(f"Помощник: не удалось принять: {e}")
        self._ui_q.put(lambda: self._after_accept(it, st, res, err))

    def _accept_guess(self, it, st):
        q = it["queue"]
        if st == q.get("stock_type"):
            confirm_queue.approve(q["id"])
            store.add_decision(q.get("part_name", ""), q.get("material_mark", ""),
                               q.get("l_part_mm"), st, q.get("drawing_no", ""))
            return {"status": "ОК", "part_name": q.get("part_name", ""),
                    "drawing_no": q.get("drawing_no", ""),
                    "new_sortament": q.get("result_line", "")}
        case = {"id": "", "source_file": it["source_file"], "part_name": it["part_name"],
                "material": it["material"], "length_mm": it["length_mm"],
                "drawing_no": it["drawing_no"]}
        res = resolve.accept(case, st)
        if not str(res.get("status", "")).startswith(("⚠", "❌")):
            confirm_queue.reject(q["id"], "исправлено технологом через помощника")
        return res

    def _after_accept(self, it, st, res, err):
        self._busy = False
        if err or res is None or str(res.get("status", "")).startswith(("⚠", "❌")):
            msg = err or f"{res.get('status')}: {res.get('error', '')}"
            messagebox.showwarning("Помощник", f"С типом «{st}» посчитать не удалось.\n{msg}")
            self._render_detail()
            return
        self._results.append(res)
        self.refresh()
        if self._items:
            self._select(self._items[0])

    def _export_client(self):
        if not self._results:
            messagebox.showinfo("Помощник", "Пока нет принятых решений.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx",
                                            filetypes=[("Excel", "*.xlsx")],
                                            initialfile="заготовки.xlsx")
        if not path:
            return
        from core.client_format import export_client_xlsx
        export_client_xlsx(self._results, path)
        messagebox.showinfo("Помощник", f"Сохранено: {path}")

    def _export(self):
        if not self._results:
            messagebox.showinfo("Помощник", "Пока нет принятых решений.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx",
                                            filetypes=[("Excel", "*.xlsx")],
                                            initialfile="решения_помощника.xlsx")
        if not path:
            return
        from ui.screens.batch_tool import export_to_excel
        export_to_excel(self._results, path)
        messagebox.showinfo("Помощник", f"Сохранено: {path}")

    # ── Обучение ────────────────────────────────────────────

    def _build_training(self):
        t = self.training
        t.grid_columnconfigure(0, weight=1)
        t.grid_rowconfigure(0, weight=1)
        self.train_scroll = ctk.CTkScrollableFrame(t, corner_radius=14, fg_color=_BG_CARD,
                                                   border_width=1, border_color=_BORDER)
        self.train_scroll.grid(row=0, column=0, sticky="nsew")
        self.train_scroll.grid_columnconfigure(0, weight=1)

    def _refresh_training(self):
        f = self.train_scroll
        for w in f.winfo_children():
            w.destroy()
        row = 0

        def label(text, size=13, bold=False, muted=False, pady=(0, 4), color=None):
            nonlocal row
            ctk.CTkLabel(f, text=text, anchor="w", justify="left", wraplength=760,
                         font=ctk.CTkFont(size=size, weight="bold" if bold else "normal"),
                         text_color=color or (_MUTED if muted else ("gray10", "gray90"))
                         ).grid(row=row, column=0, sticky="w", padx=16, pady=pady)
            row += 1

        samples = model.collect_samples()
        counts = model.class_counts(samples)
        ok_types = [c for c, n in counts.items() if n >= model.MIN_PER_CLASS]
        m = model.active_meta()

        label("Модель", 16, True, pady=(14, 4))
        if not model.available():
            label("Модуль подсказок недоступен на этом компьютере.", color="#EF5350")
        elif m:
            acc = m.get("holdout_acc")
            label(f"Активна {m['version']} · обучена {m['trained_at']} · {m['n_samples']} примеров"
                  + (f" · точность на проверочных {acc:.0%} (из {m['n_holdout']})" if acc is not None else ""))
            if m.get("confident_acc") is not None:
                label(f"При высокой уверенности верно {m['confident_acc']:.0%} "
                      f"(на {m['confident_n']} примерах). Выборка маленькая.", muted=True)
        else:
            label("Не обучена.", color="#E6B95E")
        label(f"Данных достаточно для типов: {', '.join(ok_types) or 'пока ни для одного'} "
              f"(порог {model.MIN_PER_CLASS} примеров на тип). Для остальных помощник скажет «мало данных».",
              muted=True)

        bar = ctk.CTkFrame(f, fg_color="transparent")
        bar.grid(row=row, column=0, sticky="w", padx=16, pady=6)
        row += 1
        self.btn_train = ctk.CTkButton(bar, text="Обучить модель", height=36, corner_radius=12,
                                       command=self._on_train)
        self.btn_train.pack(side="left", padx=(0, 8))
        ctk.CTkButton(bar, text="Откатить на предыдущую", height=36, corner_radius=12,
                      fg_color=("gray88", "gray25"), hover_color=("gray80", "gray30"),
                      text_color=("gray10", "gray90"), command=self._on_rollback
                      ).pack(side="left")
        label("Обучение идёт на этом компьютере без интернета. Новая версия заменяет текущую, "
              "только если не хуже на примерах, которых не видела.", muted=True)

        label("Источники данных", 16, True, pady=(14, 4))
        from core import drawing_db
        label(f"База эталонов: {len(drawing_db.get_all_etalons())}")
        label(f"Решения технолога: {len(store.load_decisions())}")
        ctk.CTkButton(f, text="Загрузить исправленный Excel", height=34, corner_radius=12,
                      fg_color=("gray88", "gray25"), hover_color=("gray80", "gray30"),
                      text_color=("gray10", "gray90"), command=self._on_import
                      ).grid(row=row, column=0, sticky="w", padx=16, pady=6)
        row += 1

        label("Состав по типам", 16, True, pady=(14, 4))
        mx = max(counts.values()) if counts else 1
        for c, n in counts.items():
            line = ctk.CTkFrame(f, fg_color="transparent")
            line.grid(row=row, column=0, sticky="ew", padx=16, pady=1)
            row += 1
            line.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(line, text=c, width=120, anchor="w").grid(row=0, column=0)
            pb = ctk.CTkProgressBar(line, height=8)
            pb.set(n / mx)
            pb.grid(row=0, column=1, sticky="ew", padx=8)
            enough = n >= model.MIN_PER_CLASS
            ctk.CTkLabel(line, text=f"{n}  {'достаточно' if enough else 'мало данных'}",
                         text_color="#4CAF50" if enough else "#E6B95E", width=140, anchor="w"
                         ).grid(row=0, column=2)

        label("История версий", 16, True, pady=(14, 4))
        vs = model.list_versions()
        if not vs:
            label("Пока пусто.", muted=True)
        for v in reversed(vs):
            acc = v.get("holdout_acc")
            label(f"{v['version']} · {v['trained_at']} · {v['n_samples']} примеров · "
                  + (f"проверка {acc:.0%}" if acc is not None else "без проверки")
                  + ("  (активна)" if v["version"] == model.active_version() else ""), muted=True)

    def _on_train(self):
        self.btn_train.configure(state="disabled", text="Обучаю...")

        def work():
            try:
                rep = model.train()
            except Exception as e:                            # noqa: BLE001
                rep = {"ok": False, "reason": str(e)}
            self._ui_q.put(lambda: self._after_train(rep))
        threading.Thread(target=work, daemon=True).start()

    def _after_train(self, rep):
        if not rep.get("ok"):
            messagebox.showwarning("Обучение", rep.get("reason", "Не удалось обучить модель."))
        elif not rep.get("replaced", True):
            messagebox.showinfo("Обучение", rep.get("reason", ""))
        else:
            acc = rep.get("holdout_acc")
            messagebox.showinfo("Обучение", f"Готова версия {rep['version']}."
                                + (f" Точность на проверочных: {acc:.0%}." if acc is not None else ""))
        self._refresh_training()
        self._update_model_label()

    def _on_rollback(self):
        prev = model.rollback()
        if prev is None:
            messagebox.showinfo("Откат", "Предыдущей версии нет.")
        self._refresh_training()
        self._update_model_label()

    def _on_import(self):
        path = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx *.xlsm")])
        if not path:
            return
        try:
            added, skipped = excel_import.import_corrected_excel(path)
        except Exception as e:                                # noqa: BLE001
            messagebox.showerror("Импорт", f"Не удалось прочитать файл: {e}")
            return
        messagebox.showinfo("Импорт", f"Добавлено примеров: {added}. Пропущено строк: {skipped}.\n"
                                      "Нажмите «Обучить модель», чтобы учесть их.")
        self._refresh_training()
