import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from mission_launch import digest
from server_plan import canonical, make_plan, projects


class ServerPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.paths = []
        for number, name, dependencies in [
            (1, "Core", [2]), (2, "CoreDependency", []), (3, "Mission", []),
            (4, "Equipment", [5]), (5, "Content", [4]), (6, "Unused", [])]:
            directory = self.base / name
            directory.mkdir()
            project = directory / "addon.gproj"
            deps = " ".join(f'"{x:016X}"' for x in dependencies)
            project.write_text(f'GameProject {{ ID "{name}" GUID "{number:016X}" Dependencies {{ {deps} }} }}')
            self.paths.append(project)
        header = self.base / "Mission" / "Mission.conf"
        header.write_text('GC_MissionHeader { World "{1111111111111111}Worlds/Test.ent" }')
        self.scenario = "{2222222222222222}Mission.conf"
        header.with_suffix(".conf.meta").write_text(f'MetaFileClass {{ Name "{self.scenario}" }}')
        (header.parent / "Worlds").mkdir()
        (header.parent / "Worlds/Test.ent.meta").write_text('MetaFileClass { Name "{1111111111111111}Worlds/Test.ent" }')
        self.assignment = {"requestId": "00000000-0000-0000-0000-000000000001",
                           "missionId": "00000000-0000-0000-0000-000000000002",
                           "manifest": {"scenario": self.scenario,
                                        "source": {"repository": "test/mission", "sourceHash": "revision"},
                                        "requiredAddonIds": ["0000000000000004"],
                                        "dependencyResolution": "authored_container_graph_partial"}}
        self.deployment = {"missionId": self.assignment["missionId"],
                           "scenario": self.scenario, "source": copy.deepcopy(self.assignment["manifest"]["source"]),
                           "missionAddonId": "0000000000000003", "headerSha256": digest(header)}
        self.deployment["manifestSha256"] = hashlib.sha256(canonical(self.assignment["manifest"])).hexdigest()
        self.installed = projects(self.paths)

    def plan(self, mode="local"):
        return make_plan(self.assignment, self.deployment, self.installed, ["0000000000000001"],
                         {"bindPort": 2010, "game": {"name": "test", "mods": [{"modId": "OLD"}]}}, mode)

    def test_selected_closure_and_cycles(self):
        plan = self.plan()
        self.assertEqual([m["modId"] for m in plan["mods"]], [f"{x:016X}" for x in range(1, 6)])
        self.assertEqual(plan["config"]["bindPort"], 2010)
        self.assertEqual(plan["config"]["game"]["scenarioId"], self.scenario)
        self.assertEqual(plan["config"]["game"]["mods"], [])
        self.assertEqual(plan["mode"], "local")
        self.assertEqual(plan["planSha256"], self.plan()["planSha256"])

    def test_missing_transitive_mod_blocks(self):
        del self.installed["0000000000000005"]
        with self.assertRaisesRegex(ValueError, "not installed"):
            self.plan()

    def test_stale_header_blocks(self):
        (self.base / "Mission" / "Mission.conf").write_text("changed")
        with self.assertRaisesRegex(ValueError, "header changed"):
            self.plan()

    def test_wrong_catalogue_revision_blocks(self):
        self.assignment["manifest"]["source"]["sourceHash"] = "new revision"
        with self.assertRaisesRegex(ValueError, "catalogue revision"):
            self.plan()

    def test_wrong_scenario_identity_blocks(self):
        (self.base / "Mission" / "Mission.conf.meta").write_text('MetaFileClass { Name "{3333333333333333}Mission.conf" }')
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.plan()

    def test_collection_blocks(self):
        self.installed["0000000000000004"]["id"] = "GlobalConflictsWorkshop"
        with self.assertRaisesRegex(ValueError, "Authoring collections"):
            self.plan()

    def test_duplicate_install_blocks(self):
        duplicate = self.base / "duplicate.gproj"
        duplicate.write_bytes(self.paths[0].read_bytes())
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            projects([*self.paths, duplicate])

    def test_path_escape_blocks(self):
        self.assignment["manifest"]["scenario"] = "{2222222222222222}../Mission.conf"
        self.deployment["manifestSha256"] = hashlib.sha256(canonical(self.assignment["manifest"])).hexdigest()
        with self.assertRaisesRegex(ValueError, "Invalid resource path"):
            self.plan()

    def test_changed_metadata_blocks(self):
        self.assignment["manifest"]["requiredAddonIds"] = []
        with self.assertRaisesRegex(ValueError, "metadata does not match"):
            self.plan()

    def test_workshop_requires_versions(self):
        with self.assertRaisesRegex(ValueError, "installed versions"):
            self.plan("workshop")
        for mod in self.installed.values():
            mod["version"] = "1.2.3"
        self.assertTrue(all(m["version"] == "1.2.3" for m in self.plan("workshop")["config"]["game"]["mods"]))

    def test_header_pin_requires_the_exact_installed_version(self):
        header = self.paths[2].with_name("Mission.conf")
        header.write_text('GC_MissionHeader { World "{1111111111111111}Worlds/Test.ent" '
                          'm_aRequiredAddonVersions { "0000000000000006=1.0.25" } }')
        self.deployment["headerSha256"] = digest(header)
        for project in self.paths:
            identity = next(mod_id for mod_id, row in self.installed.items() if row["project"] == str(project))
            version = "1.0.25" if identity.endswith("6") else "1.2.3"
            project.with_name("ServerData.json").write_text(json.dumps(
                {"id": identity, "revision": {"version": version}}))
        self.installed = projects(self.paths)

        identity = "0000000000000006"
        local = self.plan()
        self.assertEqual(local["requiredAddonVersions"], {identity: "1.0.25"})
        workshop = self.plan("workshop")
        self.assertIn({"modId": identity, "version": "1.0.25", "required": True}, workshop["config"]["game"]["mods"])
        for version in ("", "1.0.43"):
            self.installed[identity]["version"] = version
            with self.assertRaisesRegex(ValueError, "Installed addon version mismatch"):
                self.plan()

    def test_duplicate_header_pins_are_rejected(self):
        header = self.paths[2].with_name("Mission.conf")
        header.write_text('GC_MissionHeader { World "{1111111111111111}Worlds/Test.ent" '
                          'm_aRequiredAddonVersions { "0000000000000006=1.0.25" "0000000000000006=1.0.43" } }')
        self.deployment["headerSha256"] = digest(header)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.plan()


if __name__ == "__main__":
    unittest.main()
