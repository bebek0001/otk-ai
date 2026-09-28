# ===========================
# STEP_READER.PY — чтение 3D-моделей (STEP/STP)
# ===========================
#
# Читает 3D-модели в формате STEP (.stp/.step), экспортированные из КОМПАС,
# и извлекает всё нужное для определения заготовки:
#   • обозначение и наименование детали (из метаданных STEP)
#   • габариты по осям X/Y/Z
#   • объём детали и габаритный объём
#   • массу заготовки и массу чистовую
#   • тип формы (плоская → лист, тело вращения → круг/труба, призма → квадрат)
#
# ВАЖНО: формат КОМПАС .m3d — закрытый, напрямую не читается.
# Нужен экспорт в STEP (КОМПАС умеет, в т.ч. пакетно).
#
# МАТЕРИАЛ: при экспорте КОМПАС→STEP марка материала ТЕРЯЕТСЯ.
# Её нужно брать из чертежа, спецификации или вводить вручную.

import os
import re
import math
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple

from core import logger

# Плотности материалов (кг/м³)
DENSITY = {
    "сталь": 7850,
    "нержавейка": 7900,
    "алюминий": 2700,
    "медь": 8900,
    "латунь": 8500,
    "чугун": 7200,
}
DEFAULT_DENSITY = 7850.0


def ocp_available() -> tuple:
    """
    Проверяет, установлена ли библиотека OpenCASCADE (cadquery-ocp),
    без которой STEP читать нельзя.
    Возвращает (True, "") или (False, текст_ошибки).
    """
    try:
        from OCP.STEPControl import STEPControl_Reader  # noqa: F401
        return True, ""
    except Exception as e:
        return False, str(e)


@dataclass
class StepModel:
    """Результат разбора 3D-модели."""
    file_path: str
    drawing_no: str = ""          # обозначение (135517.03.00.051)
    part_name: str = ""           # наименование (Платик)

    # Габариты по осям, мм
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0

    # Отсортированные: длина >= ширина >= толщина
    length_mm: float = 0.0
    width_mm: float = 0.0
    thickness_mm: float = 0.0

    volume_mm3: float = 0.0        # реальный объём детали
    bbox_volume_mm3: float = 0.0   # габаритный объём (коробка)
    blank_volume_mm3: float = 0.0  # объём заготовки (по типу проката)

    mass_part_kg: float = 0.0     # масса чистовая (деталь)
    mass_blank_kg: float = 0.0    # масса заготовки (габарит)

    center_of_mass: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    surface_area_mm2: float = 0.0

    n_planar: int = 0             # плоских граней
    n_cylindrical: int = 0        # цилиндрических граней
    n_other: int = 0

    shape_type: str = ""          # "Лист" / "Круг" / "Труба" / "Квадрат" / "?"
    stock_size_hint: str = ""     # подсказка размера заготовки

    material_mark: Optional[str] = None   # НЕ извлекается из STEP (теряется)
    density_used: float = DEFAULT_DENSITY

    protocol: str = ""


# ============================================================
# МЕТАДАННЫЕ ИЗ ТЕКСТА STEP
# ============================================================

def _decode_step_unicode(s: str) -> str:
    """Раскодирует STEP-юникод вида \\X2\\041F043B...\\X0\\ → 'Платик'."""
    def rep(m):
        h = m.group(1)
        try:
            return "".join(chr(int(h[i:i + 4], 16)) for i in range(0, len(h), 4))
        except Exception:
            return m.group(0)
    return re.sub(r"\\X2\\([0-9A-Fa-f]+)\\X0\\", rep, s)


def _read_step_text(path: str) -> str:
    """Читает STEP как текст (КОМПАС пишет в cp1251)."""
    raw = open(path, "rb").read()
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("latin-1", errors="replace")


def extract_step_metadata(path: str) -> Dict[str, str]:
    """
    Достаёт обозначение и наименование из сущности PRODUCT.
    Пример: PRODUCT('135517.03.00.051','Платик','NONE',(#27))
    """
    out = {"drawing_no": "", "part_name": ""}
    try:
        txt = _read_step_text(path)
        m = re.search(r"PRODUCT\(\s*'([^']*)'\s*,\s*'([^']*)'", txt)
        if m:
            out["drawing_no"] = _decode_step_unicode(m.group(1)).strip()
            out["part_name"] = _decode_step_unicode(m.group(2)).strip()
        # запасной вариант — из FILE_NAME
        if not out["drawing_no"]:
            m2 = re.search(r"FILE_NAME\(\s*'([^']*)'", txt)
            if m2:
                val = _decode_step_unicode(m2.group(1)).strip()
                mm = re.match(r"([\d.]+)\s+(.*)", val)
                if mm:
                    out["drawing_no"] = mm.group(1)
                    out["part_name"] = mm.group(2)
    except Exception as e:
        logger.warn(f"STEP: не удалось прочитать метаданные: {e}")
    return out


