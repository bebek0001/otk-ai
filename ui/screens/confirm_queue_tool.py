# -*- coding: utf-8 -*-
"""
ui/screens/confirm_queue_tool.py — вкладка «Очередь подтверждения».

Сюда попадают результаты, которые не посчитаны по ГОСТ/формулам, а
УГАДАНЫ: RAG-слоем (найден похожий чертёж в базе эталонов) или Vision
(GPT посмотрел на картинку чертежа). Такие ответы не становятся новым
эталоном в базе автоматически — технолог должен каждый проверить и
явно подтвердить. Только после подтверждения запись уходит в базу
эталонов (core/drawing_db.py) и начинает участвовать в будущем RAG-поиске.

Детерминированный расчёт по ГОСТ/формулам (в т.ч. CDW+расчёт) сюда не
попадает — это точная арифметика по точным исходным данным, её
по-прежнему можно сохранять в базу сразу, кнопкой «Сохранить эталон»
во вкладке «PDF инструмент».
"""

from __future__ import annotations

import customtkinter as ctk
from tkinter import messagebox

from core import confirm_queue
from core import logger


_SOURCE_LABEL = {
    "rag":    "RAG (похожий чертёж из базы)",
    "vision": "Vision (GPT смотрел на картинку)",
}


class ConfirmQueueScreen(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._items: list[dict] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        top = ctk.CTkFrame(
            self, corner_radius=12, fg_color=("gray92", "#141416"),
            border_width=1, border_color=("gray80", "#222226"),
        )
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 10))
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            top, text="Очередь подтверждения",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 2))

        self.lbl_count = ctk.CTkLabel(
            top, text="", anchor="w", text_color=("gray40", "#A7A7A7"),
        )
        self.lbl_count.grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 12))

        ctk.CTkButton(
            top, text="Обновить", height=32, corner_radius=10, width=110,
            fg_color=("gray88", "gray25"), hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"), command=self.refresh,
        ).grid(row=0, column=2, rowspan=2, padx=16, pady=12)

        self.scroll = ctk.CTkScrollableFrame(self, corner_radius=18)
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.scroll.grid_columnconfigure(0, weight=1)

    # ── Данные ──────────────────────────────────────────────

    def refresh(self):
        try:
            self._items = confirm_queue.list_pending()
        except Exception as e:                                # noqa: BLE001
            logger.error("Ошибка чтения очереди подтверждения", e)
            self._items = []

        for w in self.scroll.winfo_children():
            w.destroy()

        n = len(self._items)
        self.lbl_count.configure(
            text=f"Ждут проверки: {n}" if n else
                 "Пусто — нет ответов, угаданных RAG/Vision, ожидающих подтверждения."
        )

        for i, item in enumerate(self._items):
            self._build_row(item, i)

    def _build_row(self, item: dict, idx: int):
        card = ctk.CTkFrame(
            self.scroll, corner_radius=14, fg_color=("gray95", "#1b1b1f"),
            border_width=1, border_color=("gray85", "#2a2a30"),
        )
        card.grid(row=idx, column=0, sticky="ew", padx=4, pady=6)
        card.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 2))
        head.grid_columnconfigure(0, weight=1)

        title = item.get("part_name") or "(без наименования)"
        dno = item.get("drawing_no") or ""
        ctk.CTkLabel(
            head, text=f"{title}" + (f"  ·  № {dno}" if dno else ""),
            font=ctk.CTkFont(size=14, weight="bold"), anchor="w",
        ).grid(row=0, column=0, sticky="w")

        src = _SOURCE_LABEL.get(item.get("source_kind", ""), item.get("source_kind", "?"))
        ctk.CTkLabel(
            head, text=f"Источник: {src}", anchor="w",
            text_color=("gray40", "#A7A7A7"), font=ctk.CTkFont(size=11),
        ).grid(row=1, column=0, sticky="w")

        if item.get("source_file"):
            ctk.CTkLabel(
                head, text=f"Файл: {item['source_file']}", anchor="w",
                text_color=("gray50", "#777777"), font=ctk.CTkFont(size=11),
            ).grid(row=2, column=0, sticky="w")

        body = ctk.CTkTextbox(card, height=90, corner_radius=10,
                               font=ctk.CTkFont(size=12), wrap="word")
        body.grid(row=1, column=0, sticky="ew", padx=14, pady=(6, 10))
        text = (
            f"Заготовка: {item.get('result_line', '')}\n"
            f"Материал: {item.get('material_mark', '')} {item.get('material_gost', '')}\n"
            f"L детали: {item.get('l_part_mm', '—')} мм   →   "
            f"L заготовки: {item.get('l_blank_mm', '—')} мм\n"
            f"Масса заготовки: {item.get('mass_kg', '—')} кг"
        )
        body.insert("1.0", text)
        body.configure(state="disabled")

        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.grid(row=2, column=0, sticky="e", padx=14, pady=(0, 12))

        ctk.CTkButton(
            btns, text="Отклонить", height=32, corner_radius=10, width=110,
            fg_color=("gray85", "#3a2020"), hover_color=("gray78", "#4a2828"),
            text_color=("gray10", "#eaaaaa"),
            command=lambda it=item: self.on_reject(it),
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            btns, text="Подтвердить → в базу", height=32, corner_radius=10, width=170,
            fg_color="#1f6aa5", hover_color="#185580",
            command=lambda it=item: self.on_approve(it),
        ).pack(side="right")

    # ── Действия ────────────────────────────────────────────

    def on_approve(self, item: dict):
        ok = messagebox.askyesno(
            "Подтвердить эталон?",
            f"«{item.get('part_name', '?')}» станет новым эталоном в базе "
            f"и будет использоваться для будущих ответов RAG.\n\n"
            f"{item.get('result_line', '')}\n\nПодтвердить?"
        )
        if not ok:
            return
        try:
            confirm_queue.approve(item["id"])
            messagebox.showinfo("Подтверждено", "Запись сохранена в базу эталонов.")
        except Exception as e:                                # noqa: BLE001
            messagebox.showerror("Ошибка подтверждения", str(e))
            logger.error("Ошибка подтверждения из очереди", e)
        self.refresh()

    def on_reject(self, item: dict):
        ok = messagebox.askyesno(
            "Отклонить?",
            f"«{item.get('part_name', '?')}» будет убран из очереди без "
            f"сохранения в базу эталонов. Продолжить?"
        )
        if not ok:
            return
        try:
            confirm_queue.reject(item["id"])
        except Exception as e:                                # noqa: BLE001
            messagebox.showerror("Ошибка отклонения", str(e))
            logger.error("Ошибка отклонения из очереди", e)
        self.refresh()
