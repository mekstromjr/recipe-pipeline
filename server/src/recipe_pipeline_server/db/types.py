"""Custom SQLAlchemy column types."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator


class StrEnumType(TypeDecorator):
    """Persist a ``StrEnum`` as plain ``VARCHAR`` while keeping ``Mapped[MyEnum]`` typing.

    No DB-level ENUM or CHECK: validity is enforced by the shared state machine and pydantic,
    so adding a status is a code change, not an ``ALTER TYPE``. Stable for Alembic autogenerate.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_cls: type[StrEnum], length: int = 32) -> None:
        self._enum_cls = enum_cls
        super().__init__(length=length)

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return self._enum_cls(value).value

    def process_result_value(self, value: Any, dialect: Any) -> StrEnum | None:
        if value is None:
            return None
        return self._enum_cls(value)
