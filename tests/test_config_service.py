import sys
import tempfile
from pathlib import Path
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.append(str(SRC_DIR))

from services.config_service import ConfigService


class ConfigServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.config_path = base / "config.yaml"
        self.example_path = base / "config.example.yaml"
        self.example_path.write_text(
            textwrap.dedent(
                """
                version: 1
                meshcore:
                  companion:
                    transport: bluetooth
                    endpoint: example.local
                    device: auto
                    channel_refresh_seconds: 45
                app:
                  theme: meshcore-dark
                  log_level: info
                  data_location: ~/.meshcore-tui
                """
            ).strip()
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _service(self) -> ConfigService:
        return ConfigService(path=self.config_path, example_path=self.example_path)

    def test_loads_from_example_when_config_missing(self) -> None:
        service = self._service()
        self.assertTrue(self.config_path.exists())
        self.assertEqual(service.config.meshcore.companion.channel_refresh_seconds, 45)

    def test_save_persists_changes(self) -> None:
        service = self._service()
        new_value = "tcp"
        service.config.meshcore.companion.transport = new_value
        service.save()

        reloaded = self._service()
        self.assertEqual(reloaded.config.meshcore.companion.transport, new_value)

    def test_mutate_helper_updates_and_saves(self) -> None:
        service = self._service()
        service.mutate(lambda cfg: setattr(cfg.app, "theme", "meshcore-light"))
        reloaded = self._service()
        self.assertEqual(reloaded.config.app.theme, "meshcore-light")


if __name__ == "__main__":
    unittest.main()
