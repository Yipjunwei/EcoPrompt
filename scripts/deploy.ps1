# deploy.ps1 — builds all images, injects secrets from .env, and deploys to Kubernetes
param(
    [switch]$IngressPortForward
)

# ── Load .env ─────────────────────────────────────────────────────────────────
if (-not (Test-Path ".env")) {
    Write-Error "ERROR: .env file not found. Copy .env.example to .env and fill in your values."
    exit 1
}

$env_vars = @{}
Get-Content ".env" | Where-Object { $_ -notmatch '^\s*#' -and $_ -match '=' } | ForEach-Object {
    $key, $value = $_ -split '=', 2
    $env_vars[$key.Trim()] = $value.Trim()
}

# ── Validate required vars ────────────────────────────────────────────────────
foreach ($var in @("GROQ_API_KEY", "SECRET_KEY", "POSTGRES_PASSWORD")) {
    if (-not $env_vars[$var]) {
        Write-Error "ERROR: $var is not set in .env"
        exit 1
    }
}

$GROQ_API_KEY      = $env_vars["GROQ_API_KEY"]
$SECRET_KEY        = $env_vars["SECRET_KEY"]
$POSTGRES_PASSWORD = $env_vars["POSTGRES_PASSWORD"]
$POSTGRES_USER     = if ($env_vars["POSTGRES_USER"]) { $env_vars["POSTGRES_USER"] } else { "eco" }
$POSTGRES_DB       = if ($env_vars["POSTGRES_DB"])   { $env_vars["POSTGRES_DB"] }   else { "eco" }

function Invoke-CheckedCommand {
    param(
        [string]$Description,
        [scriptblock]$Command
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "ERROR: $Description failed."
    }
}

function Wait-IngressAdmissionReady {
    param(
        [int]$TimeoutSeconds = 180
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $hasEndpoints = $false
        $jsonPath = '{.subsets[0].addresses[0].ip}'
        $endpointIp = kubectl get endpoints ingress-nginx-controller-admission -n ingress-nginx -o jsonpath=$jsonPath 2>$null
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($endpointIp)) {
            $hasEndpoints = $true
        }

        if ($hasEndpoints) {
            return
        }

        Start-Sleep -Seconds 3
    }

    throw "ERROR: Timed out waiting for ingress-nginx admission endpoints."
}

Write-Host "Checking Kubernetes connectivity..."
try {
    Invoke-CheckedCommand "Checking Kubernetes API connectivity" { kubectl cluster-info *> $null }
}
catch {
    Write-Error $_.Exception.Message
    Write-Error "Kubernetes is unreachable. Ensure Docker Desktop Kubernetes (or your cluster) is running, then retry."
    exit 1
}

# ── Build images ──────────────────────────────────────────────────────────────
Write-Host "Building images..."
docker build -t ecoprompt/eco-cleaner:latest   ./services/cleaner-service
docker build -t ecoprompt/eco-analytics:latest ./services/analytics-service

docker build -t ecoprompt/eco-aimodel:latest -f ./services/aimodel-service/Dockerfile .

docker build -t ecoprompt/eco-chat:latest      ./services/chat-service

# ── Inject secrets ────────────────────────────────────────────────────────────
Write-Host "Injecting secrets..."

function To-Base64 ($s) { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($s)) }

$GROQ_B64     = To-Base64 $GROQ_API_KEY
$SECRET_B64   = To-Base64 $SECRET_KEY
$PG_PASS_B64  = To-Base64 $POSTGRES_PASSWORD
$DB_URL_B64   = To-Base64 "postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres-svc:5432/${POSTGRES_DB}"

$secret = Get-Content "k8s/secret.yaml" -Raw
$secret = $secret -replace "REPLACE_WITH_BASE64_GROQ_API_KEY",      $GROQ_B64
$secret = $secret -replace "REPLACE_WITH_BASE64_SECRET_KEY",         $SECRET_B64
$secret = $secret -replace "REPLACE_WITH_BASE64_POSTGRES_PASSWORD",  $PG_PASS_B64
$secret = $secret -replace "REPLACE_WITH_BASE64_DATABASE_URL",       $DB_URL_B64
$secret | Set-Content "k8s/secret.yaml" -NoNewline

