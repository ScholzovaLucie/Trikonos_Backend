# Trikonoš.cz – Backend API

> Integrační dokumentace Django backendu pro vývoj veřejného e-shopu a administrace. Popisuje **co API poskytuje, jaké URL volat, kdy je volat a jaké platí obchodní podmínky**. Nepředepisuje technologii ani architekturu frontendu.

## 1. Základní orientace

Backend je REST API pro katalog motivů a variant oblečení, konfiguraci vlastních potisků, košík, objednávky a administraci (objednávky, výroba, zákazníci, katalog, doprava, uživatelé).

**Lokální adresa:** `http://localhost:8000`  
**Prefix endpointů:** `/api/v1/`  

### Technologie backendu

- Python 3.12, Django 5.2.17, Django REST Framework 3.15.2
- PostgreSQL 16
- drf-spectacular (OpenAPI), django-filter, djangorestframework-camel-case
- Django sessions + CSRF pro administrační přihlášení

### Lokální provoz

- API: `http://localhost:8000` (Docker Compose služba `web`)
- PostgreSQL: `localhost:5432` (služba `db`)
- MailHog UI: `http://localhost:8025` (služba `mailhog`; prohlížení testovacích e-mailů)
- Média: backend má `MEDIA_URL=/media/`; dostupnost konkrétních souborů závisí na konfiguraci URL a oprávnění.
- Adresa `FRONTEND_URL` je konfigurace backendu pro odkazy v e-mailech; **není to automaticky adresa API**.

### Produkční nasazení

Lokální `docker-compose.yml` (Django dev server, `/media/` servírované jen v `DEBUG=True`) je určený pro vývoj, ne produkci. V produkci:

- `DJANGO_DEBUG=False` (výchozí, pokud proměnná chybí - viz `settings/settings.py`) a `DJANGO_SECRET_KEY` nastavený na skutečnou tajnou hodnotu.
- Django app server přes `gunicorn settings.wsgi:application`, ne `manage.py runserver`.
- Nginx (nebo jiný reverse proxy) před Djangem - terminuje TLS, servíruje `/media/` přímo ze sdíleného volume (bez dotazu na Django) a vše ostatní proxuje na app server. Ukázková konfigurace: [`deploy/nginx.conf.example`](deploy/nginx.conf.example). Proxy musí vždy **přepsat** `X-Forwarded-Proto` vlastní hodnotou (nikdy nepřeposílat tu od klienta) - na tom spoléhá `SECURE_PROXY_SSL_HEADER` v nastavení.
- `FRONTEND_ORIGINS`/`CSRF_TRUSTED_ORIGINS`, `COOKIE_SAMESITE` a `DJANGO_ALLOWED_HOSTS` nastavené na skutečné produkční domény (viz `.env.example`).

## 2. Pravidla komunikace

- JSON klíče API jsou v **camelCase** (interní Django modely mohou používat snake_case).
- Pro veřejné endpointy není potřeba administrátorské přihlášení; administrační endpointy vyžadují session a odpovídající oprávnění.
- API má filtrování, vyhledávání parametrem `q`, řazení a stránkování tam, kde je daný endpoint nabízí. Přesné parametry jsou vypsány níže.
- Backend je autoritou pro ceny, slevy, dopravu, validaci konfigurace, povolené změny stavů a oprávnění.
- Při přístupu z jiné origin používejte cookies (`credentials: include`) a nastavte origin ve `FRONTEND_ORIGINS` na backendu.
- U zápisů se session autentizací posílejte CSRF token v `X-CSRFToken`; získáte jej přes `GET /api/v1/admin/auth/csrf`.
- `SESSION_COOKIE_HTTPONLY=True`, `CSRF_COOKIE_HTTPONLY=False`; `SameSite` se řídí nastavením `COOKIE_SAMESITE`, `Secure` závisí na `DEBUG`.
- Chyby vrací vlastní handler (`core.exceptions.custom_exception_handler`); nespoléhejte na text chyb, používejte strukturované kódy a HTTP statusy dle schématu.

## 3. Doporučené návaznosti volání

### Prohlížení a nákup běžného motivu

