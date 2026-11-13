"""Exercise the real Tach rules using isolated copies of the source tree."""

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import tomli_w

BACKEND = Path(__file__).resolve().parents[1]
BUSINESS_MODULES = ("user", "video", "channel", "comment", "moderation")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copytree(BACKEND / "src", tmp_path / "src")
    shutil.copyfile(BACKEND / "tach.toml", tmp_path / "tach.toml")
    shutil.copyfile(BACKEND / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copytree(BACKEND / "migrations", tmp_path / "migrations")
    shutil.copytree(BACKEND / "tests", tmp_path / "tests")
    # Real target symbols avoid relying on unresolved-import behavior.
    for name in BUSINESS_MODULES:
        for layer in ("domain", "application", "infrastructure", "public"):
            (tmp_path / "src" / name / layer / "example.py").write_text("VALUE = 1\n")
    return tmp_path


def run_tach(project: Path, command: str = "check") -> subprocess.CompletedProcess[str]:
    if command == "check-external":
        return subprocess.run(
            [sys.executable, "-m", "tach", "check-external", "--exclude", "tests,src/*/tests"],
            cwd=project,
            capture_output=True,
            text=True,
            check=False,
        )
    return subprocess.run(
        [sys.executable, "-m", "tach", "check"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )


def probe(project: Path, path: str, code: str, *, source_root: str = "src") -> None:
    target = project / source_root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code + "\n")


@pytest.mark.parametrize("module", BUSINESS_MODULES)
def test_controllers_can_use_public_but_cannot_import_implementation(project, module):
    probe(project, "app/http/example.py", f"from {module}.public.example import VALUE")
    result = run_tach(project)
    assert result.returncode == 0, result.stdout + result.stderr
    probe(project, "app/http/example.py", f"from {module}.application.example import VALUE")
    result = run_tach(project)
    assert result.returncode != 0
    assert f"{module}.application" in result.stdout + result.stderr


@pytest.mark.parametrize("module", BUSINESS_MODULES)
def test_public_can_import_only_own_domain_and_application(project, module):
    probe(
        project,
        f"{module}/public/facade.py",
        f"from {module}.application.example import VALUE\n"
        f"from {module}.domain.example import VALUE as DOMAIN_VALUE\n"
        "from typing import Protocol",
    )
    result = run_tach(project)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("module", BUSINESS_MODULES)
@pytest.mark.parametrize(
    "target,symbol",
    [
        ("{module}.infrastructure.example", "VALUE"),
        ("{module}.infrastructure.di", "{module_title}Provider"),
        ("common.infrastructure.database", "Base"),
        ("common.contracts.authorization", "Authorization"),
        ("common.http.schemas", "JSONModel"),
    ],
)
def test_public_cannot_import_implementation_or_common(project, module, target, symbol):
    target = target.format(module=module)
    symbol = symbol.format(module_title=module.title())
    probe(project, f"{module}/public/facade.py", f"from {target} import {symbol}")
    result = run_tach(project)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Internal Dependencies" in output and target in output


@pytest.mark.parametrize("module", BUSINESS_MODULES)
def test_public_cannot_import_another_modules_public(project, module):
    other = "video" if module == "user" else "user"
    probe(project, f"{module}/public/facade.py", f"from {other}.public.example import VALUE")
    result = run_tach(project)
    assert result.returncode != 0
    assert f"{other}.public" in result.stdout + result.stderr


@pytest.mark.parametrize(
    "source,code",
    [
        ("video/infrastructure/bridge.py", "from user.public.example import VALUE"),
        ("channel/infrastructure/bridge.py", "from video.public.example import VALUE"),
        ("comment/infrastructure/bridge.py", "from user.public.example import VALUE"),
        ("moderation/infrastructure/bridge.py", "from video.public.example import VALUE"),
        ("user/application/use_case.py", "from user.domain.example import VALUE"),
        ("user/domain/value.py", "from common.contracts.authorization import Authorization"),
    ],
)
def test_allowed_internal_dependencies(project, source, code):
    probe(project, source, code)
    result = run_tach(project)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "source,code",
    [
        ("app/http/example.py", "import user.domain.example"),
        ("app/http/example.py", "from user import infrastructure"),
        ("app/http/example.py", "import user"),
        (
            "app/http/example.py",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import user.domain.example",
        ),
        (
            "app/http/example.py",
            'import importlib\nimportlib.import_module("user.infrastructure.example")',
        ),
        ("common/http/example.py", "from user.public.example import VALUE"),
        ("common/infrastructure/example.py", "from user.public.example import VALUE"),
        ("common/contracts/example.py", "from user.public.example import VALUE"),
        ("common/http/example.py", "from app.settings import Settings"),
        ("user/domain/example.py", "from app.settings import Settings"),
        ("user/domain/example.py", "from common.http.schemas import JSONModel"),
        ("user/application/example.py", "from common.infrastructure.database import Base"),
        ("user/public/example.py", "from common.http.schemas import JSONModel"),
        ("user/domain/example.py", "from user.application.example import VALUE"),
        ("user/application/example.py", "from user.infrastructure.example import VALUE"),
        ("user/infrastructure/example.py", "from user.public.example import VALUE"),
        ("video/infrastructure/example.py", "from user.infrastructure.example import VALUE"),
        ("video/application/example.py", "from user.public.example import VALUE"),
        ("user/infrastructure/example.py", "from video.public.example import VALUE"),
    ],
)
def test_forbidden_dependencies_are_rejected(project, source, code):
    probe(project, source, code)
    result = run_tach(project)
    assert result.returncode != 0, f"Tach accepted forbidden import: {source}: {code}"
    assert "Internal Dependencies" in result.stdout + result.stderr


