import httpx
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, DataTable, Input, Button, Label, Markdown
from textual.widgets import TabbedContent, TabPane

DAEMON_URL = "http://127.0.0.1:8000"

class KryptoDBTUI(App):
    CSS = """
    #sidebar {
        width: 25%;
        border-right: solid green;
        padding: 1;
    }
    #main-content {
        width: 50%;
        padding: 1;
    }
    #right-sidebar {
        width: 25%;
        border-left: solid green;
        padding: 1;
    }
    .panel-title {
        background: $boost;
        color: $text;
        padding: 1;
        text-align: center;
        text-style: bold;
    }
    Input {
        margin-top: 1;
    }
    Button {
        margin-top: 1;
        width: 100%;
    }
    """
    
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh Data")
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("Active Partitions", classes="panel-title")
                yield DataTable(id="partition_table")
                yield Label("Manage Sessions (comma separated):")
                yield Input(placeholder="e.g. PROJECT:alpha, ORG:acme", id="partition_input")
                yield Button("Update Session", id="update_btn", variant="primary")
                yield Label("", id="status_label")
                
                yield Label("System Controls", classes="panel-title")
                yield Button("Start Daemon", id="btn_start_daemon", variant="success")
                yield Button("Stop Daemon", id="btn_stop_daemon", variant="error")
                yield Button("Load Test Data (Seed)", id="btn_seed_graph", variant="warning")
                
            with Vertical(id="main-content"):
                with TabbedContent():
                    with TabPane("Graph Edges", id="edges_tab"):
                        yield DataTable(id="edges_table")
                    with TabPane("Graph Nodes", id="nodes_tab"):
                        yield DataTable(id="nodes_table")
                        
            with Vertical(id="right-sidebar"):
                yield Label("Agent Configurator", classes="panel-title")
                yield Input(placeholder="Agent ID (e.g. campus-bot)", id="agent_id_config")
                yield Label("Discovered Scopes (Click to select):")
                yield DataTable(id="discovered_scopes_table")
                yield Input(placeholder="Selected Scopes", id="selected_scopes_config")
                yield Button("Generate config JSON", id="btn_save_agent", variant="primary")
                yield Label("Generated JSON Output:")
                yield Markdown("", id="agent_json_output")
                
        yield Footer()

    async def on_mount(self) -> None:
        pt = self.query_one("#partition_table", DataTable)
        pt.add_columns("Scope")
        
        et = self.query_one("#edges_table", DataTable)
        et.add_columns("Source", "Predicate", "Target", "Scope")
        
        nt = self.query_one("#nodes_table", DataTable)
        nt.add_columns("Node ID", "Type", "Scope", "Community ID")
        
        ds = self.query_one("#discovered_scopes_table", DataTable)
        ds.add_columns("Scope")
        ds.cursor_type = "row"
        
        await self.refresh_data()

    async def refresh_data(self) -> None:
        # Refresh Partitions
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(f"{DAEMON_URL}/session")
                if res.status_code == 200:
                    scopes = res.json().get("active_partitions", [])
                    pt = self.query_one("#partition_table", DataTable)
                    pt.clear()
                    for s in scopes:
                        pt.add_row(s)
        except Exception as e:
            self.query_one("#status_label", Label).update(f"[red]Error fetching session: {e}[/red]")
            
        # Refresh Scopes
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(f"{DAEMON_URL}/admin/scopes")
                if res.status_code == 200:
                    scopes = res.json().get("scopes", [])
                    ds = self.query_one("#discovered_scopes_table", DataTable)
                    ds.clear()
                    for s in scopes:
                        ds.add_row(s)
        except Exception:
            pass
            
        # Refresh Graph
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(f"{DAEMON_URL}/admin/graph")
                if res.status_code == 200:
                    data = res.json()
                    et = self.query_one("#edges_table", DataTable)
                    et.clear()
                    for edge in data.get("edges", []):
                        et.add_row(edge.get("source"), edge.get("predicate"), edge.get("target"), edge.get("scope"))
                        
                    nt = self.query_one("#nodes_table", DataTable)
                    nt.clear()
                    for node in data.get("nodes", []):
                        nt.add_row(node.get("id"), node.get("type"), node.get("scope"), str(node.get("community_id", "None")))
                        
                    self.query_one("#status_label", Label).update("[green]Graph refreshed[/green]")
        except Exception as e:
            self.query_one("#status_label", Label).update(f"[red]Error fetching graph: {e}[/red]")

    async def action_refresh(self) -> None:
        await self.refresh_data()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "discovered_scopes_table":
            scope = event.data_table.get_row_at(event.cursor_row)[0]
            inp = self.query_one("#selected_scopes_config", Input)
            current = [s.strip() for s in inp.value.split(",") if s.strip()]
            if scope not in current:
                current.append(scope)
            inp.value = ", ".join(current)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        import subprocess
        import os
        
        if event.button.id == "btn_save_agent":
            agent_id = self.query_one("#agent_id_config", Input).value.strip()
            scopes_str = self.query_one("#selected_scopes_config", Input).value
            scopes = [s.strip() for s in scopes_str.split(",") if s.strip()]
            
            if not agent_id:
                self.query_one("#status_label", Label).update("[red]Agent ID required[/red]")
                return
                
            try:
                async with httpx.AsyncClient() as client:
                    res = await client.post(
                        f"{DAEMON_URL}/admin/agents",
                        json={"agent_id": agent_id, "scopes": scopes}
                    )
                    if res.status_code == 200:
                        self.query_one("#status_label", Label).update("[green]Agent config updated (agents.json)[/green]")
                        self.query_one("#agent_id_config", Input).value = ""
                        self.query_one("#selected_scopes_config", Input).value = ""
                        
                        # Fetch the JSON response and format it as markdown code block
                        json_str = json.dumps(res.json(), indent=2)
                        self.query_one("#agent_json_output", Markdown).update(f"```json\n{json_str}\n```")
                    else:
                        self.query_one("#status_label", Label).update(f"[red]Failed to save agent: {res.text}[/red]")
            except Exception as e:
                self.query_one("#status_label", Label).update(f"[red]Error saving agent config: {e}[/red]")
                
        elif event.button.id == "update_btn":
            inp = self.query_one("#partition_input", Input).value
            scopes = [s.strip() for s in inp.split(",") if s.strip()]
            try:
                async with httpx.AsyncClient() as client:
                    res = await client.post(
                        f"{DAEMON_URL}/session", 
                        json={"active_partitions": scopes}
                    )
                    if res.status_code == 200:
                        self.query_one("#partition_input", Input).value = ""
                        self.query_one("#status_label", Label).update(f"[green]Session updated[/green]")
                        await self.refresh_data()
                    else:
                        self.query_one("#status_label", Label).update(f"[red]Failed: {res.text}[/red]")
            except Exception as e:
                self.query_one("#status_label", Label).update(f"[red]Error updating session: {e}[/red]")
                
        elif event.button.id == "btn_start_daemon":
            try:
                # Start daemon in a detached process
                with open(os.devnull, 'w') as devnull:
                    subprocess.Popen(
                        ["uvicorn", "synapse.daemon.server:app", "--port", "8000"], 
                        stdout=devnull, stderr=devnull
                    )
                self.query_one("#status_label", Label).update("[green]Daemon started in background.[/green]")
            except Exception as e:
                self.query_one("#status_label", Label).update(f"[red]Failed to start daemon: {e}[/red]")
                
        elif event.button.id == "btn_stop_daemon":
            try:
                os.system("pkill -f 'uvicorn synapse.daemon.server:app'")
                self.query_one("#status_label", Label).update("[yellow]Daemon stopped.[/yellow]")
            except Exception as e:
                self.query_one("#status_label", Label).update(f"[red]Failed to stop daemon: {e}[/red]")
                
        elif event.button.id == "btn_seed_graph":
            try:
                subprocess.run(["python", "seed_graph.py"], check=True)
                self.query_one("#status_label", Label).update("[green]Graph successfully seeded with test data.[/green]")
                await self.refresh_data()
            except Exception as e:
                self.query_one("#status_label", Label).update(f"[red]Failed to seed graph (is daemon running?): {e}[/red]")

