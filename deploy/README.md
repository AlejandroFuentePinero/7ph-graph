---
title: 7 Point Highlander Graph
emoji: 🕸️
colorFrom: purple
colorTo: blue
short_description: Explore the Australian 7 Point Highlander MTG metagame
sdk: gradio
sdk_version: 5.50.0
# Pinned exactly, like the requirements: the interpreter the artifact and the
# wheels were built against, rather than whatever 3.12.x the image would pick.
python_version: "3.12.12"
app_file: app.py
pinned: false
---

# 7 Point Highlander Graph

An interactive knowledge graph of the Australian 7 Point Highlander Magic: The
Gathering metagame: events, pilots, decks, and cards, down to card attributes.

Pick what to explore (a pilot head-to-head or archetype affinity, a card's usage
or co-occurrence, or hidden gems), set filters, and see a filtered subgraph of
the result. Click a node for its details; a deck links out to Moxfield.

This Space serves a prebuilt graph artifact. It is rebuilt and redeployed by
hand from [the source repo](https://github.com/AlejandroFuentePinero/7ph-graph),
so it lags the newest 7phstats events.

Free and non-commercial, per Moxfield's API terms.