1. Načíst `GET /api/v1/config` a `GET /api/v1/categories`.
2. Vypsat motivy přes `GET /api/v1/motifs` (filtrování a stránkování).
3. Detail motivu přes `GET /api/v1/motifs/{slug}`, případně související motivy přes `/related`.
4. Dostupné typy oblečení, barvy, střihy a tiskové bloky načíst z `GET /api/v1/catalog/options`.
5. Sestavit položky košíku podle schématu požadavku, ověřit přes `POST /api/v1/cart/validate`.
6. Po vyplnění zákazníka, adres, dopravy a platby odeslat `POST /api/v1/orders`.
7. Zákaznický detail objednávky načítat přes `GET /api/v1/orders/access/{accessToken}`.

### Vlastní potisk

1. Načíst možnosti konfigurace (`GET /api/v1/catalog/options`).
2. Nahrát návrh přes `POST /api/v1/uploads/designs` jako `multipart/form-data`.
3. Vrácený `fileToken` předat v umístění potisku při validaci košíku a vytváření objednávky.
4. Pokud backend vrátí `requiresQuote`, prezentovat cenu jako vyžadující individuální nacenění, nikoli jako nulu.

### Administrace

1. Získat CSRF cookie (`GET /api/v1/admin/auth/csrf`).
2. Přihlásit se přes `POST /api/v1/admin/auth/login` a ověřit identitu `GET /api/v1/admin/auth/me`.
3. Načítat administrativní přehledy, detaily a číselníky podle příslušných endpointů níže.
4. Při změnách objednávek respektovat povolené přechody stavu vrácené backendem.
5. Po odhlášení (`POST /api/v1/admin/auth/logout`) zahodit cache chráněných dat.

## 4. Kompletní katalog endpointů

Níže jsou **všechny operace ze souboru `schema-4`**. Popis endpointu vychází z OpenAPI; přesné definice polí, enumů a příkladů je nutné číst z uvedených schémat.

### Admin - přihlášení

#### `GET /api/v1/admin/auth/csrf`

Získat CSRF cookie

- **Parametry:** —
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `204` (bez těla)

#### `POST /api/v1/admin/auth/login`

Přihlásit administrátora/staffa

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `Login`, `application/x-www-form-urlencoded`: `Login`, `multipart/form-data`: `Login`
- **Úspěšná odpověď:** `200` `Me`

#### `POST /api/v1/admin/auth/logout`

Odhlásit se

- **Parametry:** —
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `204` (bez těla)

#### `GET /api/v1/admin/auth/me`

Kdo je přihlášený

- **Parametry:** —
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `Me`

#### `POST /api/v1/admin/auth/password`

Změnit si vlastní heslo

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `ChangePassword`, `application/x-www-form-urlencoded`: `ChangePassword`, `multipart/form-data`: `ChangePassword`
- **Úspěšná odpověď:** `204` (bez těla)

#### `POST /api/v1/admin/auth/password-reset`

Vyžádat odkaz na reset hesla

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `PasswordResetRequest`, `application/x-www-form-urlencoded`: `PasswordResetRequest`, `multipart/form-data`: `PasswordResetRequest`
- **Úspěšná odpověď:** `204` (bez těla)

#### `POST /api/v1/admin/auth/password-reset/confirm`

Nastavit nové heslo podle odkazu z e-mailu

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `PasswordResetConfirm`, `application/x-www-form-urlencoded`: `PasswordResetConfirm`, `multipart/form-data`: `PasswordResetConfirm`
- **Úspěšná odpověď:** `204` (bez těla)

### Admin - doprava

#### `GET /api/v1/admin/carriers`

Seznam dopravců

- **Parametry:** `page` (query), `pageSize` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `PaginatedAdminCarrierList`

#### `POST /api/v1/admin/carriers`

Vytvořit dopravce

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `AdminCarrier`, `application/x-www-form-urlencoded`: `AdminCarrier`, `multipart/form-data`: `AdminCarrier`
- **Úspěšná odpověď:** `201` `AdminCarrier`

#### `GET /api/v1/admin/carriers/{id}`

Detail dopravce

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `AdminCarrier`

#### `PATCH /api/v1/admin/carriers/{id}`

