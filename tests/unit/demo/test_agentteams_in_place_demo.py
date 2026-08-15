"""Focused offline contracts for the in-place AgentTeams Demo driver.

These tests deliberately use only temporary files and a fake in-memory
Provider.  Importing the driver and running this suite must not start Docker,
open State/Postgres, send Matrix events, read the real Provider Secret, or use
the network.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import importlib.util
import inspect
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from threading import Barrier
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4


WORKSPACE = Path(__file__).resolve().parents[3]
DRIVER = WORKSPACE / "scripts" / "demo" / "agentteams_in_place_demo.py"
LIFECYCLE = WORKSPACE / "scripts" / "demo" / "Invoke-AgentTeamsInPlaceDemo.ps1"
PUBLIC_ENTRYPOINT = WORKSPACE / "run_demo.ps1"
OFFLINE_ENTRYPOINT = WORKSPACE / "verify_offline.ps1"
RELAY_HELPER = WORKSPACE / "scripts" / "demo" / "Start-AgentTeamsDemoHostRelay.ps1"
WORKER_ENTRYPOINT = (
    WORKSPACE / "infra" / "agentteams" / "m4" / "runtime" / "worker-entrypoint.sh"
)


def _windows_powershell() -> str | None:
    candidates = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe",
        Path(shutil.which("powershell.exe") or ""),
    )
    return next((str(path) for path in candidates if path.is_file()), None)


def _run_powershell_script(script: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    powershell = _windows_powershell()
    if powershell is None:
        raise unittest.SkipTest("Windows PowerShell 5.1 is unavailable")
    return subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *arguments,
        ],
        cwd=WORKSPACE,
        check=False,
        capture_output=True,
        text=False,
        timeout=20,
    )


def _load_driver():
    module_name = "awakening_test_agentteams_in_place_demo"
    spec = importlib.util.spec_from_file_location(
        module_name,
        DRIVER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("DEMO_TEST_DRIVER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


DEMO = _load_driver()


class _ThreeWayFakeProvider:
    provider_alias = "offline-fake-provider"

    def __init__(self) -> None:
        self.barrier = Barrier(3, timeout=5)
        self.call_count = 0

    def invoke(self, request):
        self.call_count += 1
        self.barrier.wait()
        return DEMO.ProviderResponse(
            provider_request_id=f"offline-{request.model_call_id}",
            output_document={"status": "ok"},
            skill_output_document={"status": "ok"},
            input_tokens=1,
            output_tokens=1,
            cost_microunits=1,
            response_sha256="0" * 64,
        )


def _request():
    call_id = str(uuid4())
    return DEMO.ProviderRequest(
        provider_alias="offline-fake-provider",
        model_id="offline-model",
        model_call_id=call_id,
        request_sha256="1" * 64,
        input_document={"offline": True},
    )


class AgentTeamsInPlaceDemoOfflineTests(unittest.TestCase):
    def test_m4_and_m5_authorizations_are_strictly_rejected(self) -> None:
        skeleton = {
            "authorization_id": "",
            "schema_version": DEMO.SCHEMA_VERSION,
            "provider": None,
            "parameters": None,
            "caps": None,
            "state_binding": None,
            "plans": None,
        }
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "live-gateway-config.json"
            for historical_authorization in ("AUTH-M4-001", "AUTH-M5-001"):
                with self.subTest(authorization=historical_authorization):
                    document = dict(skeleton)
                    document["authorization_id"] = historical_authorization
                    config_path.write_text(
                        json.dumps(document, separators=(",", ":")),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        DEMO.LiveRuntimeConfigurationError,
                        "^DEMO_LIVE_AUTHORIZATION_ID_INVALID$",
                    ):
                        DEMO._load_demo_live_config(config_path)

    def test_demo_secret_reader_requires_exact_neutral_path_and_field(self) -> None:
        synthetic_value = "offline-only-synthetic-key-1234567890"
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            expected = temporary_path / ".secrets" / "demo-provider.env"
            expected.parent.mkdir()
            other = temporary_path / "copied-secret.env"
            valid_line = f"{DEMO.PROVIDER_SECRET_FIELD}={synthetic_value}\n"
            expected.write_text(valid_line, encoding="utf-8")
            other.write_text(valid_line, encoding="utf-8")

            with patch.object(DEMO, "DEFAULT_PROVIDER_SECRET", expected):
                self.assertEqual(
                    synthetic_value,
                    DEMO._read_demo_provider_key(expected),
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "^DEMO_PROVIDER_SECRET_EXACT_PATH_REQUIRED$",
                ):
                    DEMO._read_demo_provider_key(other)

                # The legacy M5-only field is a negative regression fixture.
                expected.write_text(
                    f"AWAKENING_M5_PROVIDER_API_KEY={synthetic_value}\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "^DEMO_PROVIDER_SECRET_FIELDS_INVALID$",
                ):
                    DEMO._read_demo_provider_key(expected)

        self.assertEqual(
            "AWAKENING_DEMO_PROVIDER_API_KEY",
            DEMO.PROVIDER_SECRET_FIELD,
        )
        self.assertEqual(
            ".secrets/demo-provider.env",
            DEMO.DEFAULT_PROVIDER_SECRET.relative_to(DEMO.WORKSPACE).as_posix(),
        )
        self.assertFalse(hasattr(DEMO, "_read_m5_provider_key"))

    @unittest.skipUnless(os.name == "nt", "Windows directory-junction contract")
    def test_demo_secret_reader_rejects_parent_junction_before_read_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "reference-workspace"
            target = root / "outside-secret-directory"
            workspace.mkdir()
            target.mkdir()
            junction = workspace / ".secrets"
            create = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
                cwd=root,
                check=False,
                capture_output=True,
                text=False,
                timeout=10,
            )
            if create.returncode != 0 or not junction.exists():
                self.skipTest("directory junction creation is unavailable")
            try:
                secret = junction / "demo-provider.env"
                (target / "demo-provider.env").write_text(
                    f"{DEMO.PROVIDER_SECRET_FIELD}=offline-only-synthetic-key-1234567890\n",
                    encoding="utf-8",
                )
                with patch.object(DEMO, "DEFAULT_PROVIDER_SECRET", secret):
                    with patch.object(
                        Path,
                        "read_text",
                        side_effect=AssertionError("SECRET_READ_MUST_NOT_OCCUR"),
                    ):
                        with self.assertRaisesRegex(
                            ValueError,
                            "^DEMO_PROVIDER_SECRET_PARENT_DIRECTORY_INVALID$",
                        ):
                            DEMO._read_demo_provider_key(secret)
            finally:
                if junction.exists():
                    os.rmdir(junction)

    def test_reference_example_declares_secret_path_without_value_channel(
        self,
    ) -> None:
        document = json.loads(
            (WORKSPACE / "config" / "reference-runtime.example.json").read_text(
                encoding="utf-8"
            )
        )
        boundary = document["credential_boundary"]
        self.assertEqual(
            ".secrets/demo-provider.env",
            boundary["provider_secret_relative_path"],
        )
        self.assertEqual(
            "AWAKENING_DEMO_PROVIDER_API_KEY",
            boundary["provider_secret_field"],
        )
        self.assertFalse(boundary["credential_value_in_config"])
        self.assertFalse(boundary["provider_secret_value_in_argv"])
        self.assertFalse(boundary["provider_secret_value_in_process_environment"])
        self.assertFalse(boundary["provider_secret_value_logged"])
        template = (
            WORKSPACE / "config" / "demo-provider.env.example"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "AWAKENING_DEMO_PROVIDER_API_KEY=<operator-supplied-value>",
            template,
        )
        # Legacy internal identifiers must never become a public success path.
        self.assertNotIn("AWAKENING_M5_PROVIDER_API_KEY", template)
        self.assertIn(
            ".secrets/",
            (WORKSPACE / ".gitignore").read_text(encoding="utf-8"),
        )

    def test_demo_provider_resolver_rejects_fake_ip_and_non_public_addresses(self) -> None:
        invalid_values = (
            None,
            "",
            "198.18.0.0",
            "198.18.0.126",
            "198.19.255.255",
            "10.0.0.1",
            "127.0.0.1",
            "169.254.1.1",
            "224.0.0.1",
            "0.0.0.0",
            "2001:4860:4860::8888",
            "1.1.1.1,1.1.1.1",
            " 1.1.1.1",
            "1.1.1.1 ",
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError,
                    "^DEMO_PROVIDER_RESOLVED_IPV4_(?:REQUIRED|INVALID)$",
                ):
                    DEMO._parse_demo_provider_resolved_ipv4(value)

        self.assertEqual(
            ("1.1.1.1", "8.8.8.8"),
            DEMO._parse_demo_provider_resolved_ipv4("1.1.1.1,8.8.8.8"),
        )

    def test_demo_provider_resolver_is_exact_host_only_and_restores(self) -> None:
        delegated: list[tuple[object, ...]] = []

        def original_getaddrinfo(
            host,
            port,
            family=0,
            type=0,
            proto=0,
            flags=0,
        ):
            delegated.append((host, port, family, type, proto, flags))
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (host, port),
                )
            ]

        with patch.dict(
            os.environ,
            {DEMO.PROVIDER_RESOLVED_IPV4_ENV: "1.1.1.1,8.8.8.8"},
            clear=True,
        ):
            with patch.object(DEMO.socket, "getaddrinfo", original_getaddrinfo):
                binding = DEMO._install_demo_provider_dns_override(
                    DEMO.PROVIDER_ENDPOINT
                )
                restore, count = binding
                installed = DEMO.socket.getaddrinfo
                self.assertEqual(2, count)
                self.assertEqual(
                    DEMO.sha256(b"1.1.1.1,8.8.8.8").hexdigest(),
                    binding.binding_sha256,
                )

                provider_results = installed(
                    DEMO.PROVIDER_HOSTNAME,
                    443,
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                )
                other_results = installed(
                    "example.com",
                    443,
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                )
                bytes_results = installed(
                    DEMO.PROVIDER_HOSTNAME.encode("ascii"),
                    443,
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                )

                self.assertEqual(2, len(provider_results))
                self.assertEqual("example.com", other_results[0][4][0])
                self.assertEqual(
                    DEMO.PROVIDER_HOSTNAME.encode("ascii"),
                    bytes_results[0][4][0],
                )
                self.assertEqual(
                    ["1.1.1.1", "8.8.8.8", "example.com", b"dashscope.aliyuncs.com"],
                    [call[0] for call in delegated],
                )
                self.assertTrue(
                    all(
                        call[5] & socket.AI_NUMERICHOST
                        for call in delegated[:2]
                    )
                )

                restore()
                self.assertIs(DEMO.socket.getaddrinfo, original_getaddrinfo)

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                ValueError,
                "^DEMO_PROVIDER_RESOLVED_IPV4_REQUIRED$",
            ):
                DEMO._install_demo_provider_dns_override(DEMO.PROVIDER_ENDPOINT)

        for proxy_name in ("HTTP_PROXY", "https_proxy", "All_Proxy"):
            with self.subTest(proxy_name=proxy_name):
                with patch.dict(
                    os.environ,
                    {
                        DEMO.PROVIDER_RESOLVED_IPV4_ENV: "1.1.1.1",
                        proxy_name: "http://offline.invalid:1234",
                    },
                    clear=True,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "^DEMO_PROVIDER_PROXY_ENV_FORBIDDEN$",
                    ):
                        DEMO._install_demo_provider_dns_override(
                            DEMO.PROVIDER_ENDPOINT
                        )

    @unittest.skipUnless(os.name == "nt", "Windows entrypoint contract")
    def test_public_entrypoint_reference_path_failures_are_stable_and_pre_action(self) -> None:
        common = (
            "-Mode",
            "Preflight",
            "-IUnderstandThisUsesDockerAndNetwork",
            "-IUnderstandThisChangesReferenceState",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "missing-reference"
            ordinary_file = root / "reference-file"
            ordinary_file.write_bytes(b"not a workspace\n")
            cases = (
                (missing, b"REFERENCE_WORKSPACE_NOT_FOUND"),
                (ordinary_file, b"REFERENCE_WORKSPACE_NOT_DIRECTORY"),
                (WORKSPACE, b"PACKAGE_ROOT_IS_NOT_A_LIVE_REFERENCE_WORKSPACE"),
            )
            for reference, expected in cases:
                with self.subTest(reference=str(reference), expected=expected):
                    completed = _run_powershell_script(
                        PUBLIC_ENTRYPOINT,
                        *common,
                        "-ReferenceWorkspace",
                        str(reference),
                    )
                    combined = completed.stdout + completed.stderr
                    self.assertNotEqual(0, completed.returncode)
                    self.assertIn(expected, combined)
                    self.assertNotIn(b"REFERENCE_ACTION_BEGIN=", combined)
                    self.assertNotIn(b"REFERENCE_PROFILE=PASS", combined)
                    self.assertNotIn(b"PACKAGE_REFERENCE_PREFLIGHT=PASS", combined)

            target = root / "junction-target"
            junction = root / "junction-reference"
            target.mkdir()
            create = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
                cwd=root,
                check=False,
                capture_output=True,
                text=False,
                timeout=10,
            )
            if create.returncode != 0 or not junction.exists():
                self.skipTest("directory junction creation is unavailable")
            try:
                completed = _run_powershell_script(
                    PUBLIC_ENTRYPOINT,
                    *common,
                    "-ReferenceWorkspace",
                    str(junction),
                )
                combined = completed.stdout + completed.stderr
                self.assertNotEqual(0, completed.returncode)
                self.assertIn(b"REFERENCE_WORKSPACE_REPARSE_POINT_DENIED", combined)
                self.assertNotIn(b"REFERENCE_ACTION_BEGIN=", combined)
                self.assertNotIn(b"REFERENCE_PROFILE=PASS", combined)
            finally:
                if junction.exists():
                    os.rmdir(junction)

    @unittest.skipUnless(os.name == "nt", "Windows entrypoint contract")
    def test_offline_entrypoint_gates_python_and_dependencies_before_total_pass(self) -> None:
        source = OFFLINE_ENTRYPOINT.read_text(encoding="utf-8")
        dependency_gate = source.index(
            'Write-Output "OFFLINE_VERIFY_DEPENDENCY_PREFLIGHT=PASS"'
        )
        payload_verifier = source.index(
            '"-I", "-B", $verifier, "--package-root", $packageRoot'
        )
        total_pass = source.index('Write-Output "PACKAGE_OFFLINE_VERIFY=PASS"')
        self.assertLess(dependency_gate, payload_verifier)
        self.assertLess(payload_verifier, total_pass)
        self.assertIn('[ValidateSet("Full", "Stdlib", "PackageOnly")]', source)
        self.assertIn('if ($effectiveMode -ceq "Full") {', source)
        self.assertIn('$effectiveMode = "PackageOnly"', source)
        self.assertIn("tests/unit/demo", source)
        self.assertIn("tests/unit/m4", source)
        self.assertIn("sys.path.insert(0, str(root / 'src'))", source)
        self.assertIn("OFFLINE_VERIFY_PYTHON_PATH_INVALID", source)
        self.assertIn("OFFLINE_VERIFY_PYTHON_VERSION_UNSUPPORTED", source)
        self.assertIn("OFFLINE_VERIFY_DEPENDENCY_MISSING", source)
        self.assertIn("OFFLINE_VERIFY_DEPENDENCY_VERSION_MISMATCH", source)
        self.assertIn("OFFLINE_VERIFY_DEPENDENCY_IMPORT_FAILED", source)
        self.assertIn("py -3.12 --version", source)

        with tempfile.TemporaryDirectory() as temporary:
            missing_python = Path(temporary) / "missing-python.exe"
            completed = _run_powershell_script(
                OFFLINE_ENTRYPOINT,
                "-PythonPath",
                str(missing_python),
            )
            combined = completed.stdout + completed.stderr
            self.assertNotEqual(0, completed.returncode)
            self.assertIn(b"OFFLINE_VERIFY_PYTHON_PATH_INVALID", combined)
            self.assertNotIn(b"PACKAGE_PAYLOAD_VERIFY=PASS", combined)
            self.assertNotIn(b"PACKAGE_OFFLINE_VERIFY=PASS", combined)

        wrong_runtime = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"
        completed = _run_powershell_script(
            OFFLINE_ENTRYPOINT,
            "-PythonPath",
            str(wrong_runtime),
        )
        combined = completed.stdout + completed.stderr
        self.assertNotEqual(0, completed.returncode)
        self.assertIn(b"OFFLINE_VERIFY_PYTHON_VERSION_UNSUPPORTED", combined)
        self.assertIn(b"py -3.12 --version", combined)
        self.assertNotIn(b"PACKAGE_PAYLOAD_VERIFY=PASS", combined)
        self.assertNotIn(b"PACKAGE_OFFLINE_VERIFY=PASS", combined)

    def test_demo_budget_is_ten_yuan_per_worker_and_thirty_yuan_total(self) -> None:
        self.assertEqual(3, DEMO.CAPS["max_calls"])
        self.assertEqual(
            10_000_000,
            DEMO.CAPS["max_cost_microunits_per_call"],
        )
        self.assertEqual(
            30_000_000,
            DEMO.CAPS["max_total_cost_microunits"],
        )
        parsed = DEMO._parse_demo_live_caps(dict(DEMO.CAPS))
        self.assertEqual(dict(DEMO.CAPS), parsed.to_dict())

        changed = dict(DEMO.CAPS)
        changed["max_cost_microunits_per_call"] -= 1
        with self.assertRaisesRegex(
            DEMO.LiveRuntimeConfigurationError,
            "^DEMO_LIVE_CAP_VALUE_INVALID$",
        ):
            DEMO._parse_demo_live_caps(changed)

        run_id = str(uuid4())
        snapshot_id = str(uuid4())
        model_call_id = str(uuid4())
        runtime = DEMO.RuntimeConfigSpec(
            run_id=run_id,
            provider_alias=DEMO.PROVIDER_ALIAS,
            model_id=DEMO.MODEL_ID,
            parameters=DEMO.PARAMETERS,
            **dict(DEMO.CAPS),
        )
        budget = DEMO.ModelBudgetRequest(
            run_id=run_id,
            model_call_id=model_call_id,
            snapshot_id=snapshot_id,
            max_input_tokens=DEMO.CAPS["max_input_tokens_per_call"],
            max_output_tokens=DEMO.CAPS["max_output_tokens_per_call"],
            max_cost_microunits=DEMO.CAPS["max_cost_microunits_per_call"],
        )
        self.assertEqual(30_000_000, runtime.max_total_cost_microunits)
        self.assertEqual(10_000_000, budget.max_cost_microunits)

    def test_live_transport_gate_and_secret_journal_contract_are_fixed(self) -> None:
        source = LIFECYCLE.read_text(encoding="utf-8")
        driver_source = DRIVER.read_text(encoding="utf-8")
        preflight = source.index("$providerTransport = Get-DemoProviderTransportBinding")
        fresh_prepare = source.index('$coreCli, "prepare"')
        self.assertLess(preflight, fresh_prepare)
        start_live = source.index("function Invoke-StartLiveGateway")
        live_transport = source.index(
            "$providerTransport = Get-DemoProviderTransportBinding",
            start_live,
        )
        stop_fail_closed = source.index("Stop-M4FailClosedGateway", live_transport)
        start_core = source.index("Start-CoreLiveGateway -Paths $paths", stop_fail_closed)
        self.assertLess(live_transport, stop_fail_closed)
        self.assertLess(stop_fail_closed, start_core)

        self.assertGreaterEqual(source.count("-q --silent --show-error"), 2)
        self.assertNotIn("--location", source)
        self.assertIn("%{http_code}|%{ssl_verify_result}|%{remote_ip}", source)
        self.assertIn("-ceq $candidate", source)
        self.assertIn("reachable_ipv4 = [string[]]$reachableIPv4", source)
        self.assertIn("AWAKENING_DEMO_PROVIDER_RESOLVED_IPV4", source)
        self.assertIn('"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"', source)
        self.assertIn("Assert-DemoLiveGatewayMarkers", source)
        self.assertIn("Assert-DemoLiveGatewayRuntimeBinding -Paths $paths", source)
        self.assertIn('-SecretValueReadStatus "true"', source)
        self.assertIn("DEMO_PROVIDER_SECRET_READ_BY_GATEWAY=true", source)
        self.assertIn('.secrets\\demo-provider.env', source)
        # These are negative assertions for removed legacy runtime coupling.
        self.assertNotIn(".env.m5.provider", source)
        self.assertNotIn("AWAKENING_M5_PROVIDER_API_KEY", source)
        self.assertNotIn("DEMO_M5_SECRET", source)
        self.assertNotIn(".env.m5.provider", driver_source)
        self.assertNotIn("AWAKENING_M5_PROVIDER_API_KEY", driver_source)
        self.assertIn("provider_model_request_sent = $false", source)
        self.assertIn("single_use_plan_claim_count = 0", source)
        self.assertNotIn('header "Authorization:', source)
        self.assertIn('"demo-live-gateway.ready.json"', driver_source)
        self.assertIn("awakening.demo.live-gateway-ready.v1", driver_source)
        self.assertIn("provider_dns_override_sha256", driver_source)
        self.assertIn("single_use_plan_claim_count", driver_source)
        marker_reader = source.split(
            "function Assert-DemoLiveGatewayMarkers", 1
        )[1].split("function Get-DemoLiveGatewaySecretReadStatus", 1)[0]
        self.assertIn("$Paths.LiveReady", marker_reader)
        self.assertNotIn("$Paths.LiveStdout", marker_reader)
        self.assertIn("Move-DemoFailedLiveEvidenceForRetry", source)
        self.assertIn("awakening.demo.live-start-failure-archive.v1", source)
        self.assertIn('live_start_attempt = $LiveStartAttempt', source)

    def test_live_start_retry_topology_allows_exactly_three_attempts(self) -> None:
        source = LIFECYCLE.read_text(encoding="utf-8")
        archive_writer = source.split(
            "function Move-DemoFailedLiveEvidenceForRetry", 1
        )[1].split("function Read-DemoLiveRetryArchiveManifest", 1)[0]
        archive_reader = source.split(
            "function Read-DemoLiveRetryArchiveManifest", 1
        )[1].split("function Start-CoreLiveGateway", 1)[0]
        live_starter = source.split("function Start-CoreLiveGateway", 1)[1].split(
            "function Stop-CoreLiveGateway", 1
        )[0]
        runtime_binding = source.split(
            "function Assert-DemoLiveGatewayRuntimeBinding", 1
        )[1].split("function Read-Baseline", 1)[0]
        selector = source.split("function Invoke-StartLiveGateway", 1)[1].split(
            "function Read-HumanRequestBinding", 1
        )[0]
        run_chain = source.split("function Invoke-RunChain", 1)[1].split(
            "function Invoke-StopRestore", 1
        )[0]

        # A failed attempt may create retry-1 or retry-2 only; a third failed
        # start is terminal and cannot manufacture a retry-3 archive.
        self.assertIn("$nextRetryAttempt -notin @(1, 2)", archive_writer)
        self.assertIn(
            '("demo-live-start-retry-" + $nextRetryAttempt)', archive_writer
        )
        self.assertIn(
            "[ValidateRange(1, 2)][int]$RetryAttempt", archive_reader
        )
        self.assertIn(
            "[ValidateRange(1, 3)][int]$LiveStartAttempt = 1", live_starter
        )

        # Selector mapping is explicit: none -> 1, retry-1 -> 2, and
        # retry-1 + retry-2 -> 3.  retry-2 alone or any extra retry archive
        # fails closed before the live process is started.
        self.assertIn(
            '"demo-live-start-retry-1\\archive-manifest.json"', selector
        )
        self.assertIn(
            '"demo-live-start-retry-2\\archive-manifest.json"', selector
        )
        self.assertIn(
            "($retryTwoPresent -and -not $retryOnePresent)", selector
        )
        self.assertIn(
            "$expectedRetryDirectoryCount = "
            "[int]$retryOnePresent + [int]$retryTwoPresent",
            selector,
        )
        self.assertIn(
            '$_.Name -notin @("demo-live-start-retry-1", '
            '"demo-live-start-retry-2")',
            selector,
        )
        self.assertIn(
            'throw "DEMO_LIVE_RETRY_ARCHIVE_TOPOLOGY_INVALID"', selector
        )
        self.assertIn(
            "Read-DemoLiveRetryArchiveManifest -Paths $paths -RetryAttempt 1",
            selector,
        )
        self.assertIn(
            "Read-DemoLiveRetryArchiveManifest -Paths $paths -RetryAttempt 2",
            selector,
        )
        self.assertIn(
            "$liveStartAttempt = $retryDocuments.Count + 1", selector
        )

        # RunChain reuses the exact runtime binding gate, whose allowlist now
        # includes the third and final live-start attempt.
        self.assertIn(
            "$liveStartAttempt -notin @(1, 2, 3)", runtime_binding
        )
        self.assertIn(
            "Assert-DemoLiveGatewayRuntimeBinding -Paths $paths", run_chain
        )

    def test_observed_provider_allows_exact_three_way_concurrency_then_denies_fourth(self) -> None:
        delegate = _ThreeWayFakeProvider()
        observed = DEMO.ObservedProvider(delegate)
        requests = [_request() for _ in range(4)]

        with redirect_stdout(StringIO()):
            with ThreadPoolExecutor(max_workers=3) as executor:
                responses = list(executor.map(observed.invoke, requests[:3]))

            self.assertEqual(3, len(responses))
            self.assertEqual(3, delegate.call_count)
            self.assertEqual(3, observed._call_count)
            self.assertEqual(3, observed._max_inflight)
            self.assertEqual(0, observed._inflight)
            with self.assertRaisesRegex(
                RuntimeError,
                "^DEMO_PROVIDER_CALL_CAP_EXCEEDED$",
            ):
                observed.invoke(requests[3])

        self.assertEqual(3, delegate.call_count)
        self.assertEqual(3, observed._call_count)

    def test_worker_dispatch_contract_is_fixed_three_way_no_retry_and_ordered(self) -> None:
        self.assertEqual(
            (
                "role_project_architect",
                "execution_evidence_coach",
                "independent_quality_reviewer",
            ),
            tuple(identity for identity, _skill, _filename in DEMO.WORKERS),
        )
        source = inspect.getsource(DEMO._run_chain)
        worker_source = inspect.getsource(DEMO._worker_call)
        self.assertIn("max_workers=3", source)
        self.assertEqual(1, source.count("executor.submit("))
        self.assertIn("enumerate(WORKERS, start=1)", source)
        self.assertIn('"provider_retry_count": 0', source)
        self.assertEqual(1, worker_source.count("port.await_response("))
        self.assertNotIn("retry", worker_source.lower())

    def test_human_gate_and_element_projection_are_mandatory(self) -> None:
        parser = DEMO._parser()
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["run-chain", "--run-dir", "offline-run"])
        parsed = parser.parse_args(
            [
                "run-chain",
                "--run-dir",
                "offline-run",
                "--human-request-event-id",
                "$offline_event_1234567890",
            ]
        )
        self.assertEqual("$offline_event_1234567890", parsed.human_request_event_id)

        lifecycle_source = LIFECYCLE.read_text(encoding="utf-8")
        self.assertIn("New-CoreBinding -Paths $paths", lifecycle_source)
        self.assertIn("Read-HumanRequestBinding -Paths $paths", lifecycle_source)
        self.assertIn("DEMO_HUMAN_REQUEST_EVENT_ID_OVERRIDE_DENIED", lifecycle_source)
        for phase in (
            "request-accepted",
            "worker-dispatched",
            "worker-completed",
            "summary-completed",
            "summary-failed",
        ):
            self.assertIn(f'"{phase}"', lifecycle_source)
        self.assertIn("DEMO_LIVE_SWITCH_RECOVERY_FAILED", lifecycle_source)
        self.assertIn('"-ValidateExisting"', lifecycle_source)
        self.assertIn("M4_FAIL_CLOSED_GATEWAY_VALIDATE_EXISTING=PASS", lifecycle_source)

    def test_demo_relay_winps51_directory_and_stage_contract_is_fixed(self) -> None:
        source = RELAY_HELPER.read_text(encoding="utf-8")
        self.assertNotIn("Split-Path -LiteralPath $resolved -Parent", source)
        self.assertIn("[IO.Path]::GetDirectoryName($resolved)", source)
        self.assertIn("[switch]$PrestartCheck", source)
        self.assertIn('Write-Output "DEMO_HOST_RELAY_PRESTART_CHECK=PASS"', source)
        self.assertIn('Write-Output "DEMO_HOST_RELAY_CONTAINER_STARTED=false"', source)
        self.assertIn("Sort-Object -Unique", source)
        self.assertIn('throw ("DEMO_HOST_RELAY_STAGE_FAILED:" + $demoStage)', source)
        self.assertNotIn("Write-Output $originalFailure", source)
        self.assertNotIn("Write-Output $originalFailure.Exception.Message", source)

        allowed_block = source.split("$allowedDemoStages = @(", 1)[1].split(")", 1)[0]
        allowed = {
            line.strip().rstrip(",").strip('"')
            for line in allowed_block.splitlines()
            if '"' in line
        }
        assigned = {
            line.split('"', 2)[1]
            for line in source.splitlines()
            if line.strip().startswith('$demoStage = "')
        }
        self.assertEqual(allowed, assigned)

    def test_attempt6_and_orphan_listener_cleanup_are_independent_and_fail_closed(self) -> None:
        source = LIFECYCLE.read_text(encoding="utf-8")
        self.assertIn("[ValidateRange(1, 7)]", source)
        self.assertIn("ResumeDemoRelayStageRecoveryMarker", source)
        self.assertIn("FailClosedDemoRelayStageRecoveryMarker", source)
        self.assertIn("ResumeDemoRelayPrestateBoundaryRecoveryMarker", source)
        self.assertIn("FailClosedDemoRelayPrestateBoundaryRecoveryMarker", source)
        self.assertIn("resume-infrastructure-demo-relay-stage-recovery", source)
        self.assertIn("resume-infrastructure-demo-relay-prestate-boundary-recovery", source)
        self.assertIn("Assert-AttemptSixRecoveryEvidence", source)
        self.assertIn("Assert-AttemptSevenRecoveryEvidence", source)
        self.assertEqual(6, source.count('throw "DEMO_RESUME_ATTEMPT_UNREACHABLE"'))
        self.assertGreaterEqual(source.count("elseif ($Attempt -eq 7)"), 2)
        self.assertEqual(4, source.count("elseif ($ResumeAttempt -eq 7)"))
        self.assertIn("(?:DEMO|M4)_[A-Za-z0-9_:.-]+", source)
        self.assertIn("DEMO_ORPHAN_LISTENER_CARDINALITY_INVALID", source)
        self.assertIn("DEMO_ORPHAN_LISTENER_ADDRESS_INVALID", source)
        self.assertIn("DEMO_ORPHAN_LISTENER_PID_AMBIGUOUS", source)
        self.assertIn("DEMO_LISTENER_PROCESS_STOP_FAILED", source)
        parent_absent = source.split("if ($candidate.Count -eq 0)", 1)[1].split(
            "[void](Get-ExactProcessRecord", 1
        )[0]
        self.assertIn("Get-NetTCPConnection", parent_absent)
        self.assertIn("Get-ExactProcessRecord", parent_absent)
        self.assertIn("Stop-Process", parent_absent)
        self.assertIn("Get-ListenerCount", parent_absent)
        self.assertEqual(3, source.count("Stop-Process -Id $"))
        self.assertGreaterEqual(
            source.count("ErrorAction SilentlyContinue).Count -ne 0"), 6
        )
        self.assertIn(
            "catch {\n            if (@(Get-Process -Id $processId ", source
        )

    def test_worker_gateway_sync_delivery_avoids_stdin_and_tmpfs_traps(self) -> None:
        source = LIFECYCLE.read_text(encoding="utf-8")
        sync_block = source.split(
            "function Invoke-DemoWorkerGatewaySyncHelper", 1
        )[1].split("function Invoke-DemoWorkerGatewayCredentialSync", 1)[0]
        self.assertIn(
            '$remoteHelperPath = "/root/.awakening-demo-worker-gateway-key-sync.sh"',
            sync_block,
        )
        self.assertNotIn('/tmp/awakening-demo-worker-gateway-key-sync.sh', sync_block)
        self.assertIn('"type=bind,source=" + $Source', sync_block)
        self.assertIn('@(& $docker cp $Source', sync_block)
        self.assertIn('/bin/chmod 600 $remoteHelperPath', sync_block)
        self.assertIn(
            '"test ! -e " + $remoteHelperPath + " && test ! -L "', sync_block
        )
        self.assertIn('/bin/rm -f -- $remoteHelperPath', sync_block)
        self.assertNotIn("RedirectStandardInput", sync_block)
        self.assertIn(
            "Assert-ContainerMatchesBaselineFrozenProjection", source
        )
        self.assertIn("Remove-DemoWorkerGatewaySyncTempResidue", source)

    def test_worker_preexec_pins_current_gateway_key_after_remote_merge(self) -> None:
        source = WORKER_ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn('gateway_key="${HICLAW_WORKER_GATEWAY_KEY:-}"', source)
        self.assertIn('[[ "${gateway_key}" =~ ^[A-Za-z0-9_-]{43}$ ]]', source)
        self.assertIn('[[ "${gateway_key}" =~ ^[A-Fa-f0-9]{64}$ ]]', source)
        self.assertIn(
            '(.models.providers["hiclaw-gateway"].apiKey | type) == "string"',
            source,
        )
        self.assertEqual(
            2,
            source.count(
                "--rawfile gateway_key <(printf '%s' \"${gateway_key}\")"
            ),
        )
        self.assertIn(
            '.models.providers["hiclaw-gateway"].apiKey = $gateway_key', source
        )
        self.assertIn(
            '.models.providers["hiclaw-gateway"].apiKey == $gateway_key', source
        )
        self.assertEqual(
            2,
            source.count(
                'del(.agents.defaults.model,.agents.defaults.models,'
                '.models.providers["hiclaw-gateway"].apiKey,'
            ),
        )
        guard_call = (
            '/bin/bash /tmp/awakening-m4-pin-agent-model.sh '
            '"${WORKSPACE}/openclaw.json"'
        )
        self.assertIn('/i\\' + guard_call, source)
        self.assertIn('/a\\        ' + guard_call, source)
        self.assertIn("grep -Fc '" + guard_call + "'", source)
        self.assertIn('"${PATCHED}")" -ne 2', source)
        self.assertIn("M4_AGENT_GATEWAY_CREDENTIAL_PREEXEC=PASS", source)
        self.assertNotIn("Authorization: Bearer ${gateway_key}", source)

        lifecycle = LIFECYCLE.read_text(encoding="utf-8")
        self.assertIn(
            '$workerEntrypointSha256 = '
            '"bf027122f86b4d6b418682883253c74035f77e30a1e8f3b6862cefbb699c6739"',
            lifecycle,
        )
        apply_then_start = lifecycle.split(
            'Write-Output "DEMO_WORKER_GATEWAY_CREDENTIAL_SYNC_APPLY=PASS"', 1
        )[1].split('Write-Output "DEMO_WORKER_GATEWAY_CREDENTIAL_SYNC_VERIFY=PASS"', 1)[0]
        self.assertLess(
            apply_then_start.index("Assert-DemoWorkerEntrypointGuard"),
            apply_then_start.index("Invoke-M4Script -Path $startAgents"),
        )

    def test_start_failure_is_preserved_and_restore_continues_past_one_container(self) -> None:
        source = LIFECYCLE.read_text(encoding="utf-8")
        invoke_m4 = source.split("function Invoke-M4Script", 1)[1].split(
            "function Use-DockerConfig", 1
        )[0]
        self.assertNotIn("$Attempt", invoke_m4)
        self.assertIn("$fixedCodes[$fixedCodes.Count - 1]", invoke_m4)

        restore = source.split("function Restore-Containers", 1)[1].split(
            "function Invoke-StopRestoreInternal", 1
        )[0]
        self.assertIn(
            '$restoreFailures = [Collections.Generic.List[string]]::new()', restore
        )
        self.assertIn("[void]$restoreFailures.Add($name)", restore)
        self.assertIn("DEMO_CONTAINER_RESTORE_FAILED:", restore)

        starter = source.split("function Invoke-StartInfrastructure", 1)[1].split(
            "function Invoke-ResumeInfrastructure", 1
        )[0]
        self.assertIn("$primaryFailure = $_", starter)
        self.assertIn("$cleanupFailure = $_", starter)
        self.assertLess(
            starter.index("throw $primaryFailure"),
            starter.index("throw $cleanupFailure"),
        )

    def test_preflight_distinguishes_pid_reuse_from_the_recorded_runtime(self) -> None:
        source = LIFECYCLE.read_text(encoding="utf-8")
        preflight = source.split("function Invoke-Preflight", 1)[1].split(
            "function Invoke-StartInfrastructure", 1
        )[0]
        self.assertIn("$stalePidSpecs = @(", preflight)
        self.assertIn("CreationDate", preflight)
        self.assertIn("$pidFileWrittenUtc.AddSeconds(1)", preflight)
        self.assertIn("$pidReused = $true", preflight)
        self.assertIn("DEMO_STALE_PID_PROCESS_IDENTITY_AMBIGUOUS", preflight)
        self.assertIn("DEMO_STALE_PID_IS_ACTIVE", preflight)
        self.assertNotIn("Stop-Process", preflight)


if __name__ == "__main__":
    unittest.main()
