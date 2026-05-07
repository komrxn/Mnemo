from __future__ import annotations

import structlog
from pydantic import BaseModel

from src.tools.registry import ToolDef, get_registry

logger = structlog.get_logger()


# ── param models ──────────────────────────────────────────────────────────────

class KgQueryParams(BaseModel):
    question: str
    mode: str = "mix"


class KgGetEntityParams(BaseModel):
    name: str


class KgGetRelatedParams(BaseModel):
    entity: str
    hops: int = 1


# ── handlers ──────────────────────────────────────────────────────────────────

async def _kg_query(p: KgQueryParams, session_id: str = "") -> str:
    try:
        from src.lightrag_svc.client import query
        return await query(p.question, mode=p.mode)
    except Exception as exc:
        logger.warning("kg_query failed", error=str(exc))
        return f"граф недоступен: {exc}"


async def _kg_get_entity(p: KgGetEntityParams, session_id: str = "") -> str:
    try:
        from src.lightrag_svc.client import query
        return await query(f"Расскажи всё что знаешь о {p.name}", mode="local")
    except Exception as exc:
        logger.warning("kg_get_entity failed", error=str(exc))
        return f"граф недоступен: {exc}"


async def _kg_get_related(p: KgGetRelatedParams, session_id: str = "") -> str:
    try:
        from src.lightrag_svc.client import query
        return await query(
            f"Какие сущности связаны с {p.entity}? Перечисли в {p.hops} уровнях связи.",
            mode="local",
        )
    except Exception as exc:
        logger.warning("kg_get_related failed", error=str(exc))
        return f"граф недоступен: {exc}"


# ── registration ──────────────────────────────────────────────────────────────

def _register() -> None:
    reg = get_registry()
    specs = [
        ("kg_query", "Поиск по графу знаний (mode: local/global/hybrid/mix)", KgQueryParams, _kg_query),
        ("kg_get_entity", "Получить все факты о сущности из графа", KgGetEntityParams, _kg_get_entity),
        ("kg_get_related", "Найти связанные сущности в графе", KgGetRelatedParams, _kg_get_related),
    ]
    for name, desc, cls, handler in specs:
        reg.register(ToolDef(name=name, description=desc, params_cls=cls, handler=handler))


_register()
