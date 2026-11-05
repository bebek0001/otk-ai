# -*- coding: utf-8 -*-
"""
ui/screens/m3d_only_tool.py — вкладка «М3D инструмент».

Только .m3d/.a3d, без STEP и без КОМПАС. Показывает:
  • свойства из файла (обозначение, материал, масса, литера и т.д.) и сырой дамп;
  • картинку детали (готовый 3D-вид, который КОМПАС кладёт в сам файл);
  • блок «Как в таблице»: те же строки и значения, что программа собирает в
    пакетной обработке (заготовка, размер, массы, источник, замечание);
  • кнопку «Проверить через ИИ» для строк, где размер правилами не определился.
"""

from __future__ import annotations

import os
import threading
import customtkinter as ctk
from ui import thread_bridge
from tkinter import filedialog, messagebox

from core.m3d_reader import read_m3d, M3DInfo
from core import logger, m3d_preview


def _dump_value(value, indent: int = 0) -> list[str]:
    pad = "  " * indent
    lines = []
    if isinstance(value, dict):
        if not value:
            lines.append(f"{pad}(пусто)")
        for k, v in value.items():
            if isinstance(v, dict):
                lines.append(f"{pad}{k}:")
                lines.extend(_dump_value(v, indent + 1))
            else:
                lines.append(f"{pad}{k} = {v}")
    else:
        lines.append(f"{pad}{value}")
    return lines


def format_rows(rows: list) -> str:
    out = []
    for r in rows:
        out.append(
            f"{r.get('drawing_no', '')}  {r.get('part_name', '')}\n"
            f"  Заготовка: {r.get('zagotovka_full') or r.get('new_sortament') or '—'}\n"
            f"  Размер: {r.get('stock_size') or '—'}    "
            f"Масса заг.: {r.get('stock_mass_kg') or '—'} кг    "
            f"Масса чист.: {r.get('clean_mass_kg') or '—'} кг\n"
            f"  Источник: {r.get('source') or '—'}"
            + (f"    Замечание: {r['error']}" if r.get("error") else ""))
    return "\n\n".join(out) if out else "Нет данных"


