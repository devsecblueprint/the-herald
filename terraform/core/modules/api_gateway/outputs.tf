output "api_gateway_url" {
  value = "${aws_api_gateway_deployment.this.invoke_url}${aws_api_gateway_resource.this.path_part}"
}
