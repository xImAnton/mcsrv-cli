import os
import pathlib
import shlex
import subprocess
from functools import cached_property
from typing import Optional

import click.exceptions
import inquirer
import psutil
from click import echo

from .util import get_running_screens, Screen, clean_path, check_ram_argument

RC_PATH = pathlib.Path("~/.mcsrvrc").expanduser()


class Server:
    @classmethod
    def get_cached_server_paths(cls) -> list[str]:
        if not RC_PATH.is_file():
            return []

        with RC_PATH.open("r") as f:
            return list(map(str.strip, f.readlines()))

    @classmethod
    def unregister_paths(cls, paths: list[str]) -> None:
        if len(paths) == 0:
            return

        registered = set(cls.get_cached_server_paths())
        to_remove = set(paths)

        with RC_PATH.open("w") as f:
            for valid_server in registered - to_remove:
                f.write(f"{valid_server}\n")

    @classmethod
    def get_registered_servers(cls) -> list["Server"]:
        paths = cls.get_cached_server_paths()
        out = []
        invalid = []

        for p in paths:
            try:
                out.append(Server(p))
            except FileNotFoundError:
                echo(f"mcsrv: warn: server directory {p} not existing, removing it")
                invalid.append(p)

        cls.unregister_paths(invalid)

        return out

    def __init__(self, path: str) -> None:
        self.path: pathlib.Path = clean_path(pathlib.Path(path).absolute())

        if not self.path.is_dir():
            raise FileNotFoundError(f"invalid server path: {self.path}")

        self.data: dict[str, str] = {}
        self._load_data()
        self.jar: pathlib.Path = self._locate_jar()
        self.save_data()

    @property
    def running(self) -> bool:
        return self.screen_handle is not None

    @property
    def autostarts(self) -> bool:
        return self.data.get("autostart") == "true"

    @autostarts.setter
    def autostarts(self, val: bool) -> None:
        self.data["autostart"] = "true" if val else "false"
        self.save_data()

    @cached_property
    def screen_handle(self) -> Optional[Screen]:
        for screen in get_running_screens():
            if screen.name == self.screen_name:
                return screen
        return None

    @property
    def id(self) -> str:
        return self.path.name.lower()

    @property
    def datafile(self) -> pathlib.Path:
        return self.path.joinpath(".mcsrvmeta")

    @property
    def screen_name(self) -> str:
        return f"mc-{self.id}"

    @property
    def ram(self) -> str:
        return check_ram_argument(self.data.get("ram", "4G"))

    @ram.setter
    def ram(self, val: str) -> None:
        self.data["ram"] = check_ram_argument(val)
        self.save_data()

    def print(self, msg: str) -> None:
        echo(f"mcsrv: {self.id}: {msg}")

    def register(self) -> "Server":
        # check if my id is already saved in another path
        servers = self.get_registered_servers()

        for other in servers:
            if other.id == self.id:
                # same server
                if str(other.path) == str(self.path):
                    return self

                # other server with same id
                self.print(
                    f"there is already a server with id {self.id} at {other.path}. rename this or that directory")
                raise click.exceptions.Exit(code=1)

        # append server if not
        with RC_PATH.open("a" if RC_PATH.is_file() else "w") as f:
            f.write(f"{self.path}\n")

        return self

    def get_stats(self) -> tuple[float, float]:
        if not self.running:
            return 0, 0

        proc: psutil.Process = psutil.Process(self.screen_handle.pid).children()[0]
        return proc.cpu_percent(interval=2.0), round(proc.memory_info().rss / 1000000000, 2)

    def send_command(self, cmd: str, execute: bool = True) -> None:
        if execute:
            cmd += "^M"

        subprocess.run(["screen", "-S", self.screen_name, "-p", "0", "-X", "stuff", cmd])

    def start(self, ram: str = None) -> None:
        if ram:
            ram = check_ram_argument(ram)
        else:
            ram = self.ram

        # invalidate screen handle
        self.__dict__.pop("screen_handle", None)

        self.print(f"starting with {ram}B RAM")
        cmd = ["screen", "-d", "-S", self.screen_name, "-m", "java", "-Xmx" + ram, "-jar", self.jar.name]
        subprocess.run(cmd, cwd=self.path.absolute())

    def _locate_jar(self) -> pathlib.Path:
        if "jar" in self.data:
            j = self.path.joinpath(self.data["jar"])
            if j.is_file():
                return j
            self.print("saved jar-file not found! locating...")

        jars = list(self.path.glob("*.jar"))
        self.print(str(self.path))

        if len(jars) == 0:
            self.print("no server found in the current directory")
            raise click.exceptions.Exit(code=1)

        if len(jars) == 1:
            self.data["jar"] = jars[0].name
            return jars[0]

        answer = inquirer.prompt([inquirer.List("jar", message="Which .jar runs your server?", choices=jars)])

        if not answer:
            raise click.exceptions.Exit(code=1)

        self.data["jar"] = answer["jar"].name
        return answer["jar"]

    def open_console(self) -> None:
        os.system(shlex.join(["screen", "-x", str(self.screen_handle)]))

    def save_data(self) -> None:
        with self.datafile.open("w") as f:
            for key, val in self.data.items():
                f.write(f"{key}={val}\n")

    def _load_data(self) -> None:
        self.data = {}

        if not self.datafile.is_file():
            self.print("no .mcsrvmeta file found")
            return

        with self.datafile.open("r") as f:
            for line in f.readlines():
                line = line.strip()
                if line.startswith("#"):
                    continue

                res = line.split("=", 1)

                if len(res) != 2:
                    continue

                self.data[res[0]] = res[1]
