"""Dependency-free document contract inspired by LangChain Document."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Document:
    page_content: str
    metadata: dict

    def as_dict(self):
        return {"page_content": self.page_content, "metadata": self.metadata}
