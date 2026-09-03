-- =============================================================================
-- Trikonoš.cz — databázové schéma v2 (PostgreSQL)
-- Katalog, klienti, objednávky a integrace (platební brána, doprava, Pohoda)
--
-- Změny oproti dodanému schema.sql jsou okomentované značkou "-- v2:"
-- Podrobné zdůvodnění je v Trikonos_DB_implementacni_plan.md
-- =============================================================================

-- gen_random_uuid() je od PostgreSQL 13 součástí jádra (netřeba extension pgcrypto)

-- -----------------------------------------------------------------------------
-- POMOCNÁ FUNKCE: automatická aktualizace updated_at
-- -----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- -----------------------------------------------------------------------------
-- ENUMY
-- -----------------------------------------------------------------------------

CREATE TYPE staff_role            AS ENUM ('ADMIN', 'STAFF');
-- v2: `cut` a `color` už NEJSOU enum, viz číselníkové tabulky níže.
CREATE TYPE size                  AS ENUM ('S', 'M', 'L', 'XL', 'XXL');
CREATE TYPE address_type          AS ENUM ('BILLING', 'SHIPPING');
CREATE TYPE team_inquiry_status   AS ENUM ('NEW', 'IN_PROGRESS', 'DONE');
CREATE TYPE order_status          AS ENUM ('NEW', 'PAID', 'PROCESSING', 'SHIPPED', 'DELIVERED', 'CANCELLED', 'REFUNDED');
-- v2: `shipping_method` už NENÍ enum vázaný na jednoho dopravce, viz tabulka `carriers` níže.
CREATE TYPE delivery_type         AS ENUM ('PICKUP_POINT', 'HOME_DELIVERY');
CREATE TYPE payment_provider      AS ENUM ('GOPAY', 'COMGATE');
CREATE TYPE payment_status        AS ENUM ('PENDING', 'PAID', 'FAILED', 'REFUNDED');
CREATE TYPE shipment_status       AS ENUM ('CREATED', 'LABEL_PRINTED', 'HANDED_OVER', 'IN_TRANSIT', 'DELIVERED', 'RETURNED');
CREATE TYPE pohoda_sync_direction AS ENUM ('EXPORT_ORDER', 'IMPORT_STOCK');
CREATE TYPE pohoda_sync_status    AS ENUM ('SUCCESS', 'FAILED', 'PENDING');

-- -----------------------------------------------------------------------------
-- ADMINISTRACE
-- -----------------------------------------------------------------------------

CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name          TEXT NOT NULL,
    role          staff_role NOT NULL DEFAULT 'STAFF',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- -----------------------------------------------------------------------------
-- ČÍSELNÍKY (admin obrazovka "Číselníky" — editovatelné za běhu, ne ENUM)
-- v2: nové tabulky nahrazující ENUM cut / TEXT color a doplňující typ oblečení
-- -----------------------------------------------------------------------------

