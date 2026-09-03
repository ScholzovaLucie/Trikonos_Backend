from django.db import models

from catalog.models import Motif, Product
from core.models import CreatedAtModel, TimestampedModel, UUIDModel


class Customer(UUIDModel, TimestampedModel):
    email = models.EmailField(unique=True)
    phone = models.TextField(null=True, blank=True)
    first_name = models.TextField(null=True, blank=True)
    last_name = models.TextField(null=True, blank=True)
    password_hash = models.TextField(null=True, blank=True, help_text='NULL = guest checkout, bez účtu')
    email_verified_at = models.DateTimeField(null=True, blank=True)
    marketing_consent = models.BooleanField(default=False)
    note = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'customers'

    def __str__(self):
        return self.email


class Address(UUIDModel):
    class Type(models.TextChoices):
        BILLING = 'BILLING', 'Fakturační'
        SHIPPING = 'SHIPPING', 'Doručovací'

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='addresses')
    type = models.CharField(max_length=10, choices=Type.choices)
    full_name = models.TextField()
    company_name = models.TextField(null=True, blank=True)
    ico = models.TextField(null=True, blank=True)
    dic = models.TextField(null=True, blank=True)
    street = models.TextField()
    city = models.TextField()
    zip = models.TextField()
    country = models.TextField(default='CZ')

    class Meta:
        db_table = 'addresses'
        verbose_name_plural = 'addresses'

    def __str__(self):
        return f'{self.full_name}, {self.street}, {self.city}'


class TeamInquiry(UUIDModel, CreatedAtModel):
    """B2B poptávky na týmová trička (homepage / detail produktu)."""

    class Status(models.TextChoices):
        NEW = 'NEW', 'Nová'
        IN_PROGRESS = 'IN_PROGRESS', 'Zpracovává se'
        DONE = 'DONE', 'Vyřízená'

    name = models.TextField()
    email = models.EmailField()
    phone = models.TextField(null=True, blank=True)
    company_name = models.TextField(null=True, blank=True)
    message = models.TextField()
    product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, related_name='team_inquiries', null=True, blank=True,
    )
    motif = models.ForeignKey(
        Motif, on_delete=models.SET_NULL, related_name='team_inquiries', null=True, blank=True,
    )
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.NEW)

    class Meta:
        db_table = 'team_inquiries'
        verbose_name_plural = 'team inquiries'

    def __str__(self):
        return f'{self.name} ({self.email})'
