from dataclasses import dataclass


class UnknownArea(ValueError):
    pass


@dataclass(frozen=True)
class Area:
    code: str
    name: str

    def __post_init__(self):
        for field in (self.code, self.name):
            if not isinstance(field, str) or not field.strip():
                raise ValueError('area code and name must be nonempty text')
            field.encode('utf-8')


@dataclass(frozen=True)
class AreaCatalog:
    areas: tuple[Area, ...]

    def __post_init__(self):
        entries = tuple(self.areas)
        codes = set()
        for area in entries:
            if not isinstance(area, Area) or area.code in codes:
                raise ValueError('catalog requires distinct Area entries')
            codes.add(area.code)
        object.__setattr__(self, 'areas', tuple(sorted(entries, key=lambda area: area.code)))

    def require_area(self, code):
        if isinstance(code, str):
            for area in self.areas:
                if area.code == code:
                    return area
        raise UnknownArea(f'unknown area: {code!r}')

    def resolve_scope(self, codes=None):
        if codes is None:
            scope = tuple(area.code for area in self.areas)
        else:
            if isinstance(codes, (str, bytes)):
                raise ValueError('scope must be a collection of area codes')
            scope = tuple(sorted({self.require_area(code).code for code in codes}))
        if not scope:
            raise ValueError('scope must contain at least one area')
        return scope