try {
    try {
        # ── Ensure ingress-nginx controller exists before applying ingress resources ──
        $hasIngressAdmissionSvc = $false
        kubectl get svc ingress-nginx-controller-admission -n ingress-nginx --no-headers *> $null
        if ($LASTEXITCODE -eq 0) { $hasIngressAdmissionSvc = $true }

        if (-not $hasIngressAdmissionSvc) {
            Write-Host "Installing ingress-nginx controller (first-time setup)..."
            Invoke-CheckedCommand "Installing ingress-nginx controller" { kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.10.1/deploy/static/provider/cloud/deploy.yaml }

            Write-Host "Waiting for ingress controller and admission webhooks to be ready..."
            Invoke-CheckedCommand "Waiting for ingress-nginx controller deployment" { kubectl wait --namespace ingress-nginx --for=condition=Available deployment/ingress-nginx-controller --timeout=240s *> $null }
            Invoke-CheckedCommand "Waiting for ingress-nginx admission-create job" { kubectl wait --namespace ingress-nginx --for=condition=complete job/ingress-nginx-admission-create --timeout=240s *> $null }
            Invoke-CheckedCommand "Waiting for ingress-nginx admission-patch job" { kubectl wait --namespace ingress-nginx --for=condition=complete job/ingress-nginx-admission-patch --timeout=240s *> $null }
        }

        Write-Host "Waiting for ingress admission service endpoints..."
        Wait-IngressAdmissionReady -TimeoutSeconds 180

        # ── Deploy ────────────────────────────────────────────────────────────────────
        Write-Host "Deploying to Kubernetes..."
        $maxAttempts = 3
        $applySucceeded = $false
        for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
            kubectl apply -k k8s/
            if ($LASTEXITCODE -eq 0) {
                $applySucceeded = $true
                break
            }

            if ($attempt -lt $maxAttempts) {
                Write-Host "kubectl apply failed (attempt $attempt/$maxAttempts). Waiting before retry..."
                Start-Sleep -Seconds 8
            }
        }

        if (-not $applySucceeded) {
            throw "ERROR: Applying Kubernetes manifests failed."
        }
    }
    finally {
        # ── Restore secret.yaml placeholders ─────────────────────────────────────────
        $secret = Get-Content "k8s/secret.yaml" -Raw
        $secret = $secret -replace $GROQ_B64,    "REPLACE_WITH_BASE64_GROQ_API_KEY"
        $secret = $secret -replace $SECRET_B64,  "REPLACE_WITH_BASE64_SECRET_KEY"
        $secret = $secret -replace $PG_PASS_B64, "REPLACE_WITH_BASE64_POSTGRES_PASSWORD"
        $secret = $secret -replace $DB_URL_B64,  "REPLACE_WITH_BASE64_DATABASE_URL"
        $secret | Set-Content "k8s/secret.yaml" -NoNewline
    }
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}

Write-Host ""
Write-Host "Done! Check status with:"
Write-Host "  kubectl get all -n ecoprompt"

Write-Host ""
Write-Host "Local access options:"
Write-Host "  Option 1 (no ingress required):"
Write-Host "    kubectl port-forward -n ecoprompt svc/chat-svc 5000:5000"
Write-Host "    kubectl port-forward -n ecoprompt svc/analytics-svc 5002:5002"
Write-Host "    Chat:      http://localhost:5000"
Write-Host "    Dashboard: http://localhost:5002/dashboard"

$ingressNsExists = $false
kubectl get namespace ingress-nginx --no-headers *> $null
if ($LASTEXITCODE -eq 0) { $ingressNsExists = $true }

if (-not $ingressNsExists) {
    Write-Host ""
    Write-Host "  Option 2 (ingress on localhost:8080):"
    Write-Host "    Ingress controller not found. Install once with:"
    Write-Host "    kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.10.1/deploy/static/provider/cloud/deploy.yaml"
    Write-Host "    Then run: kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 8080:80"
    Write-Host "    Open: http://localhost:8080 and http://localhost:8080/dashboard"
}
else {
    kubectl get svc ingress-nginx-controller -n ingress-nginx --no-headers *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "  Option 2 (ingress on localhost:8080):"
        Write-Host "    kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 8080:80"
        Write-Host "    Open: http://localhost:8080 and http://localhost:8080/dashboard"
    }
    else {
        Write-Host ""
        Write-Host "  Option 2 (ingress on localhost:8080):"
        Write-Host "    ingress-nginx namespace exists, but service ingress-nginx-controller was not found."
        Write-Host "    Reinstall ingress controller with:"
        Write-Host "    kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.10.1/deploy/static/provider/cloud/deploy.yaml"
    }
}

if ($IngressPortForward) {
    kubectl get svc ingress-nginx-controller -n ingress-nginx --no-headers *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Ingress port-forward was requested, but ingress-nginx-controller service was not found."
    }
    else {
        Write-Host ""
        Write-Host "Starting ingress port-forward in background..."
        try {
            $pfProcess = Start-Process -FilePath "kubectl" -ArgumentList @("port-forward", "-n", "ingress-nginx", "svc/ingress-nginx-controller", "8080:80") -PassThru -WindowStyle Hidden
            Start-Sleep -Seconds 1

            if ($pfProcess.HasExited) {
                Write-Warning "Ingress port-forward process exited immediately. Port 8080 may already be in use."
            }
            else {
                Write-Host "  Ingress:   http://localhost:8080"
                Write-Host "  Dashboard: http://localhost:8080/dashboard"
                Write-Host "  PID: $($pfProcess.Id)"
                Write-Host "  Stop with: Stop-Process -Id $($pfProcess.Id)"
            }
        }
        catch {
            Write-Warning "Failed to start ingress port-forward automatically: $($_.Exception.Message)"
        }
    }
}
