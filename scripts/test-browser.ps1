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
    [ValidateSet('all', 'chrome', 'firefox')]
    [string]$Browser = 'all',
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

$env:PYTHONPATH = "$wheelExtractDirectory;$root"
$env:FFL_DATACHANNEL_BROWSER = $Browser
& python -m unittest discover -s tests -p 'browser_interop_unittest.py' -v
if ($LASTEXITCODE -ne 0) {
    throw "Browser interoperability unittest suite failed"
}

Write-Host "[PASS] ffl-datachannel browser interoperability suite completed."
