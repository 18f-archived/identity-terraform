terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 3.52.0"
    }
    external = {
      source  = "hashicorp/external"
      version = "~> 2.1.0"
    }
    github = {
      source  = "integrations/github"
      version = "~> 4.13.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.1.0"
    }
    template = {
      source  = "hashicorp/template"
      version = "~> 2.2.0"
    }
    newrelic = {
      source  = "newrelic/newrelic"
      version = "~> 2.24.1"
    }
  }
  required_version = ">= 0.13.7"
}
