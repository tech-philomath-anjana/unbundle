.PHONY: test reproduce refusal dashboard clean

test:
	.venv/bin/pytest -q

reproduce:
	.venv/bin/python -m unbundle.run

refusal:
	.venv/bin/python -m unbundle.refusal

dashboard:
	.venv/bin/python -m unbundle.dashboard

clean:
	rm -rf data results
