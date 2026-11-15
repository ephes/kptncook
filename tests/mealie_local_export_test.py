"""Real loopback HTTP to the existing synthetic Mealie archive receiver."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from mealie_organizer_lookup_test import FakeMealie
from kptncook.mealie import MealieApiClient, kptncook_to_mealie
from kptncook.models import Recipe


def test_full_note_and_literal_steps_reach_local_mealie(minimal):
    backend = FakeMealie()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            reply = backend.handle(
                httpx.Request(
                    self.command,
                    "http://127.0.0.1" + self.path,
                    headers=dict(self.headers),
                    content=body,
                )
            )
            self.send_response(reply.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply.content)))
            self.end_headers()
            self.wfile.write(reply.content)

        do_GET = do_POST = respond

    minimal["imageList"] = []
    minimal["steps"][0].update(
        title={"de": 'Keep < 5 & <img src=x onerror="invented()"> <timer>.'},
        image={"name": "synthetic", "url": "https://not-allowed.invalid/image"},
    )
    minimal["ingredients"] = [
        {
            "quantity": 1,
            "measure": "g",
            "ingredient": {
                "typ": "regular",
                "category": "vegetables",
                "localizedTitle": {"de": "tomatoes, peeled, roughly chopped"},
            },
        }
    ]
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(timeout=5, trust_env=False) as http:
            client = MealieApiClient(
                f"http://127.0.0.1:{server.server_port}/api", client=http
            )
            client.create_recipe(
                kptncook_to_mealie(Recipe.model_validate(minimal), api_key="synthetic")
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    assert not thread.is_alive()
    [payload] = backend.imported
    assert payload["recipe_ingredient"][0]["note"] == "peeled, roughly chopped"
    assert (
        payload["recipe_instructions"][0]["text"]
        == 'Keep &lt; 5 &amp; &lt;img src=x onerror="invented()"&gt; `<timer>`.'
    )
