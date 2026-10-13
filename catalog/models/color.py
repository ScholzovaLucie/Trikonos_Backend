from django.db import models

from core.models import SimpleLookup


class Color(SimpleLookup):
    hex_value = models.CharField(max_length=7, help_text='E.g. #000000')

    class Meta(SimpleLookup.Meta):
        db_table = 'colors'
