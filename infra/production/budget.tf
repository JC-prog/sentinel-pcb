# A monthly cost budget that emails when spend (or the forecast) crosses a threshold. This stack is
# meant to be short-lived (docs/DEPLOYMENT.md), and a forgotten one runs about $5 a day, so this is
# the safety net for "I forgot to destroy it". It covers the whole account, not just this project:
# tag-filtered budgets need the Project tag activated as a cost allocation tag first, and an
# account-wide figure also catches strays left behind by a failed teardown.
#
# Off unless var.budget_alert_email is set. AWS billing data lags by roughly 8-24 hours, so this is
# a nudge, not a hard cap - it does not stop anything from running.

resource "aws_budgets_budget" "monthly" {
  count = var.budget_alert_email == "" ? 0 : 1

  name         = "${var.project_name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  # Warns before the limit is hit, based on this month's run rate.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}
