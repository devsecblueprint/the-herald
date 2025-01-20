locals {
  ecr_lifecycle_policy = <<EOF
{
    "rules": [
        {
            "rulePriority": 1,
            "description": "Keep only one untagged image, expire all others",
            "selection": {
                "tagStatus": "untagged",
                "countType": "imageCountMoreThan",
                "countNumber": 1
            },
            "action": {
                "type": "expire"
            }
        }
    ]
}
  EOF

  # Always add new image repositories to the bottom. 
  # Order matters in this case. It will fail if you don't.
  ecr_repo_names = [
    # Newsletter Images
    "${var.resource_prefix}-security-newsletter-image",

    # YouTube Listener Image
    "${var.resource_prefix}-youtube-subscriber-image",

    # Main Bot
    "${var.resource_prefix}-image",

    # Job Poster Image
    "${var.resource_prefix}-job-poster-image",
  ]
}
