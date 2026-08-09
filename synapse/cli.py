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

    app = KryptoDBTUI()
    app.run()

if __name__ == "__main__":
    main()
