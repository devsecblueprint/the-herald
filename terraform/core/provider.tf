terraform {
  cloud {
    workspaces {
      name = "dsb-discord-bot"
    }
  }
}

provider "aws" {
  region = var.region
}