CREATE TABLE cuts (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL UNIQUE,       -- Pánské, Dámské, Unisex, Mikina, ...
    position   INT NOT NULL DEFAULT 0,
    is_active  BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE colors (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL UNIQUE,       -- Černá, Bílá, Žlutá, ...
    hex_value  TEXT NOT NULL,              -- '#000000'
    position   INT NOT NULL DEFAULT 0,
    is_active  BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE garment_types (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL UNIQUE,       -- Tričko, Mikina, Tílko, Doplněk, ...
    position   INT NOT NULL DEFAULT 0,
    is_active  BOOLEAN NOT NULL DEFAULT true
);

-- v2: dopravci — nahrazuje dřívější enum navázaný jen na Zásilkovnu. Dalšího
-- dopravce (DPD, Česká pošta, WEDO, ...) admin přidá bez migrace, stejně jako
-- ostatní číselníky výše. `code` je stabilní klíč pro integrační vrstvu
-- (/lib/integrations), `name` je popisek pro checkout a administraci.
CREATE TABLE carriers (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                   TEXT NOT NULL UNIQUE,   -- Zásilkovna, DPD, Česká pošta, ...
    code                   TEXT NOT NULL UNIQUE,   -- interní kód, např. 'PACKETA'
    supports_pickup_point  BOOLEAN NOT NULL DEFAULT false,  -- nabízí výdejní místa/boxy
    supports_home_delivery BOOLEAN NOT NULL DEFAULT false,  -- nabízí doručení na adresu
    position               INT NOT NULL DEFAULT 0,
    is_active              BOOLEAN NOT NULL DEFAULT true
);

-- -----------------------------------------------------------------------------
-- KATALOG
-- -----------------------------------------------------------------------------

CREATE TABLE categories (
    id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name     TEXT NOT NULL,
    slug     TEXT NOT NULL UNIQUE,
    position INT NOT NULL DEFAULT 0
);

CREATE TABLE motifs (
    id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE
);

CREATE TABLE collections (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    description TEXT
);

CREATE TABLE products (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL UNIQUE,
    description     TEXT,
    base_price      INT NOT NULL CHECK (base_price >= 0),   -- v haléřích
    is_active       BOOLEAN NOT NULL DEFAULT true,
    category_id     UUID NOT NULL REFERENCES categories(id),
    garment_type_id UUID NOT NULL REFERENCES garment_types(id),  -- v2: nové, viz otevřená otázka v plánu
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_products_category ON products(category_id);
CREATE INDEX idx_products_garment_type ON products(garment_type_id);
CREATE TRIGGER trg_products_updated_at BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE product_images (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    color_id   UUID REFERENCES colors(id),  -- v2: NULL = platí pro všechny barvy, jinak jen pro danou barvu
    url        TEXT NOT NULL,
    alt_text   TEXT,
    position   INT NOT NULL DEFAULT 0
);
CREATE INDEX idx_product_images_product ON product_images(product_id);
CREATE INDEX idx_product_images_color ON product_images(color_id);

CREATE TABLE product_variants (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id     UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    sku            TEXT NOT NULL UNIQUE,
    cut_id         UUID NOT NULL REFERENCES cuts(id),     -- v2: dřív ENUM cut
    color_id       UUID NOT NULL REFERENCES colors(id),   -- v2: dřív TEXT color
    size           size NOT NULL,
    price_override INT CHECK (price_override IS NULL OR price_override >= 0),
    stock_quantity INT NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),    -- v2: sledování změn skladu/ceny
    UNIQUE (product_id, cut_id, color_id, size)
);
CREATE INDEX idx_variants_product ON product_variants(product_id);
CREATE TRIGGER trg_variants_updated_at BEFORE UPDATE ON product_variants
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- M:N — produkt může mít víc motivů
CREATE TABLE product_motifs (
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    motif_id   UUID NOT NULL REFERENCES motifs(id) ON DELETE CASCADE,
    PRIMARY KEY (product_id, motif_id)
);

-- M:N — produkt může být ve víc kolekcích
CREATE TABLE product_collections (
    product_id    UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    collection_id UUID NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    PRIMARY KEY (product_id, collection_id)
);

-- -----------------------------------------------------------------------------
-- KLIENTI A ADRESY
-- -----------------------------------------------------------------------------

CREATE TABLE customers (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email              TEXT NOT NULL UNIQUE,
    phone              TEXT,
    first_name         TEXT,
    last_name          TEXT,
    password_hash      TEXT,               -- v2: NULL = zákazník nemá účet (guest checkout je výchozí)
    email_verified_at  TIMESTAMPTZ,        -- v2: pro budoucí volitelné účty
    marketing_consent  BOOLEAN NOT NULL DEFAULT false,
    note               TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_customers_updated_at BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE addresses (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id  UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    type         address_type NOT NULL,
    full_name    TEXT NOT NULL,
    company_name TEXT,
    ico          TEXT,                      -- IČO — pro firemní fakturaci / export do Pohody
    dic          TEXT,                      -- DIČ
    street       TEXT NOT NULL,
    city         TEXT NOT NULL,
    zip          TEXT NOT NULL,
    country      TEXT NOT NULL DEFAULT 'CZ'
);
CREATE INDEX idx_addresses_customer ON addresses(customer_id);

-- B2B poptávky na týmová trička (homepage / detail produktu)
CREATE TABLE team_inquiries (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    email        TEXT NOT NULL,
    phone        TEXT,
    company_name TEXT,
    message      TEXT NOT NULL,
    product_id   UUID REFERENCES products(id) ON DELETE SET NULL,  -- v2: kontext, ze kterého produktu poptávka vznikla
    motif_id     UUID REFERENCES motifs(id) ON DELETE SET NULL,    -- v2: nepovinné, pokud šlo o konkrétní motiv
    status       team_inquiry_status NOT NULL DEFAULT 'NEW',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_team_inquiries_product ON team_inquiries(product_id);

-- -----------------------------------------------------------------------------
-- OBJEDNÁVKY
-- -----------------------------------------------------------------------------

CREATE SEQUENCE order_number_seq START 1;  -- v2: pro bezkolizní generování order_number v aplikaci

CREATE TABLE orders (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number         TEXT NOT NULL UNIQUE,
    customer_id          UUID REFERENCES customers(id),
    status               order_status NOT NULL DEFAULT 'NEW',
    subtotal             INT NOT NULL CHECK (subtotal >= 0),
    shipping_cost        INT NOT NULL CHECK (shipping_cost >= 0),
    total                INT NOT NULL CHECK (total >= 0),
    currency             TEXT NOT NULL DEFAULT 'CZK',
    carrier_id           UUID NOT NULL REFERENCES carriers(id),   -- v2: dřív enum shipping_method
    delivery_type        delivery_type NOT NULL,                  -- v2: výdejní místo / doručení na adresu
    pickup_point_id      TEXT,               -- v2: ID výdejního místa (obecné, ne jen Zásilkovna)
    pickup_point_name    TEXT,
    billing_address_id   UUID REFERENCES addresses(id),
    shipping_address_id  UUID REFERENCES addresses(id),
    note                 TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_carrier ON orders(carrier_id);
CREATE TRIGGER trg_orders_updated_at BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- v2: historie změn stavu objednávky — pro audit a zákaznický servis
CREATE TABLE order_status_history (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id    UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    status      order_status NOT NULL,
    note        TEXT,
    changed_by  UUID REFERENCES users(id),   -- NULL = změna systémem (např. webhook platby)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_order_status_history_order ON order_status_history(order_id);

CREATE TABLE order_items (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id           UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_variant_id UUID NOT NULL REFERENCES product_variants(id),
    product_name       TEXT NOT NULL,        -- snapshot v čase objednání
    variant_label      TEXT NOT NULL,        -- např. "Pánské / černá / M"
    unit_price         INT NOT NULL CHECK (unit_price >= 0),
    quantity           INT NOT NULL CHECK (quantity > 0)
);
CREATE INDEX idx_order_items_order ON order_items(order_id);

-- -----------------------------------------------------------------------------
-- PLATBY
-- -----------------------------------------------------------------------------

CREATE TABLE payments (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id            UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    provider            payment_provider NOT NULL,
    provider_payment_id TEXT,
    status              payment_status NOT NULL DEFAULT 'PENDING',
    amount              INT NOT NULL CHECK (amount >= 0),
    raw_payload         JSONB,               -- odpověď/webhook z brány
    paid_at             TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_payments_order ON payments(order_id);
-- v2: ochrana proti duplicitnímu zpracování téhož webhooku od platební brány
CREATE UNIQUE INDEX uq_payments_provider_payment_id
    ON payments(provider, provider_payment_id)
    WHERE provider_payment_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- DOPRAVA A ZÁSILKY
-- (tabulka `carriers` je definovaná výš u ostatních číselníků, protože na ni
-- odkazuje `orders` — viz sekce ČÍSELNÍKY)
-- -----------------------------------------------------------------------------

CREATE TABLE shipments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    carrier_id      UUID NOT NULL REFERENCES carriers(id),  -- v2: nové, dřív implicitně jen Zásilkovna
    tracking_number TEXT,
    label_url       TEXT,
    status          shipment_status NOT NULL DEFAULT 'CREATED',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_shipments_order ON shipments(order_id);
CREATE INDEX idx_shipments_carrier ON shipments(carrier_id);
CREATE TRIGGER trg_shipments_updated_at BEFORE UPDATE ON shipments
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- v2: cena a práh dopravy zdarma teď per dopravce + typ doručení, editovatelné bez nasazení
CREATE TABLE shipping_rates (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    carrier_id    UUID NOT NULL REFERENCES carriers(id),
    delivery_type delivery_type NOT NULL,
    price         INT NOT NULL CHECK (price >= 0),      -- v haléřích
    free_from     INT CHECK (free_from IS NULL OR free_from >= 0),  -- NULL = doprava nikdy není zdarma
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (carrier_id, delivery_type)
);
CREATE INDEX idx_shipping_rates_carrier ON shipping_rates(carrier_id);
CREATE TRIGGER trg_shipping_rates_updated_at BEFORE UPDATE ON shipping_rates
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- -----------------------------------------------------------------------------
-- POHODA — SYNCHRONIZACE
-- -----------------------------------------------------------------------------

CREATE TABLE pohoda_sync_logs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id      UUID REFERENCES orders(id),
    direction     pohoda_sync_direction NOT NULL,
    status        pohoda_sync_status NOT NULL DEFAULT 'PENDING',
    payload       JSONB,               -- odeslané/přijaté XML nebo jeho shrnutí
    error_message TEXT,
    synced_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_pohoda_sync_order ON pohoda_sync_logs(order_id);

-- -----------------------------------------------------------------------------
-- OBECNÁ KONFIGURACE E-SHOPU
-- v2: pro drobné business hodnoty, které se mění bez nasazení (texty, prahy, kontakty)
-- -----------------------------------------------------------------------------

CREATE TABLE shop_settings (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_shop_settings_updated_at BEFORE UPDATE ON shop_settings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
