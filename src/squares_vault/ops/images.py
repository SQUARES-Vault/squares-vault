"""Image processing utilities."""

import io
from PIL import Image, ImageOps


def process_image(img_data: bytes, max_size: int = 2400) -> bytes:
    """Resize image and strip EXIF data.
    
    1. Removes all EXIF metadata for privacy.
    2. Resizes so the longest edge is at most max_size (default 2400px).
    3. Converts to standard JPEG to save space.
    """
    with Image.open(io.BytesIO(img_data)) as img:
        # ImageOps.exif_transpose applies the rotation from EXIF before we strip it
        img = ImageOps.exif_transpose(img)
        
        # Convert to RGB (in case of RGBA/PNG)
        if img.mode != "RGB":
            img = img.convert("RGB")
            
        # Resize if too large
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        
        # Save without EXIF
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85)
        return out.getvalue()
