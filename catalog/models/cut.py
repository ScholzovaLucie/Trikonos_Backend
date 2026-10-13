from core.models import SimpleLookup


class Cut(SimpleLookup):
    """Men's, Women's, Unisex, Hoodie, ..."""

    class Meta(SimpleLookup.Meta):
        db_table = 'cuts'
