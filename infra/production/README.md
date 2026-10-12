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

**Upgrading an existing deployment from the LiteLLM proxy:** `apply` removes the LiteLLM Fargate
service, task definition, log group, security group, Cloud Map entry and the `sentinelchat/litellm`
secret (Secrets Manager keeps it for its recovery window), and creates `sentinelchat/openai`. The
backend task definition changes (no `OPENAI_BASE_URL`, a new secret reference), so ECS rolls the
backend. Check the plan before applying.

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

- **Chat image uploads are on local container disk** (`app/chat/uploads/service.py`), not S3. Fine at
  `desired_count = 1`; breaks if scaled to more than one task, since disk isn't shared between
  them. Move to S3 before scaling out.
- **No Ollama in this deployment.** The Local LLM option in Settings won't work in production
  until an Ollama instance is stood up and `OLLAMA_BASE_URL` is pointed at it (see `ecs.tf`).
  The OpenAI path works.
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
- **RDS is provisioned but unused.** No schema, no migrations, no app code touches it yet -
  that's the future multi-user/auth feature's job.
