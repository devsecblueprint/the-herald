terraform {
  cloud {
    workspaces {
      name = "dsb-discord-bot-repositories"
    }
  }
}

provider "aws" {
  region = var.region
}