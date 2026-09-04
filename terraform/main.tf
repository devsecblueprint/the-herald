# ============================================================================
# The Herald - Discord Bot ECS Fargate Infrastructure
# ============================================================================
# This Terraform configuration deploys The Herald Discord bot as a containerized
# FastAPI service on the existing dsb-platform ECS cluster with:
# - ECR repository for Docker images
# - ECS Task Definition (Fargate)
# - ECS Service on dsb-platform cluster
# - Parameter Store for Discord credentials
# - IAM roles and policies (least privilege)
# - CloudWatch Logs for monitoring
# ============================================================================

# ----------------------------------------------------------------------------
# ECR Repository
# ----------------------------------------------------------------------------

resource "aws_ecr_repository" "the_herald" {
  name                 = "the-herald"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name        = "the-herald"
    Environment = var.environment
  }
}

resource "aws_ecr_lifecycle_policy" "the_herald" {
  repository = aws_ecr_repository.the_herald.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep only the last 5 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 5
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# ----------------------------------------------------------------------------
# CloudWatch Log Group
# ----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "the_herald" {
  name              = "/ecs/the-herald"
  retention_in_days = var.log_retention_days

  tags = {
    Name        = "the-herald-logs"
    Environment = var.environment
  }
}

# ----------------------------------------------------------------------------
# ECS Task Definition
# ----------------------------------------------------------------------------

resource "aws_ecs_task_definition" "the_herald" {
  family                   = "the-herald"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "the-herald"
      image     = "${aws_ecr_repository.the_herald.repository_url}:latest"
      essential = true

      portMappings = [
        {
          containerPort = 8080
          protocol      = "tcp"
        }
      ]

      environment = [
        {
          name  = "PARAMETER_STORE_PREFIX"
          value = var.parameter_store_prefix
        },
        {
          name  = "DYNAMODB_TABLE_NAME"
          value = "the-herald-reminders"
        },
        {
          name  = "LOG_LEVEL"
          value = var.log_level
        },
        {
          name  = "NEWSLETTER_INTERVAL_MINUTES"
          value = tostring(var.newsletter_interval_minutes)
        },
        {
          name  = "EVENT_NOTIFICATION_INTERVAL_MINUTES"
          value = tostring(var.event_notification_interval_minutes)
        },
        {
          name  = "HERALD_YOUTUBE_ENABLED"
          value = tostring(var.youtube_enabled)
        },
        {
          name  = "HERALD_YOUTUBE_POLL_INTERVAL_MINUTES"
          value = tostring(var.youtube_poll_interval_minutes)
        },
        {
          name  = "HERALD_YOUTUBE_EXCLUDE_SHORTS"
          value = tostring(var.youtube_exclude_shorts)
        },
        {
          name  = "HERALD_DISCORD_CHANNEL_ID"
          value = var.content_corner_channel_id
        },
        {
          name  = "HERALD_DEDUP_TABLE_NAME"
          value = aws_dynamodb_table.herald_dedup.name
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.the_herald.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8080/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 10
      }
    }
  ])

  tags = {
    Name        = "the-herald"
    Environment = var.environment
  }
}

# ----------------------------------------------------------------------------
# ECS Service (on existing dsb-platform cluster)
# ----------------------------------------------------------------------------

resource "aws_ecs_service" "the_herald" {
  name            = "the-herald"
  cluster         = data.aws_ecs_cluster.dsb_platform.arn
  task_definition = aws_ecs_task_definition.the_herald.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.public.ids
    security_groups  = [aws_security_group.the_herald.id]
    assign_public_ip = true
  }

  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 100

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = {
    Name        = "the-herald"
    Environment = var.environment
  }

  depends_on = [
    aws_iam_role_policy_attachment.ecs_execution_policy
  ]
}

# ----------------------------------------------------------------------------
# Security Group
# ----------------------------------------------------------------------------

resource "aws_security_group" "the_herald" {
  name        = "the-herald-ecs"
  description = "Security group for The Herald ECS tasks"
  vpc_id      = data.aws_vpc.main.id

  # Allow all outbound traffic (needed for Discord API, RSS feeds, AWS APIs)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }

  tags = {
    Name        = "the-herald-ecs"
    Environment = var.environment
  }
}

# ----------------------------------------------------------------------------
# Parameter Store for Secrets Management
# ----------------------------------------------------------------------------

