"""
Cobblestone Real Estate — PDF Compression Backend
===================================================
Deployed on Railway. Accepts a PDF upload, compresses it using PyMuPDF
(which uses MuPDF under the hood — comparable to Ghostscript quality),
and returns the compressed PDF.

Endpoints:
  POST /compress   — accepts multipart PDF, returns compressed PDF
  GET  /health     — returns {"status": "ok"}
"""

import io
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import fitz  # PyMuPDF

app = FastAPI(title="Cobblestone PDF Compressor")

# Allow requests from your Cloudflare Pages site.
# Add your actual domain once you know it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this to your Pages URL after testing
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/compress")
async def compress_pdf(
    file: UploadFile = File(...),
    preset: str = "balanced",   # "high" | "balanced" | "small"
):
    # Validate file type
    if not (file.filename or "").lower().endswith(".pdf") and file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Preset → MuPDF compression settings
    # deflate_images controls JPEG recompression quality (0–100)
    presets = {
        "high":     {"image_quality": 85, "deflate": True},
        "balanced": {"image_quality": 65, "deflate": True},
        "small":    {"image_quality": 45, "deflate": True},
    }
    settings = presets.get(preset, presets["balanced"])

    try:
        doc = fitz.open(stream=data, filetype="pdf")

        # Re-compress all images in every page at the chosen quality
        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    # Get the image and re-compress it
                    base_image = doc.extract_image(xref)
                    img_bytes  = base_image["image"]
                    img_ext    = base_image["ext"]

                    # Only recompress JPEG and PNG — leave others alone
                    if img_ext.lower() in ("jpeg", "jpg", "png"):
                        pix = fitz.Pixmap(doc, xref)
                        if pix.alpha:
                            pix = fitz.Pixmap(fitz.csRGB, pix)  # strip alpha for JPEG
                        new_bytes = pix.tobytes(
                            output="jpeg",
                            jpg_quality=settings["image_quality"]
                        )
                        if len(new_bytes) < len(img_bytes):
                            doc.update_stream(xref, new_bytes)
                        pix = None
                except Exception:
                    pass  # skip any image that can't be recompressed

        compressed = doc.tobytes(
            deflate=settings["deflate"],   # compress all PDF streams
            garbage=4,                      # remove all unused/duplicate objects
            clean=True,                     # sanitize content streams
            deflate_images=True,            # compress image streams
            deflate_fonts=True,             # compress font streams
            linear=False,
        )
        doc.close()

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Compression failed: {str(e)}")

    original_size    = len(data)
    compressed_size  = len(compressed)

    # Never return a larger file than what was uploaded
    output = compressed if compressed_size < original_size else data

    return Response(
        content=output,
        media_type="application/pdf",
        headers={
            "X-Original-Size":    str(original_size),
            "X-Compressed-Size":  str(compressed_size),
            "X-Pct-Saved":        f"{max(0, (original_size - compressed_size) / original_size * 100):.1f}",
            "Content-Disposition": f'attachment; filename="{file.filename}"',
        },
    )
