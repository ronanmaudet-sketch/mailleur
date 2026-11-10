#requires -Version 5.1
# Tests de protocole avec doubles PowerShell ; aucun SOLIDWORKS n'est lance.
# Execution : powershell.exe -NoProfile -File test_solidworks_bridge.ps1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'solidworks_to_step.ps1')

function Assert-True($Condition, [string]$Message) {
    if (-not $Condition) { throw "ASSERTION : $Message" }
}

function Expect-Failure([scriptblock]$Action, [string]$MessageFragment) {
    $caught = $false
    try { & $Action | Out-Null } catch {
        $caught = $true
        Assert-True ($_.Exception.Message.Contains($MessageFragment)) ('Erreur inattendue : ' + $_.Exception.Message)
    }
    Assert-True $caught 'Un echec devait etre signale.'
}

function New-FakeComponent([int]$State) {
    $component = [pscustomobject]@{State=$State;Name2='component-1';IsSpeedPak=$false}
    $component | Add-Member ScriptMethod GetSuppression2 { return $this.State }
    $component | Add-Member ScriptMethod GetModelDoc2 { return [pscustomobject]@{fake_reference=$true} }
    return $component
}

function New-FakeApplication {
    $config = [pscustomobject]@{Name='Default';SpeedPak=$false}
    $config | Add-Member ScriptMethod IsSpeedPak { return $this.SpeedPak }
    $model = [pscustomobject]@{
        Path='';Title='fixture.sldprt';Visible=$false
        ConfigurationManager=[pscustomobject]@{ActiveConfiguration=$config}
        Components=@();ResolveStatus=0;LightweightCount=0;SelectionCleared=$false;Extension=$null;App=$null
    }
    $model | Add-Member ScriptMethod GetPathName { return $this.Path }
    $model | Add-Member ScriptMethod GetTitle { return $this.Title }
    $model | Add-Member ScriptMethod ResolveAllLightWeightComponents { param($WarnUser) return $this.ResolveStatus }
    $model | Add-Member ScriptMethod GetLightWeightComponentCount { return $this.LightweightCount }
    $model | Add-Member ScriptMethod GetComponents { param($TopLevelOnly) return $this.Components }
    $model | Add-Member ScriptMethod ClearSelection2 { param($All) $this.SelectionCleared=$true }
    $app = [pscustomobject]@{
        Model=$model;DocumentCount=0;OpenErrors=0;OpenWarnings=0;ActivationErrors=0
        SaveErrors=0;SaveWarnings=0;InvalidStep=$false;WrongDocument=$false
        OpenCalls=0;Closed=[Collections.Generic.List[string]]::new();WorkingDirectory='initial-workdir';ActiveDoc=$model
    }
    $model.App=$app
    $ext = [pscustomobject]@{Owner=$model}
    $ext | Add-Member ScriptMethod SaveAs {
        param($Path, $Version, $Options, $Data, $Errors, $Warnings)
        Assert-True $this.Owner.SelectionCleared 'ClearSelection2 doit preceder SaveAs.'
        $Errors.Value=$this.Owner.App.SaveErrors
        $Warnings.Value=$this.Owner.App.SaveWarnings
        $text="ISO-10303-21;`nHEADER;`nFILE_DESCRIPTION(('mock only'),'2;1');`nENDSEC;`nDATA;`nENDSEC;`nEND-ISO-10303-21;`n"
        if ($this.Owner.App.InvalidStep) { $text='invalid data' * 10 }
        [IO.File]::WriteAllText($Path,$text,[Text.Encoding]::ASCII)
        return ($Errors.Value -eq 0)
    }
    $model.Extension=$ext
    $app | Add-Member ScriptMethod GetDocumentCount { return $this.DocumentCount }
    $app | Add-Member ScriptMethod GetCurrentWorkingDirectory { return $this.WorkingDirectory }
    $app | Add-Member ScriptMethod SetCurrentWorkingDirectory { param($Path) $this.WorkingDirectory=$Path; return $true }
    $app | Add-Member ScriptMethod GetOpenDocumentByName { param($Path) return $null }
    $app | Add-Member ScriptMethod OpenDoc6 {
        param($Path,$Type,$Options,$Configuration,$Errors,$Warnings)
        Assert-True ($Options -eq 67) 'OpenDoc6 doit utiliser Silent+ReadOnly+OverrideDefaultLoadLightweight.'
        $this.OpenCalls++
        $this.Model.Path=$Path
        if ($this.WrongDocument) { $this.Model.Path=$Path+'.unowned' }
        $Errors.Value=$this.OpenErrors
        $Warnings.Value=$this.OpenWarnings
        return $this.Model
    }
    $app | Add-Member ScriptMethod ActivateDoc3 {
        param($Title,$UseUserPreferences,$Option,$Errors)
        $Errors.Value=$this.ActivationErrors
        return $this.Model
    }
    $app | Add-Member ScriptMethod CloseDoc { param($Title) [void]$this.Closed.Add($Title) }
    $app | Add-Member ScriptMethod RevisionNumber { return '28.0-MOCK-NOT-SOLIDWORKS' }
    return $app
}

