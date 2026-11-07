# Recycle the clay-debug Chrome, leaving the user's own Chrome windows alone.
# Chrome grows ~1.2 GB over a dozen CSV imports on a 7.7 GB machine; recycling
# between batches is what keeps the delivery import from wedging.
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
  Where-Object { $_.CommandLine -like '*clay-debug*' } |
  ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }
Start-Sleep -Seconds 3
$chrome = "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
Start-Process -FilePath $chrome -ArgumentList '--remote-debugging-port=9222','--user-data-dir=C:\clay-debug','https://app.clay.com'
for ($i = 0; $i -lt 12; $i++) {
  Start-Sleep -Seconds 3
  try { Invoke-RestMethod -Uri 'http://127.0.0.1:9222/json/version' -TimeoutSec 4 | Out-Null; "CDP up"; exit 0 } catch {}
}
"CDP did not come back"; exit 1
