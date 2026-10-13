# Production infrastructure (AWS, Terraform)

Provisions: ECS Fargate running the backend behind an ALB, a second Fargate service running the
ONNX classification service (`inference/`) reachable only from the backend over Cloud Map private
DNS, RDS Postgres, an S3 bucket + CloudFront distribution serving the Angular UI, and an ECR repo
per built image. Everything lives
in the account's **default VPC** in its public subnets - no NAT Gateway, no custom domain. See
the PR description / commit messages for the reasoning behind these calls.

The backend calls `api.openai.com` directly with the key in the `sentinelchat/openai` secret
(injected as `OPENAI_API_KEY`; `OPENAI_BASE_URL` is left at its default). There is no LLM gateway.

**One CloudFront distribution serves both the UI and the API** (`/api/*` routes to the ALB) so
everything ends up on one HTTPS domain - no mixed content, no CORS in production.

For a start-to-finish walkthrough (deploy, use, tear down), see
[`docs/DEPLOYMENT.md`](../../docs/DEPLOYMENT.md).

## One-time setup

```bash
cd infra/production/bootstrap
terraform init
terraform apply   # creates the S3 bucket for the real config's remote state
```

Copy the `bucket_name` output into `infra/production/backend.tf`'s `backend "s3" { bucket = ... }`.

## Deploying

```bash
cd infra/production
terraform init
terraform apply   # needs var.openai_api_key - see below
```

`var.openai_api_key` (the OpenAI key the backend uses) has no default. Put it in a git-ignored
`*.auto.tfvars`, or export `TF_VAR_openai_api_key=sk-...` before `apply`. It is written only to
the `sentinelchat/openai` secret, never to an output or a plain env var.

**Cost alerts.** Set `budget_alert_email` (a git-ignored `*.auto.tfvars`, or `TF_VAR_budget_alert_email`)
to create a monthly AWS budget (`budget.tf`) that emails at 50% and 80% of `monthly_budget_usd`
(default 50) and when the month's forecast passes 100%. It is account-wide, off while the email is
empty, and a nudge only: billing data lags by up to a day and nothing is stopped. AWS emails a
subscription confirmation the first time; the alerts work without it. The stack runs about $5 a day
if left up, so the limit trips in roughly ten days. A second, **daily** budget
(`daily_budget_usd`, default 3) emails the morning after any day the stack was running, which is
the quicker signal for a forgotten one.

**Stopping when idle.** Set `auto_stop_schedule` (an EventBridge Scheduler expression in UTC, e.g.
`cron(0 14 * * ? *)`) and `auto_stop.tf` scales the backend, inference and Qdrant services to zero
at that time - about two thirds of the cost. The ALB, RDS and public IPs keep billing until
`terraform destroy`. `terraform apply` scales the services back up.

**The ALB is CloudFront-only.** Its security group allows only CloudFront's origin-facing prefix
list, and the listener returns 403 unless the request carries the `X-Origin-Verify` header
CloudFront adds (`origin.tf`; the value is in state, so protect state access). Check a deploy
through `<cloudfront_domain>/api/health`, not the ALB's DNS name.

**Upgrading an existing deployment from the LiteLLM proxy:** `apply` removes the LiteLLM Fargate
service, task definition, log group, security group, Cloud Map entry and the `sentinelchat/litellm`
secret (Secrets Manager keeps it for its recovery window), and creates `sentinelchat/openai`. The
backend task definition changes (no `OPENAI_BASE_URL`, a new secret reference), so ECS rolls the
backend. Check the plan before applying.

The backend also gets a generated `JWT_SECRET_KEY` (`auth.tf`, the `sentinelchat/jwt` secret) and
`COOKIE_SECURE=True`; without the first, tokens would be signed with an empty secret. The backend
task is 1 vCPU / 2 GiB by default (`task_cpu`, `task_memory`), since dataset preparation and Agent 2
reviews run in it. A new JWT secret signs every user out; replacing `random_password.jwt` rotates it.

Then, to actually ship code:

**Backend:**

```bash
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <ecr_repository_url>
docker build -f infra/Dockerfile -t <ecr_repository_url>:<tag> .
docker push <ecr_repository_url>:<tag>
# If <tag> isn't "latest", update var.container_image_tag and `terraform apply` again first.
aws ecs update-service --cluster sentinelchat-cluster --service sentinelchat-backend --force-new-deployment
```

**Inference service:**

```bash
# Fill in inference/models.toml first - every REPLACE_ME must be a real Hugging Face repo/label.
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <inference_ecr_repository_url>
docker build -t <inference_ecr_repository_url>:<tag> inference/
# Private HF repos: docker build --secret id=hf_token,src=<token-file> ...
docker push <inference_ecr_repository_url>:<tag>
# If <tag> isn't "latest", set var.inference_image_tag and `terraform apply` again first.
aws ecs update-service --cluster sentinelchat-cluster --service sentinelchat-inference --force-new-deployment
```

