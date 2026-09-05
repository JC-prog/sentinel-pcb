# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Angular chat UI in the style of ChatGPT: a left history panel and a main chat panel, with text
  messages, image attachments, and a light/dark theme toggle that persists per browser.
- FastAPI backend with a stateless SSE (Server-Sent Events) streaming endpoint for chat replies,
  plus an image upload endpoint backing the UI's attachments.
- LLM provider selection in Settings: a local Ollama model, or OpenAI with a user-supplied,
  bring-your-own API key (kept in the browser only, sent with each request, never stored on the
  server).
- Local development environment under `infra/development/`: a Docker Compose stack (Postgres and
  Qdrant, both provisioned ahead of need for planned future work) plus per-OS setup scripts.
- Production infrastructure under `infra/production/` (Terraform): ECS Fargate, RDS, ECR, and
  S3 with CloudFront for the AWS deployment, once that work is picked up.
- GitHub Actions CI (backend lint/type-check/tests, frontend tests/build) and a pull request
  template.
