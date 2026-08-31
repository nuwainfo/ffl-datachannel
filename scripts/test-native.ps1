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
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$wheelExtractDirectory = Join-Path $root "out\native\wheel-extract"

if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot 'build-native.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw "Native wheel build failed"
    }
}

$extension = Get-ChildItem -LiteralPath $wheelExtractDirectory -Recurse -Filter '_ffl_datachannel*.pyd' |
    Select-Object -First 1
if (-not $extension) {
    throw "The native wheel extension is missing; run scripts\\build-native.ps1 first"
}

$env:PYTHONPATH = "$wheelExtractDirectory;$root"
& python -m unittest discover -s tests -p 'native_unittest.py' -v
if ($LASTEXITCODE -ne 0) {
    throw "Native unittest suite failed"
}

Write-Host "[PASS] Native ffl-datachannel unittest suite completed."
