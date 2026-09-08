# Infrastructure guide — source

Source for `../OnCallAssistant-Infrastructure-Guide.pdf`, the client-facing
guide for recreating this system in a customer AWS account and Slack workspace.

- `guide.html` — the document (print CSS: A4, page numbers, section breaks)
- `img/*.png`  — diagrams rendered from `../diagrams/*.svg` at 2x

## Regenerate

```bash
weasyprint guide.html ../OnCallAssistant-Infrastructure-Guide.pdf
```

Re-render the diagrams first if the SVGs changed:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless \
  --force-device-scale-factor=2 --default-background-color=FFFFFFFF \
  --window-size=1320,760 --screenshot=img/flow-a-ingestion.png \
  file://$PWD/../diagrams/flow-a-ingestion.svg
```

(WeasyPrint does not render SVG marker elements — arrowheads — so diagrams are
rasterised with Chrome rather than embedded as SVG.)

## Before sending to a client

1. Appendix E lists open items — resolve them and delete the section.
2. Re-run the leak check: no real account IDs, bucket names, KB IDs, workspace
   URLs or channel IDs may appear. Everything environment-specific is a
   `<PLACEHOLDER>` listed in Appendix A.
3. The prompt appendices are generated from `src/oncall/prompts.py`; regenerate
   whenever the prompts change.
