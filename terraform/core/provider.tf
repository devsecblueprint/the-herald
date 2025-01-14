terraform {
  cloud {
    organization = "DSB"
    workspaces {
      name = "discord-bot"
    }
  }
}

provider "aws" {
  region = var.region
}