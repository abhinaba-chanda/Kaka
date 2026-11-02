"""Kaka action - write a letter / application / essay into a file and open it (update 8)."""
from actions.email_draft import write_document

TOOL = {
    "name": "write_document",
    "description": (
        "Writes a letter, application, essay, notes or any text document: you write "
        "the full text in 'content', it is SAVED as a real file (Kaka Saved > "
        "Documents) and OPENED in Notepad (default) or Word (open_in='word'). "
        "'Principal ko leave application likho', 'Notepad mein ek essay likh do', "
        "'Word mein resume likho' -> this tool. Code goes to code_helper, emails to "
        "draft_email."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "title": {"type": "STRING", "description": "Short title, used for the file name"},
            "content": {"type": "STRING", "description": "The complete text of the document"},
            "open_in": {"type": "STRING", "description": "notepad (default) | word"},
        },
        "required": ["content"],
    },
    "handler": write_document,
}