@pytest.mark.parametrize(
    "source_root,path",
    [("migrations", "env.py"), ("tests", "test_integration_probe.py")],
)
def test_entrypoints_outside_src_must_use_public(project, source_root, path):
    probe(project, path, "from user.public.example import VALUE", source_root=source_root)
    result = run_tach(project)
    assert result.returncode == 0, result.stdout + result.stderr
    probe(project, path, "from user.infrastructure.example import VALUE", source_root=source_root)
    result = run_tach(project)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Internal Dependencies" in output
    assert f"{source_root}/{path}" in output and "user.infrastructure" in output


@pytest.mark.parametrize(
    "layer,library",
    [
        ("domain", "fastapi"),
        ("domain", "sqlalchemy"),
        ("application", "fastapi"),
        ("application", "sqlalchemy"),
        ("domain", "psycopg"),
        ("domain", "alembic"),
        ("domain", "uvicorn"),
        ("domain", "email_validator"),
        ("infrastructure", "fastapi"),
        ("infrastructure", "starlette"),
        ("infrastructure.di", "fastapi"),
        ("infrastructure.di", "starlette"),
        ("public", "fastapi"),
        ("public", "starlette"),
        ("public", "sqlalchemy"),
        ("public", "psycopg"),
        ("domain", "dishka"),
        ("application", "dishka"),
        ("public", "dishka"),
    ],
)
def test_inner_layers_cannot_import_frameworks(project, layer, library):
    probe(project, f"user/{layer}/example.py", f"import {library}")
    result = run_tach(project, "check-external")
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "External Dependencies" in output and f"user.{layer}" in output and library in output


def test_unregistered_source_package_is_rejected(project):
    probe(project, "unregistered/__init__.py", "from user.public.example import VALUE")
    result = run_tach(project)
    assert result.returncode != 0
    assert "root" in (result.stdout + result.stderr).lower()


