#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
#
# FastFileLink CLI - Fast, no-fuss file sharing
# Copyright (C) 2025-2026 FastFileLink contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

function Import-VSDeveloperEnvironment {
    # Running VsDevCmd.bat from an already initialized Developer PowerShell
    # appends the VS paths a second time.  On Windows this can push PATH past
    # cmd.exe's command-line limit and produces misleading "input line too
    # long" / syntax errors.  Reuse an existing x64 toolchain instead.
    if (
        $env:VSCMD_VER -and
        $env:VSCMD_ARG_TGT_ARCH -eq 'x64' -and
        $env:VSCMD_ARG_HOST_ARCH -eq 'x64' -and
        (Get-Command cl.exe -ErrorAction SilentlyContinue)
    ) {
        Write-Host "Using existing Visual Studio x64 developer environment"
        return
    }

    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) {
        throw "Visual Studio Installer was not found: $vswhere"
    }

    $installationPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $installationPath) {
        throw "Visual Studio with C++ build tools was not found"
    }

    $vsDevCmd = Join-Path $installationPath "Common7\Tools\VsDevCmd.bat"
    if (-not (Test-Path $vsDevCmd)) {
        throw "VsDevCmd.bat was not found: $vsDevCmd"
    }

    cmd.exe /c "`"$vsDevCmd`" -arch=x64 -host_arch=x64 >nul && set" |
        ForEach-Object {
            $name, $value = $_ -split '=', 2
            if ($name -and $null -ne $value) {
                Set-Item -Path "Env:$name" -Value $value
            }
        }
}

function Assert-StaticThirdPartyLinkage([string]$extensionPath) {
    $dependencies = & dumpbin.exe /DEPENDENTS $extensionPath
    $forbiddenDependencies = @(
        'datachannel.dll', 'juice.dll', 'usrsctp.dll', 'mbedtls.dll',
        'mbedcrypto.dll', 'mbedx509.dll'
    )
    $unexpectedDependencies = $forbiddenDependencies | Where-Object {
        $dependencies -match [regex]::Escape($_)
    }

    $dependencies
    if ($unexpectedDependencies) {
        throw "The extension has dynamically linked third-party dependencies: $($unexpectedDependencies -join ', ')"
    }
}

function Invoke-GitApplyCheck(
    [string]$repository,
    [string]$patchPath,
    [switch]$Reverse
) {
    # A failed --check is an expected probe result, not a PowerShell error.
    # Windows PowerShell can promote native stderr to an ErrorRecord when
    # $ErrorActionPreference is "Stop", so suppress all output here and make
    # the decision solely from git.exe's exit code.
    $savedErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        if ($Reverse) {
            & git.exe -C $repository apply --reverse --check $patchPath *> $null
        } else {
            & git.exe -C $repository apply --check $patchPath *> $null
        }
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $savedErrorActionPreference
    }
}

function Apply-DependencyPatch([string]$repository, [string]$patchPath) {
    if (-not (Test-Path -LiteralPath $repository)) {
        throw "Dependency repository was not found: $repository"
    }
    if (-not (Test-Path -LiteralPath $patchPath)) {
        throw "Dependency patch was not found: $patchPath"
    }

    $reverseCheck = Invoke-GitApplyCheck $repository $patchPath -Reverse
    if ($reverseCheck -eq 0) {
        Write-Host "$patchPath patch already applied"
        return
    }

    $forwardCheck = Invoke-GitApplyCheck $repository $patchPath
    if ($forwardCheck -ne 0) {
        # Re-run visibly so git prints the exact hunk/context that failed.
        $savedErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & git.exe -C $repository apply --check $patchPath
            $visibleExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $savedErrorActionPreference
        }

        throw "Dependency patch does not apply cleanly (git exit $visibleExitCode): $patchPath"
    }

    $savedErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & git.exe -C $repository apply $patchPath
        $applyExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $savedErrorActionPreference
    }

    if ($applyExitCode -ne 0) {
        throw "Unable to apply dependency patch (git exit $applyExitCode): $patchPath"
    }

    Write-Host "$patchPath patch applied"
}

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$outDirectory = Join-Path $root "out\native"
$prefixDirectory = Join-Path $outDirectory "prefix"
$mbedBuildDirectory = Join-Path $outDirectory "mbedtls"
$wheelDirectory = Join-Path $outDirectory "wheel"
$wheelExtractDirectory = Join-Path $outDirectory "wheel-extract"
$libDataChannelCMake = Join-Path $root "third_party\libdatachannel\CMakeLists.txt"
$mbedTlsCMake = Join-Path $root "third_party\mbedtls\CMakeLists.txt"
$libDataChannelPatch = Join-Path $root "patches\libdatachannel_partial_send.patch"

Import-VSDeveloperEnvironment

if ($Clean -and (Test-Path $outDirectory)) {
    Remove-Item -LiteralPath $outDirectory -Recurse -Force
}

if (-not (Test-Path $libDataChannelCMake) -or -not (Test-Path $mbedTlsCMake)) {
    Write-Host "=== 1/5 Bootstrap pinned native dependencies ==="
    & python scripts\bootstrap.py
    if ($LASTEXITCODE -ne 0) {
        throw "Native dependency bootstrap failed"
    }
}

Apply-DependencyPatch (Join-Path $root "third_party\libdatachannel") $libDataChannelPatch

Write-Host "=== 2/5 Install wheel build requirements ==="
& python -c "import build, scikit_build_core"
if ($LASTEXITCODE -ne 0) {
    & python -m pip install --disable-pip-version-check --index-url https://pypi.org/simple build scikit-build-core
    if ($LASTEXITCODE -ne 0) {
        throw "Wheel build requirement installation failed"
    }
}

Write-Host "=== 3/5 Build static Mbed TLS ==="
$mbedConfig = Join-Path $root "third_party\mbedtls\include\mbedtls\mbedtls_config.h"
$mbedConfigContent = Get-Content $mbedConfig -Raw
$patchedMbedConfigContent = $mbedConfigContent -replace `
    '(?m)^\s*//\s*#define\s+MBEDTLS_SSL_DTLS_SRTP\s*$', `
    '#define MBEDTLS_SSL_DTLS_SRTP'
if ($patchedMbedConfigContent -ne $mbedConfigContent) {
    [System.IO.File]::WriteAllText($mbedConfig, $patchedMbedConfigContent, [System.Text.UTF8Encoding]::new($false))
}
if ((Get-Content $mbedConfig -Raw) -notmatch '(?m)^\s*#define\s+MBEDTLS_SSL_DTLS_SRTP\s*$') {
    throw "MBEDTLS_SSL_DTLS_SRTP is required for libdatachannel's Mbed TLS backend"
}

& cmake `
    -S (Join-Path $root 'third_party\mbedtls') `
    -B $mbedBuildDirectory `
    -G Ninja `
    -DCMAKE_BUILD_TYPE=Release `
    "-DCMAKE_INSTALL_PREFIX=$prefixDirectory" `
    -DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL `
    -DENABLE_PROGRAMS=OFF `
    -DENABLE_TESTING=OFF `
    -DUSE_STATIC_MBEDTLS_LIBRARY=ON `
    -DUSE_SHARED_MBEDTLS_LIBRARY=OFF
if ($LASTEXITCODE -ne 0) {
    throw "Mbed TLS configure failed"
}
& cmake --build $mbedBuildDirectory --target install --parallel
if ($LASTEXITCODE -ne 0) {
    throw "Mbed TLS build/install failed"
}

New-Item -ItemType Directory -Force -Path $wheelDirectory | Out-Null
$env:CMAKE_GENERATOR = 'Ninja'
$mbedTlsConfigDirectory = Join-Path $prefixDirectory 'lib\cmake\MbedTLS'

Write-Host "=== 4/5 Build native wheel ==="
& python -m build --wheel --no-isolation --outdir $wheelDirectory `
    "--config-setting=cmake.args=-DMbedTLS_DIR=$mbedTlsConfigDirectory"
if ($LASTEXITCODE -ne 0) {
    throw "Native wheel build failed"
}

$wheel = Get-ChildItem -LiteralPath $wheelDirectory -Filter 'ffl_datachannel-*.whl' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $wheel) {
    throw "Native wheel build completed without producing an ffl_datachannel wheel"
}

if (Test-Path $wheelExtractDirectory) {
    Remove-Item -LiteralPath $wheelExtractDirectory -Recurse -Force
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($wheel.FullName, $wheelExtractDirectory)
$extension = Get-ChildItem -LiteralPath $wheelExtractDirectory -Recurse -Filter '_ffl_datachannel*.pyd' |
    Select-Object -First 1
if (-not $extension) {
    throw "Built wheel does not contain the _ffl_datachannel extension"
}

Write-Host "=== 5/5 Verify static third-party linkage ==="
Assert-StaticThirdPartyLinkage $extension.FullName
Write-Host "[PASS] Native ffl-datachannel wheel build completed: $($wheel.FullName)"
