from django.contrib import admin

from .models import Category, Color, Cut, Motif, PrintArea, ProductType

admin.site.register(Category)
admin.site.register(Motif)
admin.site.register(Color)
admin.site.register(Cut)
admin.site.register(ProductType)
admin.site.register(PrintArea)
