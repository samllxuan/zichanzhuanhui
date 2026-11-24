def select_source(row):
    """Return the original image URL or the reason this asset is skipped."""
    for field, reason in (("primaryImage", "未定妆"), ("sourceImage", "缺少原图")):
        image = row.get(field)
        url = image.get("url") if isinstance(image, dict) else None
        if not isinstance(url, str) or not url.strip():
            return None, reason
    return url.strip(), None
