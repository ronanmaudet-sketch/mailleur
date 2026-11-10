#requires -Version 5.1
<#
.SYNOPSIS
Passerelle conditionnelle SOLIDWORKS Windows COM vers un fichier STEP.
.DESCRIPTION
Requiert SOLIDWORKS desktop avec licence et API COM, PowerShell 64 bits STA.
Les API sont documentees par SOLIDWORKS ; cette passerelle n'a pas ete essayee
sur une installation SOLIDWORKS reelle. Voir docs/IMPORTS_NATIFS.md.
Ne reutilise pas de session contenant des documents, ne sauvegarde jamais
la source et ne ferme que le document dont elle a verifie le chemin.
#>
[CmdletBinding()]
param(
    [string]$InputPath,
    [string]$OutputPath,
    [string]$Configuration = '',
    [switch]$AllowSuppressedComponents
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Release-MailleurComObject {
    param($Value)
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($Value)
    }
}

function Invoke-MailleurSolidWorksToStep {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string]$ConfigurationName = '',
        [switch]$PermitSuppressed,
        # Injection uniquement pour le test du protocole ; le lanceur utilise COM.
        [scriptblock]$ApplicationFactory = { New-Object -ComObject SldWorks.Application }
    )

    $sourceItem = Get-Item -LiteralPath $Source -ErrorAction Stop
    if ($sourceItem.PSIsContainer) { throw 'La source doit etre un fichier.' }
    $sourceFull = [IO.Path]::GetFullPath($sourceItem.FullName)
    $extension = [IO.Path]::GetExtension($sourceFull).ToLowerInvariant()
    if ($extension -notin @('.sldprt', '.sldasm')) { throw 'Source attendue : .sldprt ou .sldasm.' }
    $destinationFull = [IO.Path]::GetFullPath($Destination)
    if ([IO.Path]::GetExtension($destinationFull).ToLowerInvariant() -notin @('.step', '.stp')) {
        throw 'La destination doit porter une extension .step ou .stp.'
    }
    if (Test-Path -LiteralPath $destinationFull) { throw 'La destination existe deja ; aucun ecrasement autorise.' }
    $parent = [IO.Path]::GetDirectoryName($destinationFull)
    [void][IO.Directory]::CreateDirectory($parent)
    $stage = Join-Path $parent ('.mailleur-' + [Guid]::NewGuid().ToString('N') + '.step')
    $app = $null
    $model = $null
    $active = $null
    $activated = $null
    $config = $null
    $extensionObject = $null
    $components = @()
    $openedHere = $false
    $completed = $false
    $result = $null
    $oldWorkingDirectory = $null
    $workingDirectoryChanged = $false
    try {
        $app = & $ApplicationFactory
        if ($null -eq $app) { throw 'Creation de la session SOLIDWORKS impossible.' }
        # New-Object peut rencontrer une instance COM deja presente : une session
        # sans document est une precondition, pas une preuve d'instance privee.
        if ([int]$app.GetDocumentCount() -ne 0) {
            throw 'La session SOLIDWORKS contient deja des documents. Fermer vos travaux puis recommencer.'
        }
        $existing = $app.GetOpenDocumentByName($sourceFull)
        if ($null -ne $existing) {
            Release-MailleurComObject $existing
            throw 'La source est deja ouverte ; elle ne sera ni exportee ni fermee par cette passerelle.'
        }
        # OpenDoc6 ne place pas le repertoire courant dans celui de l'assemblage.
        $oldWorkingDirectory = [string]$app.GetCurrentWorkingDirectory()
        if (-not $oldWorkingDirectory) { throw 'Repertoire courant SOLIDWORKS indisponible ; restauration impossible.' }
        if (-not [bool]$app.SetCurrentWorkingDirectory([IO.Path]::GetDirectoryName($sourceFull))) {
            throw 'Impossible de preparer le repertoire des references.'
        }
        $workingDirectoryChanged = $true
        [int]$loadErrors = 0
        [int]$loadWarnings = 0
        $documentType = 1
        if ($extension -eq '.sldasm') { $documentType = 2 }
        # Silent=1, ReadOnly=2, OverrideDefaultLoadLightweight=64 ; sans LoadLightweight.
        $model = $app.OpenDoc6($sourceFull, $documentType, 67, $ConfigurationName,
                              [ref]$loadErrors, [ref]$loadWarnings)
        if ($null -ne $model) {
            $openedPath = [IO.Path]::GetFullPath([string]$model.GetPathName())
            if (-not [StringComparer]::OrdinalIgnoreCase.Equals($openedPath, $sourceFull)) {
                throw 'Le document retourne ne correspond pas a la source ; aucune fermeture automatique.'
            }
            $openedHere = $true
        }
        if ($null -eq $model -or $loadErrors -ne 0 -or $loadWarnings -ne 0) {
            throw "Ouverture incomplete ou signalee : errors=$loadErrors warnings=$loadWarnings. Verifier les references et reconstruire dans SOLIDWORKS."
        }
        $config = $model.ConfigurationManager.ActiveConfiguration
        if ($null -eq $config) { throw 'Configuration active indisponible.' }
        $configurationUsed = [string]$config.Name
        if ($ConfigurationName -and $configurationUsed -cne $ConfigurationName) {
            throw 'La configuration demandee n''est pas celle ouverte.'
        }
        $model.Visible = $true
        [int]$activationErrors = 0
        # swDontRebuildActiveDoc=1 : une reconstruction requise est signalee,
        # jamais resolue en changeant silencieusement la piece sauvegardee.
        $activated = $app.ActivateDoc3([string]$model.GetTitle(), $false, 1, [ref]$activationErrors)
        if ($null -eq $activated -or $activationErrors -ne 0) {
            throw "Activation refusee ou reconstruction necessaire : status=$activationErrors."
        }
        $active = $app.ActiveDoc
        if ($null -eq $active) { throw 'Aucun document actif apres activation.' }
        if (-not [StringComparer]::OrdinalIgnoreCase.Equals(
            [IO.Path]::GetFullPath([string]$active.GetPathName()), $sourceFull)) {
            throw 'Le document actif ne correspond pas a la source.'
        }
        $suppressedNames = [Collections.Generic.List[string]]::new()
        [int]$componentCount = 0
        [int]$resolveStatus = 0
        if ($documentType -eq 2) {
            if ([bool]$config.IsSpeedPak()) { throw 'La configuration SpeedPak ne constitue pas un assemblage complet.' }
            $resolveStatus = [int]$model.ResolveAllLightWeightComponents($false)
            if ($resolveStatus -ne 0 -or [int]$model.GetLightWeightComponentCount() -ne 0) {
                throw "Des composants restent non resolus : resolve_status=$resolveStatus."
            }
            $rawComponents = $model.GetComponents($false)
            if ($null -ne $rawComponents) { $components = @($rawComponents) }
            $componentCount = $components.Count
            if ($componentCount -eq 0) { throw 'Assemblage sans composant inspectable.' }
            foreach ($component in $components) {
                if ($null -eq $component) { throw 'Composant absent dans la liste d''assemblage.' }
                $name = [string]$component.Name2
                $state = [int]$component.GetSuppression2()
                if ($state -eq 0) {
                    [void]$suppressedNames.Add($name)
                    continue
                }
                # FullyResolved=2 / Resolved=3 ; 1/4=lightweight, 5=ID mismatch.
                if ($state -notin @(2, 3)) { throw "Composant non resolu ou incoherent : $name (state=$state)." }
                if ([bool]$component.IsSpeedPak) { throw "Composant SpeedPak : $name." }
                $reference = $component.GetModelDoc2()
                if ($null -eq $reference) { throw "Reference non chargee : $name." }
                Release-MailleurComObject $reference
            }
            if ($suppressedNames.Count -gt 0 -and -not $PermitSuppressed) {
                throw "L'assemblage contient $($suppressedNames.Count) composant(s) supprime(s) dans cette configuration. Export partiel refuse ; choisir une configuration complete ou autoriser explicitement ces exclusions."
            }
        }
        [void]$model.ClearSelection2($true)
        $extensionObject = $model.Extension
        [int]$saveErrors = 0
        [int]$saveWarnings = 0
        # SaveAsCurrentVersion=0, SaveAsOptions_Silent=1. L'extension .step
        # demande la traduction ; aucun Save3 ni enregistrement natif n'est fait.
        $saved = [bool]$extensionObject.SaveAs($stage, 0, 1, $null,
                                             [ref]$saveErrors, [ref]$saveWarnings)
        if (-not $saved -or $saveErrors -ne 0 -or $saveWarnings -ne 0) {
            throw "Export STEP refuse ou signale : saved=$saved errors=$saveErrors warnings=$saveWarnings."
        }
        if (-not [IO.File]::Exists($stage) -or ([IO.FileInfo]$stage).Length -lt 64) {
            throw 'SOLIDWORKS n''a pas produit de fichier STEP exploitable.'
        }
        $reader = [IO.StreamReader]::new($stage, [Text.Encoding]::ASCII)
        try {
            $buffer = New-Object char[] 256
            $read = $reader.Read($buffer, 0, $buffer.Length)
            $header = (-join $buffer[0..($read - 1)]).TrimStart()
            if (-not $header.StartsWith('ISO-10303-21;', [StringComparison]::Ordinal)) {
                throw 'Signature STEP ISO-10303-21 absente.'
            }
        } finally { $reader.Dispose() }
        $revisionUsed = [string]$app.RevisionNumber()
        # File.Move a deux arguments ne remplace jamais une destination existante.
        [IO.File]::Move($stage, $destinationFull)
        $completed = $true
        $result = [ordered]@{
            schema = 'atelier.converter.solidworks.v1'
            status = 'converted'
            converter = 'SOLIDWORKS COM / IModelDocExtension.SaveAs'
            solidworks_revision = $revisionUsed
            input = $sourceFull
            output = $destinationFull
            output_bytes = ([IO.FileInfo]$destinationFull).Length
            configuration = $configurationUsed
            source_read_only = $true
            assembly_components_inspected = $componentCount
            suppressed_components_excluded = $suppressedNames.Count
            suppressed_names_sample = @($suppressedNames | Select-Object -First 20)
            suppressed_exclusions_explicitly_allowed = [bool]$PermitSuppressed
            load_errors = $loadErrors
            load_warnings = $loadWarnings
            save_errors = $saveErrors
            save_warnings = $saveWarnings
        }
    } finally {
        if ($openedHere -and $null -ne $model -and $null -ne $app) {
            try {
                $closingPath = [IO.Path]::GetFullPath([string]$model.GetPathName())
                if ([StringComparer]::OrdinalIgnoreCase.Equals($closingPath, $sourceFull) -or
                    [StringComparer]::OrdinalIgnoreCase.Equals($closingPath, $stage)) {
                    [void]$app.CloseDoc([string]$model.GetTitle())
                } else {
                    [Console]::Error.WriteLine('Nettoyage : chemin du document change ; document laisse ouvert.')
                }
            } catch { [Console]::Error.WriteLine('Nettoyage SOLIDWORKS : ' + $_.Exception.Message) }
        }
        if ($workingDirectoryChanged -and $null -ne $app) {
            try {
                if (-not [bool]$app.SetCurrentWorkingDirectory($oldWorkingDirectory)) {
                    [Console]::Error.WriteLine('Nettoyage : repertoire courant SOLIDWORKS non restaure.')
                }
            } catch { [Console]::Error.WriteLine('Restauration du repertoire SOLIDWORKS : ' + $_.Exception.Message) }
        }
        foreach ($component in $components) { Release-MailleurComObject $component }
        Release-MailleurComObject $extensionObject
        Release-MailleurComObject $config
        # ActiveDoc et model peuvent partager un RCW : ne pas FinalRelease.
        if (-not [object]::ReferenceEquals($active, $model)) { Release-MailleurComObject $active }
        if (-not [object]::ReferenceEquals($activated, $model) -and
            -not [object]::ReferenceEquals($activated, $active)) { Release-MailleurComObject $activated }
        Release-MailleurComObject $model
        Release-MailleurComObject $app
        if (-not $completed -and [IO.File]::Exists($stage)) { [IO.File]::Delete($stage) }
        # Aucun CloseAllDocuments, ExitApp ou arret de processus utilisateur.
    }
    return $result
}

