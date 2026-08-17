.PHONY: install lint format test export normalize extract validate index ask holdout pipeline lambda_zips clean

install:
	pip install -e ".[dev]"

lint:
	ruff check src tests

format:
	ruff format src tests

test:
	pytest -q

export:
	python -m oncall.ingest.slack_export --channel $(CHANNEL) --years 3 --outdir ./data

normalize:
	python -m oncall.ingest.normalize --indir ./data --outfile ./data/normalized_threads.jsonl

extract:
	python -m oncall.extract.extract --infile ./data/normalized_threads.jsonl --out ./data/structured_cases.jsonl --limit $(or $(LIMIT),30)

validate:
	python -m oncall.eval.validate --threads ./data/normalized_threads.jsonl --cases ./data/structured_cases.jsonl --out ./data/validation_report.html

pipeline: normalize extract validate

index:
	python -m oncall.retrieval.index --cases ./data/structured_cases.jsonl --out ./data/index.json --cutoff $(or $(CUTOFF),0.4)

ask:
	python -m oncall.retrieval.answer --index ./data/index.json --question "$(Q)"

holdout:
	python -m oncall.eval.holdout --cases ./data/structured_cases.jsonl --index ./data/index.json --n $(or $(N),25) --out ./data/holdout_report.html

# Build console-uploadable zips for the two Lambdas. The entry file is named
# lambda_function.py inside each zip, so the existing handler setting
# (lambda_function.lambda_handler) keeps working — upload and Deploy, no
# other console changes to the code config.
lambda_zips:
	rm -rf dist && mkdir -p dist/events dist/questions
	cp src/oncall/lambdas/post_events.py dist/events/lambda_function.py
	cp src/oncall/lambdas/live_extract.py src/oncall/lambdas/slack_verify.py src/oncall/prompts.py src/oncall/extract/parsing.py dist/events/
	cp src/oncall/lambdas/questions.py dist/questions/lambda_function.py
	cp src/oncall/lambdas/slack_verify.py src/oncall/prompts.py dist/questions/
	cd dist/events && zip -q -r ../events-lambda.zip .
	cd dist/questions && zip -q -r ../questions-lambda.zip .
	@echo "Ready to upload: dist/events-lambda.zip and dist/questions-lambda.zip"

clean:
	rm -f data/*.jsonl data/*.json data/*.html
	rm -rf dist
