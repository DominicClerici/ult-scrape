from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlencode

from app.discovery.parser import ExploreStore


@dataclass(frozen=True)
class FacetValue:
    url_name: str
    name: str
    count: int


@dataclass
class FacetCatalog:
    facets: dict[str, list[FacetValue]] = field(default_factory=dict)
    sorts: list[str] = field(default_factory=list)

    def values(self, param_name: str) -> list[FacetValue]:
        return self.facets.get(param_name, [])


@dataclass
class SliceSpec:
    filters: dict[str, int | str]
    order: str | None = None
    depth: int = 0

    def label(self) -> str:
        parts = [f"{k}={v}" for k, v in sorted(self.filters.items()) if k != "type"]
        if self.order:
            parts.append(f"order={self.order}")
        return "&".join(parts) or "type=Pro"


def catalog_from_store(store: ExploreStore) -> FacetCatalog:
    facets: dict[str, list[FacetValue]] = {}
    for f in store.filters:
        param = f.get("param_name")
        if not param:
            continue
        facets[param] = [
            FacetValue(
                url_name=str(v.get("url_name")),
                name=str(v.get("name", "")),
                count=int(v.get("count", 0)),
            )
            for v in (f.get("values") or [])
        ]
    sorts = [str(o.get("url_name")) for o in (store.order.get("all") or []) if o.get("url_name")]
    return FacetCatalog(facets=facets, sorts=sorts)


def build_query(spec: SliceSpec, page: int) -> str:
    pairs: list[tuple[str, str]] = []
    for key, value in spec.filters.items():
        pairs.append((f"{key}[]", str(value)))
    if spec.order:
        pairs.append(("order", spec.order))
    pairs.append(("page", str(page)))
    return urlencode(pairs)
