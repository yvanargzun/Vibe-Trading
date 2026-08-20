# Set OMNIROUTE_API_KEY on Synaptika Render services from the VPS gateway key.
# Requires: $env:RENDER_API_KEY (https://dashboard.render.com/u/settings#api-keys)
param(
  [string]$KeyFile = "$env:TEMP\omniroute_gateway_key.txt"
)
$ErrorActionPreference = 'Stop'
if (-not $env:RENDER_API_KEY) {
  throw 'Set RENDER_API_KEY first (Render Dashboard → Account Settings → API Keys).'
}
if (-not (Test-Path $KeyFile)) {
  scp -i "$env:USERPROFILE\.ssh\hetzner_vibe" `
    root@46.225.50.87:/root/synaptika-chat/OMNIROUTE_GATEWAY_API_KEY.txt $KeyFile
}
$key = (Get-Content -Raw $KeyFile).Trim()
if ($key.Length -lt 32) { throw 'gateway key too short' }

$headers = @{
  Authorization = "Bearer $($env:RENDER_API_KEY)"
  Accept        = 'application/json'
  'Content-Type' = 'application/json'
}
$services = Invoke-RestMethod -Headers $headers -Uri 'https://api.render.com/v1/services?limit=50'
$want = @('Synaptika-demos', 'synaptika-messengerfb')
foreach ($row in $services) {
  $s = if ($row.service) { $row.service } else { $row }
  if ($want -notcontains $s.name) { continue }
  $uri = "https://api.render.com/v1/services/$($s.id)/env-vars/OMNIROUTE_API_KEY"
  Invoke-RestMethod -Method Put -Headers $headers -Uri $uri -Body (@{ value = $key } | ConvertTo-Json)
  Write-Output "set $($s.name)"
  Invoke-RestMethod -Method Post -Headers $headers -Uri "https://api.render.com/v1/services/$($s.id)/deploys" -Body '{}'
  Write-Output "deploy $($s.name)"
}
Write-Output 'done'
