# LiteLLM proxy

The app never calls `api.openai.com` directly. It calls an OpenAI-compatible endpoint
(`settings.openai_base_url` / `OPENAI_BASE_URL`), which is always a LiteLLM proxy. That keeps
real provider keys out of app config and out of the backend task, and gives one place to add
providers, swap models, or cap spend.

| | Endpoint | Config | Upstream OpenAI key |
|---|---|---|---|
| Local dev (default) | `http://localhost:4000/v1` (`http://litellm:4000/v1` in-compose) | [config.dev.yaml](config.dev.yaml) | **each developer's own**, in their `.env` as `LITELLM_OPENAI_API_KEY` - seen only by their local proxy container |
| Production | `http://litellm.sentinelchat.internal:4000/v1` | [config.prod.yaml](config.prod.yaml) | **one shared key**, in Secrets Manager (`sentinelchat/litellm`) |
| Shared team proxy (optional) | `https://litellm.<team-host>/v1` | [config.prod.yaml](config.prod.yaml) | one shared key on the proxy host; developers point at it instead of running their own |

Model alias names in the configs match exactly what the app sends today (`gpt-4o-mini`,
`gpt-4o`, `text-embedding-3-small`), so pointing at the proxy needs no model-name changes in app
code. `config.dev.yaml` routes `gpt-4o` at `gpt-4o-mini` to keep local runs cheap.

## Local

The `litellm` service is part of the default compose stack - `setup-dev.sh` starts it. To use
the OpenAI features, put your own key in `.env`:

```
LITELLM_OPENAI_API_KEY=sk-...      # your OpenAI key; only your local proxy container sees it
```

The stock `.env` already has `OPENAI_BASE_URL=http://localhost:4000/v1` and
`OPENAI_API_KEY=sk-litellm-dev` (the proxy's master key). Leave `LITELLM_OPENAI_API_KEY` blank
to work Ollama-only - OpenAI calls just 401.

To use a shared team proxy instead of your own container, point `OPENAI_BASE_URL` at it and set
`OPENAI_API_KEY` to the key from the team vault.

## Auth model (current)

Master-key-only: there is no LiteLLM key database yet, so the backend and every developer use
the same `LITELLM_MASTER_KEY` for their respective proxy. This is deliberate for now - one
internal consumer, low volume.

When per-consumer budgets or revocable per-developer keys are needed, add `database_url:` to
`config.prod.yaml` (point it at RDS), redeploy, and mint scoped keys via the proxy admin API.
Nothing in the app changes - it already just sends a bearer token.

## Production deployment

The proxy runs as a third ECS Fargate service - see
[infra/production/litellm.tf](../production/litellm.tf). It pulls
`ghcr.io/berriai/litellm` directly (pinned tag, no ECR mirror) and the contents of
`config.prod.yaml` are passed into the task definition, so a config change is a
`terraform apply`, not an image build.
