COMPILE USL eudr DEPLOY NAMESPACE lightning.metastore.greentrace DDL
CREATE TABLE company (
  company_id varchar(40) NOT NULL PRIMARY KEY,
  name varchar(200),
  fiscal_year int,
  total_tonnes double
);
CREATE TABLE suppliers (
  supplier_id varchar(20) NOT NULL PRIMARY KEY,
  company_id varchar(40) FOREIGN KEY REFERENCES company(company_id),
  name varchar(100),
  cooperative_group varchar(100),
  country varchar(2)
);
CREATE TABLE plots (
  plot_id varchar(20) NOT NULL PRIMARY KEY,
  supplier_id varchar(20) FOREIGN KEY REFERENCES suppliers(supplier_id),
  lat double,
  lon double,
  polygon_wkt varchar(2000),
  area_ha double,
  declared_by varchar(20)
);
CREATE TABLE deliveries (
  delivery_id varchar(20) NOT NULL PRIMARY KEY,
  supplier_id varchar(20) FOREIGN KEY REFERENCES suppliers(supplier_id),
  plot_id varchar(20) FOREIGN KEY REFERENCES plots(plot_id),
  year int,
  tonnes double,
  certified varchar(3)
);
CREATE TABLE alerts (
  alert_id varchar(20) NOT NULL PRIMARY KEY,
  lat double,
  lon double,
  alert_date varchar(10),
  confidence varchar(10),
  area_ha double,
  system varchar(20)
);
CREATE TABLE report_paragraphs (
  paragraph_key varchar(40) NOT NULL PRIMARY KEY,
  doc_id varchar(20),
  section varchar(10),
  paragraph_no int,
  paragraph_text varchar(4000)
);
CREATE TABLE reference_cells (
  cell_key varchar(30) NOT NULL PRIMARY KEY,
  lat double,
  lon double,
  forest_fraction_2020 double,
  loss_year int
)
