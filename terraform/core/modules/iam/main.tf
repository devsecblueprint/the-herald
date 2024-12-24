resource "aws_iam_role" "this" {
  name                = var.name
  assume_role_policy  = var.assume_role_policy
  managed_policy_arns = var.managed_policy_arns
}

resource "aws_iam_role_policy" "inline_policy" {
  count  = var.inline_policy_enabled ? 1 : 0
  name   = var.inline_policy_name
  role   = aws_iam_role.this.id
  policy = var.inline_policy
}