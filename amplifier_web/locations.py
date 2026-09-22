"""Folder picker operations; never register a workspace or create a chat."""
from pathlib import Path

def create_folder(parent, name):
    if not name.strip() or name != name.strip() or name in {'.', '..'} or any(char in name for char in '/\\\x00'):
        raise ValueError('Enter a folder name without slashes or leading/trailing spaces.')
    parent = Path(parent).expanduser()
    if not parent.is_absolute():
        raise ValueError('Choose an absolute parent folder first.')
    parent = parent.resolve()
    if not parent.is_dir():
        raise ValueError('The parent folder is no longer available. Browse to another folder.')
    path = parent / name
    try:
        path.mkdir()
    except FileExistsError:
        raise ValueError('A file or folder with this name already exists. Choose another name.') from None
    except PermissionError:
        raise ValueError('You do not have permission to create a folder here.') from None
    return path
