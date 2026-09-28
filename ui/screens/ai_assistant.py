# ===========================
# AI_ASSISTANT.PY — ИИ Ассистент
# ===========================

import os
import threading
import customtkinter as ctk
from tkinter import filedialog

from core.engine import (
    fitz,
    extract_from_pdf,
    ai_ask,
)
from core.gost_reader import get_gost_context_for_query, get_all_gosts_summary, get_gost_context_for_ai
from core.drawing_db import (
    get_all_etalons,
    lookup_etalon,
    db_stats,
)
from core import logger


# ============================================================
# Контекст базы для ИИ
# ============================================================

def build_db_context() -> str:
    records = get_all_etalons()
    if not records:
        return "База эталонов пуста."
    lines = [f"БАЗА ЭТАЛОННЫХ ЧЕРТЕЖЕЙ ({len(records)} записей):\n"]
    for r in records:
        lines.append(
            f"  {r.get('drawing_no','-'):15s} | {r.get('part_name','-'):20s} | "
            f"{r.get('result_line','').replace(chr(10), ' | ')}"
        )
    return "\n".join(lines)


# Стандартные диаметры круга по ГОСТ 2590-2006
ROUND_DIAMETERS = [
    5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,
    26,27,28,29,30,32,34,36,38,40,42,45,48,50,53,56,60,63,65,67,
    70,75,80,85,90,95,100,105,110,115,120,125,130,140,150,160,
    170,180,190,200,210,220,240,250,260,270,280,290,300
]

# Стандартные размеры квадрата по ГОСТ 2591-2006
SQUARE_SIZES = [
    6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,
    26,27,28,29,30,32,34,36,38,40,42,45,48,50,53,56,60,63,65,
    70,75,80,85,90,95,100
]

# Стандартные толщины листа по ГОСТ 19903-2015
SHEET_THICKNESSES = [
    2,2.5,3,3.5,4,4.5,5,6,7,8,9,10,11,12,13,14,15,16,18,20,22,
    25,28,30,32,35,36,38,40,45,50,55,60,65,70,75,80,85,90,95,
    100,105,110,120,125,130,140,150,160,180,200
]


def _next_std(val: float, std_list: list) -> int:
    """Возвращает ближайший стандартный размер >= val."""
    for s in std_list:
        if s >= val:
            return s
    return std_list[-1]


SYSTEM_PROMPT = """Ты — технолог ОТК (отдел технического контроля) на машиностроительном производстве.
Твоя задача: определять НЕДОСТАЮЩУЮ заготовку на чертеже по ГОСТ.

=== ПРАВИЛА ОПРЕДЕЛЕНИЯ ТИПА ЗАГОТОВКИ ===

1. КРУГ (пруток круглый, ГОСТ 2590-2006):
   - Деталь типа вал, ось, болт, шпиндель — тела вращения
   - Тип "Круг" пишется в штампе чертежа рядом с ГОСТ 2590
   - Диаметр заготовки = ближайший стандартный по ГОСТ 2590 >= максимального диаметра детали
   - Стандартный ряд: 5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,
     30,32,34,36,38,40,42,45,48,50,53,56,60,63,65,70,75,80,85,90,95,100,105,110,115,120,125,
     130,140,150,160,170,180,190,200
   - Длина заготовки = длина детали + 20 мм (если деталь > 50мм) или + 5-10 мм (если < 50мм)

2. ЛИСТ горячекатаный (ГОСТ 19903-2015):
   - Деталь плоская: диск, фланец, крышка, платик, шайба, вставка
   - Тип "Лист" пишется в штампе чертежа рядом с ГОСТ 19903
   - Толщина листа = ближайший стандартный по ГОСТ 19903 >= высоты/толщины детали
   - Стандартный ряд: 2,3,4,5,6,8,10,12,14,16,18,20,25,28,30,32,36,40,45,50,55,60,65,70,
     75,80,85,90,95,100,110,120,130,140,150,160,180,200
   - Для круглых деталей: габарит □D (диаметр заготовки = наружный диаметр детали + 10..30мм)
   - Для прямоугольных: BхL (ширина x длина детали + 10мм на каждую сторону)

3. ТРУБА бесшовная (ГОСТ 8732-78 горячедеформированная / ГОСТ 8734-75 холоднодеформированная):
   - Деталь полая: втулка, гильза, цилиндр
   - Тип "Труба" пишется в штампе
   - Размер: ODxS где OD=наружный диаметр, S=толщина стенки
   - Длина заготовки = длина детали + 20 мм

4. КВАДРАТ (ГОСТ 2591-2006):
   - Деталь с квадратным сечением: брус, шпонка квадратная
   - Размер = ближайший стандартный >= стороны квадрата детали
   - Длина заготовки = длина детали + 20 мм

=== АЛГОРИТМ ДЛЯ КРУГА (САМЫЙ ЧАСТЫЙ СЛУЧАЙ) ===

Шаг 1: Найди тип из штампа чертежа (строка "Круг N ГОСТ 2590-2006" или "Лист N ГОСТ 19903")
Шаг 2: Найди максимальный диаметр детали из размеров на чертеже (Ø с наибольшим значением)
Шаг 3: Диаметр заготовки = ближайший стандартный по ГОСТ 2590 >= этого диаметра
Шаг 4: Найди длину детали (размер вдоль оси, обычно горизонтальный)
Шаг 5: Длина заготовки = длина детали + припуск (20мм для деталей > 50мм, 5-10мм для < 50мм)
Шаг 6: Масса = π/4 × D² × L × 7850 кг/м³ (где D и L в метрах)

=== ПРИМЕР РАСЧЁТА ===
Чертёж: Ось, Ø17g6, длина 40мм, материал Ст3пс ГОСТ 535-2005
- Тип: Круг (из штампа)
- Макс. диаметр детали: 17мм
- Стандартный ряд: ...16, 17, 18, 19, 20... → ближайший >= 17 = 17мм
  НО: нужен припуск на обработку! Ø17 g6 — шлифованная поверхность → нужен Ø20
  Правило: если допуск g6,f7,h6 (финишная обработка) → берём следующий размер выше
- Диаметр заготовки: 20мм
- Длина детали: 40мм, деталь маленькая → припуск 5мм → L=45мм
- Масса: π/4 × 0.020² × 0.045 × 7850 = 0,11 кг → округляем по факту = 0,08 кг

=== ФОРМАТ ОТВЕТА (строго) ===
[Тип] [размер] [ГОСТ сортамента];
[Марка материала] [ГОСТ материала];
L=[длина заготовки] или □[габарит] или [BхL];
Масса заготовки, кг: [масса]

=== ВАЖНО ===
- Всегда используй базу эталонов как ГЛАВНЫЙ ориентир
- Если в базе есть похожая деталь — применяй ту же логику
- Диаметр заготовки НИКОГДА не равен диаметру детали без припуска
- Не придумывай — если не уверен, напиши что именно неясно
"""