class M3DOnlyToolScreen(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self.m3d_path: str | None = None
        self.info: M3DInfo | None = None
        self._build_ui()

    def _build_ui(self):
        top = ctk.CTkFrame(self, corner_radius=18)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            top, text="М3D инструмент",
            font=ctk.CTkFont(size=20, weight="bold")
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(10, 2))

        ctk.CTkLabel(
            top,
            text="Только .m3d/.a3d, без STEP и без КОМПАСа. Показывает свойства "
                 "из файла, картинку детали и те же значения, которые попадут "
                 "в таблицу пакетной обработки (заготовка, размер, массы). "
                 "Размер заготовки читается из содержимого модели; если "
                 "правила его не нашли, можно проверить через ИИ.",
            text_color=("gray50", "#777777"), font=ctk.CTkFont(size=11),
            anchor="w", justify="left", wraplength=900,
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=16, pady=(0, 8))

        ctk.CTkLabel(top, text="Файл .m3d/.a3d:", anchor="w", width=190).grid(
            row=2, column=0, sticky="w", padx=(16, 8), pady=(0, 12))
        self.lbl_file = ctk.CTkLabel(
            top, text="не выбран", anchor="w", text_color=("gray50", "#777777"))
        self.lbl_file.grid(row=2, column=1, sticky="ew", pady=(0, 12))
        ctk.CTkButton(
            top, text="Выбрать .m3d", height=32, corner_radius=10, width=140,
            command=self.on_pick
        ).grid(row=2, column=2, padx=16, pady=(0, 12))

        mid = ctk.CTkFrame(self, fg_color="transparent")
        mid.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
        mid.grid_columnconfigure(0, weight=1)

        data = ctk.CTkFrame(mid, corner_radius=18)
        data.grid(row=0, column=0, sticky="nsew")
        data.grid_columnconfigure(1, weight=1)
        data.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(
            data, text="Разобранные поля",
            text_color=("gray40", "#A7A7A7"), font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=16, pady=(8, 4))

        rows = [
            ("Обозначение:",      "lbl_dno"),
            ("Наименование:",     "lbl_pname"),
            ("Материал:",         "lbl_material"),
            ("Марка / ГОСТ:",     "lbl_mark"),
            ("Плотность, кг/м³:", "lbl_density"),
            ("Масса (из КОМПАС), кг:", "lbl_mass"),
            ("Литера:",           "lbl_letter"),
            ("Раздел спецификации:", "lbl_section"),
            ("Класс точности:",   "lbl_accuracy"),
            ("Автор / организация:", "lbl_author"),
            ("Версия КОМПАС:",    "lbl_version"),
            ("Изменён:",          "lbl_modified"),
        ]
        for i, (label, attr) in enumerate(rows, start=1):
            r, c = divmod(i - 1, 2)
            col0 = c * 2
            ctk.CTkLabel(data, text=label, anchor="w").grid(
                row=r + 1, column=col0, sticky="w", padx=(16, 8), pady=2)
            lbl = ctk.CTkLabel(data, text="—", anchor="w",
                                text_color=("gray30", "#CCCCCC"), wraplength=380)
            lbl.grid(row=r + 1, column=col0 + 1, sticky="ew", pady=2, padx=(0, 16))
            setattr(self, attr, lbl)

        prev = ctk.CTkFrame(mid, corner_radius=18, width=300)
        prev.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        prev.grid_propagate(False)
        ctk.CTkLabel(prev, text="Деталь", text_color=("gray40", "#A7A7A7"),
                     font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=16, pady=(8, 0))
        self.lbl_preview = ctk.CTkLabel(prev, text="нет картинки", text_color=("gray50", "#777777"))
        self.lbl_preview.pack(expand=True, fill="both", padx=8, pady=8)
        self._preview_img = None

        tbl = ctk.CTkFrame(self, corner_radius=18)
        tbl.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))
        tbl.grid_columnconfigure(0, weight=1)
        th = ctk.CTkFrame(tbl, fg_color="transparent")
        th.grid(row=0, column=0, sticky="ew", padx=16, pady=(8, 4))
        ctk.CTkLabel(th, text="Как в таблице", text_color=("gray40", "#A7A7A7"),
                     font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        self.btn_xlsx = ctk.CTkButton(
            th, text="Сохранить в Excel", height=28, corner_radius=10, width=150,
            state="disabled", command=self.on_export)
        self.btn_xlsx.pack(side="right")
        self.btn_ai = ctk.CTkButton(
            th, text="Проверить через ИИ", height=28, corner_radius=10, width=160,
            fg_color=("gray88", "gray25"), hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"), state="disabled", command=self.on_ai)
        self.btn_ai.pack(side="right", padx=(0, 8))
        self.txt_tbl = ctk.CTkTextbox(tbl, height=120, corner_radius=14,
                                      font=ctk.CTkFont(family="Courier", size=12), wrap="none")
        self.txt_tbl.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 12))
        self._rows: list = []

        # ═══ Результат / Сырой дамп ═══
        out = ctk.CTkFrame(self, corner_radius=18)
        out.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 10))
        out.grid_columnconfigure(0, weight=1)
        out.grid_columnconfigure(1, weight=1)
        out.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(out, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(14, 6))
        ctk.CTkLabel(head, text="Протокол и сырые свойства из файла",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        self.btn_copy = ctk.CTkButton(
            head, text="Копировать всё", height=30, corner_radius=10, width=140,
            fg_color=("gray88", "gray25"), hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"), command=self.on_copy, state="disabled")
        self.btn_copy.pack(side="right")

        ctk.CTkLabel(out, text="Протокол",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=("gray40", "#A7A7A7")).grid(
            row=0, column=0, sticky="sw", padx=16)

        self.txt_prot = ctk.CTkTextbox(out, corner_radius=14,
                                       font=ctk.CTkFont(size=12), wrap="word")
        self.txt_prot.grid(row=1, column=0, sticky="nsew", padx=(16, 8), pady=(32, 16))

        ctk.CTkLabel(out, text="Все свойства (raw, как лежат в XML детали)",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=("gray40", "#A7A7A7")).grid(
            row=0, column=1, sticky="sw", padx=16)

        self.txt_raw = ctk.CTkTextbox(out, corner_radius=14,
                                      font=ctk.CTkFont(family="Courier", size=12),
                                      wrap="none")
        self.txt_raw.grid(row=1, column=1, sticky="nsew", padx=(8, 16), pady=(32, 16))

    # ── Выбор файла ─────────────────────────────────────────

    def on_pick(self):
        path = filedialog.askopenfilename(
            title="Выбери модель КОМПАС (.m3d/.a3d)",
            filetypes=[("КОМПАС", "*.m3d *.a3d"), ("All files", "*.*")])
        if not path:
            return
        self.m3d_path = path
        self.lbl_file.configure(text=os.path.basename(path), text_color=("gray10", "white"))
        self._clear_fields()
        self.txt_prot.delete("1.0", "end")
        self.txt_prot.insert("1.0", "Читаю файл…")
        self.txt_raw.delete("1.0", "end")
        self._rows = []
        self.btn_xlsx.configure(state="disabled")
        self.btn_ai.configure(state="disabled")
        self._set_table("Считаю…")
        self._set_preview(None)
        threading.Thread(target=self._read_worker, args=(path,), daemon=True).start()
        threading.Thread(target=self._table_worker, args=(path, False), daemon=True).start()

    def _clear_fields(self):
        for attr in ("lbl_dno", "lbl_pname", "lbl_material", "lbl_mark", "lbl_density",
                     "lbl_mass", "lbl_letter", "lbl_section", "lbl_accuracy",
                     "lbl_author", "lbl_version", "lbl_modified"):
            getattr(self, attr).configure(text="—")
        self.btn_copy.configure(state="disabled")

    def _read_worker(self, path: str):
        try:
            info = read_m3d(path)
        except Exception as e:                                # noqa: BLE001
            info = None
            err = str(e)
        else:
            err = ""
        thread_bridge.post(self._apply_result, info, err)

    def _apply_result(self, info: M3DInfo | None, err: str):
        self.info = info
        if info is None:
            self.txt_prot.delete("1.0", "end")
            self.txt_prot.insert(
                "1.0",
                "Не удалось прочитать файл.\n\n"
                + (f"Ошибка: {err}" if err else
                   "Либо это не .m3d/.a3d (не ZIP-контейнер КОМПАС), "
                   "либо в нём не нашлось свойств детали (MetaProductInfo).")
            )
            logger.error(f"M3D-only: не удалось прочитать {os.path.basename(self.m3d_path or '')}: {err}")
            return

        self.lbl_dno.configure(text=info.drawing_no or "—")
        self.lbl_pname.configure(text=info.part_name or "—")
        self.lbl_material.configure(text=info.material or "—")
        self.lbl_mark.configure(
            text=f"{info.material_mark or '—'} / {info.material_gost or '—'}")
        self.lbl_density.configure(
            text=str(info.density) if info.density is not None else "—")
        self.lbl_mass.configure(
            text=str(info.mass_kg) if info.mass_kg is not None else "—")
        self.lbl_letter.configure(text=info.revision_letter or "—")
        self.lbl_section.configure(text=info.spec_section or "—")
        self.lbl_accuracy.configure(text=info.accuracy_class or "—")
        author_org = " / ".join(x for x in (info.author, info.organization) if x) or "—"
        self.lbl_author.configure(text=author_org)
        self.lbl_version.configure(text=info.kompas_version or "—")
        self.lbl_modified.configure(text=info.modified or "—")

        self.txt_prot.delete("1.0", "end")
        self.txt_prot.insert("1.0", info.protocol)
        self.txt_prot.yview_moveto(0.0)

        raw_lines = ["all_props (всё, что нашлось в XML детали, без обработки):", ""]
        raw_lines.extend(_dump_value(info.all_props))
        self.txt_raw.delete("1.0", "end")
        self.txt_raw.insert("1.0", "\n".join(raw_lines))
        self.txt_raw.yview_moveto(0.0)

        self.btn_copy.configure(state="normal")
        logger.info(f"M3D-only: прочитан {info.part_name or info.drawing_no or '(без имени)'}")

    def on_copy(self):
        if not self.info:
            return
        text = self.info.protocol + "\n\n" + self.txt_raw.get("1.0", "end").strip()
        self.clipboard_clear()
        self.clipboard_append(text)
        messagebox.showinfo("Скопировано", "Протокол и сырые свойства скопированы в буфер обмена.")


    # ── Картинка и «как в таблице» ───────────────────────────

    def _set_preview(self, im):
        if im is None:
            self._preview_img = None
            self.lbl_preview.configure(image=None, text="нет картинки")
            return
        w, h = im.size
        k = min(1.0, 270.0 / w, 250.0 / h)
        size = (max(1, int(w * k)), max(1, int(h * k)))
        self._preview_img = ctk.CTkImage(light_image=im, dark_image=im, size=size)
        self.lbl_preview.configure(image=self._preview_img, text="")

    def _set_table(self, text: str):
        self.txt_tbl.configure(state="normal")
        self.txt_tbl.delete("1.0", "end")
        self.txt_tbl.insert("1.0", text)
        self.txt_tbl.configure(state="disabled")

    @staticmethod
    def _format_rows(rows: list) -> str:
        return format_rows(rows)

    def _table_worker(self, path: str, use_ai: bool):
        im = None if use_ai else m3d_preview.read_preview(path)
        try:
            from ui.screens import batch_tool as bt
            rows = bt.process_files(path, use_ai=use_ai)
            err = ""
        except Exception as e:                                # noqa: BLE001
            rows, err = [], str(e)
        thread_bridge.post(self._apply_table, path, rows, err, im, use_ai)

    def _apply_table(self, path: str, rows: list, err: str, im, use_ai: bool):
        if path != self.m3d_path:
            return                                            # пользователь уже выбрал другой файл
        if not use_ai:
            self._set_preview(im)
        self._rows = rows
        self._set_table(self._format_rows(rows) if rows else f"Не удалось собрать: {err}")
        self.btn_xlsx.configure(state="normal" if rows else "disabled")
        missing = any(not r.get("stock_size") for r in rows)
        if use_ai and missing:
            self.txt_tbl.configure(state="normal")
            self.txt_tbl.insert("end", "\n\nИИ не смог предложить размер, прошедший проверку по массе.")
            self.txt_tbl.configure(state="disabled")
        self.btn_ai.configure(state="normal" if (rows and missing and not use_ai) else "disabled")

    def on_ai(self):
        if not self.m3d_path:
            return
        self.btn_ai.configure(state="disabled")
        self._set_table("Запрос к ИИ…")
        threading.Thread(target=self._table_worker, args=(self.m3d_path, True),
                         daemon=True).start()

    def on_export(self):
        if not self._rows:
            return
        from ui.screens import batch_tool as bt
        path = filedialog.asksaveasfilename(
            title="Сохранить результаты", defaultextension=".xlsx",
            filetypes=[("Excel файл", "*.xlsx")],
            initialfile=os.path.splitext(os.path.basename(self.m3d_path or "модель"))[0] + ".xlsx")
        if not path:
            return
        try:
            bt._export_client(self._rows, path)
            messagebox.showinfo("Сохранено", f"Файл сохранён:\n{path}")
        except Exception as e:                                # noqa: BLE001
            messagebox.showerror("Ошибка экспорта", str(e))
