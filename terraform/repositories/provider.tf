terraform {
  cloud {
    organization = "DSB"
    workspaces {
      name = "discord-bot-repositories"
    }
  }
}

provider "aws" {
  region = var.region
}