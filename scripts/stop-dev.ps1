$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    docker compose -f deployment/docker/docker-compose.yml down
}
finally {
    Pop-Location
}
