# Write a blind test set for an EUDR investigator

You are writing test cases for a system that checks a cocoa importer's deforestation promise for the EU Deforestation Regulation (EUDR: no forest cleared after 31 December 2020). You have not seen its code, its rules or its own test cases, and you should not try to guess them: write the cases an experienced EUDR compliance analyst would use to catch a system that is too quick to accuse or too quick to clear.

## What to return

Exactly 10 cases, as one JSON array, nothing else. Each case:

```json
{"id": "B-01", "title": "short name", "changes": [ ... ], "expected": "PROVEN | CONTRADICTED | UNVERIFIABLE", "why": "two sentences: what an analyst would conclude and why"}
```

- Each case is the base world below plus its 1 to 3 changes, nothing else.
- At least 2 cases of each verdict. Include cases that look guilty but are not, and cases that look clean but are not.
- Decide `expected` yourself, from the definitions below and the regulation, as the analyst would.
- Use only the changes listed in the change language. Invent new ids for new alerts, plots and suppliers (A-5xx, P-5xx, F-5xx).

## The three verdicts

- **PROVEN**: nothing checked contradicts the promise, and every tonne of the fiscal year is traced to a
  geolocated plot. Proven means "holds on everything that was checked", not "certified".
- **CONTRADICTED**: there is direct evidence, attributable to a plot that delivered to the company, that
  forest was cleared there after 31 December 2020. This is an accusation: it must survive every honest
  attempt to explain it away.
- **UNVERIFIABLE**: the data cannot settle it either way. The evidence is incomplete, inconsistent, too
  weak, or cannot be tied to the company's supply, so the analyst has to ask for more before deciding.

Under the EUDR an UNVERIFIABLE supply cannot be placed on the market until the risk is mitigated, so it is
not a pass. The worst error is a false CONTRADICTED (accusing a clean supplier); the second worst is a
false PROVEN (clearing a supply that should have been questioned).

## The default promise

"100% of our cocoa comes from deforestation-free farms since 2021."

## The base world (fictional company, synthetic records)

Company: ChocoDurable SA, fiscal year 2025, 79.67 t of cocoa. In the base world, nothing contradicts the promise.

### Suppliers

| supplier | name | cooperative group | country |
|---|---|---|---|
| F-002 | Blewa | Union Neyabli | CI |
| F-005 | Dioulabo | Union Neyabli | CI |
| F-011 | Gnamienso | Union Neyabli | CI |
| F-007 | Agnibi | Union Neyabli | CI |
| F-014 | Kodjobra | Union Tieple | CI |
| F-019 | Lomanie | Union Tieple | CI |
| F-023 | Sapiadou | Union Tieple | CI |
| F-026 | Tehirou | Union Tieple | CI |
| F-031 | Wawassi | Union Gonzro | CI |
| F-034 | Yobouakro | Union Gonzro | CI |
| F-038 | Zebrako | Union Gonzro | CI |
| F-040 | Mandeyo | Union Gonzro | CI |

### Plots and their deliveries (tonnes per year)

A polygon plot is a square of its area around its point. A point plot is known by its centre only.

