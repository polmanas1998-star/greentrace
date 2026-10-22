-- GreenTrace synthetic world. Same tables in SQLite (sample mode) and in the
-- Postgres attached to Zetaris (live mode). Simple types only: the polygon stays
-- WKT text (empty string when a plot is declared by a single point).

CREATE TABLE company (
    company_id   TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    fiscal_year  INTEGER NOT NULL,
    total_tonnes DOUBLE PRECISION NOT NULL
);

CREATE TABLE suppliers (
    supplier_id       TEXT PRIMARY KEY,
    company_id        TEXT NOT NULL REFERENCES company (company_id),
    name              TEXT NOT NULL,
    cooperative_group TEXT NOT NULL,
    country           TEXT NOT NULL
);

-- A plot declared by two suppliers appears twice (case 10), hence the composite key.
CREATE TABLE plots (
    plot_id     TEXT NOT NULL,
    supplier_id TEXT NOT NULL REFERENCES suppliers (supplier_id),
    lat         DOUBLE PRECISION,
    lon         DOUBLE PRECISION,
    polygon_wkt TEXT NOT NULL,
    area_ha     DOUBLE PRECISION,
    declared_by TEXT NOT NULL,
    PRIMARY KEY (plot_id, supplier_id)
);

-- plot_id is NULL for volume that is not traced to any plot (case 8).
CREATE TABLE deliveries (
    delivery_id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL REFERENCES suppliers (supplier_id),
    plot_id     TEXT,
    year        INTEGER NOT NULL,
    tonnes      DOUBLE PRECISION NOT NULL,
    certified   TEXT NOT NULL CHECK (certified IN ('yes', 'no'))
);

CREATE TABLE alerts (
    alert_id   TEXT PRIMARY KEY,
    lat        DOUBLE PRECISION NOT NULL,
    lon        DOUBLE PRECISION NOT NULL,
    alert_date TEXT NOT NULL,
    confidence TEXT NOT NULL CHECK (confidence IN ('low', 'high', 'highest')),
    area_ha    DOUBLE PRECISION NOT NULL,
    system     TEXT NOT NULL
);

CREATE TABLE report_paragraphs (
    doc_id       TEXT NOT NULL,
    section      TEXT NOT NULL CHECK (section IN ('main', 'annex')),
    paragraph_no INTEGER NOT NULL,
    text         TEXT NOT NULL,
    PRIMARY KEY (doc_id, section, paragraph_no)
);
