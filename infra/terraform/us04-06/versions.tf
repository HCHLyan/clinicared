terraform {
  required_version = ">= 1.7"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.80" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # State contains generated secrets: encrypted S3 backend with locking is mandatory.
  backend "s3" {
    key          = "clinicared/us04-06/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = "clinicared-citas"
      Environment = var.environment
      ManagedBy   = "terraform"
      DataClass   = "PHI"
    }
  }
}

data "aws_caller_identity" "current" {}