$folder = Join-Path ([IO.Path]::GetTempPath()) ('mailleur-sw-mock-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($folder)
try {
    $part=Join-Path $folder 'piece with spaces.sldprt'
    $assembly=Join-Path $folder 'assembly.sldasm'
    [IO.File]::WriteAllText($part,'mock source')
    [IO.File]::WriteAllText($assembly,'mock source')
    $output=Join-Path $folder 'result.step'
    $app=New-FakeApplication
    $report=Invoke-MailleurSolidWorksToStep -Source $part -Destination $output -ApplicationFactory ({$app}.GetNewClosure())
    Assert-True ([IO.File]::Exists($output)) 'La sortie doit etre publiee apres succes.'
    Assert-True ($report.status -eq 'converted' -and $app.Closed.Count -eq 1) 'Fermer seulement le document ouvert.'
    Assert-True ($app.WorkingDirectory -eq 'initial-workdir') 'Restaurer le repertoire courant de SOLIDWORKS.'
    Assert-True ([IO.File]::ReadAllText($part) -eq 'mock source') 'La source ne doit pas changer.'
    [IO.File]::Delete($output)

    $app=New-FakeApplication
    $app.DocumentCount=1
    Expect-Failure {Invoke-MailleurSolidWorksToStep -Source $part -Destination $output -ApplicationFactory ({$app}.GetNewClosure())} 'contient deja'
    Assert-True ($app.OpenCalls -eq 0 -and $app.Closed.Count -eq 0) 'Ne pas toucher a une session occupee.'

    $app=New-FakeApplication
    $app.Model.Components=@(New-FakeComponent 0)
    Expect-Failure {Invoke-MailleurSolidWorksToStep -Source $assembly -Destination $output -ApplicationFactory ({$app}.GetNewClosure())} 'Export partiel refuse'
    Assert-True (-not [IO.File]::Exists($output)) 'Aucune sortie partielle publiee.'
    $app=New-FakeApplication
    $app.Model.Components=@(New-FakeComponent 0)
    $report=Invoke-MailleurSolidWorksToStep -Source $assembly -Destination $output -PermitSuppressed -ApplicationFactory ({$app}.GetNewClosure())
    Assert-True ($report.suppressed_components_excluded -eq 1) 'Une exclusion acceptee doit etre rapportee.'
    [IO.File]::Delete($output)

    $app=New-FakeApplication
    $app.OpenWarnings=8
    Expect-Failure {Invoke-MailleurSolidWorksToStep -Source $part -Destination $output -ApplicationFactory ({$app}.GetNewClosure())} 'Ouverture incomplete'
    Assert-True ($app.Closed.Count -eq 1) 'Nettoyer le document possede apres avertissement.'
    $app=New-FakeApplication
    $app.SaveErrors=1
    Expect-Failure {Invoke-MailleurSolidWorksToStep -Source $part -Destination $output -ApplicationFactory ({$app}.GetNewClosure())} 'Export STEP refuse'
    Assert-True (-not [IO.File]::Exists($output)) 'Un echec SaveAs ne doit rien publier.'
    Assert-True (@(Get-ChildItem -LiteralPath $folder -Filter '.mailleur-*.step').Count -eq 0) 'Supprimer seulement le fichier temporaire cree.'
    $app=New-FakeApplication
    $app.WrongDocument=$true
    Expect-Failure {Invoke-MailleurSolidWorksToStep -Source $part -Destination $output -ApplicationFactory ({$app}.GetNewClosure())} 'document retourne'
    Assert-True ($app.Closed.Count -eq 0) 'Ne pas fermer un document non possede.'
    $app=New-FakeApplication
    $app.InvalidStep=$true
    Expect-Failure {Invoke-MailleurSolidWorksToStep -Source $part -Destination $output -ApplicationFactory ({$app}.GetNewClosure())} 'Signature STEP'
    Assert-True (-not [IO.File]::Exists($output)) 'Refuser une signature de sortie invalide.'
    [IO.File]::WriteAllText($output,'keep')
    $app=New-FakeApplication
    Expect-Failure {Invoke-MailleurSolidWorksToStep -Source $part -Destination $output -ApplicationFactory ({$app}.GetNewClosure())} 'existe deja'
    Assert-True ([IO.File]::ReadAllText($output) -eq 'keep' -and $app.OpenCalls -eq 0) 'Refuser l''ecrasement avant COM.'
    Write-Output '9 scenarios de protocole reussis avec doubles ; aucune conversion SOLIDWORKS reelle testee.'
} finally {
    [IO.Directory]::Delete($folder,$true)
}