def _cmd_bootstrap(argv: list[str]) -> None:
    """
    Secure first-run agent credential provisioning.

    Usage:
        synapse bootstrap \\
            --agent-id Agent-1 \\
            --scopes research,patients \\
            --admin-key-file /run/secrets/admin_key \\
            --out-file /run/secrets/agent_key \\
            [--url http://127.0.0.1:8000]

    Reads the admin API key from a file (never from an env var or argument).
    Creates the agent credential through the daemon.
    Writes the returned raw key to --out-file with mode 0600.
    Never prints the raw key value to stdout or logs.
    """
    import argparse, os, stat, sys

    parser = argparse.ArgumentParser(
        prog="synapse bootstrap",
        description="Provision an agent API key securely (no secrets in args or env).",
    )
    parser.add_argument("--agent-id", required=True, help="Agent identity to provision")
    parser.add_argument(
        "--scopes", required=True,
        help="Comma-separated list of scopes (must have customer keys configured in production)"
    )
    parser.add_argument(
        "--admin-key-file", required=True,
        help="Path to file containing the admin API key (mode 0600 recommended)"
    )
    parser.add_argument(
        "--out-file", required=True,
        help="Path where the new agent API key will be written (created with mode 0600)"
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Daemon base URL")
    args = parser.parse_args(argv)

    # Read admin key from file — never from argv or env
    admin_key_path = os.path.abspath(args.admin_key_file)
    try:
        with open(admin_key_path, "r") as fh:
            admin_key = fh.read().strip()
    except OSError as exc:
        print(f"ERROR: Cannot read admin key file {admin_key_path}: {exc}", file=sys.stderr)
        sys.exit(1)
    if not admin_key:
        print("ERROR: Admin key file is empty.", file=sys.stderr)
        sys.exit(1)

    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    if not scopes:
        print("ERROR: --scopes must not be empty.", file=sys.stderr)
        sys.exit(1)

    # Call the daemon — TLS/mTLS is the operator's responsibility at the network layer
    payload = {"agent_id": args.agent_id, "scopes": scopes}
    try:
        resp = httpx.post(
            f"{args.url.rstrip('/')}/admin/api-keys",
            json=payload,
            headers={"Authorization": f"Bearer {admin_key}"},
            timeout=10,
        )
    except Exception as exc:
        print(f"ERROR: Request to daemon failed: {exc}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code != 200:
        # Print error body to stderr — it does not contain the secret
        print(f"ERROR: Daemon returned {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    raw_key = data.get("api_key", "")
    if not raw_key:
        print("ERROR: Daemon response did not include api_key.", file=sys.stderr)
        sys.exit(1)

    # Write credential to the output file with mode 0600 — never print to stdout
    out_path = os.path.abspath(args.out_file)
    try:
        # Open with O_CREAT | O_WRONLY | O_TRUNC, mode 0600 — never world-readable
        fd = os.open(out_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(raw_key)
        # Ensure mode 0600 even if umask was permissive
        os.chmod(out_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        print(f"ERROR: Cannot write credential to {out_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    key_id = data.get("key_id", "?")
    # Only print non-secret metadata to stdout
    print(f"OK: Agent key provisioned.")
    print(f"    key_id  : {key_id}")
    print(f"    agent   : {args.agent_id}")
    print(f"    scopes  : {scopes}")
    print(f"    written : {out_path} (mode 0600)")
    print("    Deliver the credential file to the MCP runtime via a secret mount.")


def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "ui":
        import subprocess
        import os
        ui_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui")
        print("Starting KryptoDB UI...")
        try:
            subprocess.run(["npm", "run", "dev"], cwd=ui_path, check=True)
        except KeyboardInterrupt:
            print("\nShutting down UI...")
        except Exception as e:
            print(f"Failed to start UI: {e}")
            sys.exit(1)
        return

    if len(sys.argv) > 1 and sys.argv[1] == "bootstrap":
        _cmd_bootstrap(sys.argv[2:])
        return

    app = KryptoDBTUI()
    app.run()

if __name__ == "__main__":
    main()