# Le dot-sourcing expose la fonction aux tests de protocole sans lancer COM.
if ($MyInvocation.InvocationName -ne '.') {
    $mutex = $null
    $locked = $false
    try {
        if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Passerelle SOLIDWORKS COM disponible uniquement sous Windows.' }
        if (-not [Environment]::Is64BitProcess) { throw 'Utiliser Windows PowerShell 64 bits.' }
        if ([Threading.Thread]::CurrentThread.ApartmentState -ne [Threading.ApartmentState]::STA) { throw 'Lancer PowerShell avec -STA.' }
        if (-not $InputPath -or -not $OutputPath) { throw 'Arguments obligatoires : -InputPath SOURCE -OutputPath RESULTAT.step.' }
        $mutex = [Threading.Mutex]::new($false, 'Local\MailleurSolidWorksToStep')
        $locked = $mutex.WaitOne(0)
        if (-not $locked) { throw 'Une conversion SOLIDWORKS Mailleur est deja en cours.' }
        $report = Invoke-MailleurSolidWorksToStep -Source $InputPath -Destination $OutputPath `
            -ConfigurationName $Configuration -PermitSuppressed:$AllowSuppressedComponents
        $report | ConvertTo-Json -Depth 6 -Compress
        exit 0
    } catch {
        [Console]::Error.WriteLine((@{schema='atelier.converter.solidworks.v1';status='failed';error=$_.Exception.Message} | ConvertTo-Json -Compress))
        exit 1
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        if ($null -ne $mutex) { $mutex.Dispose() }
    }
}