Upravit dopravce

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminCarrier`, `application/x-www-form-urlencoded`: `PatchedAdminCarrier`, `multipart/form-data`: `PatchedAdminCarrier`
- **Úspěšná odpověď:** `200` `AdminCarrier`

#### `DELETE /api/v1/admin/carriers/{id}`

Smazat dopravce

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `204` (bez těla)

#### `POST /api/v1/admin/carriers/reorder`

Změnit pořadí dopravců

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `Reorder`, `application/x-www-form-urlencoded`: `Reorder`, `multipart/form-data`: `Reorder`
- **Úspěšná odpověď:** `204` (bez těla)

#### `GET /api/v1/admin/packeta-config`

Zobrazit nastavení Packety

- **Parametry:** —
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` seznam `AdminPacketaConfig`

#### `PUT /api/v1/admin/packeta-config`

Nastavit napojení na Packetu

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `AdminPacketaConfig`, `application/x-www-form-urlencoded`: `AdminPacketaConfig`, `multipart/form-data`: `AdminPacketaConfig`
- **Úspěšná odpověď:** `200` `AdminPacketaConfig`

#### `GET /api/v1/admin/shipping-prices`

Seznam cen dopravy

- **Parametry:** `page` (query), `pageSize` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `PaginatedAdminShippingPriceList`

#### `POST /api/v1/admin/shipping-prices`

Vytvořit cena dopravy

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `AdminShippingPrice`, `application/x-www-form-urlencoded`: `AdminShippingPrice`, `multipart/form-data`: `AdminShippingPrice`
- **Úspěšná odpověď:** `201` `AdminShippingPrice`

#### `GET /api/v1/admin/shipping-prices/{id}`

Detail cena dopravy

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `AdminShippingPrice`

#### `PATCH /api/v1/admin/shipping-prices/{id}`

Upravit cena dopravy

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminShippingPrice`, `application/x-www-form-urlencoded`: `PatchedAdminShippingPrice`, `multipart/form-data`: `PatchedAdminShippingPrice`
- **Úspěšná odpověď:** `200` `AdminShippingPrice`

#### `DELETE /api/v1/admin/shipping-prices/{id}`

Smazat cena dopravy

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `204` (bez těla)

#### `POST /api/v1/admin/shipping-prices/reorder`

Změnit pořadí cen dopravy

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `Reorder`, `application/x-www-form-urlencoded`: `Reorder`, `multipart/form-data`: `Reorder`
- **Úspěšná odpověď:** `204` (bez těla)

#### `GET /api/v1/admin/shipping-quantity-discounts`

Seznam slevových prahů

- **Parametry:** `page` (query), `pageSize` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `PaginatedAdminShippingQuantityDiscountList`

#### `POST /api/v1/admin/shipping-quantity-discounts`

Vytvořit slevový práh

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `AdminShippingQuantityDiscount`, `application/x-www-form-urlencoded`: `AdminShippingQuantityDiscount`, `multipart/form-data`: `AdminShippingQuantityDiscount`
- **Úspěšná odpověď:** `201` `AdminShippingQuantityDiscount`

#### `GET /api/v1/admin/shipping-quantity-discounts/{id}`

Detail slevový práh

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `AdminShippingQuantityDiscount`

#### `PATCH /api/v1/admin/shipping-quantity-discounts/{id}`

Upravit slevový práh

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminShippingQuantityDiscount`, `application/x-www-form-urlencoded`: `PatchedAdminShippingQuantityDiscount`, `multipart/form-data`: `PatchedAdminShippingQuantityDiscount`
- **Úspěšná odpověď:** `200` `AdminShippingQuantityDiscount`

#### `DELETE /api/v1/admin/shipping-quantity-discounts/{id}`

Smazat slevový práh

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `204` (bez těla)

#### `POST /api/v1/admin/shipping-quantity-discounts/reorder`

Změnit pořadí slevových prahů

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `Reorder`, `application/x-www-form-urlencoded`: `Reorder`, `multipart/form-data`: `Reorder`
- **Úspěšná odpověď:** `204` (bez těla)

### Admin - číselníky

#### `GET /api/v1/admin/categories`

Seznam kategorií

- **Parametry:** `page` (query), `pageSize` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `PaginatedAdminCategoryList`

