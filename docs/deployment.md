# FinWise Backend Deployment Layout

## Repository layout

```text
backend/
  .github/workflows/        CI checks
  app/                      FastAPI application
  alembic/                  Migration workspace
  deployment/
    docker/                 Local Dockerfile and compose stack
    production/             Production compose template, env template, scripts
  docs/                     Deployment documentation
  requirements/             Split dependency files
  scripts/                  Local helper scripts
  tests/                    Backend tests
```

## Local development

Create the local environment file:

```powershell
Copy-Item .env.example .env
```

Start the local stack from the backend directory:

```powershell
docker compose -f deployment/docker/docker-compose.yml up -d --build
```

Or use the helper script:

```powershell
.\scripts\start-dev.ps1
```

Stop the stack:

```powershell
.\scripts\stop-dev.ps1
```

## Production template

The production template is in `deployment/production/config`. Copy the example
environment file, replace every placeholder, and keep the real file out of git.

```powershell
Copy-Item deployment/production/config/.env.production.example deployment/production/config/.env.production
```

Build and start the production stack:

```powershell
docker compose -f deployment/production/config/docker-compose.prod.yml up -d --build
```

Before a real deployment, set `BACKEND_IMAGE` to the registry image, put a
reverse proxy/TLS layer in front of the API, and use managed secrets instead of
plain environment files.