def test_cycles_are_rejected_even_if_edges_are_otherwise_allowed(project):
    config = project / "tach.toml"
    metadata = tomllib.loads(config.read_text())
    app = next(item for item in metadata["modules"] if item.get("path") == "app")
    common = next(item for item in metadata["modules"] if item.get("path") == "common")
    app["depends_on"].append("common")
    app["visibility"].append("common")
    common["depends_on"].append("app")
    config.write_text(tomli_w.dumps(metadata))
    probe(project, "app/cycle.py", "import common")
    probe(project, "common/cycle.py", "import app")
    result = run_tach(project)
    assert result.returncode != 0
    assert "circular" in (result.stdout + result.stderr).lower()


def test_external_check_accepts_current_source_without_waiving_import_rules(project):
    result = run_tach(project, "check-external")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("module", BUSINESS_MODULES)
def test_module_tests_can_access_own_layers(project, module):
    probe(
        project,
        f"{module}/tests/test_scenario.py",
        "\n".join(
            f"from {module}.{layer}.example import VALUE as {layer.upper()}"
            for layer in ("domain", "application", "infrastructure", "public")
        ),
    )
    result = run_tach(project)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "source,code,allowed",
    [
        ("video/tests/test_scenario.py", "from user.public.example import VALUE", True),
        ("video/tests/test_scenario.py", "from user.application.example import VALUE", False),
        ("user/tests/test_scenario.py", "from video.public.example import VALUE", False),
        ("user/application/example.py", "import user.tests", False),
        ("user/infrastructure/example.py", "import user.tests", False),
        ("user/public/example.py", "import user.tests", False),
        ("app/http/example.py", "import user.tests", False),
    ],
)
def test_module_tests_preserve_production_boundaries(project, source, code, allowed):
    probe(project, source, code)
    result = run_tach(project)
    assert (result.returncode == 0) == allowed, result.stdout + result.stderr


def test_module_tests_allow_dev_dependencies_without_waiving_production_rules(project):
    probe(project, "user/tests/test_scenario.py", "import pytest")
    result = run_tach(project, "check-external")
    assert result.returncode == 0, result.stdout + result.stderr
    probe(project, "user/application/example.py", "import pytest")
    result = run_tach(project, "check-external")
    assert result.returncode != 0
    assert "src/user/application/example.py" in result.stdout + result.stderr


