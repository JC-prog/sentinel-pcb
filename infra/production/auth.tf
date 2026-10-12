# The key the backend signs access and refresh tokens with (settings.jwt_secret_key, JWT_SECRET_KEY).
# Without it the app would sign tokens with an empty secret.
#
# Generated here and kept in Secrets Manager, injected by ECS at container start through a "secrets"
# block in ecs.tf with the ":JWT_SECRET_KEY::" suffix - never a plain task-definition env var, never
# an output. The value is stable across applies; replacing random_password.jwt (terraform taint)
# rotates it and signs every user out.

resource "random_password" "jwt" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret" "jwt" {
  name = "${var.project_name}/jwt"
}

resource "aws_secretsmanager_secret_version" "jwt" {
  secret_id = aws_secretsmanager_secret.jwt.id
  secret_string = jsonencode({
    JWT_SECRET_KEY = random_password.jwt.result
  })
}
