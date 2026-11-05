"""Shared attachment formats and upload limits."""

MAX_FILE = 20 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024
ATTACHMENTS = {
    "resume": {".pdf", ".jpg", ".jpeg", ".png"},
    "englishResume": {".pdf"},
    "photo": {".jpg", ".jpeg", ".png"},
    **{
        k: {".pdf", ".jpg", ".jpeg", ".png"}
        for k in [
            "transcript",
            "cet4",
            "cet6",
            "language",
            "certificates",
            "education",
            "internship",
        ]
    },
    "portfolio": {".pdf", ".jpg", ".jpeg", ".png", ".zip"},
    "other": {".pdf", ".jpg", ".jpeg", ".png", ".zip"},
}
