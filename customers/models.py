from django.db import models

from core.models import UUIDModel


class Customer(UUIDModel):
    """Contact record for admin lookups (order history by email). No
    password/account - created or found purely by email.
    """
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=30, blank=True, default='')
    first_name = models.CharField(max_length=255, blank=True, default='')
    last_name = models.CharField(max_length=255, blank=True, default='')
    company_name = models.CharField(max_length=255, blank=True, default='', help_text='For B2B/team orders')
    marketing_consent = models.BooleanField(default=False)
    note = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'customers'

    def __str__(self):
        return self.email
