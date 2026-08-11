# DSB Discord Bot - The Herald

![Visitors](https://api.visitorbadge.io/api/visitors?path=https%3A%2F%2Fgithub.com%2Fdevsecblueprint%2Fthe-herald&countColor=%23ffbe00)
![License](https://img.shields.io/github/license/devsecblueprint/the-herald?style=for-the-badge)
![Last Commit](https://img.shields.io/github/last-commit/devsecblueprint/the-herald?style=for-the-badge)
![Issues](https://img.shields.io/github/issues/devsecblueprint/the-herald?style=for-the-badge)
![Pull Requests](https://img.shields.io/github/issues-pr/devsecblueprint/the-herald?style=for-the-badge)
[![Discord](https://img.shields.io/discord/1269864144903864381?style=for-the-badge&logo=discord&color=B4BEFE&logoColor=B4BEFE&labelColor=302D41)](https://discord.gg/enMmUNq8jc)

<p align="center">
  <img src="./docs/imgs/the_herald.jpg" alt="The Herald Image" />
</p>

## Overview

The Herald is the ever-watchful and enigmatic bot, designed to guide, inform, and protect the DSB community in the sprawling digital world. True to its name, The Herald serves as a messenger and beacon, delivering essential updates and insights to empower users on their DevSecOps journey.

**Current Responsibilities:**

1. _Security Newsletter:_
   Curates and delivers a security newsletter by monitoring RSS feeds from sources like Bleeping Computer, The Hacker News, CNBC, and TechCrunch. New articles are posted to the appropriate Discord channels every hour.

2. _Event Notifications:_
   Monitors upcoming Discord scheduled events and sends DM reminders to interested users one hour before events start. Uses DynamoDB for deduplication to prevent duplicate notifications.

3. _Always Online:_
   Maintains a persistent Discord gateway connection so the bot always appears online in the server member list with a "Watching over the community" status.

**Planned Features:**

1. _New Video Announcements:_
   Acting as the digital voice of the DSB founder, The Herald will announce newly released videos, offering summaries and insights into their content.

## Architecture

The Herald runs as a containerized FastAPI application on AWS ECS Fargate (dsb-platform cluster), using APScheduler for periodic tasks and a persistent Discord gateway connection for presence.

```mermaid
graph TB
    subgraph "AWS Cloud - us-east-2"
        subgraph "ECS Cluster: dsb-platform"
            SVC[ECS Service<br/>the-herald<br/>Fargate 0.5 vCPU / 1GB]
        end

        ECR[ECR Repository<br/>the-herald]
        PS[Parameter Store<br/>/the-herald/prod/<br/>- discord-token<br/>- guild-id]
        CW[CloudWatch Logs<br/>/ecs/the-herald<br/>30-day retention]

        ECR -->|Pull image| SVC
        SVC -->|Read secrets| PS
        SVC -->|Write logs| CW
    end

    subgraph "External Services"
        RSS[RSS Feeds<br/>Security News]
        DC[Discord Gateway<br/>Persistent Connection]
        DAPI[Discord REST API<br/>Messages & Events]
    end

    SVC -->|Fetch feeds<br/>every 60 min| RSS
    SVC -->|WebSocket<br/>always online| DC
    SVC -->|Post messages &<br/>send reminders| DAPI

    style SVC fill:#FF9900
    style ECR fill:#FF9900
    style PS fill:#527FFF
    style CW fill:#FF9900
    style RSS fill:#90EE90
    style DC fill:#5865F2
    style DAPI fill:#5865F2
```

## Configuration

**Scheduled Jobs (via APScheduler):**

| Job | Interval | Description |
|-----|----------|-------------|
| Newsletter | 60 minutes | Fetches RSS feeds and posts new articles to Discord channels |
| Event Notifications | 5 minutes | Checks upcoming Discord events and sends DM reminders |

**Always-on:**

| Component | Description |
|-----------|-------------|
| Discord Gateway | Persistent WebSocket connection keeping the bot online with activity status |

**AWS Resources:**
- **Region**: us-east-2
- **ECS Cluster**: dsb-platform (Fargate)
- **Task Size**: 0.5 vCPU / 1 GB RAM
- **Networking**: Public subnet, public IP for outbound (no ingress rules)
- **Parameter Store Prefix**: `/the-herald/prod/`
- **Log Retention**: 30 days

**Environment Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `PARAMETER_STORE_PREFIX` | `/the-herald/prod/` | AWS Parameter Store prefix for secrets |
| `DYNAMODB_TABLE_NAME` | `the-herald-reminders` | DynamoDB table for reminder deduplication |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `NEWSLETTER_INTERVAL_MINUTES` | `60` | Newsletter job interval |
| `EVENT_NOTIFICATION_INTERVAL_MINUTES` | `5` | Event notification job interval |

## Local Development

**Prerequisites:**
- Python 3.13+
- Docker
- AWS CLI configured
- `uv` package manager

### Setup

```bash
# Install dependencies
uv sync

# Run locally with Docker
invoke run
```

This starts the container with your `.env` file mounted. The app exposes:
- `GET /health` — health check (includes Discord connection status)
- `POST /trigger/newsletter` — manually trigger newsletter job
- `POST /trigger/event-notifications` — manually trigger event notifications

### Build Tasks

All operations are managed through `tasks.py` using the `invoke` task runner:

```bash
invoke build              # Build Docker image (linux/amd64)
invoke run                # Run locally with Docker
invoke push               # Build and push image to ECR
invoke deploy             # Force new ECS deployment
invoke push-and-deploy    # Build, push, and deploy (full pipeline)
invoke terraform-apply    # Apply Terraform infrastructure changes
invoke logs               # Tail CloudWatch logs
invoke logs --follow      # Stream logs in real-time
invoke clean              # Remove old build artifacts
```

## Deployment

### Manual Deploy

```bash
# Build and push image to ECR, then update ECS
invoke push-and-deploy

# Or step by step:
invoke push --tag=v2.0.0
invoke deploy
```

### Infrastructure

Infrastructure is managed with Terraform Cloud (organization: `devsecblueprint`, workspace: `the-herald`).

```bash
invoke terraform-apply
```

**Required Terraform Cloud Variables:**

| Variable | Type | Description |
|----------|------|-------------|
| `DISCORD_TOKEN` | Sensitive | Discord bot authentication token |
| `DISCORD_GUILD_ID` | String | Discord server (guild) ID |

## Project Structure

```
the-herald/
├── app/
│   ├── main.py              # FastAPI + APScheduler + Discord presence
│   ├── models.py            # Data models (Feed, FeedsConfig)
│   ├── clients/
│   │   ├── parameter_store.py  # AWS Parameter Store client
│   │   └── dynamodb.py         # DynamoDB reminder tracking client
│   ├── config/
│   │   └── logger.py           # Logging configuration
│   ├── services/
│   │   ├── discord.py          # Discord REST API interactions
│   │   └── newsletter.py       # RSS feed fetching and publishing
│   ├── static/
│   │   └── config.yaml         # RSS feed configuration
│   └── utils/
│       └── secrets.py          # Vault secrets loader (optional)
├── terraform/
│   ├── main.tf              # ECS, ECR, IAM, Parameter Store resources
│   ├── data.tf              # Data sources (cluster, VPC, subnets)
│   ├── variables.tf         # Input variables
│   └── provider.tf          # Terraform Cloud + AWS provider config
├── Dockerfile               # Multi-stage container build (linux/amd64)
├── requirements.txt         # Pinned runtime dependencies
├── pyproject.toml           # Project metadata and dev dependencies
└── tasks.py                 # Build/deploy automation (invoke)
```

## Want To Contribute?

If you'd like to contribute to this project, check out the [Contributing Documentation](./CONTRIBUTING.md).

## Contributors

Thank you so much for making "The Herald" what it is. You all bring this person to life.
