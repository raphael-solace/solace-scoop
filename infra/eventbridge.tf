# Sunday 16:30 UTC - Incremental official-source refresh (disabled at app level
# until the reviewed source catalog is migrated and seeded).
resource "aws_cloudwatch_event_rule" "source_refresh" {
  name                = "${var.app_name}-source-refresh"
  description         = "Official source metadata refresh before weekly research"
  schedule_expression = "cron(30 16 ? * SUN *)"
  state               = "DISABLED"
}

resource "aws_cloudwatch_event_target" "source_refresh" {
  rule     = aws_cloudwatch_event_rule.source_refresh.name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.eventbridge.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.digest.arn
    task_count          = 1
    launch_type         = "FARGATE"
    network_configuration {
      subnets          = data.aws_subnets.default.ids
      security_groups  = [aws_security_group.app.id]
      assign_public_ip = true
    }
  }

  input = jsonencode({
    containerOverrides = [{
      name    = "digest"
      command = ["python", "run_sources.py", "--poll-global"]
    }]
  })
}

# Sunday 17:00 UTC - shared company pool shadow refresh. Delivery still uses the
# legacy pipeline until shadow metrics meet the cutover gate.
resource "aws_cloudwatch_event_rule" "company_pool_shadow" {
  name                = "${var.app_name}-company-pool-shadow"
  description         = "Central company research pool shadow run"
  schedule_expression = "cron(0 17 ? * SUN *)"
  state               = "DISABLED"
}

resource "aws_cloudwatch_event_target" "company_pool_shadow" {
  rule     = aws_cloudwatch_event_rule.company_pool_shadow.name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.eventbridge.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.digest.arn
    task_count          = 1
    launch_type         = "FARGATE"
    network_configuration {
      subnets          = data.aws_subnets.default.ids
      security_groups  = [aws_security_group.app.id]
      assign_public_ip = true
    }
  }

  input = jsonencode({
    containerOverrides = [{
      name    = "digest"
      command = ["python", "run_company_pool.py", "--research"]
    }]
  })
}

# Sunday 18:00 UTC (20:00 CEST) - Research
resource "aws_cloudwatch_event_rule" "digest_research" {
  name                = "${var.app_name}-research"
  description         = "Sunday research pass"
  schedule_expression = "cron(0 18 ? * SUN *)"
}

# Resume missing or invalid candidates before Monday's send. The shared research
# lock prevents overlap, and ready candidates are retained by the CLI.
resource "aws_cloudwatch_event_rule" "digest_recovery" {
  for_each = {
    sunday = "cron(0 22 ? * SUN *)"
    monday = "cron(0 3 ? * MON *)"
  }
  name                = "${var.app_name}-recovery-${each.key}"
  description         = "Resume incomplete weekly Scoop research"
  schedule_expression = each.value
}

resource "aws_cloudwatch_event_target" "digest_recovery" {
  for_each = aws_cloudwatch_event_rule.digest_recovery
  rule     = each.value.name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.eventbridge.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.digest.arn
    task_count          = 1
    launch_type         = "FARGATE"
    network_configuration {
      subnets          = data.aws_subnets.default.ids
      security_groups  = [aws_security_group.app.id]
      assign_public_ip = true
    }
  }

  input = jsonencode({
    containerOverrides = [{
      name    = "digest"
      command = ["python", "run_digest.py", "--research-only"]
    }]
  })
}

resource "aws_cloudwatch_event_target" "digest_research" {
  rule     = aws_cloudwatch_event_rule.digest_research.name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.eventbridge.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.digest.arn
    task_count          = 1
    launch_type         = "FARGATE"

    network_configuration {
      subnets          = data.aws_subnets.default.ids
      security_groups  = [aws_security_group.app.id]
      assign_public_ip = true
    }
  }

  input = jsonencode({
    containerOverrides = [{
      name    = "digest"
      command = ["python", "run_digest.py", "--research-only"]
    }]
  })
}

# Mon-Fri 10:00 UTC (12:00 CEST) - Daily scan for breaking scoops
resource "aws_cloudwatch_event_rule" "daily_scan" {
  name                = "${var.app_name}-daily-scan"
  description         = "Daily breaking scoop scan"
  schedule_expression = "cron(0 10 ? * MON-FRI *)"
}

resource "aws_cloudwatch_event_target" "daily_scan" {
  rule     = aws_cloudwatch_event_rule.daily_scan.name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.eventbridge.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.digest.arn
    task_count          = 1
    launch_type         = "FARGATE"

    network_configuration {
      subnets          = data.aws_subnets.default.ids
      security_groups  = [aws_security_group.app.id]
      assign_public_ip = true
    }
  }

  input = jsonencode({
    containerOverrides = [{
      name    = "digest"
      command = ["python", "run_digest.py", "--daily-scan"]
    }]
  })
}

# Monday 09:00 UTC (11:00 CEST) - Weekly brand monitoring (Solace mentions)
resource "aws_cloudwatch_event_rule" "brand_monitor" {
  name                = "${var.app_name}-brand-monitor"
  description         = "Weekly Solace brand mentions scan"
  schedule_expression = "cron(0 9 ? * MON *)"
}

resource "aws_cloudwatch_event_target" "brand_monitor" {
  rule     = aws_cloudwatch_event_rule.brand_monitor.name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.eventbridge.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.digest.arn
    task_count          = 1
    launch_type         = "FARGATE"

    network_configuration {
      subnets          = data.aws_subnets.default.ids
      security_groups  = [aws_security_group.app.id]
      assign_public_ip = true
    }
  }

  input = jsonencode({
    containerOverrides = [{
      name    = "digest"
      command = ["python", "run_digest.py", "--brand-monitor"]
    }]
  })
}

# Monday 06:00 UTC (08:00 CEST, 07:00 CET) - Send.
# TODO: migrate this rule to EventBridge Scheduler with Europe/Paris timezone
# after importing the live target; do not run a duplicate scheduler in parallel.
resource "aws_cloudwatch_event_rule" "digest_send" {
  name                = "${var.app_name}-send"
  description         = "Monday morning email send"
  schedule_expression = "cron(0 6 ? * MON *)"
}

resource "aws_cloudwatch_event_target" "digest_send" {
  rule     = aws_cloudwatch_event_rule.digest_send.name
  arn      = aws_ecs_cluster.main.arn
  role_arn = aws_iam_role.eventbridge.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.digest.arn
    task_count          = 1
    launch_type         = "FARGATE"

    network_configuration {
      subnets          = data.aws_subnets.default.ids
      security_groups  = [aws_security_group.app.id]
      assign_public_ip = true
    }
  }

  input = jsonencode({
    containerOverrides = [{
      name    = "digest"
      command = ["python", "run_digest.py", "--send-only"]
    }]
  })
}
