"""Report paragraphs of the base world, written by hand on 22 October 2026.

The texts are deliberately "messy" (the promise on the main pages, nuances in the
annex, dates in words) because that is where rules alone fail and the model has
to read. In the base world nothing here contradicts the claim: the NGO report
names no supplier and no plot, and the annex admits nothing.
Organisation, cooperative and village names are invented.
"""

RSE_DOC = "rse_2025"
NGO_DOC = "ngo_2025"

RSE_MAIN = [
    "ChocoDurable SA buys cocoa from twelve farmer cooperatives in the west of Cote d'Ivoire "
    "and sells chocolate in nine European markets.",
    "Since twenty twenty-one, 100% of our cocoa comes from deforestation-free farms. Not one "
    "hectare of forest has been cleared to grow the cocoa we buy.",
    "Every tonne we bought in the 2025 fiscal year is traced to a geolocated plot recorded in "
    "our supplier registry, which our cooperatives update each season.",
    "We screen every plot against public satellite deforestation alerts each quarter. A "
    "confirmed alert inside a plot suspends deliveries from that plot until a field visit "
    "clears it.",
    "Our reference date is the one set by the EU Deforestation Regulation: no forest cleared "
    "after the thirty-first of December twenty twenty.",
    "In 2025 we paid a premium above the farm-gate price to every member of our partner "
    "cooperatives, and we co-financed two village schools.",
    "Part of our volume is also certified under an independent sustainability scheme, which "
    "audits the cooperatives once a year.",
    "Our agronomists trained more than one thousand farmers in shade-grown cocoa, which keeps "
    "trees on the farm and protects soil moisture.",
    "Next year we will publish the plot map of every cooperative so that anyone can check it.",
]

RSE_ANNEX = [
    "Methodology. Plots under four hectares are geolocated by a single point, as the "
    "Regulation allows; larger plots are mapped as a polygon.",
    "Volumes are reported for the fiscal year ending on the thirtieth of September 2025, in "
    "tonnes of dry beans.",
    "Alerts are read from public deforestation alert systems; only alerts of high or highest "
    "confidence are treated as confirmed.",
]

NGO_MAIN = [
    "Terres Vivantes is an association that follows cocoa farming in the west of Cote "
    "d'Ivoire. This note covers our visits from January to June 2025.",
    "Our team visited fourteen villages and spoke with farmers, cooperative managers and "
    "buyers. We did not visit any company office.",
    "Farm-gate prices rose this season, but many farmers told us that the premiums promised "
    "by exporters arrive late, sometimes a full season later.",
    "Most cooperatives we met keep a register of their members' plots, but several registers "
    "are on paper and are not shared with the farmers themselves.",
    "Access to schools has improved in the villages served by new roads; it remains difficult "
    "further from the main axes.",
    "Farmers asked for clearer rules on which documents they must keep to sell to European "
    "buyers after the new regulation applies.",
    "We recommend that buyers share their plot maps with the cooperatives, and that "
    "cooperatives explain the maps to their members.",
    "We thank the farmers and cooperative staff who gave us their time.",
]


def base_paragraphs() -> list[dict]:
    sections = [(RSE_DOC, "main", RSE_MAIN), (RSE_DOC, "annex", RSE_ANNEX), (NGO_DOC, "main", NGO_MAIN)]
    return [{"doc_id": doc, "section": section, "paragraph_no": number, "paragraph_text": text}
            for doc, section, texts in sections
            for number, text in enumerate(texts, start=1)]
