.RECIPEPREFIX = >
.PHONY: install lint format test export normalize extract validate index ask pipeline clean

install:
> pip install -e ".[dev]"

lint:
> ruff check src tests

format:
> ruff format src tests

test:
> pytest -q

export:
> python -m oncall.ingest.slack_export --channel $(CHANNEL) --years 3 --outdir ./data

normalize:
> python -m oncall.ingest.normalize --indir ./data --outfile ./data/normalized_threads.jsonl

extract:
> python -m oncall.extract.extract --infile ./data/normalized_threads.jsonl --out ./data/structured_cases.jsonl --limit $(or $(LIMIT),30)

validate:
> python -m oncall.eval.validate --threads ./data/normalized_threads.jsonl --cases ./data/structured_cases.jsonl --out ./data/validation_report.html

pipeline: normalize extract validate

index:
> python -m oncall.retrieval.index --cases ./data/structured_cases.jsonl --out ./data/index.json --cutoff $(or $(CUTOFF),0.4)

ask:
> python -m oncall.retrieval.answer --index ./data/index.json --question "$(Q)"

clean:
> rm -f data/*.jsonl data/*.json data/*.html
