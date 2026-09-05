# Production infrastructure (AWS, Terraform)

Provisions: ECS Fargate running the backend behind an ALB, RDS Postgres (provisioned ahead of
need - no app code reads it yet), an S3 bucket + CloudFront distribution serving the Angular UI,
and an ECR repo for the backend image. Everything lives in the account's **default VPC** in its
public subnets - no NAT Gateway, no custom domain. See the PR description / commit messages for
the reasoning behind these calls.

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
terraform apply
```

Then, to actually ship code:

**Backend:**

```bash
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <ecr_repository_url>
docker build -f infra/Dockerfile -t <ecr_repository_url>:<tag> .
docker push <ecr_repository_url>:<tag>
# If <tag> isn't "latest", update var.container_image_tag and `terraform apply` again first.
aws ecs update-service --cluster sentinelchat-cluster --service sentinelchat-backend --force-new-deployment
```

**UI:**

```bash
cd ui && npm run build
aws s3 sync dist/ui/browser s3://<ui_bucket_name> --delete
aws cloudfront create-invalidation --distribution-id <cloudfront_distribution_id> --paths "/*"
```

(`<ecr_repository_url>`, `<ui_bucket_name>`, `<cloudfront_distribution_id>` are all Terraform
outputs - `terraform output`.)

## Known gaps (deliberate, not oversights)

- **Chat image uploads are on local container disk** (`app/uploads/service.py`), not S3. Fine at
  `desired_count = 1`; breaks if scaled to more than one task, since disk isn't shared between
  them. Move to S3 before scaling out.
- **No Ollama in this deployment.** The Local LLM option in Settings won't work in production
  until an Ollama instance is stood up and `OLLAMA_BASE_URL` is pointed at it (see `ecs.tf`) -
  the OpenAI (bring-your-own-key) option works as-is.
- **No custom domain / ACM cert.** Everything's on CloudFront's and the ALB's default AWS
  domains. Add Route53 + ACM (cert must be in `us-east-1` for CloudFront) as a follow-up once a
  domain is chosen.
- **RDS is provisioned but unused.** No schema, no migrations, no app code touches it yet -
  that's the future multi-user/auth feature's job.
