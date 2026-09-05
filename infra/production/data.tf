# Uses the account's default VPC and its (already public) default subnets rather than creating a
# custom VPC - keeps this small and NAT-Gateway-free, per the "public subnets, tight security
# groups" call. Revisit if this ever needs private subnets.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}
