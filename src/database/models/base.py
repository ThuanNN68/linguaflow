"""Declarative base shared by every ORM domain."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all persisted LinguaFlow models."""
