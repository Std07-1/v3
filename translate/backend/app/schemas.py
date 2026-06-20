"""Pydantic-схеми запитів/відповідей API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TranslateRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Текст для перекладу")
    source: str = Field(default="auto", description="ISO-код або 'auto'")
    target: str = Field(..., description="ISO-код цільової мови")


class TranslateResponse(BaseModel):
    text: str = Field(..., description="Перекладений текст")
    source: str = Field(..., description="Фактично використана мова джерела")
    target: str
    engine: str
    cached: bool = Field(..., description="Чи взято з кешу")


class LanguageInfo(BaseModel):
    code: str
    name_en: str
    name_native: str


class LanguagesResponse(BaseModel):
    languages: list[LanguageInfo]


class HealthResponse(BaseModel):
    status: str
    engine: str
    cache: str
    version: str
