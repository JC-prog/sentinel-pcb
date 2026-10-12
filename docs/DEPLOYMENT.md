# Deployment and teardown (AWS, short-lived)

How to bring SentinelChat up on AWS for a short session (a demo, a trial) and tear it all down
afterwards. Nothing is persistent: when you destroy the stack, the database, uploads, Qdrant data
and secrets are gone. Plan for about 45 minutes to deploy and 20 to tear down.

What gets created: one CloudFront URL that serves the UI and `/api/*`, an ALB, the backend on
Fargate (1 vCPU, 2 GiB), the inference service on Fargate, Qdrant on Fargate, RDS Postgres, two ECR
repositories, an S3 bucket for the UI, three Secrets Manager secrets, and a private DNS namespace.
Details of each piece are in [`infra/production/README.md`](../infra/production/README.md).

**Contents**

| Part | Steps |
|---|---|
| Deploy | [0. Before you start](#0-before-you-start) · [1. State bucket](#1-one-time-the-terraform-state-bucket) · [2. Repositories](#2-create-the-container-repositories-first) · [3. Build and push](#3-build-and-push-the-two-images) · [4. Create the rest](#4-create-everything-else) · [5. Health check](#5-check-that-the-backend-is-healthy) · [6. Publish the UI](#6-publish-the-ui) · [7. Use it](#7-use-it) |
| Update | [8. Updating while it is up](#8-updating-while-it-is-up) |
| **Teardown** | [**9. Teardown**](#9-teardown-destroy-everything) · [9.1 Destroy](#91-destroy-the-stack) · [9.2 Confirm nothing is left](#92-confirm-nothing-is-left-running) · [9.3 Clean up outside Terraform](#93-clean-up-outside-terraform) |

---

## 0. Before you start

You need:

| Need | Check with |
|---|---|
| An AWS account and credentials with admin-level access | `aws sts get-caller-identity` |
| AWS CLI v2, Terraform 1.10 or newer, Docker (with `buildx`), Node 22 | `aws --version`, `terraform version`, `docker buildx version`, `node -v` |
| An OpenAI API key (`sk-...`) | you have it |
| Internet access during the image build (the inference build downloads the ONNX models from Hugging Face) | |

Decide the region. The config defaults to `us-east-1`; the commands below use it. If you change
it, change it in `infra/production/backend.tf` and `bootstrap/` too.

```bash
export AWS_REGION=us-east-1
export TF_VAR_openai_api_key='sk-...'     # never commit this; it is stored only in Secrets Manager
```

> **Cost.** Expect a few dollars for a day: the ALB, RDS (`db.t4g.micro`), and three Fargate tasks
> are the main items. You pay for as long as the stack exists, so tear it down when you finish.

---

## 1. One-time: the Terraform state bucket

Terraform's state lives in an S3 bucket that has to exist before the main config can use it.
Skip this section if you (or your team) already did it in this AWS account.

```bash
cd infra/production/bootstrap
terraform init
terraform apply          # type "yes"
terraform output bucket_name
```

Open `infra/production/backend.tf` and replace `REPLACE_WITH_ACCOUNT_ID` in the `bucket` line with
the account id from that output (the full value is `sentinelchat-terraform-state-<account id>`).

---

## 2. Create the container repositories first

The ECS services pull images from ECR, so the repositories must exist and hold images before the
services can start. Create only the repositories now:

```bash
cd infra/production
terraform init
terraform apply \
  -target=aws_ecr_repository.backend \
  -target=aws_ecr_repository.inference
```

Collect the URLs and log Docker in:

```bash
export BACKEND_REPO=$(terraform output -raw ecr_repository_url)
export INFERENCE_REPO=$(terraform output -raw inference_ecr_repository_url)
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "${BACKEND_REPO%%/*}"
```

---

## 3. Build and push the two images

**Build for `linux/amd64`.** Fargate here runs x86-64. On an Apple Silicon laptop the default build
is arm64 and the task will crash on start with `exec format error`.

From the repository root:

```bash
# Backend
docker buildx build --platform linux/amd64 \
  -f infra/Dockerfile -t "$BACKEND_REPO:latest" --push .

# Inference service (downloads the ONNX models into the image, so this takes a few minutes)
docker buildx build --platform linux/amd64 \
  -t "$INFERENCE_REPO:latest" --push inference/
```

If the inference build fails fetching a model, check `inference/models.toml`: every `repo_id` must
be a Hugging Face repository you can reach (for a private one, pass the token with
`--secret id=hf_token,src=<token-file>`).

---

## 4. Create everything else

```bash
cd infra/production
terraform apply          # review the plan, then type "yes"
```

This creates RDS, the ALB, the ECS cluster and three services, Cloud Map DNS, the secrets
(including a generated JWT signing key), the S3 bucket and the CloudFront distribution.
Expect **10 to 20 minutes**: RDS and CloudFront are the slow parts.

When it finishes, note the outputs:

```bash
terraform output
```

You will use `cloudfront_domain`, `ui_bucket_name` and `cloudfront_distribution_id`.

---

## 5. Check that the backend is healthy

Give the tasks a couple of minutes to pull images and start, then:

```bash
aws ecs describe-services --region "$AWS_REGION" --cluster sentinelchat-cluster \
  --services sentinelchat-backend sentinelchat-inference sentinelchat-qdrant \
  --query 'services[].{name:serviceName,running:runningCount,desired:desiredCount}' --output table

curl -s "$(terraform output -raw cloudfront_domain)/api/health"
```

Each service should show `running` equal to `desired` (1), and the curl should answer
`{"status":"ok"}`. (The ALB only accepts traffic from CloudFront, so curling `alb_dns_name` directly
times out or answers 403 - that is the lock-down working. Check through the CloudFront URL.)

If a service has `running = 0`, read why:

```bash
aws ecs list-tasks --region "$AWS_REGION" --cluster sentinelchat-cluster \
  --service-name sentinelchat-backend --desired-status STOPPED
aws logs tail /ecs/sentinelchat-backend --since 15m
```

The usual causes are an image that was never pushed (step 3), the wrong platform (step 3), or a
bad OpenAI key in the secret.

---

## 6. Publish the UI

```bash
cd ui
npm ci
npm run build
aws s3 sync dist/ui/browser "s3://$(cd ../infra/production && terraform output -raw ui_bucket_name)" --delete
aws cloudfront create-invalidation \
  --distribution-id "$(cd ../infra/production && terraform output -raw cloudfront_distribution_id)" \
  --paths "/*"
```

The UI calls the API on its own origin (`apiBaseUrl` is empty), so no URL needs setting.

---

## 7. Use it

1. Open the `cloudfront_domain` URL from step 4.
2. **Register.** The **first** user ever registered becomes the Admin, so do this yourself first.
3. Go to the **Work** tab, add a dataset CSV and inspection XML (and the image folder), and press
   **Run Agentic Workflow**.
4. Open the **Review Console** to see the Agent 1 and Agent 2 cards, and **Model feedback** for the
   graphs.

Things to expect on a fresh deployment:

- **Runs are slow to start the first time** while the services warm up.
- **A long silent step can end in an error.** CloudFront waits at most 60 seconds for the backend
  to send something. A very large dataset can stay quiet longer than that during preparation and
  the browser will show a gateway timeout and the run may be cut off. Use a smaller dataset for the
  demo if it happens.
- **Uploaded files and Qdrant data live on the task's own disk.** If a task restarts, the
  uploads and the stored runs and reviews for that task are lost.

---

## 8. Updating while it is up

| Change | Do |
|---|---|
| Backend code | rebuild and push the backend image (step 3), then `aws ecs update-service --region "$AWS_REGION" --cluster sentinelchat-cluster --service sentinelchat-backend --force-new-deployment` |
| Inference models | rebuild and push the inference image, then the same command with `sentinelchat-inference` |
| UI | step 6 again |
| Infrastructure settings | edit the `.tf` or variables, then `terraform apply` |

---

## 9. Teardown (destroy everything)

Do this as soon as you are done; the stack costs money while it exists.

### 9.1 Destroy the stack

```bash
cd infra/production
terraform destroy        # review, then type "yes"
```

This removes the services, ALB, RDS (no final snapshot is taken), the secrets (no recovery window,
so the names are free immediately), the ECR repositories **including their images**, and the UI
bucket **including its files**. Expect **10 to 20 minutes**; CloudFront takes the longest because it
is disabled and then deleted.

If `destroy` stops with an error:

| Error | What to do |
|---|---|
| A security group "has a dependent object" | ECS network interfaces are still releasing. Wait two minutes and run `terraform destroy` again. |
| The Cloud Map namespace "has services" | Same: run it again once the ECS services are gone. |
| CloudFront "distribution not disabled" | Run it again; it needs the disable to finish first. |

### 9.2 Confirm nothing is left running

```bash
aws ecs list-clusters --region "$AWS_REGION"
aws rds describe-db-instances --region "$AWS_REGION" --query 'DBInstances[].DBInstanceIdentifier'
aws elbv2 describe-load-balancers --region "$AWS_REGION" --query 'LoadBalancers[].LoadBalancerName'
aws ecr describe-repositories --region "$AWS_REGION" --query 'repositories[].repositoryName'
aws secretsmanager list-secrets --region "$AWS_REGION" --query "SecretList[?starts_with(Name,'sentinelchat')].Name"
aws cloudfront list-distributions --query "DistributionList.Items[].Id"
```

All of these should come back empty (apart from anything unrelated in the account).

### 9.3 Clean up outside Terraform

1. **Revoke or rotate the OpenAI key** you used. It was written to the Terraform state in the S3
   state bucket, and to your shell history if you exported it there.
2. **Remove the local copy of the key:** `unset TF_VAR_openai_api_key`, and delete any
   `*.auto.tfvars` you created.
3. **The state bucket stays.** It is created by `bootstrap/` and is deliberately not part of the
   stack, so a later deployment can reuse it. It holds a small state file and costs almost
   nothing. The state file contains secrets, so keep the bucket private (it is by default). To
   remove it entirely, empty every object *and version* in the console, then run
   `terraform destroy` inside `infra/production/bootstrap`.

---

## 10. If you need it to keep data

This setup is built to be thrown away. Before using it for anything that must survive a restart,
or for more than one backend task, see "Known gaps" in
[`infra/production/README.md`](../infra/production/README.md): Qdrant and the uploads need a
persistent volume or a managed service first, and chat uploads should move to S3.
