locals {
  YOUTUBE_CHANNEL_HANDLES = [
    # "@damienjburks",
    "@damienburks9802" #only enable when testing
  ]
  account_id = data.aws_caller_identity.current.account_id
}