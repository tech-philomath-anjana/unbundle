.PHONY: test reproduce refusal clean

test:
	.venv/bin/pytest -q

reproduce:
	.venv/bin/python -m unbundle.run

refusal:
	.venv/bin/python -m unbundle.refusal

clean:
	rm -rf data results
