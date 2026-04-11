"""
Глобальні константи гідравлічних формул.

У типовому pipes_db.json коефіцієнта Hazen–Williams немає — усі втрати в модулі
за замовчуванням рахуються з цим C. Якщо в записі труби з’явиться поле c_hw (або C_hw),
ядро може підставляти його замість цього значення.
"""

DEFAULT_HAZEN_WILLIAMS_C: float = 140.0


def hazen_c_from_pipe_entry(pipe_data) -> float:
    """C для HW з запису труби в каталозі; якщо поля немає — DEFAULT_HAZEN_WILLIAMS_C."""
    if not isinstance(pipe_data, dict):
        return DEFAULT_HAZEN_WILLIAMS_C
    for key in ("c_hw", "C_hw", "hw_c"):
        if key not in pipe_data:
            continue
        try:
            v = float(pipe_data[key])
            if v > 1.0:
                return v
        except (TypeError, ValueError):
            continue
    return DEFAULT_HAZEN_WILLIAMS_C
