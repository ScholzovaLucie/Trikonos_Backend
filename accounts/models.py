from django.contrib.auth.models import AbstractUser
from django.db import models

from core.models import UUIDModel


class User(UUIDModel, AbstractUser):
    """Administrace — uživatelé backendu (ADMIN/STAFF), přihlašují se do Django adminu."""

    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        STAFF = 'STAFF', 'Staff'

    username = None
    email = models.EmailField(unique=True)
    name = models.TextField()
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.STAFF)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        db_table = 'users'

    def __str__(self):
        return self.email
