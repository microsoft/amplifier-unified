This is Amplifier Unified's web UI on a synthetic test fixture: no model and no external service; replies come from the fixture. It starts with one chat, "Settings test", with saved messages. Create other chats and workspaces in the UI as a user would.

Open the URL as it is. A proxy in the twin adds the fixture's test credentials to every request, WebSockets included, so there is no login and no header to add.