def test_default_pytest_discovery_finds_module_tests(project):
    probe(project, "user/tests/test_discovery.py", "def test_module_discovery(): pass")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=project,
        env={**os.environ, "PYTHONPATH": str(project / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "src/user/tests/test_discovery.py::test_module_discovery" in result.stdout


@pytest.mark.parametrize("module", BUSINESS_MODULES)
def test_registry_and_own_tests_can_import_module_provider(project, module):
    provider = module.title() + "Provider"
    probe(project, "app/module_providers.py", f"from {module}.infrastructure.di import {provider}")
    probe(
        project, f"{module}/tests/test_di.py", f"from {module}.infrastructure.di import {provider}"
    )
    result = run_tach(project)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "source,source_root",
    [
        ("app/http/example.py", "src"),
        ("app/main.py", "src"),
        ("app/di.py", "src"),
        ("common/infrastructure/example.py", "src"),
        ("common/http/example.py", "src"),
        ("user/application/example.py", "src"),
        ("user/domain/example.py", "src"),
        ("video/infrastructure/example.py", "src"),
        ("video/infrastructure/di.py", "src"),
        ("video/tests/test_di.py", "src"),
        ("test_di_probe.py", "tests"),
        ("env.py", "migrations"),
    ],
)
def test_other_consumers_cannot_import_module_provider(project, source, source_root):
    probe(
        project,
        source,
        "from user.infrastructure.di import UserProvider",
        source_root=source_root,
    )
    result = run_tach(project)
    assert result.returncode != 0, result.stdout + result.stderr
    assert f"{BUSINESS_MODULES[0]}.infrastructure.di" in result.stdout + result.stderr


@pytest.mark.parametrize(
    "target,symbol",
    [
        ("{module}.domain.example", "VALUE"),
        ("{module}.application.example", "VALUE"),
        ("{module}.infrastructure.example", "VALUE"),
        ("{module}.public.example", "VALUE"),
        ("common.contracts.authorization", "Authorization"),
    ],
)
def test_registry_cannot_reach_past_provider_entrypoint(project, target, symbol):
    target = target.format(module="user")
    probe(project, "app/module_providers.py", f"from {target} import {symbol}")
    result = run_tach(project)
    assert result.returncode != 0, result.stdout + result.stderr
    assert target in result.stdout + result.stderr


def test_provider_entrypoint_does_not_expose_other_symbols(project):
    target = project / "src/user/infrastructure/di.py"
    target.write_text(target.read_text() + "\nINTERNAL = 1\n")
    probe(project, "app/module_providers.py", "from user.infrastructure.di import INTERNAL")
    result = run_tach(project)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "INTERNAL" in result.stdout + result.stderr


@pytest.mark.parametrize("module", BUSINESS_MODULES)
def test_module_tests_can_use_private_provider_helpers(project, module):
    target = project / "src" / module / "infrastructure/di.py"
    target.write_text(target.read_text() + "\nINTERNAL = 1\n")
    probe(project, f"{module}/tests/test_di.py", f"from {module}.infrastructure.di import INTERNAL")
    result = run_tach(project)
    assert result.returncode == 0, result.stdout + result.stderr


def test_registry_does_not_reexport_provider_classes_to_controllers(project):
    probe(project, "app/http/example.py", "from app.module_providers import UserProvider")
    result = run_tach(project)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "UserProvider" in result.stdout + result.stderr


@pytest.mark.parametrize("module", BUSINESS_MODULES)
def test_registered_provider_resolves_public_scenario_with_own_adapter(project, module):
    # Test-only implementations live in the isolated copy, never in real modules.
    probe(
        project,
        f"{module}/application/probe.py",
        "from typing import Protocol\n"
        "class ProbePort(Protocol):\n"
        "    def read(self) -> str: ...\n"
        "class ProbeScenario:\n"
        "    def __init__(self, port: ProbePort): self.port = port\n"
        "    def run(self): return self.port.read()",
    )
    probe(
        project,
        f"{module}/infrastructure/example.py",
        "class ProbeAdapter:\n    def read(self): return 'registered-by-module'",
    )
    probe(
        project,
        f"{module}/infrastructure/di.py",
        "from dishka import Provider, Scope, provide\n"
        f"from {module}.application.probe import ProbePort, ProbeScenario\n"
        f"from {module}.infrastructure.example import ProbeAdapter\n"
        f"class {module.title()}Provider(Provider):\n"
        "    scope = Scope.REQUEST\n"
        "    adapter = provide(ProbeAdapter, provides=ProbePort)\n"
        "    scenario = provide(ProbeScenario)",
    )
    probe(
        project,
        f"{module}/public/__init__.py",
        f"from {module}.application.probe import ProbeScenario as ProbeScenario",
    )
    result = run_tach(project)
    assert result.returncode == 0, result.stdout + result.stderr
    script = (
        "import asyncio\n"
        "from redis.asyncio import Redis\n"
        "from app.di import create_container\n"
        "from app.settings import Settings\n"
        f"from {module}.public import ProbeScenario\n"
        "async def exercise():\n"
        "    cache = Redis()\n"
        "    container = create_container(Settings(), cache)\n"
        "    try:\n"
        "        async with container() as operation:\n"
        "            scenario = await operation.get(ProbeScenario)\n"
        "            assert scenario.run() == 'registered-by-module'\n"
        "            assert await operation.get(ProbeScenario) is scenario\n"
        "        async with container() as operation:\n"
        "            assert await operation.get(ProbeScenario) is not scenario\n"
        "    finally:\n"
        "        await container.close()\n"
        "        await cache.aclose()\n"
        "asyncio.run(exercise())\n"
    )
    result = subprocess.run(
        [sys.executable, "-"],
        input=script,
        cwd=project,
        env={**os.environ, "PYTHONPATH": str(project / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