# ============================================================
# ГЕОМЕТРИЯ ЧЕРЕЗ OpenCASCADE
# ============================================================

def _analyze_faces(shape) -> Dict[str, Any]:
    """
    Считает типы граней и собирает цилиндры с радиусом И осью.
    Ось важна: скругления профиля идут ВДОЛЬ детали, отверстия — поперёк.
    """
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopoDS import TopoDS
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder

    n_plane = n_cyl = n_other = 0
    cylinders: List[Dict[str, Any]] = []

    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        try:
            surf = BRepAdaptor_Surface(TopoDS.Face_s(exp.Current()))
            t = surf.GetType()
            if t == GeomAbs_Plane:
                n_plane += 1
            elif t == GeomAbs_Cylinder:
                n_cyl += 1
                cyl = surf.Cylinder()
                d = cyl.Axis().Direction()
                loc = cyl.Axis().Location()
                cylinders.append({
                    "r": round(cyl.Radius(), 3),
                    "axis": (round(abs(d.X()), 3), round(abs(d.Y()), 3), round(abs(d.Z()), 3)),
                    "loc": (loc.X(), loc.Y(), loc.Z()),
                })
            else:
                n_other += 1
        except Exception:
            n_other += 1
        exp.Next()

    return {"n_plane": n_plane, "n_cyl": n_cyl, "n_other": n_other,
            "cylinders": cylinders,
            "radii": [c["r"] for c in cylinders]}


def _axis_index(dx: float, dy: float, dz: float) -> int:
    """Индекс оси наибольшего габарита (0=X, 1=Y, 2=Z) — ось длины детали."""
    dims = [dx, dy, dz]
    return dims.index(max(dims))


def _is_along(axis_tuple, idx: int, tol: float = 0.05) -> bool:
    """Цилиндр направлен вдоль оси idx?"""
    return abs(axis_tuple[idx] - 1.0) < tol