#### `POST /api/v1/admin/categories`

Vytvořit kategorii

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `AdminCategory`, `application/x-www-form-urlencoded`: `AdminCategory`, `multipart/form-data`: `AdminCategory`
- **Úspěšná odpověď:** `201` `AdminCategory`

#### `GET /api/v1/admin/categories/{id}`

Detail kategorii

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `AdminCategory`

#### `PATCH /api/v1/admin/categories/{id}`

Upravit kategorii

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminCategory`, `application/x-www-form-urlencoded`: `PatchedAdminCategory`, `multipart/form-data`: `PatchedAdminCategory`
- **Úspěšná odpověď:** `200` `AdminCategory`

#### `DELETE /api/v1/admin/categories/{id}`

Smazat kategorii

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `204` (bez těla)

#### `POST /api/v1/admin/categories/reorder`

Změnit pořadí kategorií

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `Reorder`, `application/x-www-form-urlencoded`: `Reorder`, `multipart/form-data`: `Reorder`
- **Úspěšná odpověď:** `204` (bez těla)

#### `GET /api/v1/admin/print-areas`

Seznam tiskových bloků

- **Parametry:** `page` (query), `pageSize` (query), `productType` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `PaginatedAdminPrintAreaList`

#### `POST /api/v1/admin/print-areas`

Vytvořit tiskový blok

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `AdminPrintArea`, `application/x-www-form-urlencoded`: `AdminPrintArea`, `multipart/form-data`: `AdminPrintArea`
- **Úspěšná odpověď:** `201` `AdminPrintArea`

#### `GET /api/v1/admin/print-areas/{id}`

Detail tiskový blok

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `AdminPrintArea`

#### `PATCH /api/v1/admin/print-areas/{id}`

Upravit tiskový blok

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminPrintArea`, `application/x-www-form-urlencoded`: `PatchedAdminPrintArea`, `multipart/form-data`: `PatchedAdminPrintArea`
- **Úspěšná odpověď:** `200` `AdminPrintArea`

#### `DELETE /api/v1/admin/print-areas/{id}`

Smazat tiskový blok

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `204` (bez těla)

#### `POST /api/v1/admin/print-areas/reorder`

Změnit pořadí tiskových bloků

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `Reorder`, `application/x-www-form-urlencoded`: `Reorder`, `multipart/form-data`: `Reorder`
- **Úspěšná odpověď:** `204` (bez těla)

#### `GET /api/v1/admin/product-types`

Seznam typů oblečení

- **Parametry:** `page` (query), `pageSize` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `PaginatedAdminProductTypeList`

#### `POST /api/v1/admin/product-types`

Vytvořit typ oblečení

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `AdminProductType`, `application/x-www-form-urlencoded`: `AdminProductType`, `multipart/form-data`: `AdminProductType`
- **Úspěšná odpověď:** `201` `AdminProductType`

#### `GET /api/v1/admin/product-types/{id}`

Detail typ oblečení

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `AdminProductType`

#### `PATCH /api/v1/admin/product-types/{id}`

Upravit typ oblečení

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminProductType`, `application/x-www-form-urlencoded`: `PatchedAdminProductType`, `multipart/form-data`: `PatchedAdminProductType`
- **Úspěšná odpověď:** `200` `AdminProductType`

#### `DELETE /api/v1/admin/product-types/{id}`

Smazat typ oblečení

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `204` (bez těla)

#### `POST /api/v1/admin/product-types/reorder`

Změnit pořadí typů oblečení

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `Reorder`, `application/x-www-form-urlencoded`: `Reorder`, `multipart/form-data`: `Reorder`
- **Úspěšná odpověď:** `204` (bez těla)

### Admin - varianty

#### `GET /api/v1/admin/colors`

Seznam barev

- **Parametry:** `page` (query), `pageSize` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `PaginatedAdminColorList`

#### `POST /api/v1/admin/colors`

Vytvořit barvu

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `AdminColor`, `application/x-www-form-urlencoded`: `AdminColor`, `multipart/form-data`: `AdminColor`
- **Úspěšná odpověď:** `201` `AdminColor`

#### `GET /api/v1/admin/colors/{id}`

Detail barvu

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `AdminColor`

#### `PATCH /api/v1/admin/colors/{id}`

Upravit barvu

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminColor`, `application/x-www-form-urlencoded`: `PatchedAdminColor`, `multipart/form-data`: `PatchedAdminColor`
- **Úspěšná odpověď:** `200` `AdminColor`

