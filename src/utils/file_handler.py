import os
from pathlib import Path
from datetime import datetime
import shutil


def archive_file(file_path):
    """
    Archive the processed file with a timestamp in its filename.
    Returns the path of the archived file.
    """
    source_path = Path(file_path)
    archive_dir = source_path.parent / 'archive'
    
    # Create archive directory if it doesn't exist
    archive_dir.mkdir(exist_ok=True)
    
    # Generate new filename with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    new_filename = f"{source_path.stem}_{timestamp}{source_path.suffix}"
    archive_path = archive_dir / new_filename
    
    # Move the file to archive directory
    shutil.move(str(source_path), str(archive_path))
    print(f"Archived file to: {archive_path}")
    return archive_path