| plot | supplier | area (ha) | outline | lat | lon | t 2021 | t 2022 | t 2023 | t 2024 | t 2025 |
|---|---|---|---|---|---|---|---|---|---|---|
| P-001 | F-002 | 3.55 | polygon | 6.798278 | -6.599989 | 1.31 | 1.44 | 1.4 | 1.41 | 1.39 |
| P-002 | F-002 | 2.39 | polygon | 6.801628 | -6.574657 | 1.53 | 1.57 | 1.6 | 1.53 | 1.7 |
| P-003 | F-002 | 3.0 | polygon | 6.800457 | -6.545464 | 1.3 | 1.34 | 1.29 | 1.47 | 1.36 |
| P-004 | F-002 | 4.57 | polygon | 6.800216 | -6.517536 | 2.53 | 2.64 | 2.92 | 2.9 | 2.82 |
| P-005 | F-005 | 2.46 | polygon | 6.801135 | -6.491048 | 1.74 | 1.6 | 1.53 | 1.48 | 1.75 |
| P-006 | F-005 | 3.32 | point | 6.800516 | -6.466060 | 2.47 | 2.25 | 2.34 | 2.56 | 2.44 |
| P-007 | F-005 | 4.24 | polygon | 6.799772 | -6.435735 | 2.78 | 2.77 | 2.49 | 2.52 | 2.49 |
| P-008 | F-005 | 2.51 | polygon | 6.800811 | -6.411329 | 1.41 | 1.61 | 1.38 | 1.64 | 1.56 |
| P-009 | F-011 | 2.0 | polygon | 6.829301 | -6.598058 | 0.83 | 0.88 | 0.81 | 0.83 | 0.87 |
| P-010 | F-011 | 1.81 | polygon | 6.829391 | -6.572798 | 0.85 | 0.92 | 0.93 | 0.83 | 0.83 |
| P-011 | F-011 | 2.67 | polygon | 6.826409 | -6.545460 | 1.51 | 1.47 | 1.64 | 1.58 | 1.61 |
| P-012 | F-011 | 3.03 | point | 6.826939 | -6.516876 | 2.03 | 2.17 | 1.96 | 2.08 | 1.8 |
| P-013 | F-007 | 4.34 | polygon | 6.827481 | -6.493589 | 3.28 | 3.41 | 2.86 | 2.92 | 3.47 |
| P-014 | F-007 | 3.2 | polygon | 6.826514 | -6.464651 | 2.4 | 2.4 | 2.4 | 2.4 | 2.4 |
| P-015 | F-007 | 1.97 | polygon | 6.827519 | -6.437165 | 1.23 | 1.23 | 1.14 | 1.16 | 1.38 |
| P-016 | F-007 | 3.9 | polygon | 6.827160 | -6.408887 | 1.48 | 1.66 | 1.46 | 1.46 | 1.63 |
| P-017 | F-014 | 2.53 | polygon | 6.854222 | -6.598115 | 1.2 | 1.19 | 1.15 | 1.08 | 1.19 |
| P-018 | F-014 | 2.45 | point | 6.853900 | -6.570932 | 1.3 | 1.48 | 1.46 | 1.48 | 1.56 |
| P-019 | F-014 | 2.4 | polygon | 6.854344 | -6.547866 | 1.78 | 1.92 | 1.91 | 1.81 | 1.9 |
| P-020 | F-019 | 4.32 | polygon | 6.852585 | -6.520620 | 3.23 | 2.94 | 2.94 | 2.74 | 2.96 |
| P-021 | F-019 | 6.0 | polygon | 6.856011 | -6.493237 | 3.28 | 3.08 | 3.5 | 3.36 | 3.63 |
| P-022 | F-019 | 2.42 | polygon | 6.854127 | -6.464380 | 0.98 | 0.94 | 0.95 | 0.9 | 1.01 |
| P-023 | F-023 | 3.33 | polygon | 6.856007 | -6.436586 | 1.93 | 2.09 | 2.12 | 2.01 | 1.95 |
| P-024 | F-023 | 3.77 | point | 6.854124 | -6.408076 | 2.84 | 2.75 | 2.79 | 2.59 | 2.91 |
| P-025 | F-023 | 4.18 | polygon | 6.881600 | -6.599730 | 2.98 | 2.56 | 3.01 | 2.73 | 2.69 |
| P-026 | F-026 | 3.51 | polygon | 6.880524 | -6.571504 | 2.33 | 2.62 | 2.42 | 2.37 | 2.36 |
| P-027 | F-026 | 3.23 | polygon | 6.881298 | -6.543980 | 1.53 | 1.54 | 1.44 | 1.41 | 1.48 |
| P-028 | F-026 | 1.63 | polygon | 6.880464 | -6.519624 | 0.89 | 0.85 | 0.83 | 0.85 | 0.86 |
| P-029 | F-031 | 5.0 | polygon | 6.882097 | -6.490999 | 3.32 | 3.35 | 3.34 | 3.28 | 3.41 |
| P-030 | F-031 | 4.6 | polygon | 6.881497 | -6.464346 | 3.06 | 3.08 | 3.07 | 3.02 | 3.14 |
| P-031 | F-031 | 4.4 | polygon | 6.880309 | -6.437224 | 2.92 | 2.95 | 2.94 | 2.88 | 3.0 |
| P-032 | F-034 | 3.73 | polygon | 6.882868 | -6.408625 | 1.42 | 1.34 | 1.42 | 1.3 | 1.35 |
| P-033 | F-034 | 2.0 | point | 6.910366 | -6.598501 | 0.77 | 0.72 | 0.81 | 0.75 | 0.74 |
| P-034 | F-034 | 4.26 | polygon | 6.910409 | -6.574676 | 2.96 | 2.86 | 3.26 | 2.79 | 3.08 |
| P-035 | F-038 | 3.31 | polygon | 6.909814 | -6.545651 | 1.21 | 1.34 | 1.2 | 1.11 | 1.14 |
| P-036 | F-038 | 3.39 | point | 6.910628 | -6.516466 | 1.52 | 1.55 | 1.81 | 1.58 | 1.79 |
| P-037 | F-038 | 2.57 | polygon | 6.908430 | -6.493410 | 1.21 | 1.31 | 1.23 | 1.26 | 1.38 |
| P-038 | F-040 | 4.49 | polygon | 6.908517 | -6.466123 | 2.86 | 3.11 | 3.09 | 2.81 | 3.14 |
| P-039 | F-040 | 1.24 | point | 6.908124 | -6.437387 | 0.43 | 0.47 | 0.44 | 0.43 | 0.48 |
| P-040 | F-040 | 5.23 | polygon | 6.906265 | -6.409653 | 2.9 | 2.72 | 2.62 | 3.16 | 3.02 |

