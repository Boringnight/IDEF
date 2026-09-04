# Start cloudflared quick tunnel in the background, auto-detect the trycloudflare URL,
# highlight it in green and copy it to the clipboard for easy sharing.
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$out = Join-Path $dir 'tunnel.log'
$err = Join-Path $dir 'tunnel.err.log'
Remove-Item $out, $err -ErrorAction SilentlyContinue

Start-Process -FilePath (Join-Path $dir 'cloudflared.exe') `
  -ArgumentList 'tunnel', '--url', 'http://localhost:5174', '--no-autoupdate' `
  -WorkingDirectory $dir -WindowStyle Hidden `
  -RedirectStandardOutput $out -RedirectStandardError $err | Out-Null

Write-Host '  Fetching tunnel URL (a few seconds)...'
$url = $null
for ($i = 0; $i -lt 40; $i++) {
    if (Test-Path $out) {
        $m = Select-String -Path $out, $err -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' `
            -AllMatches -ErrorAction SilentlyContinue | Select-Object -Last 1
        if ($m) { $url = $m.Matches[0].Value; break }
    }
    Start-Sleep -Seconds 1
}

Write-Host ''
if ($url) {
    Set-Clipboard -Value $url
    Write-Host '  [tunnel] Public URL (already copied to clipboard):' -ForegroundColor Cyan
    Write-Host ('  ' + $url) -ForegroundColor Green
} else {
    Write-Host '  [tunnel] Could not fetch URL yet; please check your network.' -ForegroundColor Yellow
}