The ONNX files are baked into the image at build time (`inference/scripts/fetch_models.py`), so
retraining a model means rebuilding and redeploying this image - the backend image is untouched.

**UI:**

```bash
cd ui && npm run build
aws s3 sync dist/ui/browser s3://<ui_bucket_name> --delete
aws cloudfront create-invalidation --distribution-id <cloudfront_distribution_id> --paths "/*"
```

(`<ecr_repository_url>`, `<inference_ecr_repository_url>`, `<ui_bucket_name>`,
`<cloudfront_distribution_id>` are all Terraform outputs - `terraform output`.)

## Known gaps (deliberate, not oversights)

- **Nothing is persistent, on purpose.** This stack is meant to be brought up for a short time and
  torn down. Uploads, review images and logs live on the backend task's disk, and Qdrant (`qdrant.tf`,
  a third Fargate service on Cloud Map DNS) keeps Work-tab runs, Agent 2 reviews and chat memory on
  its own ephemeral disk, so a task restart loses them. Postgres (RDS) is the only durable store, and
  it goes with `terraform destroy` (no final snapshot). To keep data across restarts, give Qdrant and
  the backend an EFS volume first - Qdrant does not officially support network filesystems, so for
  real use prefer block storage or Qdrant Cloud.
- **The IPC corpus is restored, nothing else.** Agent 2's precedent collection
  (`pcb_standards_precedents`) is empty after every Qdrant restart, and "Explain this result" then
  answers without precedents. `ipc_corpus.tf` makes it a one-command fix: a private bucket holds an
  export of the corpus (vectors included, so loading it spends no OpenAI tokens) and a one-shot ECS
  task imports it. Nothing runs it automatically; after Qdrant starts:

  ```bash
  # once, from a machine whose Qdrant holds the corpus (needs the dev Qdrant and .env):
  uv run python -m app.workflow.services.ipc_ingest export ipc-corpus.jsonl
  aws s3 cp ipc-corpus.jsonl "s3://$(terraform output -raw ipc_corpus_bucket_name)/ipc-corpus.jsonl"

  # after every Qdrant start (the output is the full `aws ecs run-task ...` command):
  eval "$(terraform output -raw ipc_corpus_load_command)"
  ```

  Watch `/ecs/<project>-ipc-corpus-loader` in CloudWatch: `fetch` copies the file, `import` prints
  `imported N chunk(s)`. It waits up to about 3 minutes for Qdrant, and is safe to run twice. The
  export holds the documents' text, so it never goes in git or the image. Chat memory and Work-tab
  runs are still lost on a Qdrant restart: a real fix there is block storage or Qdrant Cloud, not EFS
  (Qdrant does not support network filesystems).
- **Chat image uploads are on local container disk** (`app/chat/uploads/service.py`), not S3. Fine at
  `desired_count = 1`; breaks if scaled to more than one task, since disk isn't shared between
  them. Move to S3 before scaling out.
- **One shared OpenAI key, no per-consumer budgets.** The backend holds the key in its task
  environment (via Secrets Manager). If spend limits, per-developer keys or a second consumer are
  needed later, put a gateway back in front rather than distributing the key.
- **No custom domain / ACM cert.** Everything's on CloudFront's and the ALB's default AWS
  domains. Add Route53 + ACM (cert must be in `us-east-1` for CloudFront) as a follow-up once a
  domain is chosen.
- **Inference models are baked into the image, not pulled from S3.** Simple and self-contained,
  but a model change is a full image rebuild + redeploy. Move to an S3 model bucket + task role
  if that cadence becomes painful.
- **Inference service has no autoscaling and `desired_count = 1`.** Add target-tracking on CPU,
  or a queue in front, once classification traffic justifies it.
- **The database schema comes from `create_all`, not Alembic.** The backend creates its tables at
  start-up (`app/shared/db/session.py`), which is right for a fresh, short-lived RDS instance but
  does not alter an existing one. Keeping a database across versions means running
  `alembic upgrade head` against it first.
- **"Explain this result" is not deployed.** The MCP service that serves it (`agent2-mcp`) has no
  task here, so `AGENT2_MCP_URL` is unset and the tool stays off. The IPC corpus loader above fills
  a collection that nothing reads until that service is added.

## Tearing down

`terraform destroy` removes everything, with these set so it does not get stuck: ECR repositories
use `force_delete`, the UI bucket `force_destroy`, and the three secrets (`database-url`, `openai`,
`jwt`) have no recovery window, so the same names can be created again immediately. Expect the
CloudFront distribution to take 10-15 minutes to disable and delete. The remote state bucket from
`bootstrap/` is separate and is not destroyed with this stack.