#### `DELETE /api/v1/admin/colors/{id}`

Smazat barvu

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `204` (bez těla)

#### `POST /api/v1/admin/colors/reorder`

Změnit pořadí barev

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `Reorder`, `application/x-www-form-urlencoded`: `Reorder`, `multipart/form-data`: `Reorder`
- **Úspěšná odpověď:** `204` (bez těla)

#### `GET /api/v1/admin/cuts`

Seznam střihů

- **Parametry:** `page` (query), `pageSize` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `PaginatedAdminCutList`

#### `POST /api/v1/admin/cuts`

Vytvořit střih

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `AdminCut`, `application/x-www-form-urlencoded`: `AdminCut`, `multipart/form-data`: `AdminCut`
- **Úspěšná odpověď:** `201` `AdminCut`

#### `GET /api/v1/admin/cuts/{id}`

Detail střih

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `AdminCut`

#### `PATCH /api/v1/admin/cuts/{id}`

Upravit střih

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminCut`, `application/x-www-form-urlencoded`: `PatchedAdminCut`, `multipart/form-data`: `PatchedAdminCut`
- **Úspěšná odpověď:** `200` `AdminCut`

#### `DELETE /api/v1/admin/cuts/{id}`

Smazat střih

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `204` (bez těla)

#### `POST /api/v1/admin/cuts/reorder`

Změnit pořadí střihů

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `Reorder`, `application/x-www-form-urlencoded`: `Reorder`, `multipart/form-data`: `Reorder`
- **Úspěšná odpověď:** `204` (bez těla)

### Admin - zákazníci

#### `GET /api/v1/admin/customers`

Seznam zákazníků

- **Parametry:** `marketingConsent` (query), `ordering` (query), `page` (query), `pageSize` (query), `q` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `PaginatedAdminCustomerListList`

#### `GET /api/v1/admin/customers/{id}`

Detail zákazníka

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `AdminCustomerDetail`

#### `PATCH /api/v1/admin/customers/{id}`

Upravit zákazníka

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminCustomerDetail`, `application/x-www-form-urlencoded`: `PatchedAdminCustomerDetail`, `multipart/form-data`: `PatchedAdminCustomerDetail`
- **Úspěšná odpověď:** `200` `AdminCustomerDetail`

#### `GET /api/v1/admin/customers/export-consents`

Export e-mailů se souhlasem s marketingem (CSV)

- **Parametry:** —
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` (bez těla)

### Admin - motivy

#### `GET /api/v1/admin/motifs`

Seznam motivů (administrace)

- **Parametry:** `page` (query), `pageSize` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `PaginatedAdminMotifListList`

#### `POST /api/v1/admin/motifs`

Vytvořit motiv

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `AdminMotifWrite`, `application/x-www-form-urlencoded`: `AdminMotifWrite`, `multipart/form-data`: `AdminMotifWrite`
- **Úspěšná odpověď:** `201` `AdminMotifWrite`

#### `GET /api/v1/admin/motifs/{id}`

Detail motivu (administrace)

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `AdminMotifDetail`

#### `PATCH /api/v1/admin/motifs/{id}`

Upravit motiv

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminMotifWrite`, `application/x-www-form-urlencoded`: `PatchedAdminMotifWrite`, `multipart/form-data`: `PatchedAdminMotifWrite`
- **Úspěšná odpověď:** `200` `AdminMotifWrite`

#### `DELETE /api/v1/admin/motifs/{id}`

Smazat motiv

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `204` (bez těla)

#### `POST /api/v1/admin/motifs/{id}/images`

Nahrát fotku motivu

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `MotifImageUpload`, `application/x-www-form-urlencoded`: `MotifImageUpload`, `multipart/form-data`: `MotifImageUpload`
- **Úspěšná odpověď:** `201` `AdminMotifDetail`

#### `PATCH /api/v1/admin/motifs/{id}/images/{imageId}`

Upravit popisek/barvu fotky