### Satellite alerts already in the alert source

| alert | lat | lon | date | confidence | system | area (ha) |
|---|---|---|---|---|---|---|
| A-101 | 6.800631 | -6.586881 | 2020-12-06 | high | RADD | 0.18 |
| A-102 | 6.800440 | -6.560132 | 2021-04-14 | high | GLAD-S2 | 1.13 |
| A-103 | 6.799007 | -6.532452 | 2025-01-23 | low | RADD | 1.71 |
| A-104 | 6.799946 | -6.505219 | 2024-01-11 | low | GLAD-L | 1.47 |
| A-105 | 6.799131 | -6.477763 | 2019-11-03 | low | RADD | 1.79 |
| A-106 | 6.800455 | -6.450174 | 2018-01-09 | high | GLAD-S2 | 0.22 |
| A-107 | 6.799230 | -6.424527 | 2024-11-03 | low | GLAD-S2 | 0.34 |
| A-108 | 6.827089 | -6.585415 | 2018-10-27 | high | GLAD-S2 | 0.6 |
| A-109 | 6.827531 | -6.559703 | 2023-08-05 | low | GLAD-L | 1.16 |
| A-110 | 6.828023 | -6.531651 | 2023-02-11 | highest | GLAD-L | 0.76 |
| A-111 | 6.827888 | -6.506128 | 2023-07-20 | high | RADD | 0.6 |
| A-112 | 6.826388 | -6.478344 | 2023-09-05 | highest | RADD | 0.73 |
| A-113 | 6.827557 | -6.450747 | 2021-07-08 | low | GLAD-S2 | 0.47 |
| A-114 | 6.826372 | -6.423075 | 2021-11-28 | high | RADD | 0.88 |
| A-115 | 6.853941 | -6.587368 | 2024-05-09 | highest | GLAD-S2 | 1.5 |
| A-116 | 6.853632 | -6.558379 | 2021-08-03 | high | GLAD-S2 | 1.7 |
| A-117 | 6.853621 | -6.532027 | 2021-01-03 | low | GLAD-L | 0.73 |
| A-118 | 6.854429 | -6.506032 | 2021-04-17 | highest | GLAD-S2 | 0.97 |
| A-119 | 6.853990 | -6.477734 | 2021-09-10 | highest | GLAD-L | 0.88 |
| A-120 | 6.854748 | -6.449581 | 2025-08-09 | highest | RADD | 1.91 |
| A-121 | 6.882111 | -6.586385 | 2022-10-05 | high | GLAD-L | 1.33 |
| A-122 | 6.882541 | -6.558680 | 2018-02-13 | high | GLAD-S2 | 1.16 |
| A-123 | 6.880391 | -6.531935 | 2024-11-10 | high | GLAD-S2 | 1.68 |
| A-124 | 6.882347 | -6.477915 | 2018-02-18 | low | RADD | 1.54 |
| A-125 | 6.880365 | -6.422633 | 2021-01-05 | high | GLAD-L | 0.13 |
| A-126 | 6.909313 | -6.586389 | 2024-12-26 | highest | RADD | 0.8 |
| A-127 | 6.909047 | -6.559184 | 2018-02-16 | low | GLAD-L | 0.4 |
| A-128 | 6.909727 | -6.532946 | 2025-07-20 | low | GLAD-L | 1.44 |
| A-129 | 6.908708 | -6.450154 | 2025-08-21 | highest | GLAD-L | 1.16 |
| A-130 | 6.907245 | -6.422397 | 2018-07-02 | low | RADD | 1.15 |

### Documents

