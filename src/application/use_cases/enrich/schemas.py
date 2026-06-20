"""LLM yapılandırılmış çıktı şemaları (design/05, /06, /09)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SummaryOut(BaseModel):
    summary: str = Field(description="Nesnenin ne yaptığını anlatan 1-2 cümle Türkçe özet")


class TableDescOut(BaseModel):
    table_description: str = Field(description="Tablonun ne tuttuğunu anlatan 1-2 cümle")
    columns: dict[str, str] = Field(default_factory=dict, description="kolon_adı → kısa açıklama (yalnızca belirsizler)")


class CategoryOut(BaseModel):
    category: str = Field(description="Taksonomideki birincil kategori key'i")
    secondary: list[str] = Field(default_factory=list)
    subcategory: str | None = None
    reason: str | None = None
    confidence: float = 0.5


class CategoryItem(BaseModel):
    key: str
    label: str
    description: str = ""
    subcategories: list[str] = Field(default_factory=list)


class TaxonomyOut(BaseModel):
    categories: list[CategoryItem] = Field(default_factory=list)
