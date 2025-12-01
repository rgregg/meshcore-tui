from abc import ABC, abstractmethod
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, VerticalScroll, Horizontal, HorizontalScroll, Vertical
from textual.widgets import Input, Markdown, Static, Collapsible, Footer, LoadingIndicator, ListView, ListItem, Label, Header, Button
from textual.screen import Screen, ModalScreen
from data import BaseContainerItem, BaseMessage, BaseDataProvider, FakeDataProvider, DataUpdate, ChannelMessage, UserMessage

class PromptDialog(ModalScreen[bool]):
    """Implements a Screen with a split view"""
    CSS_PATH = "prompt.tcss"
    prompt_text: str
    ok_button_text: str = "OK"
    cancel_button_text: str = "Cancel"

    def __init__(self, text: str) -> None:
        super().__init__()
        self.prompt_text = text

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.prompt_text)
            with Horizontal():
                yield Button(self.ok_button_text, id="ButtonOk", classes="accept")
                yield Button(self.cancel_button_text, id="ButtonCancel", classes="decline")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ButtonOK":
            self.dismiss(True)
        else:
            self.dismiss(False)

    