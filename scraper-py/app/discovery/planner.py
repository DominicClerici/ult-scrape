from __future__ import annotations

from app.discovery.facets import FacetCatalog, SliceSpec


def initial_slices(
    catalog: FacetCatalog,
    *,
    untagged_sweep: bool,
    genres: list[int] | None = None,
    decades: list[int] | None = None,
) -> list[SliceSpec]:
    allowed = {str(g) for g in genres} if genres is not None else None
    slices: list[SliceSpec] = []
    for fv in catalog.values("genres"):
        if allowed is not None and fv.url_name not in allowed:
            continue
        slices.append(SliceSpec(filters={"type": "Pro", "genres": fv.url_name}, depth=0))
    if untagged_sweep:
        slices.append(SliceSpec(filters={"type": "Pro"}, depth=0))
    return slices


def subdivide(
    spec: SliceSpec, catalog: FacetCatalog, ladder: list[str]
) -> list[SliceSpec] | None:
    for facet in ladder:
        if facet in spec.filters:
            continue
        values = catalog.values(facet)
        if not values:
            continue
        return [
            SliceSpec(
                filters={**spec.filters, facet: fv.url_name},
                order=spec.order,
                depth=spec.depth + 1,
            )
            for fv in values
        ]
    return None


def sort_windows(spec: SliceSpec, sorts: list[str]) -> list[SliceSpec]:
    return [
        SliceSpec(filters=dict(spec.filters), order=s, depth=spec.depth) for s in sorts
    ]