- **Parametry:** `id` (path, povinný), `imageId` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminMotifDetail`, `application/x-www-form-urlencoded`: `PatchedAdminMotifDetail`, `multipart/form-data`: `PatchedAdminMotifDetail`
- **Úspěšná odpověď:** `200` `AdminMotifDetail`

#### `DELETE /api/v1/admin/motifs/{id}/images/{imageId}`

Smazat fotku motivu

- **Parametry:** `id` (path, povinný), `imageId` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `204` (bez těla)

#### `POST /api/v1/admin/motifs/{id}/images/reorder`

Změnit pořadí fotek motivu

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `MotifImageReorder`, `application/x-www-form-urlencoded`: `MotifImageReorder`, `multipart/form-data`: `MotifImageReorder`
- **Úspěšná odpověď:** `200` `AdminMotifDetail`

#### `POST /api/v1/admin/motifs/reorder`

Změnit pořadí motivů

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `Reorder`, `application/x-www-form-urlencoded`: `Reorder`, `multipart/form-data`: `Reorder`
- **Úspěšná odpověď:** `204` (bez těla)

### Admin - objednávky

#### `GET /api/v1/admin/orders`

Seznam objednávek

- **Parametry:** `page` (query), `pageSize` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `PaginatedAdminOrderListList`

#### `POST /api/v1/admin/orders`

Ručně založit objednávku (telefon/e-mail)

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `OrderCreate`, `application/x-www-form-urlencoded`: `OrderCreate`, `multipart/form-data`: `OrderCreate`
- **Úspěšná odpověď:** `201` `AdminOrderDetail`

#### `GET /api/v1/admin/orders/{id}`

Detail objednávky

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `AdminOrderDetail`

#### `PATCH /api/v1/admin/orders/{id}`

Upravit adresy, dopravu, platební metodu, poznámku

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminOrderUpdate`, `application/x-www-form-urlencoded`: `PatchedAdminOrderUpdate`, `multipart/form-data`: `PatchedAdminOrderUpdate`
- **Úspěšná odpověď:** `200` `AdminOrderDetail`

#### `POST /api/v1/admin/orders/{id}/confirm`

Potvrdit objednávku zákazníkovi

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `AdminConfirmOrder`, `application/x-www-form-urlencoded`: `AdminConfirmOrder`, `multipart/form-data`: `AdminConfirmOrder`
- **Úspěšná odpověď:** `200` `AdminOrderDetail`

#### `PATCH /api/v1/admin/orders/{id}/items/{itemId}`

Ocenit položku objednávky

- **Parametry:** `id` (path, povinný), `itemId` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminOrderItemPrice`, `application/x-www-form-urlencoded`: `PatchedAdminOrderItemPrice`, `multipart/form-data`: `PatchedAdminOrderItemPrice`
- **Úspěšná odpověď:** `200` `AdminOrderDetail`

#### `PATCH /api/v1/admin/orders/{id}/items/{itemId}/placements/{placementId}`

Upravit umístění vlastního potisku

- **Parametry:** `id` (path, povinný), `itemId` (path, povinný), `placementId` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminPlacementUpdate`, `application/x-www-form-urlencoded`: `PatchedAdminPlacementUpdate`, `multipart/form-data`: `PatchedAdminPlacementUpdate`
- **Úspěšná odpověď:** `200` `AdminOrderDetail`

#### `GET /api/v1/admin/orders/{id}/items/{itemId}/placements/{placementId}/file`

Stáhnout originální podklad zákazníka

- **Parametry:** `id` (path, povinný), `itemId` (path, povinný), `placementId` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** viz OpenAPI

#### `PATCH /api/v1/admin/orders/{id}/payment-status`

