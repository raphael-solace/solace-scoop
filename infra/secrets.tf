resource "aws_secretsmanager_secret" "app_env" {
  name                    = "${var.app_name}/env"
  recovery_window_in_days = 0
}

# Metadata only: credentials are provisioned out of band so neither their values
# nor a Secrets Manager version are ever recorded in Terraform state.
resource "aws_secretsmanager_secret" "admin_auth" {
  name                    = "${var.app_name}/admin"
  recovery_window_in_days = 7
}

locals {
  admin_secret_keys = [
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD_HASH",
    "ADMIN_SESSION_SECRET",
  ]

  # DATABASE_URL is computed from the RDS instance so the app connects
  # to Postgres directly (asyncpg). sslmode=require -> RDS enforces TLS.
  database_url = "postgresql://${aws_db_instance.main.username}:${var.db_password}@${aws_db_instance.main.address}:${aws_db_instance.main.port}/${aws_db_instance.main.db_name}?sslmode=require"

  # Admin credentials must never be copied into the shared app environment
  # secret, which is also available to the scheduled digest task.
  non_admin_env_vars = {
    for key, value in var.env_vars : key => value
    if !contains(local.admin_secret_keys, key)
  }
  all_env_vars = merge(local.non_admin_env_vars, {
    DATABASE_URL = local.database_url
    API_URL      = var.public_api_url
  })
}

resource "aws_secretsmanager_secret_version" "app_env" {
  secret_id     = aws_secretsmanager_secret.app_env.id
  secret_string = jsonencode(local.all_env_vars)
}
