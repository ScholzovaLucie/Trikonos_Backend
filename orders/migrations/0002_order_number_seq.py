from django.db import migrations


class Migration(migrations.Migration):
    """Provisions the order_number_seq Postgres sequence used by
    orders.services.generate_order_number() - there's no Django ORM concept
    for a bare sequence, so it's created/dropped via raw SQL.
    """

    dependencies = [
        ('orders', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(
            sql='CREATE SEQUENCE IF NOT EXISTS order_number_seq;',
            reverse_sql='DROP SEQUENCE IF EXISTS order_number_seq;',
        ),
    ]