resource "aws_ssm_parameter" "discord_token" {
  name        = "${var.parameter_store_prefix}discord-token"
  description = "Discord bot authentication token"
  type        = "SecureString"
  value       = var.DISCORD_TOKEN

  key_id = var.kms_key_id != "" ? var.kms_key_id : null

  tags = {
    Name        = "the-herald-token"
    Environment = var.environment
    Purpose     = "Discord bot authentication"
  }

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "guild_id" {
  name        = "${var.parameter_store_prefix}guild-id"
  description = "Discord server (guild) ID"
  type        = "String"
  value       = var.DISCORD_GUILD_ID

  tags = {
    Name        = "discord-guild-id"
    Environment = var.environment
    Purpose     = "Discord server identification"
  }
}

resource "aws_ssm_parameter" "youtube_api_key" {
  name        = "${var.parameter_store_prefix}youtube-api-key"
  description = "YouTube Data API v3 key for reliable upload listing"
  type        = "SecureString"
  value       = var.YOUTUBE_API_KEY

  key_id = var.kms_key_id != "" ? var.kms_key_id : null

  tags = {
    Name        = "the-herald-youtube-api-key"
    Environment = var.environment
    Purpose     = "YouTube Data API access"
  }

  lifecycle {
    ignore_changes = [value]
  }
}

# ----------------------------------------------------------------------------
# IAM Roles and Policies
# ----------------------------------------------------------------------------

# ECS Execution Role (used by ECS agent to pull images, write logs)
resource "aws_iam_role" "ecs_execution_role" {
  name        = "the-herald-ecs-execution-role"
  description = "ECS execution role for The Herald - allows ECS to pull images and write logs"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name        = "the-herald-ecs-execution-role"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy_attachment" "ecs_execution_policy" {
  role       = aws_iam_role.ecs_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ECS Task Role (used by the application container at runtime)
resource "aws_iam_role" "ecs_task_role" {
  name        = "the-herald-ecs-task-role"
  description = "ECS task role for The Herald - grants application permissions"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name        = "the-herald-ecs-task-role"
    Environment = var.environment
  }
}

# ----------------------------------------------------------------------------
# DynamoDB: YouTube deduplication, source roster and channel reference cache
# ----------------------------------------------------------------------------

resource "aws_dynamodb_table" "herald_dedup" {
  name         = "the-herald-dedup"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "content_id"

  attribute {
    name = "content_id"
    type = "S"
  }

  # Per-video records and cached channel references expire on their own.
  # The source roster deliberately carries no ttl attribute: it is the only
  # record of where each partner started, so a long polling outage must not
  # silently expire it and re-onboard everyone.
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Environment = var.environment
  }
}

resource "aws_iam_role_policy" "task_dedup_table" {
  name = "the-herald-dedup-table-access"
  role = aws_iam_role.ecs_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem"
        ]
        Resource = [aws_dynamodb_table.herald_dedup.arn]
      }
    ]
  })
}

resource "aws_iam_role_policy" "task_parameter_store" {
  name = "the-herald-parameter-store-read"
  role = aws_iam_role.ecs_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        Resource = [
          aws_ssm_parameter.discord_token.arn,
          aws_ssm_parameter.guild_id.arn,
          aws_ssm_parameter.youtube_api_key.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "kms:Decrypt"
        ]
        Resource = var.kms_key_id != "" ? var.kms_key_id : "arn:aws:kms:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alias/aws/ssm"
        Condition = {
          StringEquals = {
            "kms:ViaService" = "ssm.${var.aws_region}.amazonaws.com"
          }
        }
      }
    ]
  })
}

# ----------------------------------------------------------------------------
# Outputs
# ----------------------------------------------------------------------------

output "ecr_repository_url" {
  description = "ECR repository URL for The Herald Docker image"
  value       = aws_ecr_repository.the_herald.repository_url
}

output "ecs_service_name" {
  description = "ECS service name"
  value       = aws_ecs_service.the_herald.name
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = data.aws_ecs_cluster.dsb_platform.cluster_name
}

output "cloudwatch_log_group" {
  description = "CloudWatch log group for The Herald"
  value       = aws_cloudwatch_log_group.the_herald.name
}

output "dedup_table_name" {
  description = "DynamoDB table holding YouTube dedupe records, the source roster and cached channel references"
  value       = aws_dynamodb_table.herald_dedup.name
}
