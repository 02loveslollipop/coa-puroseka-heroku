.PHONY: test build migrate deploy

test:
	.venv/bin/python -m pytest -c tests/pyproject.toml -q
build:
	npm --prefix web run build
migrate:
	.venv/bin/python scripts/migrate_postgres.py
deploy:
	.venv/bin/python scripts/deploy_heroku.py
