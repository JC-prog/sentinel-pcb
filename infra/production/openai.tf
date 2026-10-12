# The OpenAI API key the backend task calls api.openai.com with (chat, memory embeddings, the chat
# guardrails check, the Explainability & Review Agent and the Work tab planner all read
# OPENAI_API_KEY; settings.openai_base_url keeps its default, OpenAI direct).
#
# Stored in Secrets Manager and injected by ECS at container start through a "secrets" block in
# ecs.tf with the ":OPENAI_API_KEY::" suffix - never a plain task-definition env var, never an
# output. The execution role's read policy (iam.tf) covers this secret.

resource "aws_secretsmanager_secret" "openai" {
  name = "${var.project_name}/openai"
}

resource "aws_secretsmanager_secret_version" "openai" {
  secret_id = aws_secretsmanager_secret.openai.id
  secret_string = jsonencode({
    OPENAI_API_KEY = var.openai_api_key
  })
}
