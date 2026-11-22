resource "aws_ecs_cluster" "main" {
  name = var.app_name
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.app_name}"
  retention_in_days = 30
}

resource "aws_security_group" "app" {
  name        = "${var.app_name}-ecs"
  description = "Scoop ECS tasks"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    # The public routing setup maintains the live ingress rules.
    ignore_changes = [ingress]
  }
}

# --- API Service (always-on) ---

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.app_name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "api"
    image     = "${aws_ecr_repository.app.repository_url}:latest"
    essential = true
    command   = ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

    portMappings = [{
      containerPort = 8000
      protocol      = "tcp"
    }]

    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=5)\" || exit 1"]
      interval    = 30
      timeout     = 10
      retries     = 3
      startPeriod = 30
    }

    secrets = concat(
      [for k, _ in local.all_env_vars : {
        name      = k
        valueFrom = "${aws_secretsmanager_secret.app_env.arn}:${k}::"
      }],
      [for k in local.admin_secret_keys : {
        name      = k
        valueFrom = "${aws_secretsmanager_secret.admin_auth.arn}:${k}::"
      }]
    )

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.app.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "api"
      }
    }
  }])
}

locals {
  active_api_task_definition    = coalesce(var.deployed_api_task_definition, aws_ecs_task_definition.api.arn)
  active_digest_task_definition = coalesce(var.deployed_digest_task_definition, aws_ecs_task_definition.digest.arn)
}

resource "aws_ecs_service" "api" {
  name            = "${var.app_name}-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = local.active_api_task_definition
  desired_count   = 1
  launch_type     = "FARGATE"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true
  }

  lifecycle {
    # ALB attachments and their startup grace period belong to public routing.
    ignore_changes = [load_balancer, health_check_grace_period_seconds]
  }
}

# --- Digest Task (scheduled) ---

resource "aws_ecs_task_definition" "digest" {
  family                   = "${var.app_name}-digest"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "digest"
    image     = "${aws_ecr_repository.app.repository_url}:latest"
    essential = true
    command   = ["python", "run_digest.py", "--research-only"]

    secrets = [for k, _ in local.all_env_vars : {
      name      = k
      valueFrom = "${aws_secretsmanager_secret.app_env.arn}:${k}::"
    }]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.app.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "digest"
      }
    }
  }])
}
