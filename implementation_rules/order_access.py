"""
Trikonoš.cz — přístupové tokeny k veřejnému detailu objednávky.

Zákazník nemá účet, takže se ke své objednávce vrací přes odkaz s podepsaným
tokenem (v e-mailu, na děkovné stránce). Token je podepsané ID objednávky s
platností 180 dní, bez vlastního sloupce v databázi. Neplatný nebo prošlý token
se z pohledu veřejnosti tváří, jako by objednávka neexistovala (API: 404).

Referenční implementace přes `django.core.signing`. Pokud váš backend přístupové
tokeny už má (podle `GET /orders/access/{accessToken}` ve spec má), nahraďte těla
obou funkcí svými — odkaz v e-mailu (`notifications.py`) volá jen tyhle dvě.
"""

import uuid

from django.core import signing
from django.core.exceptions import ValidationError

SALT = "trikonos.order-access"
MAX_AGE = 180 * 24 * 3600  # 180 dní


def make_access_token(order_id) -> str:
    return signing.dumps(str(order_id), salt=SALT)


def resolve_access_token(token: str) -> uuid.UUID:
    try:
        return uuid.UUID(signing.loads(token, salt=SALT, max_age=MAX_AGE))
    except (signing.BadSignature, ValueError):   # SignatureExpired je potomek BadSignature
        raise ValidationError("Odkaz k objednávce je neplatný nebo mu vypršela platnost.", code="INVALID_ACCESS_TOKEN")
