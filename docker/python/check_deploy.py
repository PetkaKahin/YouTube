"""Focused shell workflow checks with fake host commands; never install on the host."""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
FAKE = r"""import json, os, subprocess, sys
from pathlib import Path
name=Path(sys.argv[0]).name
args=sys.argv[1:]
root=Path(os.environ["DEPLOY_TEST_ROOT"])
project=root/"project with spaces"
with (root/"trace.jsonl").open("a") as log:
    log.write(json.dumps([name,args])+"\n")
ready=root/"docker-ready"
if name=="apt-get":
    if "docker-ce" in args: ready.touch()
elif name=="dpkg":
    print("amd64")
elif name=="dpkg-query":
    if args[-1]==os.environ.get("CONFLICT_PACKAGE"): print("install ok installed")
    else: sys.exit(1)
elif name=="curl":
    if "-o" in args: Path(args[args.index("-o")+1]).write_text("test-signing-key\n")
elif name=="systemctl":
    (root/"service-started").touch()
elif name=="usermod":
    (root/"group-ready").touch()
elif name=="runuser":
    command=args[args.index("--")+1:]
    if (command==["docker","info"] and os.environ.get("NEEDS_GROUP")
            and not (root/"group-ready").exists()):
        sys.exit(1)
    sys.exit(subprocess.call(command))
elif name=="docker":
    if args[0]=="info":
        stopped=os.environ.get("DAEMON_STOPPED") and not (root/"service-started").exists()
        sys.exit(0 if ready.exists() and not stopped else 1)
    if args[:2]==["buildx","version"]:
        if not ready.exists(): sys.exit(1)
        print("github.com/docker/buildx v0.30.0"); sys.exit(0)
    if args[0]=="inspect":
        files=str(project/"docker-compose.yml")
        if os.environ.get("CONFIG_MODE")!="prod": files+=","+str(project/"docker-compose.dev.yml")
        print(files); sys.exit(0)
    if args[0]=="volume":
        path=root/"volumes.json"
        volumes=json.loads(path.read_text())
        volume=args[-1]
        if args[1]=="inspect":
            if volume not in volumes: sys.exit(1)
            if "--format" in args: print(volumes[volume])
        elif args[1]=="rm":
            del volumes[volume]; path.write_text(json.dumps(volumes))
        else: sys.exit(3)
        sys.exit(0)
    if args[0]!="compose": sys.exit(3)
    position=1
    while args[position] in ("--project-directory","--env-file","-f"):
        position+=2
    command=args[position]
    rest=args[position+1:]
    if command=="version":
        if not ready.exists(): sys.exit(1)
        print(os.environ.get("COMPOSE_VERSION","5.6.1"))
    elif command=="config" and "--format" in rest:
        keys=("postgres_data","job_error_spool","media_scratch")
        print(json.dumps({"name":"dev-test","volumes":{
            key:{"name":"dev-test-"+key} for key in keys}}))
    elif command=="ps": print("test-python")
    elif command=="build":
        if os.environ.get("FAIL_BUILD"): sys.exit(9)
        (root/"image-ready").touch()
    elif command=="run":
        if any(value.endswith("/prepare_env.py") for value in rest):
            env=project/".env"
            if not env.exists(): env.write_text((project/".env.example").read_text())
            keys=("postgres_password","valkey_password","rustfs_access_key",
                  "rustfs_secret_key","csrf_secret")
            for key in keys:
                path=project/"docker/secrets"/key
                path.parent.mkdir(parents=True,exist_ok=True)
                if not path.exists(): path.write_text("existing-demo-file\n")
        elif "-c" in rest:
            if not (root/"image-ready").exists(): print("Image automatically built",flush=True)
            sys.exit(subprocess.call([sys.executable,"-c",rest[rest.index("-c")+1]]))
    elif command=="port": print("127.0.0.1:8080")
else: sys.exit(3)
"""


class DeployChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="youtube-deploy-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project with spaces"
        (self.project / "docker").mkdir(parents=True)
        (self.project / "backend").mkdir()
        (self.project / "frontend").mkdir()
        (self.project / "backend/uv.lock").touch()
        (self.project / "frontend/package-lock.json").touch()
        for name in ("Makefile", ".env.example", "docker-compose.dev.yml", "docker/run"):
            shutil.copy2(ROOT / name, self.project / name)
        self.system = self.root / "system"
        (self.system / "apt/sources.list.d").mkdir(parents=True)
        self.os_release = self.system / "os-release"
        self.set_ubuntu("24.04")
        installer = (
            (ROOT / "install.sh")
            .read_text()
            .replace("/etc/os-release", str(self.os_release))
            .replace("/etc/apt/", str(self.system / "apt") + "/")
        )
        (self.project / "install.sh").write_text(installer)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        script = self.bin / "command.py"
        script.write_text("#!" + sys.executable + "\n" + FAKE)
        script.chmod(0o755)
        for name in (
            "docker",
            "apt-get",
            "curl",
            "systemctl",
            "dpkg",
            "dpkg-query",
            "runuser",
            "usermod",
        ):
            (self.bin / name).symlink_to(script)
        self.environment = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "DEPLOY_TEST_ROOT": str(self.root),
            "SUDO_USER": "",
        }
        self.volumes = {
            "dev-test-" + key: "dev-test"
            for key in (
                "postgres_data",
                "job_error_spool",
                "media_scratch",
                "rustfs_data",
                "node_modules",
            )
        }
        self.volumes["another-project-postgres"] = "another-project"
        (self.root / "volumes.json").write_text(json.dumps(self.volumes))

    def set_ubuntu(self, version):
        self.os_release.write_text(f'ID=ubuntu\nVERSION_ID="{version}"\n')

    def ready(self):
        (self.root / "docker-ready").touch()
        (self.project / ".env").write_text('APP_NAME="keep $(touch must-not-exist)"\n')
        for key in (
            "postgres_password",
            "valkey_password",
            "rustfs_access_key",
            "rustfs_secret_key",
            "csrf_secret",
        ):
            path = self.project / "docker/secrets" / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("keep-existing-value\n")

    def invoke(self, script="install.sh", arguments=(), answer="\n", **environment):
        return subprocess.run(  # noqa: S603 -- controlled temporary scripts and command stubs
            [shutil.which("bash"), str(self.project / script), *arguments],
            cwd=self.project,
            env={**self.environment, **environment},
            input=answer,
            text=True,
            capture_output=True,
            check=False,
        )

    def trace(self):
        path = self.root / "trace.jsonl"
        return [json.loads(row) for row in path.read_text().splitlines()] if path.exists() else []

    def docker_calls(self):
        return [args for name, args in self.trace() if name == "docker"]

    def seeded(self):
        return any(
            args[-4:] == ["media-worker", "python", "-m", "app.seed"]
            for args in self.docker_calls()
        )

    def test_install_supported_ubuntu_and_default_seed(self):
        for version, suite in (("24.04", "noble"), ("26.04", "resolute")):
            with self.subTest(version=version):
                self.set_ubuntu(version)
                (self.root / "docker-ready").unlink(missing_ok=True)
                result = self.invoke()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(
                    f"Suites: {suite}\n",
                    (self.system / "apt/sources.list.d/docker.sources").read_text(),
                )
                self.assertTrue(self.seeded())
                self.assertIn("http://localhost:8080", result.stdout)
                packages = [
                    args for name, args in self.trace() if name == "apt-get" and "docker-ce" in args
                ]
                self.assertTrue(packages)
                self.assertIn("docker-compose-plugin", packages[-1])
                self.assertIn("docker-buildx-plugin", packages[-1])
                (self.root / "trace.jsonl").unlink()

    def test_repeat_no_seed_preserves_config_and_secrets(self):
        self.ready()
        files = [self.project / ".env", *sorted((self.project / "docker/secrets").iterdir())]
        before = [path.read_bytes() for path in files]
        result = self.invoke(answer="n\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.seeded())
        self.assertEqual([path.read_bytes() for path in files], before)
        self.assertFalse((self.project / "must-not-exist").exists())
        self.assertFalse(
            any(name == "apt-get" and "docker-ce" in args for name, args in self.trace())
        )
        self.assertFalse(
            any("down" in args or args[:2] == ["volume", "rm"] for args in self.docker_calls())
        )
        calls = self.docker_calls()
        stopped = next(index for index, args in enumerate(calls) if "stop" in args)
        migration = next(
            index for index, args in enumerate(calls) if args[-3:] == ["alembic", "upgrade", "head"]
        )
        self.assertLess(stopped, migration)

    def test_eof_and_wrong_os_stop_before_installation(self):
        result = self.invoke(answer="")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.trace())
        self.set_ubuntu("22.04")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.trace())

    def test_invalid_answer_is_repeated(self):
        self.ready()
        result = self.invoke(answer="maybe\nn\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("Использовать сидеры"), 2)
        self.assertFalse(self.seeded())

    def test_conflicting_packages_are_not_removed(self):
        result = self.invoke(CONFLICT_PACKAGE="containerd")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("конфликтующие пакеты", result.stderr)
        self.assertFalse(
            any(
                name == "apt-get" and ("remove" in args or "docker-ce" in args)
                for name, args in self.trace()
            )
        )
        self.assertFalse(self.seeded())

    def test_missing_group_is_fixed_without_docker_reinstall(self):
        self.ready()
        result = self.invoke(answer="n\n", SUDO_USER="app", NEEDS_GROUP="yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(["usermod", ["-aG", "docker", "app"]], self.trace())
        self.assertFalse(
            any(name == "apt-get" and "docker-ce" in args for name, args in self.trace())
        )

    def test_existing_stopped_docker_is_started_without_reinstall(self):
        self.ready()
        result = self.invoke(answer="n\n", DAEMON_STOPPED="yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(["systemctl", ["enable", "--now", "docker"]], self.trace())
        self.assertFalse(
            any(name == "apt-get" and "docker-ce" in args for name, args in self.trace())
        )

    def test_old_compose_and_build_failure_stop_before_seed(self):
        self.ready()
        result = self.invoke(COMPOSE_VERSION="2.23.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.seeded())
        result = self.invoke(FAIL_BUILD="yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.seeded())

    def test_refresh_scopes_volumes_and_migrates_before_workers(self):
        self.ready()
        result = self.invoke("docker/run", ("refresh",), SEED_MODE="prod")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        remaining = json.loads((self.root / "volumes.json").read_text())
        removed = set(self.volumes) - set(remaining)
        self.assertEqual(
            removed,
            {"dev-test-postgres_data", "dev-test-job_error_spool", "dev-test-media_scratch"},
        )
        calls = self.docker_calls()
        self.assertFalse(any("prune" in args for args in calls))
        down = next(index for index, args in enumerate(calls) if "down" in args)
        migration = next(
            index for index, args in enumerate(calls) if args[-3:] == ["alembic", "upgrade", "head"]
        )
        start = next(
            index
            for index, args in enumerate(calls)
            if args[-3:] == ["-d", "--wait", "rustfs-init"] or args[-3:] == ["up", "-d", "--wait"]
        )
        self.assertLess(down, migration)
        self.assertLess(migration, start)
        self.assertTrue(self.seeded())

    def test_refresh_refuses_prod_and_foreign_volume_before_down(self):
        self.ready()
        for mode in ("prod", "foreign-volume"):
            with self.subTest(mode=mode):
                if mode == "foreign-volume":
                    self.volumes["dev-test-postgres_data"] = "another-project"
                    (self.root / "volumes.json").write_text(json.dumps(self.volumes))
                result = self.invoke("docker/run", ("refresh",), CONFIG_MODE=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(
                    any(
                        "down" in args or args[:2] == ["volume", "rm"]
                        for args in self.docker_calls()
                    )
                )
                (self.root / "trace.jsonl").unlink()

    def test_refresh_build_failure_keeps_data(self):
        self.ready()
        result = self.invoke("docker/run", ("refresh",), FAIL_BUILD="yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads((self.root / "volumes.json").read_text()), self.volumes)
        self.assertFalse(any("down" in args for args in self.docker_calls()))

    def test_make_refresh_always_dispatches_dev_reset(self):
        result = subprocess.run(  # noqa: S603 -- dry-run of the controlled Makefile
            [shutil.which("make"), "--no-print-directory", "-n", "refresh", "SEED_MODE=prod"],
            cwd=self.project,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "./docker/run refresh")

    def test_bash_syntax(self):
        for path in (ROOT / "install.sh", ROOT / "docker/run"):
            result = subprocess.run(  # noqa: S603 -- syntax only, controlled shell sources
                [shutil.which("bash"), "-n", str(path)], capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