# ============================================================
# Экран ИИ Ассистента
# ============================================================

class AIAssistantScreen(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.pdf_path: str | None = None
        self.pdf_text: str | None = None
        self.chat_history: list = []
        self._thinking = False

        self._build_ui()
        self._show_welcome()

    def _build_ui(self):
        # Верхняя панель
        top = ctk.CTkFrame(self, corner_radius=18)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            top, text="ИИ Ассистент",
            font=ctk.CTkFont(size=16, weight="bold")
        ).grid(row=0, column=0, padx=16, pady=12, sticky="w")

        stats = db_stats()
        self.lbl_db = ctk.CTkLabel(
            top,
            text=f"База: {stats['total']} эталонов",
            text_color=("green4", "#4CAF50"),
            font=ctk.CTkFont(size=12)
        )
        self.lbl_db.grid(row=0, column=1, padx=8, pady=12, sticky="w")

        btn_frame = ctk.CTkFrame(top, fg_color="transparent")
        btn_frame.grid(row=0, column=2, padx=12, pady=8, sticky="e")

        ctk.CTkButton(
            btn_frame, text="Загрузить PDF",
            height=34, corner_radius=10, width=130,
            fg_color=("gray88", "gray25"),
            hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"),
            command=self.on_load_pdf
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_frame, text="Очистить чат",
            height=34, corner_radius=10, width=120,
            fg_color=("gray88", "gray25"),
            hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"),
            command=self.on_clear_chat
        ).pack(side="left", padx=4)

        self.lbl_pdf = ctk.CTkLabel(
            top, text="PDF не загружен", text_color=("gray35", "#888888"),
            font=ctk.CTkFont(size=11)
        )
        self.lbl_pdf.grid(row=1, column=0, columnspan=3, padx=16, pady=(0, 10), sticky="w")

        # Чат
        chat_frame = ctk.CTkFrame(self, corner_radius=18)
        chat_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 6))
        chat_frame.grid_rowconfigure(0, weight=1)
        chat_frame.grid_columnconfigure(0, weight=1)

        self.txt_chat = ctk.CTkTextbox(
            chat_frame, corner_radius=14,
            font=ctk.CTkFont(family="Courier", size=13),
            wrap="word", state="disabled",
            fg_color=("gray95", "gray17"),
            text_color=("gray10", "gray90"),
        )
        self.txt_chat.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)

        self.txt_chat.tag_config("user",   foreground="#1565C0")
        self.txt_chat.tag_config("ai",     foreground="#2E7D32")
        self.txt_chat.tag_config("system", foreground="#757575")
        self.txt_chat.tag_config("pdf",    foreground="#E65100")
        self.txt_chat.tag_config("error",  foreground="#C62828")

        # Поле ввода
        input_frame = ctk.CTkFrame(self, corner_radius=18)
        input_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        input_frame.grid_columnconfigure(0, weight=1)

        self.entry = ctk.CTkEntry(
            input_frame,
            placeholder_text="Задай вопрос о чертеже или заготовке...",
            height=42, corner_radius=12,
            font=ctk.CTkFont(size=13)
        )
        self.entry.grid(row=0, column=0, padx=(12, 6), pady=10, sticky="ew")
        self.entry.bind("<Return>", lambda e: self.on_send())

        self.btn_send = ctk.CTkButton(
            input_frame, text="Отправить",
            height=42, corner_radius=12, width=130,
            fg_color=("gray88", "gray25"),
            hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"),
            command=self.on_send
        )
        self.btn_send.grid(row=0, column=1, padx=(0, 12), pady=10)

        # Быстрые кнопки
        quick_frame = ctk.CTkFrame(input_frame, fg_color="transparent")
        quick_frame.grid(row=1, column=0, columnspan=2, padx=12, pady=(0, 8), sticky="w")

        quick_btns = [
            ("Показать базу",    self.on_show_db),
            ("Анализ чертежа",   self.on_analyze_pdf),
            ("Логика подбора",   self.on_help_blank),
            ("Показать ГОСТы",   self.on_show_gosts),
        ]
        for label, cmd in quick_btns:
            ctk.CTkButton(
                quick_frame, text=label, height=28,
                corner_radius=8, width=160,
                fg_color=("gray88", "gray25"),
                hover_color=("gray80", "gray30"),
                text_color=("gray10", "gray90"),
                font=ctk.CTkFont(size=11),
                command=cmd
            ).pack(side="left", padx=4)

    # ── Вывод в чат ─────────────────────────────────────────

    def _append(self, text: str, tag: str = "ai"):
        self.txt_chat.configure(state="normal")
        self.txt_chat.insert("end", text + "\n", tag)
        self.txt_chat.see("end")
        self.txt_chat.configure(state="disabled")

    def _show_welcome(self):
        stats = db_stats()
        self._append(
            f"OTK.AI — ИИ Ассистент технолога\n"
            f"База эталонов: {stats['total']} чертежей | "
            f"Типы: {stats['by_type']}\n"
            f"{'─' * 55}",
            tag="system"
        )
        self._append(
            "Загрузи PDF чертежа или задай вопрос текстом.\n"
            "Я знаю все эталоны из базы и помогу определить заготовку.\n",
            tag="ai"
        )

    # ── Загрузка PDF ────────────────────────────────────────

    def on_load_pdf(self):
        path = filedialog.askopenfilename(
            title="Выбери PDF чертёж",
            filetypes=[("PDF files", "*.pdf")]
        )
        if not path:
            return

        if fitz is None:
            self._append("PyMuPDF не установлен. pip install pymupdf", "error")
            return

        try:
            ex = extract_from_pdf(path, max_pages=2)
            self.pdf_path = path
            self.pdf_text = ex.pdf_text
            name = os.path.basename(path)
            self.lbl_pdf.configure(text=f"Загружен: {name}", text_color=("darkorange3", "#FFB74D"))
            logger.info(f"ИИ Ассистент: загружен PDF {name}")

            found = lookup_etalon(ex.pdf_text, ex.part_name)
            self._append(f"\nЗагружен: {name}", "pdf")

            if found:
                self._append(
                    f"Чертёж найден в базе эталонов.\n\n"
                    f"Чертёж: {found['drawing_no']} / {found['part_name']}\n"
                    f"Материал: {found['material_mark']} {found['material_gost']}\n\n"
                    f"НЕТ НА ЧЕРТЕЖЕ:\n{found['result_line']}\n",
                    tag="ai"
                )
            else:
                self._append(
                    "Чертёж не найден в базе. "
                    "Нажми «Анализ чертежа» — ИИ определит заготовку по аналогии с базой.\n",
                    tag="system"
                )

        except Exception as e:
            self._append(f"Ошибка загрузки PDF: {e}", "error")
            logger.error("ИИ Ассистент: ошибка загрузки PDF", e)

    # ── Быстрые действия ────────────────────────────────────

    def on_show_db(self):
        records = get_all_etalons()
        if not records:
            self._append("База эталонов пуста.", "system")
            return
        self._append("\nБаза эталонов:", "system")
        for r in records:
            line = r.get('result_line', '').replace('\n', ' | ')
            self._append(
                f"  {r['drawing_no']:15s} {r.get('part_name',''):20s} -> {line}",
                "ai"
            )
        self._append("", "system")

    def on_analyze_pdf(self):
        if not self.pdf_text:
            self._append("Сначала загрузи PDF чертежа.", "system")
            return
        self._send_to_ai(
            "Проанализируй этот чертёж и определи недостающую заготовку. "
            "Используй базу эталонов как ориентир. Дай ответ в стандартном формате."
        )

    def on_help_blank(self):
        self._send_to_ai(
            "Объясни кратко логику определения типа и размера заготовки. "
            "Приведи примеры из базы эталонов."
        )

    # ── Отправка сообщения ──────────────────────────────────

    def on_send(self):
        msg = self.entry.get().strip()
        if not msg:
            return
        self.entry.delete(0, "end")
        self._send_to_ai(msg)

    def _send_to_ai(self, user_msg: str):
        if self._thinking:
            return

        self._append(f"\nТехнолог: {user_msg}", "user")
        self.chat_history.append({"role": "user", "content": user_msg})

        self._thinking = True
        self.btn_send.configure(state="disabled", text="Ожидание...")
        self._append("ИИ: ...", "system")

        def worker():
            try:
                prompt = self._build_prompt(user_msg)
                answer = ai_ask(prompt, model="gpt-4o-mini")
                self.chat_history.append({"role": "assistant", "content": answer})
                logger.info(f"ИИ Ассистент: ответ получен, {len(answer)} символов")
            except Exception as e:
                answer = f"Ошибка: {e}"
                logger.error("ИИ Ассистент: ошибка запроса", e)

            def ui():
                self.txt_chat.configure(state="normal")
                content = self.txt_chat.get("1.0", "end")
                last = content.rfind("ИИ: ...")
                if last >= 0:
                    line_no = content[:last].count("\n") + 1
                    self.txt_chat.delete(f"{line_no}.0", f"{line_no}.end+1c")
                self.txt_chat.configure(state="disabled")

                self._append(f"ИИ: {answer}\n", "ai")
                self.btn_send.configure(state="normal", text="Отправить")
                self._thinking = False

            self.after(0, ui)

        threading.Thread(target=worker, daemon=True).start()

    def _build_prompt(self, user_msg: str) -> str:
        db_ctx = build_db_context()

        pdf_ctx = ""
        if self.pdf_text:
            name = os.path.basename(self.pdf_path or "")
            pdf_short = self.pdf_text[-800:].strip() if len(self.pdf_text) > 800 else self.pdf_text
            pdf_ctx = (
                f"\nЗАГРУЖЕНЫЙ ЧЕРТЁЖ: {name}\n"
                f"Текст PDF:\n{pdf_short}\n"
            )

        history_ctx = ""
        if len(self.chat_history) > 1:
            recent = self.chat_history[-6:-1]
            history_lines = []
            for m in recent:
                role = "Технолог" if m["role"] == "user" else "ИИ"
                history_lines.append(f"{role}: {m['content']}")
            history_ctx = "\nИСТОРИЯ ДИАЛОГА:\n" + "\n".join(history_lines) + "\n"

        # ГОСТ контекст — читаем таблицы из локальных PDF
        gost_ctx = get_gost_context_for_query(user_msg)
        # Если PDF загружен — добавляем ГОСТ для определённого типа
        if not gost_ctx and self.pdf_text:
            # Пробуем определить тип из PDF текста
            import re
            for kw in ['Круг', 'Лист', 'Труба', 'Квадрат', 'Шестигранник']:
                if re.search(kw, self.pdf_text, re.IGNORECASE):
                    gost_ctx = get_gost_context_for_ai(kw)
                    break

        return (
            f"{SYSTEM_PROMPT}\n\n"
            f"{db_ctx}\n\n"
            f"{gost_ctx}"
            f"{pdf_ctx}"
            f"{history_ctx}"
            f"\nВОПРОС: {user_msg}\n\n"
            f"Дай чёткий конкретный ответ. "
            f"Если определяешь заготовку — используй стандартный формат."
        )

    # ── Утилиты ─────────────────────────────────────────────

    def on_show_gosts(self):
        summary = get_all_gosts_summary()
        self._append("\n" + summary + "\n", "system")
        self._append(
            "Спроси меня про любой ГОСТ — например: "
            "'покажи таблицу диаметров ГОСТ 2590' или "
            "'какие размеры листа по ГОСТ 19903'\n",
            "ai"
        )

    def on_clear_chat(self):
        self.txt_chat.configure(state="normal")
        self.txt_chat.delete("1.0", "end")
        self.txt_chat.configure(state="disabled")
        self.chat_history.clear()
        self.pdf_path = None
        self.pdf_text = None
        self.lbl_pdf.configure(text="PDF не загружен", text_color=("gray35", "#888888"))
        self._show_welcome()
        logger.info("ИИ Ассистент: чат очищен")