.PHONY: install backend frontend up down logs dev-up dev-down test typecheck

install:
	python -m pip install -r backend/requirements.txt
	npm --prefix frontend install

backend:
	python -m uvicorn app.main:app --app-dir backend --reload --host 0.0.0.0 --port 8000

frontend:
	npm --prefix frontend run dev

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f

dev-up:
	python scripts/dev.py

dev-down:
	python scripts/stop.py

test:
	python -m pytest -q

typecheck:
	npm --prefix frontend run typecheck
