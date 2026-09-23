param([string]$Prompt)
if ($Prompt -match '(?i)username') {
  [Console]::Out.Write($env:GITEE_ASKPASS_USERNAME)
} else {
  [Console]::Out.Write($env:GITEE_ASKPASS_TOKEN)
}
