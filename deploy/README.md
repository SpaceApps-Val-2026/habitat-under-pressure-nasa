# Deployment

One image holds the API, the executor and the built web app. The data folder is always mounted, never part of
the image.

| Target | Files | Notes |
| --- | --- | --- |
| Local | `Dockerfile`, `compose.yaml` | `docker compose -f deploy/compose.yaml up --build -d`, then <http://localhost:8080>. Reads an optional `.env` at the repository root and mounts `data/` |
| Google Cloud Run | `cloudrun/service.yaml` | The data folder is a mounted bucket. Set `HUP_EVENTS_LOCAL_DIR` and `HUP_SPLIT_APPENDS=1` so that appends work on it, and the limits for a public URL |
| A single server | `vps/compose.prod.yaml`, `vps/Caddyfile`, `vps/check_deploy.py` | The application listens only on `127.0.0.1`; Caddy serves ports 80 and 443 with automatic HTTPS for `HUP_DOMAIN`, without buffering the event stream |

`GET /health` answers with the version and the commit the image was built from (build argument `HUP_COMMIT`).

Secrets are set as environment variables of the service by a person; they never go into the image or the
repository. The variables are described in [`.env.example`](../.env.example).
