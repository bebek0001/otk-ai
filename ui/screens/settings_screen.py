import customtkinter as ctk
from tkinter import messagebox
from core.settings import load_settings, save_settings


class SettingsScreen(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.settings = load_settings()

        # Заголовок
        ctk.CTkLabel(
            self, text="Настройки",
            font=ctk.CTkFont(size=24, weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 8))

        # Вкладки
        self.tabs = ctk.CTkTabview(
            self, corner_radius=12,
            segmented_button_selected_color=("gray70", "gray35"),
            segmented_button_selected_hover_color=("gray65", "gray40"),
            segmented_button_unselected_color=("gray88", "gray20"),
            segmented_button_unselected_hover_color=("gray80", "gray28"),
            segmented_button_fg_color=("gray85", "gray17"),
            text_color=("gray10", "gray90"),
        )
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        self.tabs.add("Промпты ИИ")
        self.tabs.add("Расчёты")
        self.tabs.add("Интерфейс")
        self.tabs.add("О программе")

        self._build_prompts_tab()
        self._build_calc_tab()
        self._build_ui_tab()
        self._build_about_tab()

        # Кнопка сохранить
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=2, column=0, sticky="e", padx=18, pady=(0, 18))
        ctk.CTkButton(
            btn_row, text="Сохранить настройки",
            height=36, corner_radius=12,
            command=self.on_save
        ).pack(side="right")
        ctk.CTkButton(
            btn_row, text="Сбросить по умолчанию",
            height=36, corner_radius=12,
            fg_color="transparent",
            border_width=1,
            command=self.on_reset
        ).pack(side="right", padx=(0, 8))

    # ── Промпты ─────────────────────────────────────────────

    def _build_prompts_tab(self):
        tab = self.tabs.tab("Промпты ИИ")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)
        tab.grid_rowconfigure(4, weight=1)

        # Модель ИИ
        model_frame = ctk.CTkFrame(tab, fg_color="transparent")
        model_frame.grid(row=0, column=0, sticky="ew", pady=(8, 6))
        ctk.CTkLabel(model_frame, text="Модель ИИ:", width=120, anchor="w").pack(side="left")
        self.var_model = ctk.StringVar(value=self.settings.get("ai_model", "gpt-4o-mini"))
        ctk.CTkOptionMenu(
            model_frame,
            values=["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
            variable=self.var_model,
            width=200, height=32, corner_radius=8,
            fg_color=("gray88", "gray25"),
            button_color=("gray75", "gray35"),
            button_hover_color=("gray65", "gray40"),
            text_color=("gray10", "gray90"),
            dropdown_fg_color=("gray95", "gray20"),
            dropdown_hover_color=("gray85", "gray30"),
            dropdown_text_color=("gray10", "gray90"),
        ).pack(side="left")

        # Движок распознавания чертежа (Vision)
        engine_frame = ctk.CTkFrame(tab, fg_color="transparent")
        engine_frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        ctk.CTkLabel(engine_frame, text="Движок Vision:", width=120, anchor="w").pack(side="left")

        # Отображаемые названия ↔ внутренние значения
        self._vision_map = {"GPT (OpenAI)": "openai", "Claude (Anthropic)": "claude"}
        self._vision_map_rev = {v: k for k, v in self._vision_map.items()}
        cur_engine = (self.settings.get("vision_engine", "openai") or "openai").lower()
        self.var_vision = ctk.StringVar(
            value=self._vision_map_rev.get(cur_engine, "GPT (OpenAI)")
        )
        ctk.CTkOptionMenu(
            engine_frame,
            values=list(self._vision_map.keys()),
            variable=self.var_vision,
            width=200, height=32, corner_radius=8,
            fg_color=("gray88", "gray25"),
            button_color=("gray75", "gray35"),
            button_hover_color=("gray65", "gray40"),
            text_color=("gray10", "gray90"),
            dropdown_fg_color=("gray95", "gray20"),
            dropdown_hover_color=("gray85", "gray30"),
            dropdown_text_color=("gray10", "gray90"),
        ).pack(side="left")
        ctk.CTkLabel(
            engine_frame,
            text="   какой ИИ «смотрит» на чертёж при поиске заготовки",
            text_color=("gray50", "#888888"), font=ctk.CTkFont(size=11),
        ).pack(side="left")

        ctk.CTkLabel(tab, text="Промпт для PDF инструмента:",
                     font=ctk.CTkFont(size=12), anchor="w").grid(
            row=2, column=0, sticky="w", pady=(0, 4)
        )
        self.txt_pdf = ctk.CTkTextbox(tab, corner_radius=10, height=160)
        self.txt_pdf.grid(row=3, column=0, sticky="nsew", pady=(0, 10))
        self.txt_pdf.insert("1.0", self.settings.get("ai_prompt_pdf", ""))

        ctk.CTkLabel(tab, text="Промпт для 3D инструмента:",
                     font=ctk.CTkFont(size=12), anchor="w").grid(
            row=4, column=0, sticky="w", pady=(0, 4)
        )
        self.txt_3d = ctk.CTkTextbox(tab, corner_radius=10, height=120)
        self.txt_3d.grid(row=5, column=0, sticky="nsew")
        self.txt_3d.insert("1.0", self.settings.get("ai_prompt_3d", ""))

    # ── Расчёты ─────────────────────────────────────────────

    def _build_calc_tab(self):
        tab = self.tabs.tab("Расчёты")
        tab.grid_columnconfigure(1, weight=1)

        rows = [
            ("Припуск по длине (мм):",          "allowance_length_mm", "20",
             "Прибавляется к длине детали для стандартных заготовок"),
            ("Припуск для малых деталей (мм):",  "allowance_small_mm",  "5",
             "Для деталей длиной < 50 мм"),
            ("Плотность стали (кг/м³):",         "density_steel",       "7850",
             "Используется при расчёте массы заготовки"),
            ("Макс. страниц PDF для чтения:",    "pdf_max_pages",       "3",
             "Сколько страниц читать из чертежа (1–10)"),
        ]

        self._calc_vars = {}
        for i, (label, key, default, hint) in enumerate(rows):
            ctk.CTkLabel(tab, text=label, anchor="w").grid(
                row=i*2, column=0, padx=(0, 16), pady=(14, 2), sticky="w"
            )
            var = ctk.StringVar(value=self.settings.get(key, default))
            self._calc_vars[key] = var
            ctk.CTkEntry(tab, textvariable=var, height=34, corner_radius=8, width=120).grid(
                row=i*2, column=1, sticky="w", pady=(14, 2)
            )
            ctk.CTkLabel(tab, text=hint, text_color=("gray50", "#888888"),
                         font=ctk.CTkFont(size=11), anchor="w").grid(
                row=i*2+1, column=0, columnspan=2, sticky="w", pady=(0, 2)
            )

        # Автосохранение эталонов
        r = len(rows) * 2
        ctk.CTkLabel(tab, text="Автосохранение эталонов:", anchor="w").grid(
            row=r, column=0, padx=(0, 16), pady=(14, 2), sticky="w"
        )
        self.var_autosave = ctk.StringVar(
            value="1" if self.settings.get("auto_save_etalon", "0") == "1" else "0"
        )
        switch = ctk.CTkSwitch(
            tab, text="Сохранять эталон автоматически после расчёта",
            variable=self.var_autosave, onvalue="1", offvalue="0",
            font=ctk.CTkFont(size=12),
        )
        switch.grid(row=r, column=1, sticky="w", pady=(14, 2))
        ctk.CTkLabel(
            tab, text="Если включено — эталон сохраняется без нажатия кнопки",
            text_color=("gray50", "#888888"), font=ctk.CTkFont(size=11), anchor="w"
        ).grid(row=r+1, column=0, columnspan=2, sticky="w", pady=(0, 2))

        # Разделитель
        ctk.CTkFrame(tab, height=1, fg_color=("gray80", "#333333")).grid(
            row=r+2, column=0, columnspan=2, sticky="ew", pady=(12, 8)
        )

        # Папка для экспорта
        ctk.CTkLabel(tab, text="Папка для сохранения Excel:", anchor="w").grid(
            row=r+3, column=0, padx=(0, 16), pady=(0, 4), sticky="w"
        )
        ctk.CTkLabel(
            tab, text="Сюда будут сохраняться все выгрузки из пакетной обработки по умолчанию",
            text_color=("gray50", "#888888"), font=ctk.CTkFont(size=11), anchor="w"
        ).grid(row=r+4, column=0, columnspan=2, sticky="w", pady=(0, 6))

        self.var_export_folder = ctk.StringVar(
            value=self.settings.get("export_folder", "")
        )
        folder_frame = ctk.CTkFrame(tab, fg_color="transparent")
        folder_frame.grid(row=r+5, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        folder_frame.grid_columnconfigure(0, weight=1)

        self.entry_export_folder = ctk.CTkEntry(
            folder_frame,
            textvariable=self.var_export_folder,
            height=34, corner_radius=8,
            placeholder_text="Не выбрана — будет запрашиваться каждый раз",
        )
        self.entry_export_folder.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        ctk.CTkButton(
            folder_frame, text="Выбрать папку",
            height=34, corner_radius=8, width=130,
            fg_color=("gray88", "gray25"),
            hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"),
            command=self._on_pick_export_folder,
        ).grid(row=0, column=1)

        ctk.CTkButton(
            folder_frame, text="Очистить",
            height=34, corner_radius=8, width=80,
            fg_color=("gray88", "gray25"),
            hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"),
            command=lambda: self.var_export_folder.set(""),
        ).grid(row=0, column=2, padx=(8, 0))

    # ── Интерфейс ───────────────────────────────────────────

    def _build_ui_tab(self):
        tab = self.tabs.tab("Интерфейс")
        tab.grid_columnconfigure(1, weight=1)

        # Тема
        ctk.CTkLabel(tab, text="Тема оформления:", anchor="w").grid(
            row=0, column=0, padx=(0, 16), pady=(14, 6), sticky="w"
        )
        self.var_theme = ctk.StringVar(value=self.settings.get("ui_theme", "Тёмная"))

        radio_frame = ctk.CTkFrame(tab, fg_color="transparent")
        radio_frame.grid(row=0, column=1, sticky="w", pady=(14, 6))
        for _theme_name in ["Тёмная", "Светлая", "Системная"]:
            ctk.CTkRadioButton(
                radio_frame,
                text=_theme_name,
                variable=self.var_theme,
                value=_theme_name,
                command=lambda v=_theme_name: self._on_theme_preview(v),
                font=ctk.CTkFont(size=13),
                radiobutton_width=16,
                radiobutton_height=16,
            ).pack(side="left", padx=(0, 20))

        ctk.CTkLabel(
            tab, text="Изменение применяется сразу",
            text_color=("gray50", "#888888"), font=ctk.CTkFont(size=11), anchor="w"
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))

        # Разделитель
        ctk.CTkFrame(tab, height=1, fg_color=("gray80", "#333333")).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=8
        )

        # Язык
        ctk.CTkLabel(tab, text="Язык интерфейса:", anchor="w").grid(
            row=3, column=0, padx=(0, 16), pady=(12, 4), sticky="w"
        )
        self.var_lang = ctk.StringVar(value=self.settings.get("language", "Русский"))
        ctk.CTkOptionMenu(
            tab,
            values=["Русский"],
            variable=self.var_lang,
            width=160, height=32, corner_radius=8,
            state="disabled",
            fg_color=("gray88", "gray25"),
            button_color=("gray75", "gray35"),
            text_color=("gray40", "gray60"),
        ).grid(row=3, column=1, sticky="w", pady=(12, 4))
        ctk.CTkLabel(
            tab, text="Другие языки будут добавлены в следующих версиях",
            text_color=("gray50", "#888888"), font=ctk.CTkFont(size=11), anchor="w"
        ).grid(row=4, column=0, columnspan=2, sticky="w")

    def _on_theme_preview(self, value):
        """Предпросмотр темы без сохранения."""
        mapping = {"Тёмная": "dark", "Светлая": "light", "Системная": "system"}
        import customtkinter as ctk2
        ctk2.set_appearance_mode(mapping.get(value, "dark"))

    # ── О программе ─────────────────────────────────────────

    def _build_about_tab(self):
        tab = self.tabs.tab("О программе")
        tab.grid_columnconfigure(0, weight=1)

        info_frame = ctk.CTkFrame(tab, corner_radius=12)
        info_frame.grid(row=0, column=0, sticky="ew", pady=(14, 12))
        info_frame.grid_columnconfigure(1, weight=1)

        fields = [
            ("Приложение:",  "OTK.AI"),
            ("Версия:",      "1.0.0"),
            ("Назначение:",  "Определение недостающих заготовок на чертежах по ГОСТ"),
            ("ИИ модель:",   "OpenAI GPT-4o-mini"),
            ("Форматы:",     "PDF чертежи, 3D модели"),
        ]
        for i, (label, value) in enumerate(fields):
            ctk.CTkLabel(
                info_frame, text=label, anchor="w",
                text_color=("gray50", "#888888"), font=ctk.CTkFont(size=12)
            ).grid(row=i, column=0, padx=16, pady=4, sticky="w")
            ctk.CTkLabel(
                info_frame, text=value, anchor="w",
                font=ctk.CTkFont(size=12)
            ).grid(row=i, column=1, padx=16, pady=4, sticky="w")

        # База эталонов статистика
        try:
            from core.drawing_db import db_stats
            stats = db_stats()
            ctk.CTkLabel(
                tab,
                text=f"База эталонов: {stats['total']} чертежей  |  "
                     f"Типы: {', '.join(f'{k}: {v}' for k, v in stats['by_type'].items())}",
                font=ctk.CTkFont(size=12),
                text_color=("green4", "#4CAF50"),
                anchor="w",
            ).grid(row=1, column=0, sticky="w", pady=(0, 8), padx=4)
        except Exception:
            pass

        ctk.CTkLabel(
            tab,
            text="Производственное ПО для ОТК. Все расчёты выполняются\n"
                 "на основе ГОСТ и базы эталонных чертежей предприятия.",
            text_color=("gray45", "#666666"),
            font=ctk.CTkFont(size=11),
            anchor="w",
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=8, padx=4)

    # ── Сохранение ──────────────────────────────────────────

    def _on_pick_export_folder(self):
        from tkinter import filedialog
        folder = filedialog.askdirectory(title="Выберите папку для сохранения Excel")
        if folder:
            self.var_export_folder.set(folder)

    def on_save(self):
        s = self.settings.copy()
        s["ai_prompt_pdf"]      = self.txt_pdf.get("1.0", "end").strip()
        s["ai_prompt_3d"]       = self.txt_3d.get("1.0", "end").strip()
        s["ai_model"]           = self.var_model.get()
        s["vision_engine"]      = self._vision_map.get(self.var_vision.get(), "openai")
        s["ui_theme"]           = self.var_theme.get()
        s["language"]           = self.var_lang.get()
        s["auto_save_etalon"]   = self.var_autosave.get()
        s["export_folder"]      = self.var_export_folder.get().strip()

        for key, var in self._calc_vars.items():
            s[key] = var.get().strip()

        save_settings(s)
        self.settings = s
        messagebox.showinfo("Сохранено", "Настройки сохранены.")

    def on_reset(self):
        if messagebox.askyesno("Сброс", "Сбросить все настройки по умолчанию?"):
            from core.settings import DEFAULTS
            self.settings = dict(DEFAULTS)
            # Обновляем поля
            self.txt_pdf.delete("1.0", "end")
            self.txt_pdf.insert("1.0", self.settings["ai_prompt_pdf"])
            self.txt_3d.delete("1.0", "end")
            self.txt_3d.insert("1.0", self.settings["ai_prompt_3d"])
            self.var_model.set(self.settings["ai_model"])
            self.var_vision.set(
                self._vision_map_rev.get(self.settings.get("vision_engine", "openai"), "GPT (OpenAI)")
            )
            self.var_theme.set(self.settings["ui_theme"])
            self.var_autosave.set(self.settings["auto_save_etalon"])
            for key, var in self._calc_vars.items():
                var.set(self.settings.get(key, ""))
            self.var_export_folder.set("")
            save_settings(self.settings)
            messagebox.showinfo("Сброшено", "Настройки сброшены по умолчанию.")