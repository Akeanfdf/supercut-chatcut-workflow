param(
    [string]$Source = $PSScriptRoot,
    [string]$Sha256 = "",
    [string]$SkillsDir = "",
    [string]$ConfigTarget = "",
    [string]$Manifest = ""
)

$installer = Join-Path $PSScriptRoot "scripts\install_workflow.py"
$arguments = @($installer, "--source", $Source)
if ($Sha256) { $arguments += @("--sha256", $Sha256) }
if ($SkillsDir) { $arguments += @("--skills-dir", $SkillsDir) }
if ($ConfigTarget) { $arguments += @("--config-target", $ConfigTarget) }
if ($Manifest) { $arguments += @("--manifest", $Manifest) }
python @arguments
exit $LASTEXITCODE