Změnit stav platby

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminPaymentStatus`, `application/x-www-form-urlencoded`: `PatchedAdminPaymentStatus`, `multipart/form-data`: `PatchedAdminPaymentStatus`
- **Úspěšná odpověď:** `200` `AdminOrderDetail`

#### `GET /api/v1/admin/orders/{id}/print-sheet`

Data pro výrobní list (volitelné)

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `AdminOrderDetail`

#### `POST /api/v1/admin/orders/{id}/resend-email`

Znovu odeslat e-mail k objednávce

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `AdminResendEmail`, `application/x-www-form-urlencoded`: `AdminResendEmail`, `multipart/form-data`: `AdminResendEmail`
- **Úspěšná odpověď:** `204` (bez těla)

#### `POST /api/v1/admin/orders/{id}/shipment`

Založit zásilku u Packety

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `AdminCreateShipment`, `application/x-www-form-urlencoded`: `AdminCreateShipment`, `multipart/form-data`: `AdminCreateShipment`
- **Úspěšná odpověď:** `200` `AdminOrderDetail`

#### `PATCH /api/v1/admin/orders/{id}/shipment-status`

Změnit stav zásilky

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminShipmentStatus`, `application/x-www-form-urlencoded`: `PatchedAdminShipmentStatus`, `multipart/form-data`: `PatchedAdminShipmentStatus`
- **Úspěšná odpověď:** `200` `AdminOrderDetail`

#### `GET /api/v1/admin/orders/{id}/shipment/label`

Stáhnout štítek zásilky

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` (bez těla)

#### `GET /api/v1/admin/orders/{id}/shipment/status`

Zjistit aktuální stav zásilky u Packety

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `AdminOrderDetail`

#### `PUT /api/v1/admin/orders/{id}/shipping-cost`

Nastavit cenu dopravy

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `AdminShippingCost`, `application/x-www-form-urlencoded`: `AdminShippingCost`, `multipart/form-data`: `AdminShippingCost`
- **Úspěšná odpověď:** `200` `AdminOrderDetail`

#### `POST /api/v1/admin/orders/{id}/status`

Změnit stav objednávky

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `AdminOrderStatus`, `application/x-www-form-urlencoded`: `AdminOrderStatus`, `multipart/form-data`: `AdminOrderStatus`
- **Úspěšná odpověď:** `200` `AdminOrderDetail`

### Admin - výroba

#### `GET /api/v1/admin/production/placements`

Podklady k tisku vlastních potisků

- **Parametry:** `createdFrom` (query), `createdTo` (query), `printArea` (query), `status` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` seznam `ProductionPlacementRow`

#### `GET /api/v1/admin/production/summary`

Souhrn kusů k výrobě podle typu/střihu/barvy/velikosti

- **Parametry:** `createdFrom` (query), `createdTo` (query), `status` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` seznam `ProductionSummaryRow`

### Admin - přehled

#### `GET /api/v1/admin/search`

Globální hledání (objednávky, zákazníci, motivy)

- **Parametry:** `q` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `AdminSearchResponse`

### Admin - uživatelé

#### `GET /api/v1/admin/users`

Seznam uživatelů administrace

- **Parametry:** `page` (query), `pageSize` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `PaginatedAdminUserList`

#### `POST /api/v1/admin/users`

Založit uživatele administrace

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `AdminUserCreate`, `application/x-www-form-urlencoded`: `AdminUserCreate`, `multipart/form-data`: `AdminUserCreate`
- **Úspěšná odpověď:** `201` `AdminUserCreate`

#### `GET /api/v1/admin/users/{id}`

Detail uživatele administrace

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `AdminUser`

#### `PATCH /api/v1/admin/users/{id}`

Upravit uživatele administrace

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** `application/json`: `PatchedAdminUserUpdate`, `application/x-www-form-urlencoded`: `PatchedAdminUserUpdate`, `multipart/form-data`: `PatchedAdminUserUpdate`
- **Úspěšná odpověď:** `200` `AdminUserUpdate`

#### `DELETE /api/v1/admin/users/{id}`

Smazat uživatele administrace

- **Parametry:** `id` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `204` (bez těla)

### Checkout

#### `POST /api/v1/cart/validate`

Ověřit košík před odeslením objednávky

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `CartValidate`, `application/x-www-form-urlencoded`: `CartValidate`, `multipart/form-data`: `CartValidate`
- **Úspěšná odpověď:** `200` `CartValidateResponse`

#### `POST /api/v1/orders`

Odeslat objednávku (checkout)

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `OrderCreate`, `application/x-www-form-urlencoded`: `OrderCreate`, `multipart/form-data`: `OrderCreate`
- **Úspěšná odpověď:** `201` `OrderCreateResponse`

#### `GET /api/v1/orders/access/{accessToken}`

Detail objednávky přes přístupový token

- **Parametry:** `accessToken` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `OrderCustomerDetail`

#### `POST /api/v1/orders/lookup`

Dohledat objednávku (číslo + e-mail)

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `OrderLookupRequest`, `application/x-www-form-urlencoded`: `OrderLookupRequest`, `multipart/form-data`: `OrderLookupRequest`
- **Úspěšná odpověď:** `200` `OrderCustomerDetail`

#### `POST /api/v1/uploads/designs`

Nahrát podklad pro vlastní potisk

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `DesignUploadRequest`, `application/x-www-form-urlencoded`: `DesignUploadRequest`, `multipart/form-data`: `DesignUploadRequest`
- **Úspěšná odpověď:** `201` `DesignUploadResponse`

### Katalog

#### `GET /api/v1/catalog/options`

Všechny aktivní číselníky jedním voláním (konfigurátor, checkout)

- **Parametry:** —
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `CatalogOptionsResponse`

#### `GET /api/v1/categories`

Seznam kategorií (menu, homepage)

- **Parametry:** `ordering` (query), `q` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` seznam `Category`