def _detect_shape_type(dx: float, dy: float, dz: float, faces: Dict[str, Any],
                       volume_mm3: float) -> Tuple[str, str, float]:
    """
    Определяет тип заготовки по геометрии.

    Порядок проверок важен:
      1) тело вращения (труба/круг) — по коаксиальным цилиндрам вдоль оси длины
      2) профиль (низкая заполненность + скругления вдоль оси)
      3) лист (плоская, заполненность высокая)
      4) квадрат (два поперечных габарита равны, заполненность высокая)

    Возвращает (тип, подсказка_размера, объём_заготовки_мм3).

    Объём заготовки считается ПО ТИПУ проката, а не по габаритному ящику:
    для листа заготовка = габарит, для трубы = кольцо, для профиля = сечение×длина.
    """
    dims = [dx, dy, dz]
    ax = _axis_index(dx, dy, dz)          # ось длины
    L = dims[ax]
    cross = [d for i, d in enumerate(dims) if i != ax]   # два поперечных габарита
    c1, c2 = max(cross), min(cross)

    bbox_vol = dx * dy * dz
    fill = volume_mm3 / bbox_vol if bbox_vol > 0 else 0.0

    cyls = faces.get("cylinders", [])
    # цилиндры, идущие ВДОЛЬ длины (скругления профиля / тело вращения)
    axial = [c for c in cyls if _is_along(c["axis"], ax)]
    axial_r = sorted({c["r"] for c in axial}, reverse=True)

    cross_equal = (c1 > 0) and (abs(c1 - c2) / c1 < 0.02)

    # ── 1) Тело вращения: крупный осевой цилиндр ≈ поперечный габарит ──
    if axial_r and cross_equal:
        r_out = axial_r[0]
        if r_out * 2 >= c1 * 0.9:
            D = c1
            inner = [r for r in axial_r if r < r_out * 0.95]
            if inner:
                r_in = inner[0]
                wall = r_out - r_in
                ring_vol = math.pi * (r_out ** 2 - r_in ** 2) * L
                if ring_vol > 0 and abs(ring_vol - volume_mm3) / ring_vol < 0.35:
                    return "Труба", f"Труба {D:.0f}x{wall:.0f}, L={L:.0f}", ring_vol
            # сплошной круг: заполненность близка к π/4 ≈ 0.785
            if fill > 0.55:
                return "Круг", f"Круг {D:.0f}, L={L:.0f}", math.pi * (D ** 2) / 4 * L

    # ── 2) Профиль: низкая заполненность + вытянутость + осевые скругления ──
    if fill < 0.45 and L > c1 * 1.5:
        area_cm2 = (volume_mm3 / L) / 100.0 if L else 0.0
        n_fillets = len(axial)
        if cross_equal and n_fillets >= 4:
            # Квадратная профильная труба: 4 наружных + 4 внутренних скругления.
            # Толщину стенки оцениваем из площади сечения: A ≈ 4·s·(a − s)
            s_est = None
            a = c1
            disc = a * a - (volume_mm3 / L) if L else -1
            if disc >= 0:
                s_est = (a - math.sqrt(disc)) / 2.0
            if s_est and s_est > 0:
                # округляем до стандартной стенки профтрубы
                std_walls = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10, 12]
                s_std = min(std_walls, key=lambda w: abs(w - s_est))
                hint = (f"Труба проф. {c1:.0f}x{c2:.0f}x{s_std:g}, L={L:.0f} "
                        f"(сечение {area_cm2:.2f} см², стенка ≈{s_est:.1f})")
            else:
                hint = f"Труба проф. {c1:.0f}x{c2:.0f}, L={L:.0f} (сечение {area_cm2:.2f} см²)"
            blank_v = (4 * s_std * (a - s_std) * L) if s_est else volume_mm3
            return "ТрубаПроф", hint, blank_v
        return "Профиль", (f"Сечение {c1:.0f}x{c2:.0f}, L={L:.0f}, "
                           f"площадь {area_cm2:.2f} см² — уточнить сортамент"), volume_mm3

    # ── 3) Лист: плоская деталь, заполненность приличная ──
    sd = sorted(dims, reverse=True)
    Lmax, Wmid, Tmin = sd[0], sd[1], sd[2]
    if Lmax > 0 and (Tmin / Lmax) < 0.4 and Tmin <= 200 and fill >= 0.4 and not cross_equal:
        return "Лист", f"Лист {Tmin:.0f}, {Lmax:.0f}х{Wmid:.0f}", Tmin * Wmid * Lmax

    # ── 4) Квадрат: два поперечных равны, почти полностью заполняет ──
    if cross_equal and fill > 0.85:
        return "Квадрат", f"Квадрат {c1:.0f}, L={L:.0f}", c1 * c1 * L

    # ── 5) Лист (запасной путь, если поперечные равны, но деталь плоская) ──
    if Lmax > 0 and (Tmin / Lmax) < 0.4 and fill >= 0.4:
        return "Лист", f"Лист {Tmin:.0f}, {Lmax:.0f}х{Wmid:.0f}", Tmin * Wmid * Lmax

    return "?", f"{Lmax:.0f}х{Wmid:.0f}х{Tmin:.0f} (заполненность {fill*100:.0f}%)", bbox_vol


