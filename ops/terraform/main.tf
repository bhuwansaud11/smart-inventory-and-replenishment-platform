terraform {
  required_version = ">= 1.5.0"
  required_providers {
    docker = {
      source = "kreuzwerker/docker"
      version = "~> 3.0"
    }
  }
}
provider "docker" {}
variable "secret_key" {
  type = string
  sensitive = true
}
variable "demo_password" {
  type = string
  sensitive = true
}
resource "docker_image" "app" {
  name = "stockpilot:terraform"
  build {
    context = "${path.module}/../.."
  }
}
resource "docker_volume" "data" {
  name = "stockpilot-terraform-data"
}
resource "docker_container" "app" {
  name = "stockpilot-terraform"
  image = docker_image.app.image_id
  env = ["SECRET_KEY=${var.secret_key}", "DEMO_PASSWORD=${var.demo_password}"]
  ports {
    internal = 5000
    external = 5000
    ip = "127.0.0.1"
  }
  volumes {
    volume_name = docker_volume.data.name
    container_path = "/data"
  }
}
