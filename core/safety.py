"""Проверки безопасности путей — без зависимости от Qt.

Вынесено отдельным модулем, чтобы правила «куда можно писать» можно было
прогнать в тестах и вызвать из GUI ДО запуска воркера, а не в момент, когда
папка уже удаляется.
"""
from __future__ import annotations

from pathlib import Path


class UnsafeOutputDir(Exception):
    """Выходная папка выбрана так, что перевод уничтожил бы данные."""


def check_output_dir(project_dir: Path, output_dir: Path) -> None:
    """Проверяет, что выходную папку можно безопасно пересоздать.

    Перед копированием проекта выходная папка удаляется целиком. Раньше
    единственной защитой было точное равенство путей, поэтому выбор `www` при
    источнике `www/data` приводил к `shutil.rmtree(www)` — вместе с исходником
    и всей игрой. Здесь запрещены все варианты, при которых удаление задевает
    исходные данные или заведомо чужие файлы.
    """
    project = Path(project_dir).resolve()
    output = Path(output_dir).resolve()

    if output == project:
        raise UnsafeOutputDir("Выходная папка не может совпадать с исходной.")
    if _is_within(project, output):
        raise UnsafeOutputDir(
            f"Выходная папка «{output}» содержит внутри себя исходную «{project}».\n"
            "При запуске она удаляется целиком — вместе с игрой. "
            "Выбери отдельную папку рядом, например «data_translated»."
        )
    if _is_within(output, project):
        raise UnsafeOutputDir(
            f"Выходная папка «{output}» находится внутри исходной «{project}».\n"
            "Копия проекта попала бы сама в себя. Выбери папку вне исходной."
        )
    if output.parent == output:
        raise UnsafeOutputDir("Нельзя использовать корень диска как выходную папку.")

    # Существующая папка с чужим содержимым: удалять её молча нельзя.
    if output.exists():
        if not output.is_dir():
            raise UnsafeOutputDir(f"«{output}» — это файл, а не папка.")
        if _looks_foreign(output):
            raise UnsafeOutputDir(
                f"В папке «{output}» лежат посторонние файлы, а перед переводом "
                "она удаляется целиком.\n"
                "Выбери пустую или ранее использованную для перевода папку."
            )


def _is_within(inner: Path, outer: Path) -> bool:
    """True, если inner лежит внутри outer (или совпадает с ней)."""
    try:
        inner.relative_to(outer)
        return True
    except ValueError:
        return False


# Признаки того, что папка уже была выходной папкой перевода или папкой Data.
_KNOWN_MARKERS = ("_translation_cache.json", "_glossary.json", "System.json",
                  "Actors.json", "CommonEvents.json", "MapInfos.json")


def _looks_foreign(output: Path) -> bool:
    try:
        names = {p.name for p in output.iterdir()}
    except OSError:
        return True
    if not names:
        return False        # пустая папка — безопасно
    if names & set(_KNOWN_MARKERS):
        return False        # наша прошлая выходная папка или копия Data
    # Папка только из Map###.json тоже наша.
    if all(n.startswith("Map") and n.endswith(".json") for n in names):
        return False
    return True
