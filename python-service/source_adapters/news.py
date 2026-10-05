class NewsAdapter:
    source_name = "news"

    def fetch(self, *args, **kwargs):
        raise RuntimeError("News is not configured. Verify licensing before enabling it.")

    def normalize(self, *args, **kwargs):
        return []
