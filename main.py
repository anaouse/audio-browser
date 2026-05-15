from pathlib import Path
from textual.app import App, ComposeResult
from textual.widgets import Tree, Header, Footer
from textual.binding import Binding
from just_playback import Playback


class AudioBrowserApp(App):
    """A Textual TUI app to browse and play wav files."""

    TITLE = "Audio File Browser"

    BINDINGS = [
        Binding("q", "quit", "Quit")
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        tree = Tree("[bold yellow]audio/[/]")
        yield tree
        yield Footer()

    def on_mount(self) -> None:
        """Called automatically when the app starts."""
        tree = self.query_one(Tree)
        tree.root.collapse()

        audio_dir = Path("./audio")

        if not audio_dir.exists() or not audio_dir.is_dir():
            tree.root.add_leaf("[dim italic]No ./audio directory found[/]")
            return

        # Preload all .wav files as Playback objects, stored on each leaf's data
        # Root-level .wav files
        for file_path in sorted(audio_dir.glob("*.wav")):
            pb = Playback(str(file_path))
            leaf = tree.root.add_leaf(
                f"[#e4e4e4]♪ {file_path.name}[/]",
                data=pb,
            )

        # Subdirectories and their .wav files
        for sub_dir in sorted(audio_dir.iterdir()):
            if sub_dir.is_dir():
                sub_node = tree.root.add(
                    f"[bold blue]{sub_dir.name}/[/]",
                    expand=False,
                )
                for wav_file in sorted(sub_dir.glob("*.wav")):
                    pb = Playback(str(wav_file))
                    leaf = sub_node.add_leaf(
                        f"[#e4e4e4]♪ {wav_file.name}[/]",
                        data=pb,
                    )

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Play the WAV file when a leaf node is clicked."""
        node_data = event.node.data
        if isinstance(node_data, Playback):
            node_data.play()

if __name__ == "__main__":
    app = AudioBrowserApp()
    app.run()