#### `GET /api/v1/categories/{slug}`

Detail kategorie podle slugu

- **Parametry:** `slug` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `Category`

#### `GET /api/v1/motifs`

Výpis motivů (katalog, homepage Novinky)

- **Parametry:** `page` (query), `pageSize` (query)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `PaginatedMotifListList`

#### `GET /api/v1/motifs/{slug}`

Detail motivu podle slugu (konfigurátor)

- **Parametry:** `slug` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `MotifDetail`

#### `GET /api/v1/motifs/{slug}/related`

Doporučené motivy ("Do party se hodí i...")

- **Parametry:** `page` (query), `pageSize` (query), `slug` (path, povinný)
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `PaginatedMotifListList`

### Konfigurace

#### `GET /api/v1/config`

Konfigurace obchodu pro hlavičku, patičku a checkout

- **Parametry:** —
- **Tělo požadavku:** —
- **Úspěšná odpověď:** `200` `ConfigResponse`

#### `POST /api/v1/contact`

Odeslat kontaktní/poptávkový formulář

- **Parametry:** —
- **Tělo požadavku:** `application/json`: `Contact`, `application/x-www-form-urlencoded`: `Contact`, `multipart/form-data`: `Contact`
- **Úspěšná odpověď:** `204` (bez těla)

## 5. Praktické integrační poznámky

### Bezpečnost a oprávnění

- Veřejný přístup k objednávce přes `accessToken` nepovažujte za přihlášení; token je citlivý a nemá se posílat analytice ani zapisovat do logů.
- Administrátorské přihlášení je **session-based**, nikoli JWT.
- Skrytí akce ve frontendovém UI není autorizace; backend musí rozhodnout, zda ji může uživatel vykonat.

### Objednávky a ceny

- Neodvozujte konečnou cenu pouze z katalogového zobrazení. Před objednávkou použijte validaci košíku.
- Vlastní potisky mohou vyžadovat nabídku ceny; pracujte s hodnotami `null` a `requiresQuote`.
- Objednávka vzniká až po úspěšném `POST /api/v1/orders`; validace košíku sama o sobě nic neobjednává.
- U dopravy, výdejních míst a platebních metod používejte hodnoty poskytované API; nepředpokládejte fixní seznam.

### Obrázky a soubory

- Upload návrhu používá multipart, ne JSON.
- Stažení výrobních souborů a štítků může vracet binární obsah; nepokoušejte se odpověď automaticky parsovat jako JSON.
- Obrázky motivů mohou být navázané na barvu varianty; používejte odpovídající metadata.

### Práce se specifikací

- **Při rozdílu mezi tímto přehledem a aktuálním schématem má přednost aktuální OpenAPI.**
- Endpointy a HTTP metody jsou převzaté ze schématu `schema-4`; v různých nasazeních se mohou lišit.
- Případné frontendové routy (např. `/kosik`, `/admin/objednavky`) nejsou součástí backendového kontraktu a dokument je neurčuje.
