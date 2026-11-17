"""ORM-модели предметной области. Импорт модуля регистрирует таблицы в ``Base.metadata``."""

from app.models.group import Group
from app.models.lesson import Lesson, LessonFormat, LessonStatus

__all__ = ["Group", "Lesson", "LessonFormat", "LessonStatus"]