.PHONY: test clean

test:
	.venv/bin/pytest -q

clean:
	rm -rf data results