def read_step(path: str, density: float = DEFAULT_DENSITY) -> Optional[StepModel]:
    """
    Читает STEP-файл и возвращает StepModel со всей геометрией.
    density — плотность материала (кг/м³), по умолчанию сталь 7850.
    """
    if not path or not os.path.exists(path):
        logger.warn(f"STEP: файл не найден: {path}")
        return None

    try:
        from OCP.STEPControl import STEPControl_Reader
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.BRepBndLib import BRepBndLib
        from OCP.Bnd import Bnd_Box
        from OCP.GProp import GProp_GProps
        from OCP.BRepGProp import BRepGProp
    except Exception as e:
        logger.error(f"STEP: OpenCASCADE (cadquery-ocp) не установлен: {e}")
        return None

    logger.info(f"STEP: читаю модель {os.path.basename(path)}")

    reader = STEPControl_Reader()
    if reader.ReadFile(path) != IFSelect_RetDone:
        logger.error(f"STEP: не удалось прочитать файл {path}")
        return None
    reader.TransferRoots()
    shape = reader.OneShape()

    # Габариты (оптимальный bbox + округление: OpenCASCADE добавляет
    # микроскопический зазор ~2e-7 мм, из-за него 10.0 превращается в 10.0000002
    # и толщина листа уезжает на следующий стандарт)
    bbox = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bbox, True, False)
    except Exception:
        BRepBndLib.Add_s(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    dx = round(xmax - xmin, 3)
    dy = round(ymax - ymin, 3)
    dz = round(zmax - zmin, 3)

    # Объём и центр масс
    vprops = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, vprops)
    volume = vprops.Mass()
    com = vprops.CentreOfMass()

    # Площадь поверхности
    sprops = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, sprops)
    area = sprops.Mass()

    faces = _analyze_faces(shape)
    shape_type, size_hint, blank_vol = _detect_shape_type(dx, dy, dz, faces, volume)

    dims = sorted([dx, dy, dz], reverse=True)
    bbox_vol = dx * dy * dz

    meta = extract_step_metadata(path)

    m = StepModel(
        file_path=path,
        drawing_no=meta.get("drawing_no", ""),
        part_name=meta.get("part_name", ""),
        dx=dx, dy=dy, dz=dz,
        length_mm=dims[0], width_mm=dims[1], thickness_mm=dims[2],
        volume_mm3=volume,
        bbox_volume_mm3=bbox_vol,
        mass_part_kg=round(volume / 1e9 * density, 3),
        mass_blank_kg=round(blank_vol / 1e9 * density, 3),
        blank_volume_mm3=blank_vol,
        center_of_mass=(com.X(), com.Y(), com.Z()),
        surface_area_mm2=area,
        n_planar=faces["n_plane"],
        n_cylindrical=faces["n_cyl"],
        n_other=faces["n_other"],
        shape_type=shape_type,
        stock_size_hint=size_hint,
        material_mark=None,     # STEP от КОМПАС не несёт материал
        density_used=density,
    )

    prot = [
        "Протокол чтения 3D-модели (STEP)",
        f"- Файл: {os.path.basename(path)}",
        f"- Обозначение: {m.drawing_no or '—'}",
        f"- Наименование: {m.part_name or '—'}",
        f"- Габариты X×Y×Z: {dx:.1f} × {dy:.1f} × {dz:.1f} мм",
        f"- Длина/ширина/толщина: {m.length_mm:.1f} / {m.width_mm:.1f} / {m.thickness_mm:.1f} мм",
        f"- Объём детали: {volume/1000:.2f} см³",
        f"- Габаритный объём (коробка): {bbox_vol/1000:.2f} см³",
        f"- Объём заготовки ({shape_type}): {blank_vol/1000:.2f} см³",
        f"- Плотность: {density} кг/м³",
        f"- Масса заготовки (габарит): {m.mass_blank_kg:.3f} кг",
        f"- Масса чистовая (деталь): {m.mass_part_kg:.3f} кг",
        f"- Грани: плоских {m.n_planar}, цилиндрических {m.n_cylindrical}, прочих {m.n_other}",
        f"- Определённый тип заготовки: {shape_type}",
        f"- Подсказка размера: {size_hint}",
        "- Материал: в STEP отсутствует (берётся из чертежа/спецификации/ввода)",
    ]
    m.protocol = "\n".join(prot)

    logger.info(f"STEP: {m.part_name} → {shape_type}, {size_hint}, "
                f"масса заг. {m.mass_blank_kg} кг")
    return m


def density_for_material(material_mark: str) -> float:
    """Подбирает плотность по марке материала (грубо, по префиксу)."""
    s = (material_mark or "").upper().replace(" ", "")
    if not s:
        return DEFAULT_DENSITY
    # алюминиевые сплавы
    if s.startswith(("АД", "АМГ", "АМЦ", "АВ", "Д16", "В95")):
        return DENSITY["алюминий"]
    # нержавеющие
    if re.match(r"^\d{2}Х\d{2}", s) or "Х18Н" in s or "Х17Н" in s:
        return DENSITY["нержавейка"]
    # латунь / медь
    if s.startswith("Л") and len(s) <= 4:
        return DENSITY["латунь"]
    if s.startswith("М") and len(s) <= 3:
        return DENSITY["медь"]
    # чугун
    if s.startswith(("СЧ", "ВЧ", "КЧ")):
        return DENSITY["чугун"]
    return DENSITY["сталь"]