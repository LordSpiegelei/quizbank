"""Form input helpers shared by the routes."""


class FormError(ValueError):
    """Validation failure; carries a locale key so callers stay free of translation."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


def optional_number[T: (int, float)](raw: str, kind: type[T]) -> T | None:
    """A non-negative number with decimal comma or point; empty means not given."""
    value = raw.strip()
    if not value:
        return None
    try:
        number = kind(value.replace(",", "."))
    except ValueError:
        raise FormError("error.not_a_number", value=value) from None
    if number < 0:
        raise FormError("error.not_a_number", value=value)
    return number
