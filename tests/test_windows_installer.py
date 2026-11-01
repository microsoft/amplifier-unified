"""Portable source guards plus real PowerShell fixtures when available.

Source guards are not native installation qualification. The executable fixture
is skipped, not simulated, on hosts without native Windows PowerShell.
"""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/install.ps1"
FIXTURE = ROOT / "tests/windows/install.Tests.ps1"


class WindowsInstallerSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def function(self, name):
        match = re.search(
            rf"(?ms)^function {re.escape(name)}\b.*?(?=^function |\Z)",
            self.source,
        )
        self.assertIsNotNone(match, name)
        return match.group()

    def test_review_first_parameters_and_ps51_syntax_baseline(self):
        self.assertTrue(self.source.startswith("#requires -Version 5.1"))
        for parameter in ("$Version = 'latest'", "[switch] $NoOpen", "[switch] $Anonymous"):
            self.assertIn(parameter, self.source)
        self.assertIn("[CmdletBinding()]", self.source)  # builtin -Verbose
        self.assertIn("Do not change execution policy", self.source)
        self.assertNotIn("??", self.source)
        self.assertNotIn("ForEach-Object -Parallel", self.source)

    def test_actual_os_and_processor_not_shell_environment(self):
        function = self.function("Assert-NativePlatform")
        for required in (
            "Win32_OperatingSystem", "Win32_Processor", "$os.ProductType -ne 1",
            "$architectures[0] -ne 9", "[Environment]::Is64BitProcess", "22000",
        ):
            self.assertIn(required, function)
        self.assertNotIn("PROCESSOR_ARCHITECTURE", function)

    def test_existing_release_rejected_before_helpers_or_package_mutation(self):
        main = self.function("Invoke-UnifiedInstall")
        refusal = main.index("has no native Windows support record")
        for effect in ("Find-InstallHelper", "Install-VerifiedHelper", "'tool', 'install'"):
            self.assertLess(refusal, main.index(effect))
        self.assertEqual(main.count('Get-BoundedDownload "$api/$selector"'), 1)
        self.assertIn("$release.prerelease -ne $false", main)
        self.assertIn("$release.draft -ne $false", main)
        self.assertIn("Get-ExactChecksum $sums $supportName", main)

    def test_activation_handoff_is_closed_without_force_bypass(self):
        gate = self.function("Assert-NativeActivationContract")
        self.assertIn("throw 'Native activation integration is pending", gate)
        self.assertNotIn("return", gate)
        main = self.function("Invoke-UnifiedInstall")
        self.assertLess(main.index("Assert-NativeActivationContract"), main.index("'tool', 'install'"))
        self.assertNotIn("$env:AMPLIFIER_FORCE", self.source)

    def test_checksum_parser_demands_exact_unique_names(self):
        parser = self.function("Get-ExactChecksum")
        self.assertIn("$Matches[2] -ceq $Name", parser)
        self.assertIn("$matchesForName.Count -ne 1", parser)
        self.assertIn("Malformed SHA256SUMS", parser)
        self.assertIn("Get-FileHash -LiteralPath $Path -Algorithm SHA256", self.source)

    def test_downloads_stream_with_explicit_size_time_redirect_tls_limits(self):
        download = self.function("Get-BoundedDownload")
        for required in (
            "Invoke-WebRequest", "-UseBasicParsing", "-TimeoutSec 30",
            "$attempt -lt 2", "$redirect -le 5", "$current.Scheme -ne 'https'",
            "$current.UserInfo", "$allowedHosts -notcontains",
            "$request.AllowAutoRedirect = $false", "$request.ReadWriteTimeout = 30000",
            "$total -gt $MaxBytes", "$clock.Elapsed.TotalSeconds -gt 180",
            "[IO.FileMode]::CreateNew", "$firstFailure.Exception.GetType().Name",
        ):
            self.assertIn(required, download)
        self.assertNotIn("-SkipCertificateCheck", self.source)
        self.assertNotIn("ServerCertificateValidationCallback", self.source)

    def test_archives_prevalidate_all_paths_before_extraction(self):
        unzip = self.function("Expand-ContainedZip")
        self.assertLess(unzip.index("$target.StartsWith"), unzip.index("::ExtractToFile"))
        for required in (
            "OrdinalIgnoreCase", "$seen.Add", "$unixType -eq 40960",
            "ExternalAttributes -band 1024", "536870912", "20000",
            "CON|PRN|AUX|NUL", "$script:InstallStorage.CreateDirectory($Destination)",
        ):
            self.assertIn(required, unzip)
        self.assertNotIn("Test-Path", unzip)
        self.assertNotIn("[IO.Directory]::CreateDirectory", unzip)

    def test_helpers_have_inspected_official_exact_assets_and_notices(self):
        helper = self.function("Install-VerifiedHelper")
        for value in (
            "https://github.com/astral-sh/uv/releases/download/0.12.18/uv-x86_64-pc-windows-msvc.zip",
            "cae6a3bc25239f83dffb467a4b180508d9da23986c04639ebfa44e43e6a84bff",
            "https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.4/MinGit-2.56.0.4-64-bit.zip",
            "e3bf78351fe777fba47ddee5950082f8a98bb0f02604fdc7ba3ee2cb68ffa877",
            "LICENSE.txt", "LICENSE-MIT", "LICENSE-APACHE", "bootstrap-receipt.json",
            "01cb90c1a4f88af09906cb60de9766d2add4a062",
        ):
            self.assertIn(value, helper)
        self.assertLess(helper.index("Assert-FileChecksum $archive"), helper.index("Expand-ContainedZip"))
        self.assertNotIn("winget", helper)

    def test_log_directory_acl_exists_at_exclusive_creation(self):
        private = self.function("Initialize-InstallStorage")
        self.assertIn('CreateDirectoryW(path, ref attributes)', private)
        self.assertIn('"D:P(A;OICI;FA;;;"', private)
        self.assertIn("info.attributes & 0x400", private)
        self.assertNotIn("Set-Acl", private)
        main = self.function("Invoke-UnifiedInstall")
        self.assertLess(main.index("New-PrivateInstallDirectory"), main.index("Write-PrivateInstallText"))

    def test_existing_ancestor_mutation_acl_and_handle_identity_are_checked(self):
        private = self.function("Initialize-InstallStorage")
        for required in (
            "GetSecurityInfo(pin.handle", "new RawSecurityDescriptor", "Foreign mutation",
            "const uint Mutation = 0x500D0156u", "Mutation & ~4u",
            "AceFlags.InheritOnly", "ControlFlags.DiscretionaryAclPresent",
            "GetFileInformationByHandle", "info.high != pin.high",
            "GetFinalPathNameByHandleW", "GetVolumeInformationByHandleW",
            'fs.ToString() != "NTFS"', "GetDriveTypeW", "fixed local NTFS",
            "mutable ? 3u : 1u", "0x02200000", "order[i].handle.Dispose()",
        ):
            self.assertIn(required, private)
        self.assertLess(private.index("parent = Address(parent); Traverse(parent)"),
                        private.index("CreateExact(Root)"))
        self.assertNotIn("Directory.CreateDirectory(", private)
        self.assertNotIn("SetAccessRule", private)

    def test_directory_pins_request_list_access_for_sharing_enforcement(self):
        private = self.function("Initialize-InstallStorage")
        self.assertIn(
            "CreateFileW(path, 0x20080u | (directory ? 1u : 0x80000000u)",
            private,
        )
        self.assertIn("mutable ? 3u : 1u", private)  # never share DELETE
        self.assertNotIn("directory ? 0u", private)  # metadata-only is not a pin

    def test_native_fixture_tags_refusals_and_checks_exact_private_owner(self):
        fixture = FIXTURE.read_text(encoding="utf-8")
        for tag in (
            "pinned-ancestor-rename", "pinned-private-root-rename",
            "hashed-file-append", "hashed-file-rename", "extraction-root-rename",
        ):
            self.assertIn(f"Assert-SharingRefused '{tag}'", fixture)
        self.assertNotRegex(fixture, r"Assert-SharingRefused\s+\{")
        self.assertIn("Fixture [$Case]", fixture)
        self.assertIn("$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value", fixture)
        self.assertIn("Import-Module Microsoft.PowerShell.Security -ErrorAction Stop", fixture)
        # Keep the successful post-dispose rename as the refusal's control.
        self.assertIn("finally { $pinned.Dispose() }", fixture)
        self.assertIn("[IO.Directory]::Move(($ancestor + '-replacement'), $ancestor)", fixture)

    def test_fixture_launcher_module_path_is_child_only_and_edition_specific(self):
        # Exercise the real launcher with only process execution mocked.
        inherited = {
            "PSModulePath": r"C:\fixture\PowerShell\7\Modules",
            "pSmOdUlEpAtH": r"C:\fixture\other-modules",
            "PATH": r"C:\fixture\bin",
            "FIXTURE_MARKER": "unchanged",
        }
        with mock.patch.object(os, "environ", inherited):
            before = dict(os.environ)
            for name in ("powershell.exe", "pwsh.exe"):
                with self.subTest(name=name), mock.patch.object(
                    shutil, "which", return_value=rf"C:\fixture\{name}",
                ), mock.patch.object(subprocess, "run") as run:
                    run.return_value = subprocess.CompletedProcess(
                        [], 0, "OFFLINE FIXTURE PASS", "",
                    )
                    case = NativePowerShellInstallerTests(
                        "test_offline_fixture_windows_powershell_51",
                    )
                    case.run_fixture(name)
                expected = {
                    key: value for key, value in before.items()
                    if name != "powershell.exe" or key.upper() != "PSMODULEPATH"
                }
                self.assertIn("env", run.call_args.kwargs)
                self.assertEqual(run.call_args.kwargs["env"], expected)
                self.assertEqual(dict(os.environ), before)
                self.assertNotIn("-ExecutionPolicy", run.call_args.args[0])

    def test_pins_cover_writes_hashes_extraction_and_existing_executable_probes(self):
        writer = self.function("Write-PrivateInstallText")
        self.assertLess(writer.index("AssertOwnedPath"), writer.index("[IO.File]::Open"))
        self.assertIn("[IO.FileMode]::CreateNew", writer)
        download = self.function("Get-BoundedDownload")
        self.assertIn("$script:InstallStorage.Validate()", download)
        self.assertIn("HoldFile($Destination, $false)", download)
        checksum = self.function("Assert-FileChecksum")
        self.assertLess(checksum.index("HoldFile"), checksum.index("Get-FileHash"))
        child = self.function("Invoke-InstallChild")
        self.assertLess(child.index("TrustExecutable($Executable)"), child.index("$process.Start()"))
        main = self.function("Invoke-UnifiedInstall")
        self.assertLess(main.index("stopped. $safe"), main.index("$script:InstallStorage.Dispose()"))
        self.assertIn("$script:InstallStorage.Dispose()", main[main.index("} finally {"):])

    def test_printed_launcher_uses_powershell_not_crt_quoting(self):
        formatter = self.function("Format-PowerShellLaunchCommand")
        self.assertIn('$Executable.Replace("\'", "\'\'")', formatter)
        self.assertNotIn("ConvertTo-NativeArgument", formatter)
        main = self.function("Invoke-UnifiedInstall")
        self.assertIn("Format-PowerShellLaunchCommand $launcher", main)
        fixture = FIXTURE.read_text(encoding="utf-8")
        for marker in (
            "O'Brien", "Parser]::ParseInput", "CommandElements[0].Value",
            "DeleteSubdirectoriesAndFiles", "WriteAttributes", "TakeOwnership",
            "ReadAndExecute", "Assert-SharingRefused", "never adopted",
            "CreateJunction", "outside remains unchanged", "shadow.exe",
        ):
            self.assertIn(marker, fixture)

    def test_child_only_anonymous_env_and_no_output_or_browser_token_logging(self):
        child = self.function("Invoke-InstallChild")
        for key in (
            "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT",
            "credential.helper", "core.askPass", "GH_TOKEN", "GITHUB_TOKEN",
        ):
            self.assertIn(key, child)
        self.assertIn("$info.EnvironmentVariables", child)
        self.assertIn("$info.UseShellExecute = $false", child)
        self.assertNotIn("SetEnvironmentVariable", self.source)
        self.assertNotRegex(self.source, r"\$env:\w+\s*=(?!=)")
        self.assertNotIn("control-token", self.source)
        self.assertNotIn("Write-Host $output", child)
        self.assertNotIn("AppendAllText($script:InstallLog, $output", child)

    def test_install_selects_managed_ordinary_313_without_native_fallback(self):
        main = self.function("Invoke-UnifiedInstall")
        self.assertIn("'python', 'install', '3.13', '--no-bin', '--no-registry'", main)
        self.assertIn("'--managed-python', '3.13'", main)
        self.assertIn('not sysconfig.get_config_var("Py_GIL_DISABLED")', main)
        self.assertIn("'--no-build-package', $name", main)
        self.assertIn("@($support.nativePackages)", main)
        self.assertNotRegex(main, r"'--no-build'\s*[,)]")
        self.assertEqual(main.count("'tool', 'install'"), 1)
        self.assertNotIn("ReadToEndAsync", main)

    def test_lifecycle_uses_current_cli_without_replacing_unmanaged_task(self):
        main = self.function("Invoke-UnifiedInstall")
        self.assertIn("@('--no-open', 'service', 'install')", main)
        self.assertIn("@('--no-open', 'service', 'status', '--wait', '120', '--json')", main)
        self.assertIn("@('doctor', '--json')", main)
        self.assertIn("if (-not $SkipOpen)", main)
        self.assertNotIn("'--replace'", main)
        self.assertNotIn("Register-ScheduledTask", self.source)
        self.assertNotIn("Start-Process", self.source)


@unittest.skipUnless(sys.platform == "win32", "native Windows PowerShell fixtures pending")
class NativePowerShellInstallerTests(unittest.TestCase):
    def run_fixture(self, name):
        shell = shutil.which(name)
        if not shell:
            self.skipTest(f"{name} unavailable; no native pass claimed")
        # pwsh -> Python -> powershell.exe does not get pwsh's direct-child
        # PSModulePath sanitization. Let 5.1 rebuild its own defaults; leave the
        # parent process and the PowerShell 7 fixture's environment unchanged.
        child_env = {
            key: value for key, value in os.environ.items()
            if name != "powershell.exe" or key.upper() != "PSMODULEPATH"
        }
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(FIXTURE),
             "-PythonExecutable", sys.executable],
            cwd=ROOT, text=True, capture_output=True, timeout=90,
            env=child_env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OFFLINE FIXTURE PASS", result.stdout)

    def test_offline_fixture_windows_powershell_51(self):
        self.run_fixture("powershell.exe")

    def test_offline_fixture_powershell_7(self):
        self.run_fixture("pwsh.exe")


if __name__ == "__main__":
    unittest.main()