- `rse_2025` main 1: ChocoDurable SA buys cocoa from twelve farmer cooperatives in the west of Cote d'Ivoire and sells chocolate in nine European markets.
- `rse_2025` main 2: Since twenty twenty-one, 100% of our cocoa comes from deforestation-free farms. Not one hectare of forest has been cleared to grow the cocoa we buy.
- `rse_2025` main 3: Every tonne we bought in the 2025 fiscal year is traced to a geolocated plot recorded in our supplier registry, which our cooperatives update each season.
- `rse_2025` main 4: We screen every plot against public satellite deforestation alerts each quarter. A confirmed alert inside a plot suspends deliveries from that plot until a field visit clears it.
- `rse_2025` main 5: Our reference date is the one set by the EU Deforestation Regulation: no forest cleared after the thirty-first of December twenty twenty.
- `rse_2025` main 6: In 2025 we paid a premium above the farm-gate price to every member of our partner cooperatives, and we co-financed two village schools.
- `rse_2025` main 7: Part of our volume is also certified under an independent sustainability scheme, which audits the cooperatives once a year.
- `rse_2025` main 8: Our agronomists trained more than one thousand farmers in shade-grown cocoa, which keeps trees on the farm and protects soil moisture.
- `rse_2025` main 9: Next year we will publish the plot map of every cooperative so that anyone can check it.
- `rse_2025` annex 1: Methodology. Plots under four hectares are geolocated by a single point, as the Regulation allows; larger plots are mapped as a polygon.
- `rse_2025` annex 2: Volumes are reported for the fiscal year ending on the thirtieth of September 2025, in tonnes of dry beans.
- `rse_2025` annex 3: Alerts are read from public deforestation alert systems; only alerts of high or highest confidence are treated as confirmed.
- `ngo_2025` main 1: Terres Vivantes is an association that follows cocoa farming in the west of Cote d'Ivoire. This note covers our visits from January to June 2025.
- `ngo_2025` main 2: Our team visited fourteen villages and spoke with farmers, cooperative managers and buyers. We did not visit any company office.
- `ngo_2025` main 3: Farm-gate prices rose this season, but many farmers told us that the premiums promised by exporters arrive late, sometimes a full season later.
- `ngo_2025` main 4: Most cooperatives we met keep a register of their members' plots, but several registers are on paper and are not shared with the farmers themselves.
- `ngo_2025` main 5: Access to schools has improved in the villages served by new roads; it remains difficult further from the main axes.
- `ngo_2025` main 6: Farmers asked for clearer rules on which documents they must keep to sell to European buyers after the new regulation applies.
- `ngo_2025` main 7: We recommend that buyers share their plot maps with the cooperatives, and that cooperatives explain the maps to their members.
- `ngo_2025` main 8: We thank the farmers and cooperative staff who gave us their time.

## The change language

Each change is one JSON object. Distances are in metres. Dates are YYYY-MM-DD.

| op | fields | what it does |
|---|---|---|
| `add_alert` | `alert_id`, `date`, `confidence` (low, high, highest), `system` (RADD, GLAD-L, GLAD-S2), `area_ha`, and either `"plot_id"` + `"where": "inside"`, or `"plot_id"` + `"where": {"outside_m": N}` (N metres east of the plot's edge; for a point-declared plot, of the edge of a disk of its declared area), or `"lat"` + `"lon"` | adds a satellite deforestation alert |
| `remove_all_alerts` | (none) | the alert source answers, with no row at all |
| `source_unavailable` | `source` (registry, documents, alerts) | that source does not answer during the investigation |
| `declare_by_point` | `plot_id` | the plot loses its polygon and is known by its centre point and area only |
| `move_plot` | `plot_id`, `north_m`, `east_m` | moves the plot's point and polygon |
| `swap_lat_lon` | `plot_id` | the registry row has latitude and longitude swapped |
| `set_plot_area` | `plot_id`, `area_ha` | changes the declared area (the polygon is left as it is) |
| `declare_plot_also_by` | `plot_id`, `supplier_id` | a second supplier declares the same plot |
| `remove_plots_of_supplier` | `supplier_id` | the supplier keeps delivering but none of its plots is in the registry |
| `set_delivery_tonnes` | `plot_id`, `year`, `tonnes` | changes that plot's delivery for that year |
| `add_delivery` | `supplier_id`, `plot_id` (or null for untraced volume), `year`, `tonnes`, `certified` (yes, no) | adds a delivery row |
| `stop_deliveries` | `plot_id`, `after_year` | removes that plot's deliveries after that year |
| `add_supplier` | `supplier_id`, `name`, `cooperative_group`, `country`, and optional `plot`: `{plot_id, near_plot, north_m, east_m, area_ha}` (a polygon plot placed relative to an existing plot) | a supplier in the registry; it delivers nothing unless you add deliveries |
| `add_paragraph` | `doc` (rse_2025, ngo_2025), `section` (main, annex), `text` | adds a paragraph at the end of that section |
| `replace_paragraph` | `doc`, `section`, `number`, `text` | rewrites an existing paragraph |

A case may also set `"claim"` to investigate another promise than the default one.
