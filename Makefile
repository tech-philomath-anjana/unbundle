.PHONY: test reproduce refusal bench dashboard clean

test:
	.venv/bin/pytest -q

reproduce:
	.venv/bin/python -m unbundle.run

refusal:
	.venv/bin/python -m unbundle.refusal

bench:
	.venv/bin/python -m unbundle.bench

dashboard:
	.venv/bin/python -m unbundle.dashboard

clean:
	rm -rf data results
