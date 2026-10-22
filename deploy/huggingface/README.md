---
title: GreenTrace
emoji: 🌳
colorFrom: green
colorTo: gray
sdk: docker
app_port: 8000
pinned: false
license: apache-2.0
short_description: Checks a deforestation-free claim and explains why
---

# GreenTrace (sample mode)

Our entry for **The Agent That Can Explain Why** (Open Agent Hackathon 2026).

GreenTrace checks a company's "deforestation-free" promise against the evidence, says whether it is
PROVEN, CONTRADICTED or UNVERIFIABLE, and shows why, down to the database row. Every verdict ships with
a receipt that `python verify.py receipt.json` re-derives offline.

This Space runs the Docker image in **sample mode**: a synthetic company in SQLite and the real forest
maps around Soubre, Côte d'Ivoire, with the same agents and rules as the live mode. It holds no key and
calls no model: the model-assisted parts say so in the trace. The Meterless World Model and H-MEM
servers run inside the container; the memory is reset when the Space restarts.

Source, documentation and the full README: https://github.com/polmanas1998-star/greentrace

Data: Hansen/UMD/Google/USGS/NASA Global Forest Change v1.12 (CC BY 4.0), EU JRC Global Forest Cover
2020 v4, and Sentinel-2 backgrounds that contain modified Copernicus Sentinel data (2025, 2026).
GreenTrace is decision support, not a legal opinion: every verdict needs a human review.
