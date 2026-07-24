# Self-hosted fonts (v1 visual direction §3, #132)

Two latin-subset woff2 faces, base64-embedded into the injected stylesheet by
`theme.build_css()` so the Space serves no external font request:

| File | Family | Weights | Source |
|---|---|---|---|
| `fraunces-600.woff2` | Fraunces (display serif) | 600, opsz 9–144 variable | Google Fonts |
| `hanken.woff2` | Hanken Grotesk (body sans) | 400–700 variable | Google Fonts |

Both are licensed under the SIL Open Font License 1.1, which permits
self-hosting and redistribution; the license text and copyright notices are in
`OFL.txt` beside the fonts (the OFL requires each redistributed copy to carry
them). They echo the Claude presentation pairing (Copernicus/Tiempos + Styrene),
whose actual faces are commercially licensed and cannot be self-hosted here.

Regenerate (latin subset only) from Google Fonts:

    curl -A "<modern browser UA>" \
      "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&display=swap"
    curl -A "<modern browser UA>" \
      "https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@400;500;600;700&display=swap"

then download the `/* latin */` block's woff2 for each (Hanken serves one
variable file across all weights).
