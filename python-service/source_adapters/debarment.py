class DebarmentAdapter:
    source_name = "debarment"

    def fetch(self, *args, **kwargs):
        raise RuntimeError("Debarment provider is not configured.")

    def normalize(self, *args, **kwargs):
        return []
