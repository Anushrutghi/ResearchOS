import http.client
import json
from http.server import ThreadingHTTPServer
from threading import Thread
import tempfile
import unittest
from unittest.mock import patch

from researchos.security.auth import create_user, authenticate, revoke_user
from researchos.projects.store import ProjectStore, page_items
from researchos.plugins.registry import PluginRegistry, validate_manifest
from researchos.client import ResearchOSClient, ResearchOSError
from researchos.web import make_handler


def manifest(plugin_id="example.search", permissions=None):
    from researchos.plugins.registry import SEARCH_INPUT, SEARCH_OUTPUT
    return {"schema_version": 1, "plugin_id": plugin_id, "name": "Example search", "version": "1.0.0",
            "api_version": "1", "dependencies": {}, "permissions": permissions or ["external.network.read", "literature.search"],
            "capabilities": [{"capability_id": "search", "handler": "literature.openalex.search",
                "input_schema": SEARCH_INPUT, "output_schema": SEARCH_OUTPUT}]}


class EcosystemTests(unittest.TestCase):
    def test_auth_tokens_are_hashed_one_time_and_revocable(self):
        with tempfile.TemporaryDirectory() as directory:
            user, token = create_user(directory, "alice", "Alice")
            self.assertTrue(user["admin"])
            self.assertEqual(authenticate(directory, token)["username"], "alice")
            with open(f"{directory}/auth/users.json", encoding="utf-8") as file:
                saved = file.read()
            self.assertNotIn(token, saved)
            revoke_user(directory, "alice")
            self.assertIsNone(authenticate(directory, token))

    def test_project_roles_collections_and_revisions_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            owner, owner_token = create_user(directory, "owner", "Owner")
            editor, _ = create_user(directory, "editor", "Editor")
            viewer, _ = create_user(directory, "viewer", "Viewer")
            store = ProjectStore(directory)
            project = store.create({"title": "Study"}, owner)
            with self.assertRaisesRegex(ValueError, "owner's role"):
                store.add_member(project["project_id"], {"username": "owner", "role": "viewer"}, owner)
            store.add_member(project["project_id"], {"username": "editor", "role": "editor"}, owner)
            store.add_member(project["project_id"], {"username": "viewer", "role": "viewer"}, owner)
            with self.assertRaises(PermissionError):
                store.update(project["project_id"], {"title": "Denied"}, viewer)
            store.update(project["project_id"], {"title": "Updated"}, editor)
            project = store.create_collection(project["project_id"], {"title": "Sources", "records": [{"record_type": "paper", "record_id": "doi:10.x"}]}, editor)
            self.assertEqual(project["revision"], 5)
            self.assertEqual(store.get(project["project_id"], viewer)["collections"][0]["records"][0]["record_id"], "doi:10.x")
            self.assertEqual(store.revisions(project["project_id"], viewer)[0]["actor"], "editor")
            self.assertEqual(store.revisions(project["project_id"], viewer)[0]["snapshot"]["records"][0]["record_id"], "doi:10.x")
            self.assertEqual(page_items(list(range(5)), 2, None)["next_cursor"], "Mg")
            self.assertEqual(page_items(list(range(5)), 2, "Mg")["items"], [2, 3])

    def test_manifest_validation_and_plugin_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = PluginRegistry(directory)
            with self.assertRaises(ValueError):
                validate_manifest({**manifest(), "schema_version": True})
            installed = registry.install(manifest(permissions=["literature.search"]), "admin")
            self.assertEqual(installed["status"], "disabled")
            enabled = registry.set_enabled("example.search", True, "admin")
            self.assertEqual(enabled["status"], "enabled")
            with self.assertRaises(PermissionError):
                registry.run("example.search", "search", {"query": "climate", "limit": 5})

            registry.install(manifest("allowed.search"), "admin")
            registry.set_enabled("allowed.search", True, "admin")
            class Paper:
                def to_dict(self):
                    return {"work_id": "W1", "title": "A study"}
            with patch("researchos.plugins.registry.OpenAlexProvider") as provider_type:
                provider_type.return_value.search.return_value = [Paper()]
                output = registry.run("allowed.search", "search", {"query": "climate", "limit": 1})
            self.assertEqual(output["works"], [{"work_id": "W1", "title": "A study"}])
            self.assertEqual(output["provider"], "OpenAlex")

    def test_authenticated_routes_roles_and_client_pagination(self):
        with tempfile.TemporaryDirectory() as directory:
            owner, token = create_user(directory, "owner", "Owner")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(directory))
            thread = Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request("GET", "/api/projects")
                response = connection.getresponse(); response.read()
                self.assertEqual(response.status, 401)
                client = ResearchOSClient(f"http://127.0.0.1:{server.server_port}", token)
                project = client.create_project("Client project")["project"]
                self.assertEqual(client.get_project(project["project_id"])["project"]["owner"], "owner")
                self.assertEqual(client.project_lifecycle(project["project_id"])["lifecycle"]["valid_record_count"], 0)
                collection = client.create_collection(project["project_id"], "Papers", records=[])["project"]["collections"][0]
                client.update_collection(project["project_id"], collection["collection_id"], description="Reviewed source set")
                client.add_review(project["project_id"], "Collection reviewed", "approve", collection["collection_id"])
                client.create_project("Second project")
                self.assertEqual(len(list(client.iter_projects(page_size=1))), 2)
                reader, reader_token = create_user(directory, "reader", "Reader")
                client.add_member(project["project_id"], "reader", "viewer")
                reader_client = ResearchOSClient(f"http://127.0.0.1:{server.server_port}", reader_token)
                with self.assertRaises(ResearchOSError) as denied:
                    reader_client.update_project(project["project_id"], title="Forbidden")
                self.assertEqual(denied.exception.status, 403)
                self.assertEqual(ProjectStore(directory).get(project["project_id"], reader)["title"], "Client project")
                revisions = client.revisions(project["project_id"], limit=1)
                self.assertEqual(len(revisions["items"]), 1)
                self.assertTrue(revisions["next_cursor"])
                with self.assertRaises(ResearchOSError) as error:
                    ResearchOSClient(f"http://127.0.0.1:{server.server_port}", "bad").list_projects()
                self.assertEqual(error.exception.status, 401)
                connection.close()
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
